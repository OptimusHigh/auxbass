import {
  getCachedUrl,
  setCachedUrl,
  preloadTrackWithAudio,
  getPreloadTrackId,
  markTrackUnstreamable,
} from './playerCache'

import {
  hasCachedTrack,
  saveTrackToCache,
  evictOldTracksIfNeeded,
} from '../utils/audioCacheDb'

// ============== Adaptive Preload System ==============
let _userInteractionTime = 0
const USER_INTERACTION_COOLDOWN = 1000

export const markUserInteraction = () => {
  _userInteractionTime = Date.now()
}

export const isUserActivelyBrowsing = () => {
  if (typeof document !== 'undefined' && document.hidden) return false
  return Date.now() - _userInteractionTime < USER_INTERACTION_COOLDOWN
}

// Active preload controllers
const _preloadingTracks = new Map()

/**
 * Cancel preloads that are no longer relevant (not in next N positions).
 *
 * @param {Set<number>} relevantIds - track IDs that should be kept
 */
export function cancelIrrelevantPreloads(relevantIds) {
  for (const [trackId, controller] of _preloadingTracks.entries()) {
    if (!relevantIds.has(trackId)) {
      console.log(`[Preload] Cancelling irrelevant preload: track ${trackId}`)
      controller.abort()
      _preloadingTracks.delete(trackId)
    }
  }
}

/**
 * Collect the set of track IDs that are relevant for the next 1-3 positions.
 * Works for all modes: lazy shuffle, regular shuffle, normal.
 */
export function collectRelevantIds({
  isLazy, lazyShuffleIds, lazyShuffleIndex,
  shuffle, shuffleOrder, shuffleIndex,
  queue, queueIndex, repeat
}) {
  const ids = new Set()
  const N = 3

  if (isLazy) {
    for (let off = 1; off <= N; off++) {
      let idx = lazyShuffleIndex + off
      if (idx >= lazyShuffleIds.length) {
        if (repeat === 'all') idx = idx % lazyShuffleIds.length
        else continue
      }
      ids.add(lazyShuffleIds[idx])
    }
  } else if (shuffle && shuffleOrder.length > 0) {
    for (let off = 1; off <= N; off++) {
      let si = shuffleIndex + off
      if (si >= shuffleOrder.length) {
        if (repeat === 'all') si = si % shuffleOrder.length
        else continue
      }
      const qi = shuffleOrder[si]
      if (queue[qi]) ids.add(queue[qi].id)
    }
  } else {
    for (let off = 1; off <= N; off++) {
      let idx = queueIndex + off
      if (idx >= queue.length) {
        if (repeat === 'all') idx = idx % queue.length
        else continue
      }
      if (queue[idx]) ids.add(queue[idx].id)
    }
  }
  return ids
}

/**
 * Collect track objects/IDs that need preloading (not yet cached).
 * Returns { trackIds: number[], nextTrack: object|null }
 */
export function collectTracksToPreload({
  isLazy, lazyShuffleIds, lazyShuffleIndex,
  shuffle, shuffleOrder, shuffleIndex,
  queue, queueIndex, repeat,
  getNextTrackForPreload
}) {
  const idsToFetch = []
  const N = 3

  if (isLazy) {
    for (let off = 1; off <= N; off++) {
      let idx = lazyShuffleIndex + off
      if (idx >= lazyShuffleIds.length) {
        if (repeat === 'all' && idx < lazyShuffleIds.length + N) {
          idx = idx % lazyShuffleIds.length
        } else continue
      }
      const tid = lazyShuffleIds[idx]
      if (!getCachedUrl(tid)) idsToFetch.push(tid)
    }
    return { trackIds: idsToFetch, nextTrack: null, isLazy: true }
  }

  const tracksToPreload = []
  if (shuffle && shuffleOrder.length > 0) {
    for (let off = 1; off <= N; off++) {
      let si = shuffleIndex + off
      if (si >= shuffleOrder.length) {
        if (repeat === 'all' && si < shuffleOrder.length + N) {
          si = si % shuffleOrder.length
        } else continue
      }
      const qi = shuffleOrder[si]
      const t = queue[qi]
      if (t && !getCachedUrl(t.id)) tracksToPreload.push(t)
    }
  } else {
    for (let off = 1; off <= N; off++) {
      let idx = queueIndex + off
      if (idx >= queue.length) {
        if (repeat === 'all') idx = idx % queue.length
        else break
      }
      const t = queue[idx]
      if (t && !getCachedUrl(t.id)) tracksToPreload.push(t)
    }
  }

  return {
    trackIds: tracksToPreload.map(t => t.id),
    tracks: tracksToPreload,
    nextTrack: getNextTrackForPreload ? getNextTrackForPreload() : null,
    isLazy: false
  }
}

