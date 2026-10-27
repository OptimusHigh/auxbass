<template>
  <div class="virtual-track-list" ref="containerRef">
    <!-- Initial loading skeleton (before first fetch) -->
    <div v-if="loading && total === 0" class="track-list initial-loading">
      <TrackSkeleton v-for="i in skeletonCount" :key="`init-skel-${i}`" />
    </div>

    <!-- Virtual track list with windowed rendering -->
    <div
      v-else-if="total > 0"
      class="virtual-track-list-canvas"
      :style="{ height: `${totalHeight}px` }"
    >
      <div
        class="virtual-track-list-window track-list"
        :style="{ transform: `translateY(${topOffset}px)` }"
      >
        <template v-for="vItem in visibleItems" :key="vItem.index">
          <TrackItem
            v-if="!vItem.isPlaceholder && vItem.data"
            :track="vItem.data"
            :trackNumber="showTrackNumber ? vItem.index + 1 : undefined"
            :isPlaying="playerStore.currentTrack?.id === vItem.data.id"
            :isActive="playerStore.isPlaying && playerStore.currentTrack?.id === vItem.data.id"
            :isLiked="vItem.data.is_liked"
            :showAlbum="showAlbum"
            :showAddToLibrary="showAddToLibrary"
            :inLibrary="vItem.data.in_library"
            @click="handleClick(vItem.data, vItem.index)"
            @like="handleLike(vItem.data)"
            @menu="(e) => handleMenu(vItem.data, vItem.index, e)"
            @download="handleDownload(vItem.data)"
            @addToLibrary="handleAddToLibrary(vItem.data)"
            @hdNotice="handleHdNotice"
          />
          <TrackSkeleton v-else />
        </template>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="!loading && total === 0" class="empty-state">
      <slot name="empty">
        <span class="empty-icon"><Music :size="48" /></span>
        <p>Нет треков</p>
      </slot>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, onMounted, onUnmounted } from 'vue'
import { usePlayerStore } from '@/stores/player'
import { useVirtualScroll } from '@/composables/useVirtualScroll'
import TrackItem from '@/components/TrackItem.vue'
import TrackSkeleton from '@/components/TrackSkeleton.vue'
import { Music } from 'lucide-vue-next'

const props = defineProps({
  // Fetch function: ({ offset, limit }) => Promise<{ items, total }>
  fetchFn: {
    type: Function,
    required: true
  },
  // Number of items per page
  pageSize: {
    type: Number,
    default: 50
  },
  // Initial skeleton count
  skeletonCount: {
    type: Number,
    default: 12
  },
  // Item height in px (TrackItem is 64px)
  itemHeight: {
    type: Number,
    default: 64
  },
  // Gap between track items
  gap: {
    type: Number,
    default: 4
  },
  // Number of items to render above and below viewport
  overscan: {
    type: Number,
    default: 10
  },
  // Show track numbers
  showTrackNumber: {
    type: Boolean,
    default: false
  },
  // Show album info in track item
  showAlbum: {
    type: Boolean,
    default: false
  },
  // Show add to library button
  showAddToLibrary: {
    type: Boolean,
    default: false
  },
  // Menu context for track actions
  menuContext: {
    type: String,
    default: 'library'
  }
})

const emit = defineEmits([
  'click',
  'like',
  'menu',
  'download',
  'addToLibrary',
  'hdNotice',
  'update:items',
  'update:total'
])

const playerStore = usePlayerStore()

// Setup virtual scroll engine
const {
  containerRef,
  total,
  loading,
  loadingMore,
  error,
  totalHeight,
  topOffset,
  visibleItems,
  getLoadedItems,
  patchItem,
  removeItem,
  reset,
  refresh,
  updateScroll
} = useVirtualScroll({
  fetchFn: props.fetchFn,
  pageSize: props.pageSize,
  itemHeight: props.itemHeight,
  gap: props.gap,
  overscan: props.overscan,
  columns: 1,
  immediate: true
})

// Emit updates when total changes
watch(total, (newTotal) => {
  emit('update:total', newTotal)
})

// Event handlers
const handleClick = (track, index) => {
  emit('click', { track, index, allTracks: getLoadedItems() })
}

const handleLike = (track) => {
  emit('like', track)
}

const handleMenu = (track, index, event) => {
  emit('menu', { track, index, event, context: props.menuContext })
}

const handleDownload = (track) => {
  emit('download', track)
}

const handleAddToLibrary = (track) => {
  emit('addToLibrary', track)
}

const handleHdNotice = () => {
  emit('hdNotice')
}

// Get all loaded tracks (for playback)
const getLoadedTracks = () => {
  return getLoadedItems()
}

// Global track events sync
const onTrackChanged = (event) => {
  const { trackId, data } = event.detail || {}
  if (!trackId || !data) return
  patchItem(trackId, data)
  emit('update:items', getLoadedItems())
}

const onTrackRemoved = (event) => {
  const { trackId } = event.detail || {}
  if (!trackId) return
  removeItem(trackId)
  emit('update:total', total.value)
  emit('update:items', getLoadedItems())
}

const onLibraryTrackRemoved = (event) => {
  if (props.menuContext === 'library') {
    onTrackRemoved(event)
  }
}

const onLibraryTrackAdded = () => {
  if (props.menuContext === 'library') {
    reset({ _cb: Date.now() })
  }
}

onMounted(() => {
  window.addEventListener('track:changed', onTrackChanged)
  window.addEventListener('track:removed', onTrackRemoved)
  window.addEventListener('track:removed:library', onLibraryTrackRemoved)
  window.addEventListener('track:added:library', onLibraryTrackAdded)
})

onUnmounted(() => {
  window.removeEventListener('track:changed', onTrackChanged)
  window.removeEventListener('track:removed', onTrackRemoved)
  window.removeEventListener('track:removed:library', onLibraryTrackRemoved)
  window.removeEventListener('track:added:library', onLibraryTrackAdded)
})

// Expose methods & properties for parent components
defineExpose({
  reset,
  refresh,
  getLoadedTracks,
  total,
  items: computed(() => getLoadedItems()),
  updateScroll
})
</script>

<style scoped>
.virtual-track-list {
  position: relative;
  width: 100%;
  padding-bottom: 32px;
}

.virtual-track-list-canvas {
  position: relative;
  width: 100%;
  contain: layout paint;
}

.virtual-track-list-window {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  width: 100%;
  will-change: transform;
  contain: layout;
}

.track-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.initial-loading {
  padding: 0;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  text-align: center;
  color: var(--c-text-2);
}

.empty-icon {
  color: var(--c-text-3);
  margin-bottom: 16px;
}
</style>
