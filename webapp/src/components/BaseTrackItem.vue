<template>
  <div 
    class="base-track-item"
    :class="{ 
      'is-dragging': isDragging, 
      'drag-over': isDragOver, 
      'is-playing': isCurrentTrack,
      'dimmed': dimmed || track.is_disliked
    }"
    :data-track-index="index"
    :draggable="draggable && !isSeeking"
    @dragstart="handleDragStart"
    @dragend="$emit('dragend')"
    @dragover.prevent="$emit('dragover', $event)"
    @dragenter.prevent="$emit('dragenter', $event)"
    @drop="$emit('drop', $event)"
  >
    <!-- Drag handle (optional) -->
    <div 
      v-if="showDragHandle" 
      class="drag-handle"
      @touchstart="$emit('handleTouchStart', $event)"
      @touchmove="$emit('handleTouchMove', $event)"
      @touchend="$emit('handleTouchEnd', $event)"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
        <path d="M3 15h18v-2H3v2zm0 4h18v-2H3v2zm0-8h18V9H3v2zm0-6v2h18V5H3z"/>
      </svg>
    </div>
    
    <!-- Track number (optional) -->
    <div v-if="showIndex" class="track-number">{{ index + 1 }}</div>
    
    <!-- Cover with play button -->
    <div class="cover-wrapper" @click.stop="togglePlay">
      <div class="cover" ref="coverElRef">
        <img v-if="resolvedSrc && !imageError && !isCoverError" :src="resolvedSrc" alt="" @error="imageError = true" />
        <Music v-else :size="20" />
      </div>
      <div class="play-overlay" :class="{ 'is-playing': isCurrentTrack && playerStore.isPlaying }">
        <svg v-if="isCurrentTrack && playerStore.isPlaying" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
        </svg>
        <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <path d="M8 5v14l11-7z"/>
        </svg>
      </div>
    </div>
    
    <!-- Track info with progress -->
    <div class="track-info">
      <div class="track-title">{{ track.title }}</div>
      <div class="track-artist">{{ track.artist }}</div>
      <div 
        v-if="isCurrentTrack" 
        class="progress" 
        :class="{ 'is-seeking': isSeeking }"
        draggable="false"
        @click.left.stop="seek" 
        @mousedown.left.stop.prevent="startDrag"
        @touchstart.stop.prevent="startTouchDrag"
        @dragstart.stop.prevent
      >
        <div class="progress-fill" :style="{ width: displayPercent + '%' }"></div>
        <div class="progress-thumb" :style="{ left: displayPercent + '%' }"></div>
      </div>
    </div>
    
    <!-- Time display -->
    <div class="time" v-if="isCurrentTrack">{{ displayTime }}</div>
    
    <!-- Action slot -->
    <slot name="action"></slot>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { usePlayerStore } from '@/stores/player'
import { getCoverUrl, CoverSize } from '@/utils'
import { useLazyCover } from '@/composables/useCoverLoader'
import { Music } from 'lucide-vue-next'

const imageError = ref(false)
const coverElRef = ref(null)

const targetCoverUrl = computed(() => {
  if (!props.track?.cover_url) return null
  return getCoverUrl(props.track.cover_url, CoverSize.SMALL)
})

const { resolvedSrc, isError: isCoverError } = useLazyCover(coverElRef, targetCoverUrl)

watch(() => props.track?.id || props.track?.cover_url, () => {
  imageError.value = false
})

const props = defineProps({
  track: { type: Object, required: true },
  index: { type: Number, default: 0 },
  allTracks: { type: Array, default: () => [] },
  showDragHandle: { type: Boolean, default: false },
  showIndex: { type: Boolean, default: false },
  draggable: { type: Boolean, default: false },
  isDragging: { type: Boolean, default: false },
  isDragOver: { type: Boolean, default: false },
  dimmed: { type: Boolean, default: false }
})

const emit = defineEmits([
  'dragstart',
  'dragend',
  'dragover',
  'dragenter',
  'drop',
  'handleTouchStart',
  'handleTouchMove',
  'handleTouchEnd'
])

const playerStore = usePlayerStore()

// Current track state
const isCurrentTrack = computed(() => playerStore.currentTrack?.id === props.track.id)
const progressPercent = computed(() => (playerStore.progress / playerStore.duration) * 100 || 0)

// Seeking state
const isSeeking = ref(false)
const seekPercent = ref(0)

// Expose isSeeking for parent components
defineExpose({ isSeeking })

// Show local position during drag, otherwise show actual progress
const displayPercent = computed(() => isSeeking.value ? seekPercent.value : progressPercent.value)

