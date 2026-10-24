/**
 * Player Audio Engine
 * Manages audio element lifecycle, event listeners, and listener cleanup.
 * Extracted from player.js to fix BUG-3 (event listener leak) and reduce god-object.
 */

import { isCachedUnstreamable } from './playerCache'

// WeakMap to track listeners per audio element — prevents accumulation on swap
const _listenerMap = new WeakMap()

/**
 * Remove all player-attached event listeners from an audio element.
 * Must be called before re-attaching (swap) or disposal.
 */
export function cleanupAudioListeners(el) {
  if (!el) return
  const entries = _listenerMap.get(el)
  if (entries) {
    for (const [event, handler] of entries) {
      el.removeEventListener(event, handler)
    }
    _listenerMap.delete(el)
  }
}

/**
 * Attach all core playback event listeners to an audio element.
 * Automatically cleans up previous listeners first (fixes BUG-3 listener leak).
 *
 * @param {HTMLAudioElement} el - target audio element
 * @param {Object} handlers - callback map from the player store
 * @param {Function} handlers.onCanPlay
 * @param {Function} handlers.onPlaying
 * @param {Function} handlers.onWaiting
 * @param {Function} handlers.onStalled
 * @param {Function} handlers.onSuspend
 * @param {Function} handlers.onTimeUpdate
 * @param {Function} handlers.onProgress
 * @param {Function} handlers.onDurationChange
 * @param {Function} handlers.onEnded
 * @param {Function} handlers.onPlay
 * @param {Function} handlers.onPause
 * @param {Function} handlers.onError
 * @param {Function} handlers.onCanPlayThrough
 */
export function setupAudioListeners(el, handlers) {
  if (!el) return

  // === BUG-3 FIX: always clean up old listeners before attaching new ones ===
  cleanupAudioListeners(el)

  const entries = []
  const on = (event, handler) => {
    el.addEventListener(event, handler)
    entries.push([event, handler])
  }

  on('canplay',        handlers.onCanPlay)
  on('playing',        handlers.onPlaying)
  on('waiting',        handlers.onWaiting)
  on('stalled',        handlers.onStalled)
  on('suspend',        handlers.onSuspend)
  on('timeupdate',     handlers.onTimeUpdate)
  on('progress',       handlers.onProgress)
  on('durationchange', handlers.onDurationChange)
  on('ended',          handlers.onEnded)
  on('play',           handlers.onPlay)
  on('pause',          handlers.onPause)
  on('error',          handlers.onError)
  on('canplaythrough', handlers.onCanPlayThrough)

  _listenerMap.set(el, entries)
}

/**
 * Check if track is HD format or too large for streaming.
 * Pure function — moved from module scope in player.js for reuse.
 */
const HD_MIME_TYPES = [
  'audio/flac', 'audio/x-flac',
  'audio/wav', 'audio/x-wav',
  'audio/aiff', 'audio/x-aiff',
  'audio/x-m4a', 'audio/mp4',
  'audio/alac', 'audio/x-alac'
]
const MAX_STREAMABLE_SIZE = 2000 * 1024 * 1024

export const isTrackNotStreamable = (track) => {
  if (!track) return false
  if (track.is_unavailable === true) return true
  if (isCachedUnstreamable(track.id)) return true
  if (track.streamable_id) return false
  if (track.mime_type && HD_MIME_TYPES.includes(track.mime_type.toLowerCase())) return true
  if (track.file_size && track.file_size > MAX_STREAMABLE_SIZE) return true
  if (track.is_streamable === false) return true
  return false
}

/**
 * Create and configure a new Audio element.
 */
export function createAudioElement(volume) {
  const audio = new Audio()
  audio.crossOrigin = 'anonymous'
  audio.volume = volume
  audio.preload = 'auto'
  return audio
}
