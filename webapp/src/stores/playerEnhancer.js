/**
 * Player Audio Enhancer
 * WebAudio EQ (bass/treble) + dynamics compressor.
 * Extracted from player.js to reduce god-object.
 */

let audioCtx = null
let sourceNode = null
let bassNode = null
let trebleNode = null
let compressorNode = null
let masterGainNode = null

/**
 * Initialise the WebAudio processing graph.
 * Safe to call multiple times — no-ops after first init.
 */
export function initAudioContext() {
  if (audioCtx) return

  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext
    audioCtx = new AudioContext()

    bassNode = audioCtx.createBiquadFilter()
    bassNode.type = 'lowshelf'
    bassNode.frequency.value = 200

    trebleNode = audioCtx.createBiquadFilter()
    trebleNode.type = 'highshelf'
    trebleNode.frequency.value = 3000

    compressorNode = audioCtx.createDynamicsCompressor()
    compressorNode.threshold.value = -24
    compressorNode.knee.value = 30
    compressorNode.ratio.value = 12
    compressorNode.attack.value = 0.003
    compressorNode.release.value = 0.25

    masterGainNode = audioCtx.createGain()
    masterGainNode.gain.value = 1.0

    // Chain: source → bass → treble → compressor → gain → destination
    bassNode.connect(trebleNode)
    trebleNode.connect(compressorNode)
    compressorNode.connect(masterGainNode)
    masterGainNode.connect(audioCtx.destination)

    console.log('[Audio Enhancer] Context Initialized')
  } catch (e) {
    console.error('[Audio Enhancer] Not supported', e)
  }
}

export function resumeAudioContext() {
  if (audioCtx && audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {})
  }
}

// Background Audio Keep-Alive for Android / Mobile
// Loops an inaudible digital silence buffer through WebAudio.
// Keeps the underlying Android OpenSL ES / AAudio stream alive in audioserver
// so the OS does not put the tab/process into deep sleep when an HTML5 audio element
// ends or reloads its src while the screen is locked.
let keepAliveSource = null
let keepAliveGain = null

export function startAudioKeepAlive() {
  if (!audioCtx) initAudioContext()
  if (!audioCtx) return

  try {
    if (audioCtx.state === 'suspended') {
      audioCtx.resume().catch(() => {})
    }
    if (keepAliveSource) return // already running

    const sampleRate = audioCtx.sampleRate || 44100
    const buffer = audioCtx.createBuffer(1, sampleRate, sampleRate)

    if (!keepAliveGain) {
      keepAliveGain = audioCtx.createGain()
      keepAliveGain.gain.value = 0.00001
      keepAliveGain.connect(audioCtx.destination)
    }

    keepAliveSource = audioCtx.createBufferSource()
    keepAliveSource.buffer = buffer
    keepAliveSource.loop = true
    keepAliveSource.connect(keepAliveGain)
    keepAliveSource.start()
    console.log('[Audio Enhancer] Background keep-alive started')
  } catch (e) {
    console.warn('[Audio Enhancer] Could not start keep-alive:', e)
  }
}

export function stopAudioKeepAlive() {
  if (keepAliveSource) {
    try {
      keepAliveSource.stop()
      keepAliveSource.disconnect()
    } catch (_) {}
    keepAliveSource = null
    console.log('[Audio Enhancer] Background keep-alive stopped')
  }
}

let analyserNode = null

/**
 * Get or create a lightweight WebAudio AnalyserNode for spectrum visualization.
 * fftSize = 64 produces 32 frequency bins with virtually zero CPU overhead.
 */
export function getAudioAnalyser() {
  if (!audioCtx || !masterGainNode) return null
  if (!analyserNode) {
    try {
      analyserNode = audioCtx.createAnalyser()
      analyserNode.fftSize = 64
      analyserNode.smoothingTimeConstant = 0.8
      masterGainNode.connect(analyserNode)
    } catch (e) {
      console.warn('[Audio Enhancer] Could not create analyser', e)
    }
  }
  return analyserNode
}

/**
 * Connect (or re-connect) the given HTMLAudioElement to the processing graph.
 * Handles the one-source-per-element restriction via element tagging.
 */
export function connectAudioSource(audioEl) {
  if (!audioCtx || !audioEl) return

  try {
    if (sourceNode) sourceNode.disconnect()

    sourceNode = audioCtx.createMediaElementSource(audioEl)
    sourceNode.connect(bassNode)

    resumeAudioContext()
    console.log('[Audio Enhancer] Source connected')
  } catch (e) {
    // Element already has a source node (reused element)
    if (audioEl._sourceNode) {
      sourceNode = audioEl._sourceNode
      try { sourceNode.connect(bassNode) } catch (_) { /* already connected */ }
    }
    console.log('[Audio Enhancer] Connect skipped/reused', e)
  }

  if (sourceNode) audioEl._sourceNode = sourceNode
}

/**
 * Apply current enhancer parameter values to WebAudio nodes.
 *
 * @param {Object} params
 * @param {boolean} params.enabled
 * @param {number}  params.bass   - dB (-10..10)
 * @param {number}  params.treble - dB (-10..10)
 * @param {boolean} params.autoGain - enable compressor
 */
export function updateEnhancerParams({ enabled, bass, treble, autoGain }) {
  if (!audioCtx) return

  if (enabled) {
    try {
      bassNode.gain.setTargetAtTime(bass, audioCtx.currentTime, 0.1)
      trebleNode.gain.setTargetAtTime(treble, audioCtx.currentTime, 0.1)

      trebleNode.disconnect()
      if (autoGain) {
        trebleNode.connect(compressorNode)
      } else {
        trebleNode.connect(masterGainNode)
      }
    } catch (e) { console.error(e) }
  } else {
    try {
      bassNode.gain.setTargetAtTime(0, audioCtx.currentTime, 0.1)
      trebleNode.gain.setTargetAtTime(0, audioCtx.currentTime, 0.1)
      trebleNode.disconnect()
      trebleNode.connect(masterGainNode)
    } catch (_) { /* noop */ }
  }
}
