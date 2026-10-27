<template>
  <div class="library-tracks">
    <!-- Action Bar: Shuffle, Sort & Expandable Search -->
    <div v-if="!hideToolbar" class="library-toolbar" :class="{ 'search-active': isSearchOpen }">
      <!-- Left action controls (hidden when search is open) -->
      <div v-if="!isSearchOpen" class="toolbar-controls">
        <button class="shuffle-all-btn" @click="shuffleAll" :disabled="!total || shuffling">
          <div v-if="shuffling" class="spinner small"></div>
          <Shuffle v-else :size="16" class="shuffle-icon" />
          <span class="shuffle-text">
            <template v-if="shuffling">Загрузка...</template>
            <template v-else-if="total > 0">Перемешать ({{ total }})</template>
            <template v-else>Перемешать</template>
          </span>
        </button>
        <SortChips
          :currentOption="currentOption"
          :sortOrder="sortOrder"
          @next="onNextSort"
          @toggle-order="onToggleOrder"
        />
      </div>

      <!-- Right action controls: Expandable Search -->
      <div class="toolbar-actions-right" :class="{ 'search-active': isSearchOpen }">
        <!-- Expandable Search -->
        <ExpandableSearch
          v-model="localQuery"
          v-model:open="isSearchOpen"
          placeholder="Название или исполнитель..."
          title="Поиск по трекам"
          @input="onSearchInput"
          @clear="onSearchClear"
        />
      </div>
    </div>

    <!-- Virtual track list (without search) -->
    <div v-if="!effectiveSearchQuery" class="virtual-tracks-section">
      <VirtualTrackList
        ref="virtualTrackListRef"
        :fetchFn="fetchTracks"
        :pageSize="50"
        :skeletonCount="12"
        :showAlbum="false"
        menuContext="library"
        @click="handleVirtualClick"
        @like="handleLikeTrack"
        @menu="handleVirtualMenu"
        @download="handleDirectDownload"
        @hdNotice="handleHdNotice"
        @update:total="virtualTotal = $event"
      >
        <template #empty>
          <span class="empty-icon"><Music :size="48" /></span>
          <h3>Библиотека пуста</h3>
          <p>Отправьте аудио боту, чтобы добавить треки</p>
        </template>
      </VirtualTrackList>
    </div>

    <!-- Regular track list with search -->
    <div v-else class="track-list search-results" ref="trackListRef">
      <!-- Loading state with skeletons -->
      <template v-if="loading && !tracks.length">
        <TrackSkeleton v-for="i in 12" :key="i" />
      </template>
      
      <template v-else>
        <!-- Section: My Library results -->
        <template v-if="tracks.length">
          <div class="section-header">
            <span class="section-title">Моя библиотека</span>
            <span class="section-count">{{ tracks.length }}<template v-if="searchTotal > tracks.length"> из {{ searchTotal }}</template></span>
          </div>
          
          <TrackItem
            v-for="track in tracks"
            :key="track.id"
            :track="track"
            :isPlaying="playerStore.currentTrack?.id === track.id"
            :isActive="playerStore.isPlaying && playerStore.currentTrack?.id === track.id"
            :isLiked="track.is_liked"
            @click="playTrack(track)"
            @like="handleLikeTrack(track)"
            @menu="(e) => openMenu('track', track, 'library', e)"
            @download="handleDirectDownload(track)"
            @hdNotice="handleHdNotice"
          />

          <!-- Load more trigger for my library search infinite scroll -->
          <div v-if="hasMore" ref="loadTriggerRef" class="load-trigger">
            <div v-if="loadingMore" class="loading-more">
              <div class="spinner small"></div>
            </div>
          </div>

          <!-- Load more my tracks button -->
          <button v-if="hasMore" class="load-more-btn" :disabled="loadingMore" @click="loadMore">
            <template v-if="loadingMore">
              <div class="spinner small"></div>
              <span>Загрузка...</span>
            </template>
            <template v-else>
              <span>Показать ещё ({{ tracks.length }} из {{ searchTotal }})</span>
            </template>
          </button>

          <!-- Subtle shortcut to global search at the end of library results -->
          <div class="search-global-footer">
            <button 
              type="button" 
              class="btn-global-search-subtle" 
              @click="goToGlobalSearch"
            >
              <Search :size="15" />
              <span>Искать «{{ localQuery || effectiveSearchQuery }}» в глобальном поиске</span>
            </button>
          </div>
        </template>
        
        <!-- Empty state when no tracks found in library -->
        <div v-else-if="!loading" class="empty-state search-empty">
          <span class="empty-icon"><Music :size="48" /></span>
          <h3>Ничего не найдено</h3>
          <p class="empty-subtext" v-if="localQuery || effectiveSearchQuery">
            По запросу «{{ localQuery || effectiveSearchQuery }}» в медиатеке нет треков
          </p>
          <button 
            v-if="localQuery || effectiveSearchQuery" 
            type="button" 
            class="btn-global-search" 
            @click="goToGlobalSearch"
          >
            <Search :size="16" />
            <span>Искать в глобальном поиске</span>
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useLibraryStore } from '@/stores/library'
import { usePlayerStore } from '@/stores/player'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { useSort, useTrackActions, useTrackSync } from '@/composables'
import { useContextMenu } from '@/composables/useContextMenu'
import VirtualTrackList from '@/components/VirtualTrackList.vue'
import TrackItem from '@/components/TrackItem.vue'
import TrackSkeleton from '@/components/TrackSkeleton.vue'
import SortChips from '@/components/SortChips.vue'
import ExpandableSearch from '@/components/ui/ExpandableSearch.vue'
import api from '@/api/client'
import apiCache from '@/utils/apiCache'
import { getAllCachedTracks } from '@/utils/audioCacheDb'
import { Music, Shuffle, Search } from 'lucide-vue-next'

