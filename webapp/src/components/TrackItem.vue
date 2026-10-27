<template>
  <div 
    class="track-item" 
    :class="{ playing: isPlaying, compact: compact, unavailable: track.is_unavailable, disliked: track.is_disliked }" 
    @click="handleClick"
    @contextmenu.prevent="$emit('menu', $event)"
    @touchstart="handleTouchStart"
    @touchmove="handleTouchMove"
    @touchend="handleTouchEnd"
    @touchcancel="handleTouchEnd"
  >
    <!-- Track number (for album view) -->
    <span v-if="trackNumber" class="track-number">{{ trackNumber }}</span>
    
    <!-- Cover with generated gradient -->
    <div v-if="!hideCover" ref="coverElRef" class="track-cover" :style="coverStyle">
      <img 
        v-if="resolvedSrc && !track.is_unavailable && !imageError && !isCoverError" 
        :src="resolvedSrc" 
        alt=""
        class="cover-image"
        @error="imageError = true"
      />
      <span v-else class="cover-text">{{ track.is_unavailable ? '' : coverInitials }}<X v-if="track.is_unavailable" :size="16" /></span>
      
      <!-- Playing indicator -->
      <div v-if="isPlaying" class="playing-indicator">
        <div class="bar"></div>
        <div class="bar"></div>
        <div class="bar"></div>
      </div>
      
      <!-- Unavailable overlay -->
      <div v-if="track.is_unavailable" class="unavailable-overlay">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
        </svg>
      </div>
    </div>
    
    <!-- Mini cover placeholder for hideCover mode -->
    <div v-else class="track-cover-mini" :style="coverStyle">
      <div v-if="isPlaying" class="playing-indicator-mini">
        <div class="bar"></div>
        <div class="bar"></div>
        <div class="bar"></div>
      </div>
    </div>
    
    <div class="track-info">
      <div class="track-title">{{ getDisplayTitle(track) }}</div>
      <div class="track-meta">
        <span v-if="track.is_disliked" class="disliked-badge" title="Не нравится"><ThumbsDown :size="12" /> Не нравится</span>
        <span v-if="!hideArtist" class="track-artist">{{ getDisplayArtist(track) }}</span>
        <span v-if="showAlbum && albumName" class="track-album">{{ albumName }}</span>
        <span v-else-if="track.play_count && !hideArtist" class="play-count">• {{ track.play_count }} прослушиваний</span>
        <span v-if="firstTag && !compact" class="track-tag-badge" @click.stop="handleTagClick(firstTag)">#{{ firstTag }}</span>
      </div>
    </div>
    
    <!-- Lossless HD without MP3 streamable copy: show download button -->
    <template v-if="isHdFormat && !track.streamable_id && !track.is_unavailable">
      <span 
        class="hd-badge-mini" 
        title="Lossless (FLAC/WAV) - только скачивание"
      >
        HD
      </span>
      <span class="track-filesize">{{ fileSizeMB }} MB</span>
      <button 
        class="track-download" 
        @click.stop="$emit('download')"
        title="Скачать HD версию"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
        </svg>
      </button>
    </template>
    
    <!-- Streamable tracks (including large mixes & files): show duration with subtle mix icon -->
    <div v-else class="track-duration" :class="{ 'is-mix': isMix }">
      <Disc3 v-if="isMix" :size="12" class="mix-icon" title="Микс" />
      <span>{{ formatDuration(track.duration) }}</span>
    </div>
    
    <button 
      class="track-like" 
      :class="{ liked: isLiked }" 
      @click.stop="$emit('like')"
    >
      <svg v-if="isLiked" width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
      </svg>
      <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <path d="M16.5 3c-1.74 0-3.41.81-4.5 2.09C10.91 3.81 9.24 3 7.5 3 4.42 3 2 5.42 2 8.5c0 3.78 3.4 6.86 8.55 11.54L12 21.35l1.45-1.32C18.6 15.36 22 12.28 22 8.5 22 5.42 19.58 3 16.5 3zm-4.4 15.55l-.1.1-.1-.1C7.14 14.24 4 11.39 4 8.5 4 6.5 5.5 5 7.5 5c1.54 0 3.04.99 3.57 2.36h1.87C13.46 5.99 14.96 5 16.5 5c2 0 3.5 1.5 3.5 3.5 0 2.89-3.14 5.74-7.9 10.05z"/>
      </svg>
    </button>
    
    <!-- Add to library button for global tracks -->
    <button 
      v-if="showAddToLibrary && !inLibrary" 
      class="track-add-library" 
      @click.stop="$emit('addToLibrary')"
      title="Добавить в библиотеку"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
      </svg>
    </button>
    <span v-else-if="showAddToLibrary && inLibrary" class="in-library-badge" title="В библиотеке">
      <Check :size="16" />
    </span>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { formatDuration, getTrackCoverStyle, getTrackInitials, getDisplayTitle, getDisplayArtist, getCoverUrl, CoverSize, triggerHaptic, suppressNextClick, suppressNextContextMenu } from '@/utils'
