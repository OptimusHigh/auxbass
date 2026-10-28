<template>
  <div class="library-albums">
    <!-- Info banner for global scope -->
    <InfoBanner
      v-if="scope === 'global'"
      :icon="Disc3"
      title="Общая коллекция альбомов"
      description="Все альбомы, доступные в системе"
    />

    <!-- Action Bar: Sort & Expandable Search -->
    <div v-if="!hideToolbar" class="library-toolbar" :class="{ 'search-active': isSearchOpen }">
      <!-- Back button + section title -->
      <div v-if="showBack || title" class="toolbar-title">
        <button
          v-if="showBack"
          class="toolbar-back-btn"
          @click="emit('back')"
          title="Назад в медиатеку"
          aria-label="Назад в медиатеку"
        >
          <ChevronLeft :size="18" />
        </button>
        <h1 v-if="title" class="toolbar-heading">{{ title }}</h1>
      </div>

      <!-- Sort controls (hidden when search is open) -->
      <div v-if="!isSearchOpen" class="toolbar-controls">
        <SortChips
          :currentOption="currentOption"
          :sortOrder="sortOrder"
          @next="onNextSort"
          @toggle-order="onToggleOrder"
        />
        <div class="stats" v-if="virtualGridRef?.total">
          {{ virtualGridRef?.total }}
        </div>
      </div>

      <!-- Expandable Search -->
      <ExpandableSearch
        v-model="localQuery"
        v-model:open="isSearchOpen"
        placeholder="Поиск альбомов..."
        title="Поиск альбомов"
        @input="onSearchInput"
        @clear="onSearchClear"
      />
    </div>

    <!-- Spotify-style virtual grid -->
    <VirtualGrid
      ref="virtualGridRef"
      type="album"
      :fetchFn="fetchAlbums"
      :pageSize="30"
      :skeletonCount="12"
      @click="goToAlbum"
      @play="playAlbum"
      @contextmenu="handleContextMenu"
    >
      <template #empty>
        <span class="empty-icon"><Disc3 :size="48" /></span>
        <template v-if="effectiveSearchQuery || localQuery">
          <h3>Альбомы не найдены</h3>
          <p class="empty-subtext">По запросу «{{ localQuery || effectiveSearchQuery }}» в альбомах ничего не найдено</p>
          <button 
            type="button" 
            class="btn-global-search" 
            @click="goToGlobalSearch"
          >
            <Search :size="16" />
            <span>Искать в глобальном поиске</span>
          </button>
        </template>
        <p v-else>{{ scope === 'global' ? 'Нет альбомов в коллекции' : 'В библиотеке нет альбомов' }}</p>
      </template>
    </VirtualGrid>
  </div>
</template>

<script setup>
import { ref, watch, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useSort } from '@/composables'
import { useContextMenu } from '@/composables/useContextMenu'
import SortChips from '@/components/SortChips.vue'
import ExpandableSearch from '@/components/ui/ExpandableSearch.vue'
import VirtualGrid from '@/components/VirtualGrid.vue'
import InfoBanner from '@/components/InfoBanner.vue'
import api from '@/api/client'
import { Disc3, Search, ChevronLeft } from 'lucide-vue-next'

// Universal context menu
const { openMenu } = useContextMenu()

const props = defineProps({
  searchQuery: {
    type: String,
    default: ''
  },
  title: {
    type: String,
    default: ''
  },
  scope: {
    type: String,
    default: 'library',
    validator: v => ['library', 'global'].includes(v)
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
const playerStore = usePlayerStore()
const virtualGridRef = ref(null)

// Sort state (persisted to localStorage) - separate key per scope
const sortStorageKey = computed(() => 
  props.scope === 'global' ? 'global-albums-sort' : 'library-albums-sort'
)

const { 
  sortBy, 
  sortOrder, 
  currentOption, 
  nextSort, 
  toggleOrder 
} = useSort(sortStorageKey.value, 'albums', { sortBy: 'release_date', sortOrder: 'desc' })

// Fetch function for virtual grid
const fetchAlbums = async ({ offset, limit }) => {
  const params = { 
    offset, 
    limit,
    sort_by: sortBy.value,
    sort_order: sortOrder.value
  }
  if (effectiveSearchQuery.value) {
    params.search = effectiveSearchQuery.value
  }
  // For global scope, require at least 1 track
  if (props.scope === 'global') {
    params.min_tracks = 1
  }
  const endpoint = props.scope === 'global' ? '/albums/global' : '/albums'
  const response = await api.get(endpoint, { params })
  return response.data
}

// Sort handlers
const onNextSort = () => {
  nextSort()
  virtualGridRef.value?.reset()
}

const onToggleOrder = () => {
  toggleOrder()
  virtualGridRef.value?.reset()
}

// Watch search query to reload
watch(effectiveSearchQuery, () => {
  virtualGridRef.value?.reset()
})

onUnmounted(() => {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
})

// Navigation
const goToAlbum = (album) => {
  const query = props.scope === 'global' ? { scope: 'global' } : {}
  router.push({ 
    path: `/album/${album.id}`,
    query
  })
}

// Play album
const playAlbum = async (album) => {
  try {
    const params = props.scope === 'global' ? { scope: 'global' } : {}
    const response = await api.get(`/albums/${album.id}`, { params })
    const albumData = response.data
    
    // Get playable tracks - from full_tracklist or tracks array
    let tracks = []
    if (albumData.full_tracklist?.length) {
      tracks = albumData.full_tracklist
        .filter(item => item.track)
        .map(item => item.track)
    } else if (albumData.tracks?.length) {
      tracks = albumData.tracks
    }
    
    if (tracks.length) {
      playerStore.playTrack(tracks[0], tracks, { type: 'album', id: album.id, name: album.name || albumData.name })
    }
  } catch (error) {
    console.error('Failed to load album:', error)
  }
}

// Context menu
const handleContextMenu = ({ item, event }) => {
  openMenu('album', item, props.scope, event)
}

// Watch scope changes to reload
watch(() => props.scope, () => {
  virtualGridRef.value?.reset()
})

// Expose for parent
defineExpose({
  reset: () => virtualGridRef.value?.reset(),
  refresh: () => virtualGridRef.value?.reset()
})
</script>

<style scoped>
.library-albums {
  padding-bottom: 20px;
}

.library-toolbar {
  position: sticky;
  top: 0;
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

.library-toolbar.search-active {
  justify-content: stretch;
}

.toolbar-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  min-width: 0;
}

.toolbar-back-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: var(--c-text-1, #fff);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
  user-select: none;
}

.toolbar-back-btn:hover {
  background: rgba(255, 255, 255, 0.14);
  border-color: rgba(255, 255, 255, 0.2);
  transform: scale(1.05);
}

.toolbar-back-btn:active {
  transform: scale(0.94);
  background: rgba(255, 255, 255, 0.22);
}

.toolbar-heading {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.5px;
  color: var(--c-text-1, #fff);
  margin: 0;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (min-width: 1024px) {
  .toolbar-heading {
    font-size: 24px;
  }
}

.toolbar-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: nowrap;
  flex-shrink: 0;
}

.stats {
  color: var(--c-text-2);
  font-size: 13px;
  font-weight: 500;
}

@media (max-width: 480px) {
  .stats {
    display: none;
  }
}
</style>
