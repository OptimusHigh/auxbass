/**
 * Player Media Session & Keyboard Shortcuts
 * Handles lock-screen controls, Bluetooth, media keys, and keyboard shortcuts.
 * Extracted from player.js to reduce god-object.
 */
import { getDisplayTitle, getDisplayArtist, getCoverUrl, CoverSize } from '../utils/formatters'

/**
 * Helper to ensure URLs are absolute for Android MediaSession/WebView compatibility
 */
const toAbsoluteUrl = (path) => {
  if (!path) return ''
  try {
    return new URL(path, window.location.origin).href
  } catch (_) {
    return path
  }
}

/**
 * Fallback artwork for Media Session when track has no cover.
 * Android may not show lock-screen controls without artwork.
 */
const FALLBACK_ARTWORK = [
  { src: toAbsoluteUrl('/icons/icon-96x96.png'), sizes: '96x96', type: 'image/png' },
  { src: toAbsoluteUrl('/icons/icon-128x128.png'), sizes: '128x128', type: 'image/png' },
  { src: toAbsoluteUrl('/icons/icon-192x192.png'), sizes: '192x192', type: 'image/png' },
  { src: toAbsoluteUrl('/icons/icon-512x512.png'), sizes: '512x512', type: 'image/png' },
]

/**
 * Update Media Session metadata from current track.
 */
export function updateMediaSession(track, updatePlaybackStateFn) {
  if (!('mediaSession' in navigator) || !track) return

  try {
    let artwork = FALLBACK_ARTWORK
    const coverUrl = track.cover_url
    if (coverUrl) {
      try {
        const formatted = getCoverUrl(coverUrl, CoverSize.MEDIUM) || coverUrl
        const absUrl = toAbsoluteUrl(formatted)
        artwork = [
          { src: absUrl, sizes: '96x96', type: 'image/jpeg' },
          { src: absUrl, sizes: '128x128', type: 'image/jpeg' },
          { src: absUrl, sizes: '256x256', type: 'image/jpeg' },
          { src: absUrl, sizes: '512x512', type: 'image/jpeg' },
        ]
      } catch (_) {
        artwork = FALLBACK_ARTWORK
      }
    }

    navigator.mediaSession.metadata = new MediaMetadata({
      title: getDisplayTitle(track) || 'Unknown Track',
      artist: getDisplayArtist(track) || 'Unknown Artist',
      album: track.album || track.album_title || '',
      artwork
    })

    // Pre-initialize position state to 0 for new track if duration is available
    if (track.duration && isFinite(track.duration) && track.duration > 0 && 'setPositionState' in navigator.mediaSession) {
      try {
        navigator.mediaSession.setPositionState({
          duration: track.duration,
          playbackRate: 1,
          position: 0
        })
      } catch (_) {}
    }

    if (updatePlaybackStateFn) updatePlaybackStateFn()
  } catch (e) {
    console.warn('[MediaSession] updateMediaSession error:', e)
  }
}

/**
 * Sync Media Session playback state.
 */
export function updatePlaybackState(isPlaying) {
  if (!('mediaSession' in navigator)) return
  try {
    navigator.mediaSession.playbackState = isPlaying ? 'playing' : 'paused'
  } catch (_) {}
}

/**
 * Sync Media Session position state.
 */
export function updatePositionState(audio, progressVal, durationVal) {
  if (!('mediaSession' in navigator) || !('setPositionState' in navigator.mediaSession)) return
  if (!durationVal || !isFinite(durationVal) || durationVal <= 0) return
  try {
    const position = Math.min(Math.max(0, progressVal || 0), durationVal)
    if (isFinite(position) && position >= 0) {
      navigator.mediaSession.setPositionState({
        duration: durationVal,
        playbackRate: audio?.playbackRate || 1,
        position
      })
    }
  } catch (_) { /* ignore during transitions */ }
}

/**
 * Register Media Session action handlers.
 * Safe to call multiple times (re-registers = replaces handlers).
 * Each registration and callback is wrapped in try-catch for Android/Firefox resilience.
 * @param {Object} actions - { play, pause, prev, next, seek, stop }
 */
export function setupMediaSession(actions) {
  if (!('mediaSession' in navigator)) return

  const ms = navigator.mediaSession

  // Wrap each setActionHandler in try-catch: if a browser doesn't support
  // a specific action, we skip it without breaking subsequent registrations.
  // Wrap each callback in try-catch: prevents unhandled errors from
  // causing the browser to deactivate the Media Session (Android/Firefox issue).
  const safeSet = (name, fn) => {
    try {
      ms.setActionHandler(name, (...args) => {
        try {
          console.log(`[MediaSession] action: ${name}`)
          const result = fn(...args)
          // Catch promise rejections from async handlers (next/prev)
          if (result instanceof Promise) {
            result.catch(e => console.warn(`[MediaSession] ${name} async error:`, e))
          }
        } catch (e) {
          console.error(`[MediaSession] ${name} handler error:`, e)
        }
      })
    } catch (_) {
      console.warn(`[MediaSession] ${name} not supported`)
    }
  }

  safeSet('play', () => actions.play())
  safeSet('pause', () => actions.pause())
  safeSet('previoustrack', () => actions.prev())
  safeSet('nexttrack', () => actions.next())
  safeSet('seekto', (details) => {
    if (details.seekTime != null) actions.seek(details.seekTime)
  })
  safeSet('seekbackward', (details) => {
    actions.seekBackward(details.seekOffset || 10)
  })
  safeSet('seekforward', (details) => {
    actions.seekForward(details.seekOffset || 10)
  })
  safeSet('stop', () => actions.stop())

  console.log('[MediaSession] All action handlers registered')
}

let _keyboardAttached = false

/**
 * Attach global keyboard shortcuts (idempotent).
 * @param {Object} actions - { toggle, next, prev, seek, seekBack, setVolume, toggleMute, toggleShuffle, toggleRepeat, getVolume, getProgress, getDuration }
 */
export function setupKeyboardShortcuts(actions) {
  if (_keyboardAttached) return
  _keyboardAttached = true

  document.addEventListener('keydown', (e) => {
    const tag = e.target.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target.isContentEditable) return

    switch (e.code) {
      case 'Space':
      case 'MediaPlayPause':
        e.preventDefault(); actions.toggle(); break

      case 'MediaTrackNext':
        e.preventDefault(); actions.next(); break

      case 'MediaTrackPrevious':
        e.preventDefault(); actions.prev(); break

      case 'ArrowRight':
        if (!e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault()
          actions.seek(Math.min(actions.getDuration(), actions.getProgress() + 10))
        }
        break

      case 'ArrowLeft':
        if (!e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault()
          actions.seek(Math.max(0, actions.getProgress() - 10))
        }
        break

      case 'KeyM':
        if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); actions.toggleMute() }
        break

      case 'KeyN':
        if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); actions.next() }
        break

      case 'KeyP':
        if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); actions.prev() }
        break

      case 'KeyS':
        if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); actions.toggleShuffle() }
        break

      case 'KeyR':
        if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); actions.toggleRepeat() }
        break

      case 'ArrowUp':
        if (!e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault()
          actions.setVolume(Math.min(1, actions.getVolume() + 0.1))
        }
        break

      case 'ArrowDown':
        if (!e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault()
          actions.setVolume(Math.max(0, actions.getVolume() - 0.1))
        }
        break
    }
  })
}