import { useLazyCover } from '@/composables/useCoverLoader'
import { X, Check, ThumbsDown, Disc3 } from 'lucide-vue-next'

const isMix = computed(() => {
  const d = props.track?.duration
  return Boolean(d && d >= 900)
})

const router = useRouter()
const imageError = ref(false)
const coverElRef = ref(null)

const targetCoverUrl = computed(() => {
  if (props.hideCover || props.track?.is_unavailable || !props.track?.cover_url) {
    return null
  }
  return getCoverUrl(props.track.cover_url, CoverSize.SMALL)
})

const { resolvedSrc, isError: isCoverError } = useLazyCover(coverElRef, targetCoverUrl)

watch(() => props.track?.id || props.track?.cover_url, () => {
  imageError.value = false
})

const props = defineProps({
  track: {
    type: Object,
    required: true
  },
  isPlaying: {
    type: Boolean,
    default: false
  },
  isLiked: {
    type: Boolean,
    default: false
  },
  compact: {
    type: Boolean,
    default: false
  },
  trackNumber: {
    type: [Number, String],
    default: null
  },
  hideCover: {
    type: Boolean,
    default: false
  },
  hideArtist: {
    type: Boolean,
    default: false
  },
  showAlbum: {
    type: Boolean,
    default: false
  },
  showAddToLibrary: {
    type: Boolean,
    default: false
  },
  inLibrary: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['click', 'menu', 'like', 'addToLibrary', 'download', 'hdNotice'])

// Long-press handling for mobile touch
let longPressTimer = null
let touchMoved = false
let touchStartX = 0
let touchStartY = 0
let isLongPressTriggered = false

const handleTouchStart = (e) => {
  touchMoved = false
  isLongPressTriggered = false
  if (e.touches.length === 1) {
    touchStartX = e.touches[0].clientX
    touchStartY = e.touches[0].clientY
    longPressTimer = setTimeout(() => {
      if (!touchMoved) {
        isLongPressTriggered = true
        triggerHaptic()
        suppressNextClick()
        suppressNextContextMenu()
        emit('menu', e)
      }
    }, 450)
  }
}

const handleTouchMove = (e) => {
  if (e.touches.length === 1) {
    const dx = Math.abs(e.touches[0].clientX - touchStartX)
    const dy = Math.abs(e.touches[0].clientY - touchStartY)
    if (dx > 10 || dy > 10) {
      touchMoved = true
      clearTimeout(longPressTimer)
    }
  }
}

const handleTouchEnd = () => {
  clearTimeout(longPressTimer)
}

const handleClick = (e) => {
  if (isLongPressTriggered) {
    isLongPressTriggered = false
    e?.preventDefault?.()
    e?.stopPropagation?.()
    return
  }
  
  if (props.track.is_unavailable) {
    // Don't play unavailable tracks, just emit for potential action
    emit('menu', e)
    return
  }
  
  emit('click')
}

const coverStyle = computed(() => getTrackCoverStyle(props.track))

const coverInitials = computed(() => getTrackInitials(props.track))

// HD MIME types that cannot be streamed directly (lossless formats)
const HD_MIME_TYPES = [
  'audio/flac', 'audio/x-flac',           // FLAC
  'audio/wav', 'audio/x-wav',             // WAV
  'audio/aiff', 'audio/x-aiff',           // AIFF
  'audio/x-m4a', 'audio/mp4',             // M4A (ALAC)
  'audio/alac', 'audio/x-alac'            // ALAC
]

// Check if track is HD format (FLAC, WAV, M4A/ALAC, etc.)
const isHdFormat = computed(() => {
  if (!props.track?.mime_type) return false
  return HD_MIME_TYPES.includes(props.track.mime_type.toLowerCase())
})

// Check if track is not streamable (HD or too large >20MB)
const isNotStreamable = computed(() => {
  // HD format
  if (isHdFormat.value) return true
  // Too large
  if (props.track?.file_size && props.track.file_size > 20 * 1024 * 1024) return true
  // Explicitly marked as not streamable
  if (props.track?.is_streamable === false) return true
  return false
})

// Check if file is too large for streaming (>20MB) - legacy, now uses isNotStreamable
const isLargeFile = computed(() => {
  return isNotStreamable.value
})

const fileSizeMB = computed(() => {
  if (!props.track?.file_size) return 0
  return (props.track.file_size / (1024 * 1024)).toFixed(1)
})

const albumName = computed(() => {
  return props.track?.album?.name || props.track?.album_name || null
})

const firstTag = computed(() => {
  return props.track?.tags?.[0] || null
})

const handleTagClick = (tag) => {
  if (!tag) return
  const cleanTag = tag.replace(/^#/, '')
  router.push({ path: '/search', query: { tag: cleanTag } })
}
</script>

<style scoped>
/* ═══════════════════════════════════════════════════════════
   🎵 TRACK ITEM - Neumorphic Style
   Individual track row with soft shadows
   ═══════════════════════════════════════════════════════════ */

.track-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 8px 10px 8px 12px; /* Less right padding, scrollbar is there */
  margin: 0 0 0 8px; /* No vertical margin, spacing handled by gap */
  border-radius: var(--r-md);
  cursor: pointer;
  transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
  background: transparent;
  border: 1px solid transparent;
  box-sizing: border-box;
}

.track-item:hover {
  background: var(--c-bg-2);
  border: var(--border-neu);
  box-shadow: 
    3px 3px 8px var(--sh-dark),
    -2px -2px 5px var(--sh-light);
}

.track-item:active {
  background: var(--c-bg-3);
  transform: scale(0.98);
  box-shadow: 
    inset 2px 2px 5px var(--sh-inset-dark),
    inset -1px -1px 3px var(--sh-inset-light);
}

.track-item.playing {
  background: var(--c-bg-2);
  border: var(--border-neu);
  box-shadow: 
    inset 2px 2px 5px var(--sh-inset-dark),
    inset -1px -1px 3px var(--sh-inset-light);
}

.track-item.compact {
  padding: 6px 4px 6px 10px;
  margin: 2px 0 2px 6px;
}

/* ─── Track Cover ─── */
.track-cover {
  width: 48px;
  height: 48px;
  border-radius: var(--r-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  position: relative;
  overflow: hidden;
  box-shadow: 
    4px 4px 8px var(--sh-dark),
    -2px -2px 4px var(--sh-light);
  transition: box-shadow 0.2s ease;
}

.track-item.playing .track-cover {
  box-shadow: 
    4px 4px 8px var(--sh-dark),
    -2px -2px 4px var(--sh-light);
}

.compact .track-cover {
  width: 40px;
  height: 40px;
}

.cover-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-text {
  font-size: 16px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.8);
}

.compact .cover-text {
  font-size: 14px;
}

/* ─── Playing Indicator (Equalizer) ─── */
.playing-indicator {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 3px;
  padding-bottom: 12px;
}

.playing-indicator .bar {
  width: 4px;
  background: var(--c-accent);
  border-radius: 2px;
  animation: equalizer 0.8s ease-in-out infinite;
  box-shadow: 0 0 6px var(--c-accent-glow);
}

.playing-indicator .bar:nth-child(1) {
  height: 10px;
  animation-delay: 0s;
}

.playing-indicator .bar:nth-child(2) {
  height: 18px;
  animation-delay: 0.2s;
}

.playing-indicator .bar:nth-child(3) {
  height: 14px;
  animation-delay: 0.4s;
}

@keyframes equalizer {
  0%, 100% { transform: scaleY(1); }
  50% { transform: scaleY(0.4); }
}

/* ─── Track Info ─── */
.track-info {
  flex: 1;
  min-width: 0;
}

.track-title {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--c-text-1);
  transition: color 0.15s ease;
}