// Universal context menu
const { openMenu } = useContextMenu()

// Track actions (download, HD notice, like)
const { handleDirectDownload, handleHdNotice, handleLikeTrack } = useTrackActions()

const props = defineProps({
  searchQuery: {
    type: String,
    default: ''
  },
  showBack: {
    type: Boolean,
    default: true
  },
  hideToolbar: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['back', 'update:searchQuery'])

// localQuery controls the raw input value
const localQuery = ref(props.searchQuery || '')
const isSearchOpen = ref(Boolean(props.searchQuery?.trim()))

// effectiveSearchQuery is the debounced query that drives search results
const effectiveSearchQuery = ref(props.searchQuery || '')

let searchDebounceTimer = null
const onSearchInput = () => {
  clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => {
    const trimmed = (localQuery.value || '').trim()
    effectiveSearchQuery.value = trimmed
    emit('update:searchQuery', trimmed)
  }, 300)
}

const onSearchClear = () => {
  clearTimeout(searchDebounceTimer)
  localQuery.value = ''
  effectiveSearchQuery.value = ''
  emit('update:searchQuery', '')
}

// Watch external prop updates without overwriting active user typing
watch(() => props.searchQuery, (newVal) => {
  const val = newVal || ''
  if (props.hideToolbar) {
    effectiveSearchQuery.value = val.trim()
    return
  }
  if (val !== localQuery.value && val.trim() !== effectiveSearchQuery.value) {
    localQuery.value = val
    effectiveSearchQuery.value = val.trim()
    if (val.trim()) {
      isSearchOpen.value = true
    }
  }
})

const goToGlobalSearch = () => {
  const q = (localQuery.value || effectiveSearchQuery.value || '').trim()
  if (q) {
    router.push({ path: '/search', query: { q } })
  }
}

const router = useRouter()
const libraryStore = useLibraryStore()
const playerStore = usePlayerStore()
const authStore = useAuthStore()
const uiStore = useUIStore()

// Sort state (persisted to localStorage)
const { 
  sortBy, 
  sortOrder, 
  currentOption, 
  nextSort, 
  toggleOrder 
} = useSort('library-sort', 'library', { sortBy: 'added_at', sortOrder: 'desc' })

// Sort handlers
const onNextSort = async () => {
  nextSort()
  if (effectiveSearchQuery.value) {
    page.value = 1
    await loadSearchTracks()
  } else {
    virtualTrackListRef.value?.reset()
  }
}

const onToggleOrder = async () => {
  toggleOrder()
  if (effectiveSearchQuery.value) {
    page.value = 1
    await loadSearchTracks()
  } else {
    virtualTrackListRef.value?.reset()
  }
}

// Virtual track list ref
const virtualTrackListRef = ref(null)
const virtualTotal = ref(0)

const loading = ref(true)
const loadingMore = ref(false)
const shuffling = ref(false)
const tracks = ref([])
const page = ref(1)
const searchTotal = ref(0) // Real total from API during search
const total = computed(() => {
  if (effectiveSearchQuery.value) {
    return searchTotal.value
  }
  return virtualTotal.value
})
const perPage = 50
const loadTriggerRef = ref(null)
let observer = null