/**
 * Execute the batch URL fetch and start Audio-element preloading.
 *
 * @param {Object} params
 * @param {number[]} params.trackIds
 * @param {Function} params.getBatchUrls — playerApi.getBatchUrls
 * @param {Object|null} params.nextTrack — object with .id for Audio preload
 * @param {Function} params.onNextPreloaded — setter for nextTrackPreloaded ref
 */
export async function executeBatchPreload({ trackIds, getBatchUrls, nextTrack, onNextPreloaded }) {
  if (trackIds.length === 0) {
    // All cached — just ensure Audio preload for immediate next
    if (nextTrack) {
      const url = getCachedUrl(nextTrack.id)
      if (url && getPreloadTrackId() !== nextTrack.id) {
        preloadTrackWithAudio(nextTrack.id, url)
        if (onNextPreloaded) onNextPreloaded({ track: nextTrack, url, audioPreloaded: true })
      }
    }
    return
  }

  try {
    console.log(`[Preload] Fetching batch URLs for ${trackIds.length} tracks`)
    const response = await getBatchUrls(trackIds)
    const urlData = response.data.urls || []

    for (const item of urlData) {
      if (item.url && !item.error && item.url.startsWith('/api/player/audio/')) {
        setCachedUrl(item.track_id, item.url, item.expires_at)
      } else if (item.error) {
        console.warn(`[Preload] Track ${item.track_id} error: ${item.error}`)
        markTrackUnstreamable(item.track_id)
      }
    }

    // Start Audio preload for immediate next
    if (nextTrack) {
      const nextUrl = getCachedUrl(nextTrack.id)
      if (nextUrl && getPreloadTrackId() !== nextTrack.id) {
        preloadTrackWithAudio(nextTrack.id, nextUrl)
        if (onNextPreloaded) onNextPreloaded({ track: nextTrack, url: nextUrl, audioPreloaded: true })
      }
    }

    console.log(`[Preload] Cached ${urlData.filter(u => u.url).length} URLs`)
  } catch (e) {
    console.error('[Preload] Batch URL fetch failed:', e)
  }
}

// Active background auto-caching controllers
const _cachingTracks = new Map()

/**
 * Cache a track in background using its active/prefetched stream URL.
 * Does not block audio playback.
 *
 * @param {Object} track - track metadata
 * @param {string} streamUrl - audio stream URL
 * @param {number} [maxCacheBytes] - max allowed cache size for LRU eviction
 */
export async function cacheTrackInBackground(track, streamUrl, maxCacheBytes = 1073741824) {
  if (!track || !track.id || !streamUrl || _cachingTracks.has(track.id)) return
  if (track.is_chunk) {
    console.log(`[AutoCache] Skipping background caching for preview chunk "${track.title}" (track ${track.id})`)
    return
  }

  // Check if already in IndexedDB
  const isCached = await hasCachedTrack(track.id)
  if (isCached) return

  const controller = new AbortController()
  _cachingTracks.set(track.id, controller)

  try {
    console.log(`[AutoCache] Background caching started for "${track.title}" (track ${track.id})`)
    const response = await fetch(streamUrl, { signal: controller.signal })
    if (!response.ok) return

    const blob = await response.blob()
    if (blob.size > 0) {
      await saveTrackToCache(track, blob, blob.type || 'audio/mpeg')
      if (maxCacheBytes > 0) {
        await evictOldTracksIfNeeded(maxCacheBytes)
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.warn(`[AutoCache] Failed to cache track "${track.title}":`, e.message)
    }
  } finally {
    if (_cachingTracks.get(track.id) === controller) {
      _cachingTracks.delete(track.id)
    }
  }
}