.compact .track-title {
  font-size: 13px;
}

.track-item.playing .track-title {
  color: var(--c-accent);
}

.track-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
  overflow: hidden;
  max-width: 100%;
}

.track-artist {
  font-size: 12px;
  color: var(--c-text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.compact .track-artist {
  font-size: 11px;
}

.play-count {
  font-size: 10px;
  color: var(--c-text-3);
  white-space: nowrap;
  flex-shrink: 0;
}

.track-tag-badge {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.07);
  color: var(--c-accent);
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.track-tag-badge:hover {
  background: rgba(255, 255, 255, 0.15);
  transform: scale(1.05);
}

@media (max-width: 768px) {
  .track-tag-badge {
    display: none !important;
    pointer-events: none !important;
  }
}

/* ─── Duration ─── */
.track-duration {
  font-size: 12px;
  font-weight: 500;
  color: var(--c-text-3);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.mix-icon {
  color: var(--c-accent);
  opacity: 0.85;
}

/* ─── HD Badge (for FLAC/WAV files) ─── */
.hd-badge-mini {
  padding: 2px 5px;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.3px;
  background: linear-gradient(135deg, #ffd700, #ff8c00);
  color: #1a1a2e;
  border-radius: 3px;
  flex-shrink: 0;
  box-shadow: 0 0 4px rgba(255, 215, 0, 0.4);
}

/* ─── File Size (for large files) ─── */
.track-filesize {
  width: 52px;
  text-align: right;
  font-size: 11px;
  font-weight: 600;
  color: var(--c-warning, #FFA000);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

/* ─── Download Button ─── */
.track-download {
  width: 32px;
  height: 32px;
  background: var(--c-bg-3);
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-warning, #FFA000);
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
  border-radius: var(--r-sm);
  box-shadow: 
    2px 2px 4px var(--sh-dark),
    -1px -1px 2px var(--sh-light);
}

.track-download:hover,
.track-download:active {
  background: var(--c-warning, #FFA000);
  color: #000;
  transform: scale(0.95);
  box-shadow: 
    inset 1px 1px 2px rgba(0, 0, 0, 0.2),
    0 0 8px rgba(255, 160, 0, 0.4);
}

/* ─── Like Button ─── */
.track-like {
  width: 32px;
  height: 32px;
  background: none;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-text-3);
  cursor: pointer;
  opacity: 0.5;
  transition: all 0.2s ease;
  flex-shrink: 0;
  border-radius: var(--r-full);
}

.track-like:hover,
.track-like:active {
  opacity: 1;
  background: var(--c-bg-3);
}

.track-like.liked {
  color: var(--c-accent);
  opacity: 1;
}

.track-like.liked svg {
  filter: drop-shadow(0 0 6px var(--c-accent-glow));
}

/* ─── Add to Library Button ─── */
.track-add-library {
  width: 32px;
  height: 32px;
  background: var(--c-accent);
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #000;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
  border-radius: var(--r-full);
  box-shadow: 
    2px 2px 4px var(--sh-dark),
    -1px -1px 2px var(--sh-light),
    0 0 8px var(--c-accent-glow));
}

.track-add-library:hover {
  transform: scale(1.05);
  box-shadow: 
    3px 3px 6px var(--sh-dark),
    -2px -2px 4px var(--sh-light),
    0 0 12px var(--c-accent-glow));
}

.track-item:active {
  transform: scale(0.99);
}

.track-item.disliked {
  opacity: 0.52;
}

.track-item.disliked:hover {
  opacity: 0.85;
}

.disliked-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: #ff5555;
  font-size: 11px;
  font-weight: 500;
}

.track-add-library:active {
  transform: scale(0.95);
  box-shadow: 
    inset 1px 1px 2px rgba(0, 0, 0, 0.2),
    0 0 8px var(--c-accent-glow));
}

.in-library-badge {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-accent);
  font-size: 16px;
  font-weight: bold;
  flex-shrink: 0;
}

/* ─── Unavailable Track Styles ─── */
.track-item.unavailable {
  opacity: 0.4;
}

.track-item.unavailable .track-title {
  text-decoration: line-through;
  color: var(--c-text-3);
}

.track-item.unavailable .track-cover {
  filter: grayscale(100%);
}

.unavailable-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-accent);
}



/* ─── Track Number ─── */
.track-number {
  width: 28px;
  text-align: center;
  color: var(--c-text-3);
  font-size: 14px;
  font-weight: 500;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.track-item.playing .track-number {
  color: var(--c-accent);
}

/* ─── Mini Cover for hideCover mode ─── */
.track-cover-mini {
  width: 8px;
  height: 32px;
  border-radius: 4px;
  flex-shrink: 0;
  position: relative;
}

.playing-indicator-mini {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 1px;
}

.playing-indicator-mini .bar {
  width: 2px;
  background: var(--c-accent);
  border-radius: 1px;
  animation: equalizer 0.8s ease-in-out infinite;
}

.playing-indicator-mini .bar:nth-child(1) {
  height: 8px;
  animation-delay: 0s;
}

.playing-indicator-mini .bar:nth-child(2) {
  height: 14px;
  animation-delay: 0.2s;
}

.playing-indicator-mini .bar:nth-child(3) {
  height: 10px;
  animation-delay: 0.4s;
}

/* ─── Track Album ─── */
.track-album {
  font-size: 11px;
  color: var(--c-text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.track-album::before {
  content: '•';
  margin: 0 4px;
}

</style>