const hasMore = computed(() => tracks.value.length < searchTotal.value)

// Sync local track arrays with track changes/removals
useTrackSync(tracks, { isLibraryList: true })

const isRefreshing = ref(false)

// Fetch function for VirtualTrackList (without search)
const fetchTracks = async ({ offset, limit, signal, _cb }) => {
  try {
    const shouldBypass = isRefreshing.value || Boolean(_cb)
    const response = await api.get('/tracks', {
      params: {
        offset,
        limit,
        sort_by: sortBy.value,
        sort_order: sortOrder.value,
        ...(_cb ? { _cb } : {})
      },
      signal,
      bypassCache: shouldBypass
    })
    return response.data
  } catch (err) {
    if (err?.name === 'AbortError' || err?.code === 'ERR_CANCELED') {
      throw err
    }
    // If offline / network error, fallback to cached tracks in IndexedDB
    try {
      const cached = await getAllCachedTracks()
      return {
        items: cached.slice(offset, offset + limit),
        total: cached.length,
        offset,
        limit
      }
    } catch (_) {
      return { items: [], total: 0, offset, limit }
    }
  }
}

// Auto-sync tracks on library events
const onLibraryTrackAdded = () => {
  if (effectiveSearchQuery.value) {
    page.value = 1
    loadSearchTracks()
  } else {
    virtualTrackListRef.value?.reset({ _cb: Date.now() })
  }
}

const onTrackRemovedGlobal = () => {
  if (searchTotal.value > 0) {
    searchTotal.value = Math.max(0, searchTotal.value - 1)
  }
}

onMounted(() => {
  window.addEventListener('track:added:library', onLibraryTrackAdded)
  window.addEventListener('track:removed', onTrackRemovedGlobal)
  window.addEventListener('track:removed:library', onTrackRemovedGlobal)
})

onUnmounted(() => {
  window.removeEventListener('track:added:library', onLibraryTrackAdded)
  window.removeEventListener('track:removed', onTrackRemovedGlobal)
  window.removeEventListener('track:removed:library', onTrackRemovedGlobal)
})

// Handle click from VirtualTrackList
const handleVirtualClick = ({ track, index, allTracks }) => {
  const query = effectiveSearchQuery.value ? effectiveSearchQuery.value.trim() : undefined
  if (playerStore.shuffle) {
    playerStore.playShuffleAll('library', null, null, {
      startingTrack: track,
      search: query
    })
  } else {
    playerStore.playTrack(track, allTracks, { type: 'library', search: query })
  }
}

// Handle menu from VirtualTrackList  
const handleVirtualMenu = ({ track, index, event }) => {
  openMenu('track', track, 'library', event)
}

const loadSearchTracks = async () => {
  loading.value = true
  try {
    const q = effectiveSearchQuery.value
    await libraryStore.fetchTracks({
      page: page.value,
      per_page: perPage,
      search: q || undefined,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    })
    tracks.value = libraryStore.tracks
    searchTotal.value = libraryStore.total
  } catch (error) {
    console.error('Failed to load search tracks:', error)
    // Offline search fallback
    try {
      const cached = await getAllCachedTracks()
      const q = (effectiveSearchQuery.value || '').toLowerCase()
      const filtered = cached.filter(t => 
        (t.title && t.title.toLowerCase().includes(q)) || 
        (t.artist && t.artist.toLowerCase().includes(q))
      )
      tracks.value = filtered
      searchTotal.value = filtered.length
    } catch (_) {}
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

const loadMore = async () => {
  if (loadingMore.value || loading.value || !hasMore.value) return
  loadingMore.value = true
  page.value++
  await loadSearchTracks()
}

const findScrollContainer = (el) => {
  let parent = el?.parentElement
  while (parent) {
    const style = window.getComputedStyle(parent)
    if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
      return parent
    }
    parent = parent.parentElement
  }
  return window
}

// Setup IntersectionObserver for infinite scroll
const setupObserver = () => {
  if (observer) {
    observer.disconnect()
    observer = null
  }
  
  if (!loadTriggerRef.value) return

  const container = findScrollContainer(loadTriggerRef.value)
  const isWindow = container === window
  
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0]?.isIntersecting && hasMore.value && !loading.value && !loadingMore.value) {
        loadMore()
      }
    },
    {
      root: isWindow ? null : container,
      rootMargin: '300px'
    }
  )
  
  observer.observe(loadTriggerRef.value)
}

// Watch loadTriggerRef to setup observer when element appears
watch(loadTriggerRef, (el) => {
  if (el) setupObserver()
})

