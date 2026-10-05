/**
 * Player Store — Core
 *
 * Refactored from a 2728-line god-object into a ~900-line core that delegates to:
 *   - playerAudioEngine.js  — audio element lifecycle, event listeners (BUG-3 fix)
 *   - playerEnhancer.js     — WebAudio EQ / compressor
 *   - playerShuffle.js      — Fisher-Yates shuffle order, lazy shuffle helpers
 *   - playerPreload.js      — batch URL fetch, adaptive preload, blob caching
 *   - playerMediaSession.js — lock screen, media keys, keyboard shortcuts
 *   - playerStallRecovery.js — stall detection, retry, cascading-skip guard
 *   - playerResolver.js     — resolveAudioSource (eliminates 4× priority-cascade duplication)
 *   - playerCache.js        — URL + blob cache (unchanged)
 *   - playerStorage.js      — localStorage persistence (unchanged)
 */
import { defineStore } from 'pinia'
import { ref, watch, computed } from 'vue'
import { playerApi, tracksApi, playlistsApi, albumsApi, socialApi } from '../api/client'
import { useNetworkMonitor } from '../composables/useNetworkMonitor'
import { useAuthStore } from './auth'

// --- Extracted modules ---
import {
  setupAudioListeners,
  cleanupAudioListeners,
  isTrackNotStreamable,
  createAudioElement,
} from './playerAudioEngine'

import {
  initAudioContext,
  connectAudioSource as connectEnhancer,
  updateEnhancerParams as _updateEnhancer,
  startAudioKeepAlive,
  stopAudioKeepAlive,
} from './playerEnhancer'

import {
  generateShuffleOrder as genShuffle,
  getNextTrackIndex as calcNextIdx,
} from './playerShuffle'

import {
  markUserInteraction,
  isUserActivelyBrowsing,
  cancelIrrelevantPreloads,
  collectRelevantIds,
  collectTracksToPreload,
  executeBatchPreload,
  cacheTrackInBackground,
} from './playerPreload'
import { requestStoragePersistence, hasCachedTrack } from '../utils/audioCacheDb'

import {
  updateMediaSession as _updateMS,
  updatePlaybackState as _updatePBS,
  updatePositionState as _updatePS,
  setupMediaSession,
  setupKeyboardShortcuts,
} from './playerMediaSession'

import {
  clearStallTimer,
  startStallTimer,
  resetStallRetry,
  handleStallTimeout,
  checkCascadingSkips,
  resetSkipCount,
  getAudioRetryCount,
  incrementAudioRetry,
  resetAudioRetry,
  getAudioRetryDelay,
  getMaxAudioRetries,
} from './playerStallRecovery'

import { resolveAudioSource } from './playerResolver'

// --- Existing helpers ---
import {
  loadSettings, saveSettings, loadPlayerState, savePlayerState,
  clearPlayerState, STATE_SAVE_INTERVAL,
} from './playerStorage'

import {
  getCachedUrl, setCachedUrl, deleteCachedUrl,
  getCachedAudio, setCachedAudio, deleteCachedAudio,
  preloadTrackWithAudio, getPreloadedAudio,
  clearPreloadAudio, getPreloadTrackId, recyclePreloadAudio,
} from './playerCache'

import { getDisplayTitle, getDisplayArtist } from '../utils/formatters'

// Load saved settings/state
const savedSettings = loadSettings()
const savedState = loadPlayerState()