const displayTime = computed(() => {
  const totalSeconds = isSeeking.value 
    ? (seekPercent.value / 100) * playerStore.duration 
    : playerStore.progress
  if (!totalSeconds || isNaN(totalSeconds)) return '0:00'
  const mins = Math.floor(totalSeconds / 60)
  const secs = Math.floor(totalSeconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
})

const togglePlay = () => {
  if (isCurrentTrack.value) {
    playerStore.toggle()
  } else {
    const queue = props.allTracks.length ? props.allTracks : [props.track]
    playerStore.play(props.track, queue)
  }
}

const handleDragStart = (event) => {
  if (isSeeking.value) {
    event.preventDefault()
    return
  }
  emit('dragstart', event)
}

const seek = (event) => {
  if (event && event.button !== 0) return
  if (!isCurrentTrack.value) return
  const rect = event.currentTarget.getBoundingClientRect()
  const percent = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  playerStore.seek(percent * playerStore.duration)
}

const startDrag = (event) => {
  if (event && event.button !== 0) return
  if (!isCurrentTrack.value) return
  
  isSeeking.value = true
  const progressBar = event.currentTarget
  
  const rect = progressBar.getBoundingClientRect()
  seekPercent.value = Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100))
  
  const onMouseMove = (e) => {
    const rect = progressBar.getBoundingClientRect()
    seekPercent.value = Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100))
  }
  
  const onMouseUp = () => {
    playerStore.seek((seekPercent.value / 100) * playerStore.duration)
    isSeeking.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }
  
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

const startTouchDrag = (event) => {
  if (!isCurrentTrack.value) return
  
  isSeeking.value = true
  const progressBar = event.currentTarget
  const touch = event.touches[0]
  
  const rect = progressBar.getBoundingClientRect()
  seekPercent.value = Math.max(0, Math.min(100, ((touch.clientX - rect.left) / rect.width) * 100))
  
  const onTouchMove = (e) => {
    const touch = e.touches[0]
    const rect = progressBar.getBoundingClientRect()
    seekPercent.value = Math.max(0, Math.min(100, ((touch.clientX - rect.left) / rect.width) * 100))
  }
  
  const onTouchEnd = () => {
    playerStore.seek((seekPercent.value / 100) * playerStore.duration)
    isSeeking.value = false
    document.removeEventListener('touchmove', onTouchMove)
    document.removeEventListener('touchend', onTouchEnd)
  }
  
  document.addEventListener('touchmove', onTouchMove)
  document.addEventListener('touchend', onTouchEnd)
}
</script>

<style scoped>
.base-track-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--r-md);
  transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
  background: transparent;
  border: 1px solid transparent;
  position: relative;
}

.base-track-item[draggable="true"] { user-select: none; }
.base-track-item:hover { 
  background: var(--c-bg-2);
  border: var(--border-neu);
  box-shadow: 3px 3px 8px var(--sh-dark), -2px -2px 5px var(--sh-light);
}
.base-track-item.is-dragging { opacity: 0.4; transform: scale(0.98); }
.base-track-item.is-dragging * { pointer-events: none; }
.base-track-item.drag-over { background: var(--c-bg-4); }
.base-track-item.is-playing { 
  background: var(--c-bg-2);
  border: var(--border-neu);
  box-shadow: 
    inset 2px 2px 5px var(--sh-inset-dark),
    inset -1px -1px 3px var(--sh-inset-light);
}
.base-track-item.dimmed { opacity: 0.6; }

.base-track-item.drag-over::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--c-accent);
  border-radius: 1px;
}

.drag-handle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 32px;
  cursor: grab;
  color: var(--c-text-3);
  flex-shrink: 0;
  user-select: none;
  touch-action: none;
  transition: color 0.15s ease;
}

.drag-handle:hover { color: var(--c-text-1); }
.drag-handle:active { cursor: grabbing; color: var(--c-accent); }

.track-number {
  width: 20px;
  text-align: center;
  font-size: 12px;
  color: var(--c-text-3);
  flex-shrink: 0;
}

.cover-wrapper {
  position: relative;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  cursor: pointer;
  border-radius: 4px;
  overflow: hidden;
}

.cover {
  width: 100%;
  height: 100%;
  background: var(--c-bg-4);
  display: flex;
  align-items: center;
  justify-content: center;
}

.cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-wrapper:hover .cover img { filter: brightness(0.6); }

.play-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  opacity: 0;
  transition: opacity 0.2s;
  color: #fff;
}

.cover-wrapper:hover .play-overlay { opacity: 1; }
.play-overlay.is-playing { opacity: 1; background: rgba(0, 0, 0, 0.3); }

.track-info {
  flex: 1;
  min-width: 0;
}

.track-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--c-text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.track-artist {
  font-size: 12px;
  color: var(--c-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.progress {
  position: relative;
  width: 100%;
  height: 12px;
  display: flex;
  align-items: center;
  cursor: pointer;
  padding: 4px 0;
  margin-top: 4px;
}

.progress::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  height: 3px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
}

.progress-fill {
  position: absolute;
  left: 0;
  height: 3px;
  background: var(--c-accent);
  border-radius: 2px;
  pointer-events: none;
}

.progress-thumb {
  position: absolute;
  width: 10px;
  height: 10px;
  background: var(--c-accent);
  border-radius: 50%;
  transform: translateX(-50%);
  opacity: 0;
  pointer-events: none;
}

.progress:hover .progress-thumb { opacity: 1; }
.progress.is-seeking .progress-thumb { opacity: 1; }

.time {
  font-size: 12px;
  color: var(--c-accent);
  font-variant-numeric: tabular-nums;
  min-width: 36px;
  text-align: right;
  flex-shrink: 0;
}
</style>