// Watch effectiveSearchQuery to trigger search mode
watch(effectiveSearchQuery, async (newVal) => {
  // If query changes, reset page
  page.value = 1
  
  if (newVal) {
    // Search mode - load tracks via library store
    await loadSearchTracks()
  } else {
    // No search - reset to virtual list mode
    tracks.value = []
    // Reset virtual list
    if (virtualTrackListRef.value) {
      virtualTrackListRef.value.reset()
    }
  }
}, { immediate: true })



const playTrack = (track) => {
  const query = effectiveSearchQuery.value ? effectiveSearchQuery.value.trim() : undefined
  if (playerStore.shuffle) {
    playerStore.playShuffleAll('library', null, null, {
      startingTrack: track,
      search: query
    })
  } else {
    playerStore.playTrack(track, tracks.value, { type: 'library', search: query })
  }
}

// Shuffle all library tracks using lazy loading
const shuffleAll = async () => {
  if (shuffling.value) return
  shuffling.value = true
  try {
    const trimmedQuery = effectiveSearchQuery.value ? effectiveSearchQuery.value.trim() : ''
    await playerStore.playShuffleAll('library', null, null, {
      search: trimmedQuery || undefined
    })
  } finally {
    shuffling.value = false
  }
}

onMounted(() => {
  // VirtualTrackList handles initial load automatically
  // Only need observer for search mode
})

onUnmounted(() => {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
  if (observer) observer.disconnect()
})
</script>

<style scoped>
/* Reuse existing styles */
.library-tracks {
  padding-bottom: 20px;
}

/* Virtual tracks section for Spotify-style scrolling */
.virtual-tracks-section {
  min-height: 200px;
}

/* Search results section */
.search-results {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.library-toolbar {
  position: sticky;
  top: var(--library-nav-height, 58px);
  z-index: 25;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
  padding: 12px 16px;
  margin-inline: -16px;
  margin-bottom: 14px;
  background: rgba(14, 16, 16, 0.88);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

@media (min-width: 768px) {
  .library-toolbar {
    padding: 12px 24px;
    margin-inline: -24px;
  }
}

@media (min-width: 1024px) {
  .library-toolbar {
    top: 0;
  }
}

.library-toolbar.search-active {
  justify-content: stretch;
}

.toolbar-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: nowrap;
  flex-shrink: 0;
}

.shuffle-all-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: auto;
  padding: 0 16px;
  justify-content: center;
  flex-shrink: 0;
}

.shuffle-all-btn .shuffle-text {
  display: inline-block;
  white-space: nowrap;
}

.shuffle-all-btn .spinner {
  width: 16px;
  height: 16px;
  border-width: 2px;
  border-color: rgba(0, 0, 0, 0.2);
  border-top-color: var(--c-accent-text, #000);
  flex-shrink: 0;
}

.shuffle-icon {
  flex-shrink: 0;
}

.toolbar-actions-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.toolbar-actions-right.search-active {
  flex: 1;
  width: 100%;
}


.spin-anim {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Loading state - uses .spinner from design-system.css */
.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px;
  color: var(--c-text-2);
}

/* Section header styling */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 4px 10px;
  margin-top: 8px;
  user-select: none;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--c-text-1, #fff);
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-count {
  font-size: 13px;
  font-weight: 500;
  color: var(--c-text-3, rgba(255, 255, 255, 0.5));
}

/* Global search footer button inside library search */
.search-global-footer {
  display: flex;
  justify-content: center;
  padding: 20px 0 12px;
}

.btn-global-search-subtle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 18px;
  background: var(--c-bg-3, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--c-bg-4, rgba(255, 255, 255, 0.12));
  border-radius: 20px;
  color: var(--c-accent, #1db954);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  user-select: none;
}

.btn-global-search-subtle:hover {
  background: rgba(29, 185, 84, 0.15);
  border-color: rgba(29, 185, 84, 0.35);
  transform: translateY(-1px);
}

.btn-global-search-subtle:active {
  transform: scale(0.98);
}

.load-more-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 12px;
  margin-top: 12px;
  margin-bottom: 8px;
  background: var(--c-bg-3, rgba(255, 255, 255, 0.05));
  color: var(--c-accent, #10b981);
  border: 1px solid var(--c-bg-4, rgba(255, 255, 255, 0.1));
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.load-more-btn:hover {
  background: var(--bg-hover, rgba(255, 255, 255, 0.08));
}

.load-more-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.load-trigger {
  min-height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