export const usePlayerStore = defineStore('player', () => {
  // ===================== STATE =====================
  const audio = ref(null)
  const currentTrack = ref(null)
  const queue = ref([])
  const queueIndex = ref(-1)
  const shuffleOrder = ref([])
  const shuffleIndex = ref(-1)
  const isPlaying = ref(false)
  const progress = ref(0)
  const duration = ref(0)
  const volume = ref(savedSettings.volume ?? 1)
  const isMuted = ref(savedSettings.isMuted ?? false)
  const shuffle = ref(savedSettings.shuffle ?? false)
  const repeat = ref(savedSettings.repeat ?? 'none')
  const uiScale = ref(savedSettings.uiScale ?? 1.0)

  // Enhancer
  const enhancerEnabled = ref(savedSettings.enhancerEnabled ?? false)
  const bassGain = ref(savedSettings.bassGain ?? 0)
  const trebleGain = ref(savedSettings.trebleGain ?? 0)
  const autoGain = ref(savedSettings.autoGain ?? false)

  // Auto-Cache Settings
  const autoCacheEnabled = ref(savedSettings.autoCacheEnabled ?? true)
  const cacheMaxBytes = ref(savedSettings.cacheMaxBytes ?? 1073741824) // 1 GB default

  const loading = ref(false)
  const isChangingTrack = ref(false)
  const buffered = ref(0)
  const nextTrackPreloaded = ref(null)
  const lastError = ref(null)
  const stateRestored = ref(false)
  const hdTrackInfo = ref(null)

  // Lazy shuffle
  const lazyShuffleIds = ref(savedState?.lazyShuffleIds || [])
  const lazyShuffleIndex = ref(savedState?.lazyShuffleIndex ?? -1)
  const lazyShuffleContext = ref(savedState?.lazyShuffleContext || null)
  const lazyUpcomingTracks = ref([])

  // Playback Context & Recent Playlists
  const RECENT_PLAYLISTS_KEY = 'tg_player_recent_playlists'
  const loadRecentPlaylists = () => {
    try {
      const saved = localStorage.getItem(RECENT_PLAYLISTS_KEY)
      return saved ? JSON.parse(saved) : []
    } catch {
      return []
    }
  }

  const recentPlaylists = ref(loadRecentPlaylists())
  const playbackContext = ref(savedState?.playbackContext || null)

  const recordPlaylistPlayed = (playlistId) => {
    const numId = Number(playlistId)
    if (!numId) return
    const now = Date.now()
    const filtered = recentPlaylists.value.filter(item => item.id !== numId)
    recentPlaylists.value = [{ id: numId, lastPlayedAt: now }, ...filtered].slice(0, 30)
    try {
      localStorage.setItem(RECENT_PLAYLISTS_KEY, JSON.stringify(recentPlaylists.value))
    } catch (_) {}
    window.dispatchEvent(new CustomEvent('playlists:recent-updated', { detail: { id: numId } }))
  }

  const setPlaybackContext = (ctx) => {
    if (ctx && typeof ctx === 'object' && ctx.type) {
      playbackContext.value = ctx
      if (ctx.type === 'playlist' && ctx.id) {
        recordPlaylistPlayed(ctx.id)
      }
    } else if (ctx === null) {
      playbackContext.value = null
    }
  }

  const currentPlaylistId = computed(() => {
    if (lazyShuffleContext.value?.type === 'playlist') {
      return Number(lazyShuffleContext.value.id)
    }
    if (playbackContext.value?.type === 'playlist') {
      return Number(playbackContext.value.id)
    }
    return null
  })

  const currentAlbumId = computed(() => {
    if (lazyShuffleContext.value?.type === 'album') {
      return Number(lazyShuffleContext.value.id)
    }
    if (playbackContext.value?.type === 'album') {
      return Number(playbackContext.value.id)
    }
    if (!lazyShuffleContext.value && !playbackContext.value) {
      const aId = currentTrack.value?.album_id || currentTrack.value?.album?.id
      return aId ? Number(aId) : null
    }
    return null
  })

  const currentContext = computed(() => {
    return lazyShuffleContext.value || playbackContext.value || null
  })

  // Private flags
  let stateSaveInterval = null
  let preloadTriggered = false
  let preloadNearEndTriggered = false
  let isSkipping = false
  let _playGeneration = 0
  let shuffleInProgress = false
  let lastPositionUpdate = 0

  // Callback for track unavailable
  let onTrackUnavailableCallback = null
  const setOnTrackUnavailable = (cb) => { onTrackUnavailableCallback = cb }

  // ===================== NETWORK RECOVERY =====================
  const _networkMonitor = useNetworkMonitor()
  watch(() => _networkMonitor.networkRecovered.value, (recovered) => {
    if (recovered && currentTrack.value && !isPlaying.value && lastError.value?.type === 'stall_timeout') {
      console.log('[Player] Network recovered, retrying last stalled track')
      lastError.value = null
      resetStallRetry()
      play(currentTrack.value)
    }
  })

  // ===================== ENHANCER WATCHERS =====================
  watch([enhancerEnabled, bassGain, trebleGain, autoGain], () => {
    _updateEnhancer({
      enabled: enhancerEnabled.value,
      bass: bassGain.value,
      treble: trebleGain.value,
      autoGain: autoGain.value,
    })
    persistSettings()
  })
  watch(uiScale, () => persistSettings())
  watch([autoCacheEnabled, cacheMaxBytes], () => persistSettings())

  // ===================== HELPERS =====================
  const isLazyShuffleMode = () => lazyShuffleIds.value.length > 0 && lazyShuffleIndex.value >= 0

  /**
   * Search ahead in queue for the next track cached in IndexedDB (used during offline mode)
   */
  const findNextCachedTrackInQueue = async () => {
    if (!queue.value || !queue.value.length) return null
    const isShuff = shuffle.value && shuffleOrder.value && shuffleOrder.value.length > 0
    const startIndex = (isShuff ? shuffleIndex.value : queueIndex.value) + 1
    const total = isShuff ? shuffleOrder.value.length : queue.value.length

    for (let i = startIndex; i < total; i++) {
      const idx = isShuff ? shuffleOrder.value[i] : i
      const candidate = queue.value[idx]
      if (candidate?.id && await hasCachedTrack(candidate.id)) {
        return candidate
      }
    }
    return null
  }

  const updateLazyUpcomingTracks = async () => {
    if (!isLazyShuffleMode()) {
      lazyUpcomingTracks.value = []
      return
    }
    const upcomingIds = []
    const N = 5
    for (let i = 1; i <= N; i++) {
      let idx = lazyShuffleIndex.value + i
      if (idx >= lazyShuffleIds.value.length) {
        if (repeat.value === 'all') idx = idx % lazyShuffleIds.value.length
        else break
      }
      upcomingIds.push(lazyShuffleIds.value[idx])
    }
    if (!upcomingIds.length) {
      lazyUpcomingTracks.value = []
      return
    }
    try {
      const loaded = await Promise.all(upcomingIds.map(id => loadTrackById(id)))
      lazyUpcomingTracks.value = loaded.filter(Boolean)
    } catch (e) {
      console.warn('[Lazy Upcoming] Failed to load:', e)
    }
  }

  const clearLazyShuffle = () => {
    lazyShuffleIds.value = []
    lazyShuffleIndex.value = -1
    lazyShuffleContext.value = null
    lazyUpcomingTracks.value = []
  }

  const generateShuffleOrder = (startingIndex = -1) => {
    const result = genShuffle(queue.value.length, startingIndex)
    shuffleOrder.value = result.order
    shuffleIndex.value = result.index
  }

  const getNextTrackIndex = () => calcNextIdx({
    shuffle: shuffle.value,
    shuffleOrder: shuffleOrder.value,
    shuffleIndex: shuffleIndex.value,
    queueIndex: queueIndex.value,
    queueLength: queue.value.length,
    repeat: repeat.value,
  })

  const getNextTrackForPreload = () => {
    const idx = getNextTrackIndex()
    return idx === -1 ? null : queue.value[idx] || null
  }

  const _trackCache = new Map()

  const loadTrackById = async (trackId) => {
    if (!trackId) return null
    if (_trackCache.has(trackId)) {
      return _trackCache.get(trackId)
    }
    const inUpcoming = lazyUpcomingTracks.value.find(t => t.id === trackId)
    if (inUpcoming) {
      _trackCache.set(trackId, inUpcoming)
      return inUpcoming
    }
    const inQueue = queue.value.find(t => t.id === trackId)
    if (inQueue) {
      _trackCache.set(trackId, inQueue)
      return inQueue
    }
    try {
      const r = await tracksApi.getOne(trackId)
      if (r?.data) {
        _trackCache.set(trackId, r.data)
        return r.data
      }
    } catch (e) {
      console.error(`[Lazy Shuffle] Failed to load track ${trackId}:`, e)
    }
    return null
  }

  // ===================== RELEVANT IDS HELPER =====================
  const _collectRelevantIds = () => collectRelevantIds({
    isLazy: isLazyShuffleMode(),
    lazyShuffleIds: lazyShuffleIds.value,
    lazyShuffleIndex: lazyShuffleIndex.value,
    shuffle: shuffle.value,
    shuffleOrder: shuffleOrder.value,
    shuffleIndex: shuffleIndex.value,
    queue: queue.value,
    queueIndex: queueIndex.value,
    repeat: repeat.value,
  })

  // ===================== MEDIA SESSION WRAPPERS =====================
  const updateMediaSession = () => _updateMS(currentTrack.value, updatePlaybackState)
  const updatePlaybackState = () => _updatePBS(isPlaying.value)
  const updatePositionState = () => _updatePS(audio.value, progress.value, duration.value)

  // ===================== PERSISTENCE =====================
  const persistSettings = () => {
    saveSettings({
      volume: volume.value, isMuted: isMuted.value,
      shuffle: shuffle.value, repeat: repeat.value,
      uiScale: uiScale.value,
      enhancerEnabled: enhancerEnabled.value,
      bassGain: bassGain.value, trebleGain: trebleGain.value,
      autoGain: autoGain.value,
      autoCacheEnabled: autoCacheEnabled.value,
      cacheMaxBytes: cacheMaxBytes.value,
    })
  }

  const persistState = () => {
    if (!currentTrack.value || currentTrack.value.is_chunk) { clearPlayerState(); return }
    savePlayerState({
      currentTrack: currentTrack.value,
      queue: queue.value.filter(t => !t.is_chunk),
      queueIndex: queueIndex.value,
      progress: progress.value,
      duration: duration.value,
      playbackContext: playbackContext.value,
      lazyShuffleIds: lazyShuffleIds.value,
      lazyShuffleIndex: lazyShuffleIndex.value,
      lazyShuffleContext: lazyShuffleContext.value,
    })
  }

  const startStateSaving = () => {
    if (stateSaveInterval) return
    stateSaveInterval = setInterval(() => {
      if (currentTrack.value && isPlaying.value) persistState()
    }, STATE_SAVE_INTERVAL)
  }

  const stopStateSaving = () => {
    if (stateSaveInterval) { clearInterval(stateSaveInterval); stateSaveInterval = null }
  }

  // ===================== PRELOAD ORCHESTRATOR =====================
  const preloadNextTracks = async () => {
    if (isUserActivelyBrowsing()) {
      setTimeout(() => preloadNextTracks(), 1000)
      return
    }

    const params = {
      isLazy: isLazyShuffleMode(),
      lazyShuffleIds: lazyShuffleIds.value,
      lazyShuffleIndex: lazyShuffleIndex.value,
      shuffle: shuffle.value,
      shuffleOrder: shuffleOrder.value,
      shuffleIndex: shuffleIndex.value,
      queue: queue.value,
      queueIndex: queueIndex.value,
      repeat: repeat.value,
      getNextTrackForPreload,
    }

    const { trackIds, nextTrack: collectedNext, isLazy } = collectTracksToPreload(params)

    // For lazy mode, determine next track from lazy shuffle IDs
    let nextT = collectedNext
    if (isLazy && lazyShuffleIds.value.length > 0) {
      const nextIdx = lazyShuffleIndex.value + 1
      if (nextIdx < lazyShuffleIds.value.length) {
        nextT = { id: lazyShuffleIds.value[nextIdx] }
      }
    }

    await executeBatchPreload({
      trackIds,
      getBatchUrls: playerApi.getBatchUrls.bind(playerApi),
      nextTrack: nextT,
      onNextPreloaded: (info) => { nextTrackPreloaded.value = info },
    })
  }

  // ===================== AUDIO EVENT HANDLERS =====================
  const buildAudioHandlers = () => ({
    onCanPlay: () => {
      loading.value = false; clearStallTimer(); resetStallRetry()
    },
    onPlaying: () => {
      loading.value = false; clearStallTimer(); resetStallRetry(); resetAudioRetry()
      isChangingTrack.value = false
      isPlaying.value = true
      updatePlaybackState()
      startAudioKeepAlive()
    },
    onWaiting: () => {
      if (audio.value && audio.value.readyState < 3) {
        loading.value = true
        const isInitial = audio.value.readyState === 0 && audio.value.currentTime === 0
        startStallTimer(isInitial, () => stallRecovery())
      }
    },
    onStalled: () => {
      if (audio.value && (!audio.value.paused || loading.value)) {
        startStallTimer(false, () => stallRecovery())
      }
    },
    onSuspend: () => { /* normal browser behavior */ },
    onTimeUpdate: () => {
      if (!audio.value) return
      progress.value = audio.value.currentTime
      if (audio.value.buffered.length > 0) {
        for (let i = 0; i < audio.value.buffered.length; i++) {
          if (audio.value.buffered.start(i) <= audio.value.currentTime &&
              audio.value.buffered.end(i) >= audio.value.currentTime) {
            buffered.value = audio.value.buffered.end(i)
            break
          }
        }
      }
      clearStallTimer()

      // Self-heal isPlaying if desynced from actual DOM audio state (only outside transitions)
      if (!audio.value._obsolete && !isChangingTrack.value && !isSkipping) {
        if (!audio.value.paused && !isPlaying.value) {
          isPlaying.value = true
          updatePlaybackState()
        } else if (audio.value.paused && isPlaying.value) {
          isPlaying.value = false
          updatePlaybackState()
        }
      }

      const now = Date.now()
      if (now - lastPositionUpdate >= 1000) {
        lastPositionUpdate = now
        updatePositionState()
      }
      // Fallback preload trigger
      if (duration.value > 0 && !preloadTriggered && progress.value > 0.5) {
        preloadTriggered = true
        preloadNextTracks()
      }
      // Proactive near-end preload trigger: 15s before track ends, make sure next track is preloaded
      if (duration.value > 20 && duration.value - progress.value < 15 && !preloadNearEndTriggered) {
        preloadNearEndTriggered = true
        preloadNextTracks()
      }
    },
    onProgress: () => {
      if (!audio.value) return
      if (audio.value.buffered.length > 0) {
        buffered.value = audio.value.buffered.end(audio.value.buffered.length - 1)
        if (loading.value && buffered.value - progress.value > 2) {
          loading.value = false; clearStallTimer()
        }
      }
    },
    onDurationChange: () => {
      if (audio.value) { duration.value = audio.value.duration; updatePositionState() }
    },
    onEnded: (e) => {
      if (e.target?._obsolete) return
      clearStallTimer()
      handleEnded()
    },
    onPlay: (e) => {
      if (e?.target?._obsolete) return
      if (audio.value) audio.value._obsolete = false
      isChangingTrack.value = false
      isPlaying.value = true
      isSkipping = false
      resetSkipCount(); resetAudioRetry(); resetStallRetry()
      clearStallTimer()
      updatePlaybackState()
      startAudioKeepAlive()
      startStateSaving()
    },
    onPause: (e) => {
      // Skip MediaSession update when swapping tracks or skipping.
      // Without this, the brief 'paused' state dismisses the Android lock screen notification.
      if (e?.target?._obsolete) return
      if (isChangingTrack.value || isSkipping) {
        console.log('[Player] onPause ignored during track transition')
        return
      }
      isPlaying.value = false
      clearStallTimer()
      updatePlaybackState()
      stopAudioKeepAlive()
      persistState()
    },
    onError: async (e) => {
      loading.value = false; clearStallTimer()
      isChangingTrack.value = false
      if (e.target?._obsolete) return
      const el = audio.value
      if (!el) return
      const errorCode = el.error?.code
      if (errorCode === 1 || isSkipping) return

      // Network retry (only if device is online)
      const track = currentTrack.value
      const isOfflineNow = typeof navigator !== 'undefined' && !navigator.onLine
      if (errorCode === 2 && !isOfflineNow && getAudioRetryCount() < getMaxAudioRetries() && track) {
        incrementAudioRetry()
        loading.value = true
        try {
          await new Promise(r => setTimeout(r, getAudioRetryDelay()))
          const resp = await playerApi.getStreamUrl(track.id)
          setCachedUrl(track.id, resp.data.url, resp.data.expires_at)
          const savedTime = el.currentTime || 0
          el.src = resp.data.url
          if (savedTime > 1) el.currentTime = savedTime - 0.5
          await el.play()
          loading.value = false
          return
        } catch (_) { loading.value = false }
      }

      // Cascading skip guard
      if (checkCascadingSkips()) {
        lastError.value = { type: 'cascade_error', track, message: 'Слишком много ошибок подряд, воспроизведение остановлено' }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))
        isPlaying.value = false
        return
      }

      const errorNames = { 2: 'NETWORK', 3: 'DECODE', 4: 'SRC_NOT_SUPPORTED' }
      if (errorCode >= 2) {
        lastError.value = { type: 'audio_error', track, message: `Ошибка аудио: ${errorNames[errorCode] || errorCode}` }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))
        isSkipping = true
        setTimeout(() => { next(); isSkipping = false }, 1000)
      }
    },
    onCanPlayThrough: () => {
      if (!preloadTriggered && duration.value > 0) {
        preloadTriggered = true
        preloadNextTracks()
      }
    },
  })

  // Stall recovery helper
  const stallRecovery = () => {
    handleStallTimeout({
      audio: audio.value,
      track: currentTrack.value,
      generation: _playGeneration,
      currentGeneration: _playGeneration,
      getStreamUrl: playerApi.getStreamUrl.bind(playerApi),
      setCachedUrl,
      onRecovered: () => { resetStallRetry() },
      onFailed: (track) => {
        lastError.value = { type: 'stall_timeout', track, message: 'Не удалось загрузить аудио — проблемы с сетью' }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))
        isSkipping = true
        setTimeout(() => { next(); isSkipping = false }, 500)
      },
    })
  }

  // ===================== MEDIA SESSION ACTIONS =====================
  // Extracted so we can re-register after audio element swap (Android fix).
  // Uses refs/closures — always accesses current audio.value at call time.
  const _msActions = () => ({
    play: () => {
      if (!audio.value?.src && currentTrack.value) {
        play(currentTrack.value)
      } else {
        audio.value?.play()
      }
    },
    pause: () => audio.value?.pause(),
    prev: () => prev(),
    next: () => next(),
    seek: (t) => seek(t),
    seekBackward: (s) => seek(Math.max(0, progress.value - s)),
    seekForward: (s) => seek(Math.min(duration.value, progress.value + s)),
    stop: () => {
      if (audio.value) { audio.value.pause(); audio.value.currentTime = 0 }
      isPlaying.value = false
      isChangingTrack.value = false
      updatePlaybackState()
      stopAudioKeepAlive()
    },
  })

  // ===================== INIT AUDIO =====================
  const initAudio = () => {
    if (audio.value) return
    audio.value = createAudioElement(volume.value)

    initAudioContext()
    connectEnhancer(audio.value)

    setupMediaSession(_msActions())

    setupKeyboardShortcuts({
      toggle, next, prev, seek,
      setVolume, toggleMute, toggleShuffle, toggleRepeat,
      getVolume: () => volume.value,
      getProgress: () => progress.value,
      getDuration: () => duration.value,
    })

    setupAudioListeners(audio.value, buildAudioHandlers())
  }

  const reattachAudioListeners = () => {
    if (!audio.value) return
    // BUG-3 FIX: setupAudioListeners uses WeakMap to clean up old listeners before attaching
    setupAudioListeners(audio.value, buildAudioHandlers())
  }

  // ===================== UNIFIED SOURCE APPLICATION =====================
  /**
   * Apply a resolved audio source to the audio element.
   * Used by play(), next(), prev(), resumeFromState() — eliminating 4× duplication.
   */
  const applySource = async (track, source) => {
    switch (source.type) {
      case 'blob': {
        audio.value.pause()
        audio.value.currentTime = 0
        audio.value.src = source.src
        buffered.value = duration.value
        audio.value.load()
        await audio.value.play()
        isPlaying.value = true
        hdTrackInfo.value = null
        break
      }
      case 'cached-url':
      case 'fresh-url': {
        audio.value.src = source.src
        buffered.value = 0
        await audio.value.play()
        isPlaying.value = true
        if (source.meta?.hdInfo) {
          hdTrackInfo.value = source.meta.hdInfo
        } else if (source.type !== 'cached-url') {
          hdTrackInfo.value = null
        }
        break
      }
      case 'offline-unavailable': {
        const err = new Error(source.reason || 'offline-unavailable')
        err.code = 'offline-unavailable'
        throw err
      }
      case 'error':
        throw source.error || new Error(source.reason)
    }
  }

  // ===================== PLAY =====================
  const play = async (track, newQueue = null, context = null) => {
    if (!track) return

    // Auto-escalate to full collection shuffle if shuffle is ON and context supports it
    if (newQueue && shuffle.value && context && !shuffleInProgress) {
      if (context.type === 'library') {
        await playShuffleAll('library', null, null, { startingTrack: track, search: context.search })
        return
      } else if (context.type === 'liked') {
        await playShuffleAll('liked', null, 'Любимые треки', { startingTrack: track })
        return
      } else if (context.type === 'playlist' && context.id) {
        await playShuffleAll('playlist', context.id, context.name, { startingTrack: track })
        return
      } else if (context.type === 'album' && context.id) {
        await playShuffleAll('album', context.id, context.name, { startingTrack: track })
        return
      } else if (context.type === 'artist' && (context.id || context.name)) {
        await playShuffleAll('artist', context.id, context.name, { startingTrack: track })
        return
      } else if (context.type === 'user_library' && context.id) {
        await playShuffleAll('user_library', context.id, context.name, { startingTrack: track })
        return
      }
    }
    stateRestored.value = true // Prevent late cache restoration from clobbering active playback
    initAudio()
    markUserInteraction()
    cancelIrrelevantPreloads(_collectRelevantIds())

    if (context) {
      setPlaybackContext(context)
    } else if (newQueue) {
      setPlaybackContext(null)
    }

    // Update queue
    if (newQueue) {
      clearLazyShuffle()
      queue.value = [...newQueue]
      queueIndex.value = newQueue.findIndex(t => t.id === track.id)
      if (shuffle.value) generateShuffleOrder(queueIndex.value)
    } else if (shuffle.value && shuffleOrder.value.length === 0 && !isLazyShuffleMode()) {
      generateShuffleOrder(queueIndex.value)
    }

    // Same track → toggle
    if (currentTrack.value?.id === track.id) { toggle(); return }

    // Known unavailable check
    if (track.is_unavailable) {
      console.warn(`[Play] Track "${track.title}" is marked unavailable`)
      if (!isSkipping && onTrackUnavailableCallback) {
        onTrackUnavailableCallback(track, 'Трек недоступен в Telegram', false)
      }
      setTimeout(() => next(), 50)
      return
    }

    // HD check with streamable substitution
    if (isTrackNotStreamable(track)) {
      console.log(`[Play] Track "${track.title}" is HD/large (mime: ${track.mime_type}, size: ${track.file_size})`)
      if (track.streamable_id) {
        try {
          const r = await tracksApi.getOne(track.streamable_id)  // BUG-1 FIX: was getById (undefined)
          hdTrackInfo.value = { id: track.id, title: track.title, isOriginalHd: true }
          const merged = { ...r.data, cover_url: track.cover_url || r.data.cover_url, _originalHdId: track.id }
          currentTrack.value = null
          await play(merged, null)
          return
        } catch (_) { /* fall through — API will try auto-substitution */ }
      }
      console.log('[Play] No pre-resolved streamable_id, will try API auto-substitution')
    }

    preloadTriggered = false
    preloadNearEndTriggered = false
    isChangingTrack.value = true
    loading.value = true
    currentTrack.value = track
    lastError.value = null
    const generation = ++_playGeneration
    updateMediaSession()

    try {
      const source = await resolveAudioSource(track, playerApi.getStreamUrl.bind(playerApi))
      await applySource(track, source)
      loading.value = false
      isChangingTrack.value = false
      nextTrackPreloaded.value = null
      persistState()
      startStateSaving()
      preloadNextTracks()

      // Background auto-caching if enabled and playing from network (chunks are never cached)
      if (autoCacheEnabled.value && source.src && source.type !== 'blob' && !track.is_chunk) {
        cacheTrackInBackground(track, source.src, cacheMaxBytes.value)
      }
      // Request persistent storage on playback
      requestStoragePersistence()
    } catch (error) {
      isChangingTrack.value = false
      if (error.name === 'AbortError') { loading.value = false; return }
      if (generation !== _playGeneration) return

      console.error('[Play Error]', error)
      const statusCode = error.response?.status
      const rawDetail = error.response?.data?.detail
      const isObjDetail = typeof rawDetail === 'object' && rawDetail !== null
      const errCode = isObjDetail ? rawDetail.code : error.response?.data?.code
      const detail = (isObjDetail ? rawDetail.message : rawDetail) || error.message || 'Ошибка воспроизведения'

      // Handle offline track unavailable: skip gracefully without cascading errors
      if (error.code === 'offline-unavailable' || error.message?.includes('offline-unavailable') || error.message?.includes('не сохранён для оффлайн')) {
        isPlaying.value = false
        loading.value = false
        lastError.value = {
          type: 'offline_unavailable',
          track,
          message: 'Трек не сохранён для оффлайн-прослушивания'
        }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))

        // Auto-skip to the next available cached track in the queue
        const nextCached = await findNextCachedTrackInQueue()
        if (nextCached) {
          setTimeout(() => play(nextCached), 400)
        }
        return
      }

      // Check if bot lacks access to backup channel (e.g. new bot or bot kicked)
      if (errCode === 'channel_bot_missing' || (typeof detail === 'string' && (detail.includes('Бот не имеет доступа к вашему каналу') || detail.includes('channel_bot_missing')))) {
        const authStore = useAuthStore()
        const isCurrentUsersTrack = !track.uploader_id || (authStore.user?.id && track.uploader_id === authStore.user.id)
        
        if (isCurrentUsersTrack) {
          isPlaying.value = false
          loading.value = false
          lastError.value = {
            type: 'channel_bot_missing',
            track,
            message: detail,
            channel_title: isObjDetail ? rawDetail.channel_title : null,
            bot_username: isObjDetail ? rawDetail.bot_username : null
          }
          window.dispatchEvent(new CustomEvent('player:channel-access-required', { detail: lastError.value }))
          return
        }
        // If it's a foreign track, do not prompt user to configure their own channel!
        console.warn(`[Player] Track ${track.id} channel error belongs to user ${track.uploader_id}, skipping...`)
        try { await tracksApi.markUnavailable(track.id); track.is_unavailable = true } catch (_) {}
        if (onTrackUnavailableCallback && !isSkipping) {
          onTrackUnavailableCallback(track, 'Трек временно недоступен у автора', false)
        }
        setTimeout(() => next(), 50)
        return
      }

      if (checkCascadingSkips()) {
        lastError.value = { type: 'cascade_error', track, message: 'Слишком много ошибок подряд' }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))
        isPlaying.value = false; loading.value = false
        return
      }

      if (statusCode === 503) {
        const isLargeFile = detail.includes('слишком большой') || (track.file_size && track.file_size > 20 * 1024 * 1024)
        const isHdFormat = detail.includes('высокого качества') || detail.includes('HD') || detail.includes('FLAC')
        lastError.value = { type: isLargeFile ? 'too_large' : isHdFormat ? 'hd_only' : 'unavailable', track, message: detail }
        if (!isLargeFile && !isHdFormat) {
          try { await tracksApi.markUnavailable(track.id); track.is_unavailable = true } catch (_) {}
        }
        if (onTrackUnavailableCallback && !isSkipping) {
          onTrackUnavailableCallback(track, detail, isLargeFile || isHdFormat)
        }
        setTimeout(() => next(), 50)
      } else if (statusCode === 401) {
        deleteCachedUrl(track.id)
        lastError.value = { type: 'auth_expired', track, message: 'Токен истёк, переключаем трек...' }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))
        setTimeout(() => next(), 300)
      } else if (statusCode === 404) {
        deleteCachedAudio(track.id).catch(() => {})
        deleteCachedUrl(track.id)
        lastError.value = { type: 'not_found', track, message: 'Трек не найден' }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))
        try { await tracksApi.markUnavailable(track.id); track.is_unavailable = true } catch (_) {}
        setTimeout(() => next(), 300)
      } else {
        lastError.value = { type: 'playback_error', track, message: detail }
        window.dispatchEvent(new CustomEvent('player:error', { detail: lastError.value }))
        setTimeout(() => next(), 500)
      }
    } finally {
      loading.value = false
    }
  }

  // ===================== PLAY SHUFFLE ALL =====================
  const playShuffleAll = async (context, contextId = null, contextName = null, options = {}) => {
    if (shuffleInProgress) return
    shuffleInProgress = true
    loading.value = true

    try {
      let response
      switch (context) {
        case 'library':  
          response = await tracksApi.getAllIds({ 
            sort_by: 'random', 
            search: options?.search || undefined 
          }); 
          break
        case 'liked':
          response = await tracksApi.getAllIds({
            liked_only: true,
            sort_by: 'random'
          });
          break
        case 'user_library': {
          if (!contextId) throw new Error('User ID required')
          response = await socialApi.getUserTrackIds(contextId, { sort_by: 'random' })
          break
        }
        case 'artist': {
          const name = contextName || contextId
          if (!name) throw new Error('Artist name required')
          response = await tracksApi.getArtistIds(name, { shuffle: true, role: 'primary' })
          break
        }
        case 'album':    if (!contextId) throw new Error('Album ID required'); response = await albumsApi.getIds(contextId, { shuffle: true }); break
        case 'playlist': if (!contextId) throw new Error('Playlist ID required'); response = await playlistsApi.getIds(contextId, { shuffle: true }); break
        default: throw new Error(`Unknown context: ${context}`)
      }

      let ids = response.data?.ids || response.data
      if (!ids?.length) { loading.value = false; shuffleInProgress = false; return }

      // Make a fresh mutable copy of IDs
      ids = [...ids]

      // Starting track priority: place starting track at the very top of shuffle queue
      const startTrack = options?.startingTrack || null
      const startId = startTrack?.id || options?.startingTrackId || null

      if (startId) {
        const existingIdx = ids.indexOf(startId)
        if (existingIdx > -1) {
          ids.splice(existingIdx, 1)
        }
        ids.unshift(startId)
      }

      lazyShuffleIds.value = ids
      lazyShuffleIndex.value = 0
      lazyShuffleContext.value = { 
        type: context, 
        id: contextId, 
        name: context === 'artist' ? (contextName || contextId) : contextName,
        search: options?.search || null
      }
      if (context === 'playlist' && contextId) {
        recordPlaylistPlayed(contextId)
      }
      shuffle.value = true
      saveSettings({ shuffle: true, volume: volume.value, isMuted: isMuted.value, repeat: repeat.value })
      queue.value = []; queueIndex.value = -1; shuffleOrder.value = []; shuffleIndex.value = -1

      const firstTrack = startTrack || await loadTrackById(ids[0])
      if (!firstTrack) { clearLazyShuffle(); loading.value = false; shuffleInProgress = false; return }

      queue.value = [firstTrack]; queueIndex.value = 0
      duration.value = 0; currentTrack.value = null
      await play(firstTrack, null, {
        type: context,
        id: contextId,
        name: context === 'artist' ? (contextName || contextId) : contextName,
        search: options?.search || null
      })
      shuffleInProgress = false
      updateLazyUpcomingTracks()
      persistState()
    } catch (error) {
      console.error('[Lazy Shuffle] Failed:', error)
      clearLazyShuffle(); loading.value = false; shuffleInProgress = false
    }
  }

  // ===================== TOGGLE / SEEK =====================
  const toggle = async () => {
    if (currentTrack.value && (!audio.value || !audio.value.src)) { await resumeFromState(); return }
    if (!audio.value) return

    // Check actual DOM state to prevent desync
    if (audio.value.paused) {
      try {
        await audio.value.play()
        isPlaying.value = true
        startAudioKeepAlive()
      } catch (err) {
        console.error('[Player] Failed to play on toggle:', err)
      }
    } else {
      audio.value.pause()
      isPlaying.value = false
      stopAudioKeepAlive()
    }
    updatePlaybackState()
  }

  const seek = (time) => {
    if (!audio.value) return
    audio.value.currentTime = time
    updatePositionState()
  }

  // ===================== NEXT =====================
  const next = async () => {
    isSkipping = true
    isChangingTrack.value = true
    markUserInteraction()
    cancelIrrelevantPreloads(_collectRelevantIds())
    preloadTriggered = false
    preloadNearEndTriggered = false
    clearPreloadAudio()

    // === LAZY SHUFFLE ===
    if (isLazyShuffleMode()) {
      const generation = ++_playGeneration
      lazyShuffleIndex.value++
      if (lazyShuffleIndex.value >= lazyShuffleIds.value.length) {
        if (repeat.value === 'all') lazyShuffleIndex.value = 0
        else {
          isPlaying.value = false
          isSkipping = false
          isChangingTrack.value = false
          clearLazyShuffle()
          updatePlaybackState()
          stopAudioKeepAlive()
          return
        }
      }
      const nextTrackId = lazyShuffleIds.value[lazyShuffleIndex.value]

      loading.value = true
      const t = await loadTrackById(nextTrackId)
      if (generation !== _playGeneration) return

      if (!t || t.is_disliked || t.is_unavailable || isTrackNotStreamable(t)) {
        loading.value = false
        await next()
        return
      }
      queue.value = [t]; queueIndex.value = 0; shuffleOrder.value = []; shuffleIndex.value = -1
      await play(t)
      updateLazyUpcomingTracks()
      persistState()
      return
    }

    // === REGULAR ===
    if (queue.value.length === 0) {
      isSkipping = false
      isChangingTrack.value = false
      return
    }

    let nextIndex
    let attempts = 0
    const maxAttempts = queue.value.length

    while (attempts < maxAttempts) {
      if (shuffle.value && shuffleOrder.value.length > 0) {
        shuffleIndex.value++
        if (shuffleIndex.value >= shuffleOrder.value.length) {
          if (repeat.value === 'all') generateShuffleOrder()
          else {
            isPlaying.value = false
            isSkipping = false
            isChangingTrack.value = false
            updatePlaybackState()
            stopAudioKeepAlive()
            return
          }
        }
        nextIndex = shuffleOrder.value[shuffleIndex.value]
      } else {
        nextIndex = queueIndex.value + 1
        if (nextIndex >= queue.value.length) {
          if (repeat.value === 'all') nextIndex = 0
          else {
            isPlaying.value = false
            isSkipping = false
            isChangingTrack.value = false
            updatePlaybackState()
            stopAudioKeepAlive()
            return
          }
        }
      }

      queueIndex.value = nextIndex
      const candidateTrack = queue.value[nextIndex]
      // Skip known unavailable tracks, disliked tracks, and unstreamable tracks immediately
      if (candidateTrack && !candidateTrack.is_unavailable && !candidateTrack.is_disliked && !isTrackNotStreamable(candidateTrack)) {
        break
      }
      attempts++
    }

    const nextTrack = queue.value[queueIndex.value]
    if (!nextTrack || nextTrack.is_unavailable || nextTrack.is_disliked || isTrackNotStreamable(nextTrack)) {
      isPlaying.value = false
      isSkipping = false
      isChangingTrack.value = false
      updatePlaybackState()
      stopAudioKeepAlive()
      return
    }

    // Use unified resolveAudioSource instead of 4× duplicated cascade
    const generation = ++_playGeneration
    initAudio()
    loading.value = true
    currentTrack.value = nextTrack
    updateMediaSession()

    try {
      const source = await resolveAudioSource(nextTrack, playerApi.getStreamUrl.bind(playerApi))
      if (generation !== _playGeneration) return
      await applySource(nextTrack, source)
      loading.value = false
      isSkipping = false
      isChangingTrack.value = false
      nextTrackPreloaded.value = null
      clearPreloadAudio()
      persistState()
      preloadNextTracks()
    } catch (e) {
      if (generation !== _playGeneration) return
      if (e.name === 'AbortError') return
      console.error('[Next] Failed:', e)
      if (nextTrack) {
        nextTrack.is_unavailable = true
      }
      await next()
    }
  }

  // ===================== PREV =====================
  const prev = async () => {
    isSkipping = true
    isChangingTrack.value = true
    markUserInteraction()
    preloadTriggered = false
    preloadNearEndTriggered = false
    clearPreloadAudio()

    if (progress.value > 3) {
      seek(0)
      isSkipping = false
      isChangingTrack.value = false
      return
    }

    // === LAZY SHUFFLE ===
    if (isLazyShuffleMode()) {
      const generation = ++_playGeneration
      lazyShuffleIndex.value--
      if (lazyShuffleIndex.value < 0) {
        if (repeat.value === 'all') lazyShuffleIndex.value = lazyShuffleIds.value.length - 1
        else lazyShuffleIndex.value = 0
      }
      const prevTrackId = lazyShuffleIds.value[lazyShuffleIndex.value]
      loading.value = true
      const t = await loadTrackById(prevTrackId)
      if (generation !== _playGeneration) return

      if (!t || t.is_disliked || t.is_unavailable || isTrackNotStreamable(t)) {
        loading.value = false
        await prev()
        return
      }
      queue.value = [t]; queueIndex.value = 0; shuffleOrder.value = []; shuffleIndex.value = -1
      await play(t)
      updateLazyUpcomingTracks()
      persistState()
      return
    }

    if (queue.value.length === 0) {
      isSkipping = false
      isChangingTrack.value = false
      return
    }

    let prevIndex
    let attempts = 0
    const maxAttempts = queue.value.length

    while (attempts < maxAttempts) {
      prevIndex = queueIndex.value - 1
      if (prevIndex < 0) prevIndex = repeat.value === 'all' ? queue.value.length - 1 : 0
      queueIndex.value = prevIndex
      const candidateTrack = queue.value[prevIndex]
      if (candidateTrack && !candidateTrack.is_unavailable && !candidateTrack.is_disliked && !isTrackNotStreamable(candidateTrack)) {
        break
      }
      attempts++
      if (prevIndex === 0 && repeat.value !== 'all') break
    }

    const prevTrack = queue.value[queueIndex.value]
    if (!prevTrack || prevTrack.is_unavailable || prevTrack.is_disliked || isTrackNotStreamable(prevTrack)) {
      isPlaying.value = false
      isSkipping = false
      isChangingTrack.value = false
      updatePlaybackState()
      stopAudioKeepAlive()
      return
    }

    const generation = ++_playGeneration
    initAudio()
    loading.value = true
    currentTrack.value = prevTrack
    updateMediaSession()

    try {
      const source = await resolveAudioSource(prevTrack, playerApi.getStreamUrl.bind(playerApi))
      if (generation !== _playGeneration) return
      await applySource(prevTrack, source)
      loading.value = false
      isSkipping = false
      isChangingTrack.value = false
      persistState()
      preloadNextTracks()
    } catch (e) {
      if (generation !== _playGeneration) return
      if (e.name === 'AbortError') return
      console.error('[Prev] Failed:', e)
      if (prevTrack) {
        prevTrack.is_unavailable = true
      }
      await prev()
    }
  }

  // ===================== HANDLE ENDED =====================
  const handleEnded = async () => {
    const track = currentTrack.value
    if (!track || !audio.value?.src) return

    isChangingTrack.value = true

    // Background fire-and-forget: do NOT await recordPlay to avoid delaying next track start while screen is locked
    if (track.id) {
      playerApi.recordPlay(track.id).catch(() => {})
    }

    if (repeat.value === 'one') {
      seek(0)
      try {
        await audio.value.play()
        isChangingTrack.value = false
      } catch (_) {
        isChangingTrack.value = false
      }
    } else {
      await next()
    }
  }

  // ===================== QUEUE MANAGEMENT =====================
  const playFromQueue = async (relativeIndex) => {
    if (isLazyShuffleMode()) {
      const targetIndex = lazyShuffleIndex.value + 1 + relativeIndex
      if (targetIndex >= 0 && targetIndex < lazyShuffleIds.value.length) {
        lazyShuffleIndex.value = targetIndex - 1
        await next()
      }
      return
    }
    const idx = queueIndex.value + 1 + relativeIndex
    if (idx >= 0 && idx < queue.value.length) { queueIndex.value = idx; await play(queue.value[idx]) }
  }

  const toggleShuffle = async () => {
    if (shuffle.value && isLazyShuffleMode()) {
      shuffle.value = false; shuffleOrder.value = []; shuffleIndex.value = -1
      clearLazyShuffle(); preloadTriggered = false; preloadNextTracks(); persistSettings(); persistState()
      return
    }
    if (isLazyShuffleMode()) {
      if (!shuffle.value) { shuffle.value = true; persistSettings(); persistState() }
      return
    }
    shuffle.value = !shuffle.value
    if (shuffle.value) {
      const ctx = playbackContext.value
      if (ctx?.type === 'library' || (!ctx && currentTrack.value)) {
        await playShuffleAll('library', null, null, { startingTrack: currentTrack.value, search: ctx?.search || undefined })
        return
      } else if (ctx?.type === 'liked') {
        await playShuffleAll('liked', null, 'Любимые треки', { startingTrack: currentTrack.value })
        return
      } else if (ctx?.type === 'playlist' && ctx.id) {
        await playShuffleAll('playlist', ctx.id, ctx.name, { startingTrack: currentTrack.value })
        return
      } else if (ctx?.type === 'album' && ctx.id) {
        await playShuffleAll('album', ctx.id, ctx.name, { startingTrack: currentTrack.value })
        return
      } else if (ctx?.type === 'artist' && (ctx.name || ctx.id)) {
        await playShuffleAll('artist', ctx.id, ctx.name, { startingTrack: currentTrack.value })
        return
      } else if (ctx?.type === 'user_library' && ctx.id) {
        await playShuffleAll('user_library', ctx.id, ctx.name, { startingTrack: currentTrack.value })
        return
      }
      generateShuffleOrder(queueIndex.value)
    } else {
      shuffleOrder.value = []; shuffleIndex.value = -1; clearLazyShuffle()
    }
    preloadTriggered = false; preloadNextTracks(); persistSettings(); persistState()
  }

  const toggleRepeat = () => {
    const modes = ['none', 'all', 'one']
    repeat.value = modes[(modes.indexOf(repeat.value) + 1) % modes.length]
    persistSettings()
  }

  const setVolume = (val) => {
    volume.value = Math.max(0, Math.min(1, val))
    if (audio.value) audio.value.volume = volume.value
    if (volume.value > 0) isMuted.value = false
    persistSettings()
  }

  const toggleMute = () => {
    isMuted.value = !isMuted.value
    if (audio.value) audio.value.muted = isMuted.value
    persistSettings()
  }

  const playNext = (track) => {
    // Handle Lazy Shuffle mode
    if (isLazyShuffleMode()) {
      const tid = track.id
      const idx = lazyShuffleIds.value.indexOf(tid)
      if (idx !== -1) {
        lazyShuffleIds.value.splice(idx, 1)
        if (idx <= lazyShuffleIndex.value) lazyShuffleIndex.value--
      }
      lazyShuffleIds.value.splice(lazyShuffleIndex.value + 1, 0, tid)
      persistState(); return
    }

    const insertIndex = queueIndex.value + 1
    const existing = queue.value.findIndex(t => t.id === track.id)
    if (existing !== -1) {
      if (shuffle.value && shuffleOrder.value.length > 0) {
        const sp = shuffleOrder.value.indexOf(existing)
        if (sp !== -1) { shuffleOrder.value.splice(sp, 1); if (sp <= shuffleIndex.value) shuffleIndex.value-- }
        shuffleOrder.value = shuffleOrder.value.map(i => i > existing ? i - 1 : i)
      }
      queue.value.splice(existing, 1)
      if (existing < queueIndex.value) queueIndex.value--
    }
    queue.value.splice(insertIndex, 0, track)
    if (shuffle.value && shuffleOrder.value.length > 0) {
      shuffleOrder.value = shuffleOrder.value.map(i => i >= insertIndex ? i + 1 : i)
      shuffleOrder.value.splice(shuffleIndex.value + 1, 0, insertIndex)
    }
    persistState()
  }

  const addToQueue = (track) => {
    if (!queue.value.find(t => t.id === track.id)) { queue.value.push(track); persistState() }
  }

  const removeFromQueue = (relIdx) => {
    const idx = queueIndex.value + 1 + relIdx
    if (idx > queueIndex.value && idx < queue.value.length) { queue.value.splice(idx, 1); persistState() }
  }

  /**
   * Remove all occurrences of a track (by id) from the queue.
   * Adjusts queueIndex if items before it are removed.
   */
  const removeTrackFromQueue = (trackId) => {
    let i = queue.value.length
    while (i--) {
      if (queue.value[i].id === trackId) {
        if (i === queueIndex.value) continue // don't remove currently playing
        queue.value.splice(i, 1)
        if (i < queueIndex.value) queueIndex.value--
      }
    }
    persistState()
  }

  const moveInQueue = (fromRel, toRel) => {
    const from = queueIndex.value + 1 + fromRel
    const to = queueIndex.value + 1 + toRel
    if (from > queueIndex.value && from < queue.value.length && to > queueIndex.value && to <= queue.value.length) {
      const [item] = queue.value.splice(from, 1)
      queue.value.splice(to > from ? to - 1 : to, 0, item)
      persistState()
    }
  }

  const stop = () => {
    if (audio.value) { audio.value._obsolete = true; audio.value.pause(); audio.value.src = '' }
    currentTrack.value = null; isPlaying.value = false
    isChangingTrack.value = false
    progress.value = 0; duration.value = 0
    queue.value = []; queueIndex.value = -1
    stopAudioKeepAlive()
    stopStateSaving(); clearPlayerState()
  }

  /**
   * Patch track data in-place (currentTrack + queue).
   * Used by notifyTrackChange to keep player UI in sync after track edits.
   */
  const patchTrack = (trackId, data) => {
    if (currentTrack.value?.id === trackId) { Object.assign(currentTrack.value, data); updateMediaSession() }
    for (const t of queue.value) { if (t.id === trackId) Object.assign(t, data) }
  }

  // ===================== STATE RESTORE =====================
  const restoreState = async () => {
    if (stateRestored.value || !savedState) return false
    // If user has already started playing or loaded a track before cache restore, do not overwrite
    if (currentTrack.value || isPlaying.value) {
      stateRestored.value = true
      return false
    }
    stateRestored.value = true
    const maxAge = 24 * 60 * 60 * 1000
    if (savedState.savedAt && Date.now() - savedState.savedAt > maxAge) { clearPlayerState(); return false }
    if (!savedState.currentTrack || (!savedState.queue?.length && !savedState.lazyShuffleIds?.length)) return false

    queue.value = savedState.queue || []
    queueIndex.value = savedState.queueIndex ?? 0
    currentTrack.value = savedState.currentTrack
    duration.value = savedState.duration ?? 0
    progress.value = savedState.progress ?? 0
    if (savedState.playbackContext) {
      playbackContext.value = savedState.playbackContext
    }
    if (savedState.lazyShuffleIds?.length) {
      lazyShuffleIds.value = savedState.lazyShuffleIds
      lazyShuffleIndex.value = savedState.lazyShuffleIndex ?? 0
      lazyShuffleContext.value = savedState.lazyShuffleContext || null
      updateLazyUpcomingTracks()
    }

    initAudio(); updateMediaSession(); startStateSaving()

    // Deferred background prefetch to not compete with initial page rendering
    const schedulePrefetch = typeof window !== 'undefined' && window.requestIdleCallback
      ? window.requestIdleCallback
      : (cb) => setTimeout(cb, 1200)

    schedulePrefetch(() => {
      if (!currentTrack.value) return
      const prefetchIds = [savedState.currentTrack, ...savedState.queue.slice((savedState.queueIndex ?? 0) + 1, (savedState.queueIndex ?? 0) + 4)].map(t => t.id)
      playerApi.getBatchUrls(prefetchIds)
        .then(r => { for (const item of (r.data.urls || [])) { if (item.url && !item.error) setCachedUrl(item.track_id, item.url, item.expires_at) } })
        .catch(() => {})
    })
    return true
  }

  const resumeFromState = async () => {
    if (!currentTrack.value) return
    const savedProgress = progress.value
    try {
      loading.value = true
      const source = await resolveAudioSource(currentTrack.value, playerApi.getStreamUrl.bind(playerApi))
      await applySource(currentTrack.value, source)
      if (savedProgress > 0 && audio.value && savedProgress < (audio.value.duration || duration.value) - 1) {
        audio.value.currentTime = savedProgress
      }
      loading.value = false
      startStateSaving()
      preloadNextTracks()
    } catch (e) {
      console.error('Failed to resume playback:', e)
      loading.value = false
    }
  }

  const hasSavedState = () => savedState && savedState.currentTrack && (savedState.queue?.length > 0 || savedState.lazyShuffleIds?.length > 0)

  // ===================== RETURN =====================
  return {
    currentTrack,
    queue,
    queueIndex,
    shuffleOrder,
    shuffleIndex,
    isPlaying,
    progress,
    duration,
    volume,
    isMuted,
    shuffle,
    repeat,
    
    // UI Scale
    uiScale,
    
    // Enhancer
    enhancerEnabled,
    bassGain,
    trebleGain,
    autoGain,
    
    // Auto-Cache
    autoCacheEnabled,
    cacheMaxBytes,
    
    loading,
    buffered,
    lastError,
    stateRestored,
    hdTrackInfo,
    lazyShuffleContext,
    lazyShuffleIndex,
    lazyShuffleIds,
    lazyUpcomingTracks,
    playbackContext,
    currentContext,
    currentPlaylistId,
    currentAlbumId,
    recentPlaylists,
    recordPlaylistPlayed,
    setPlaybackContext,
    play,
    playTrack: play,
    playShuffleAll,
    toggle,
    togglePlay: toggle,
    next,
    prev,
    seek,
    setVolume,
    toggleMute,
    playNext,
    addToQueue,
    removeFromQueue,
    removeTrackFromQueue,
    moveInQueue,
    toggleShuffle,
    toggleRepeat,
    playFromQueue,
    stop,
    patchTrack,
    setOnTrackUnavailable,
    restoreState,
    resumeFromState,
    hasSavedState,
    persistState,
    isLazyShuffleMode,
  }
})
