<template>
  <div class="library-artists">
    <!-- Info banner for global scope -->
    <InfoBanner
      v-if="scope === 'global'"
      :icon="User"
      title="Общая коллекция артистов"
      description="Все артисты, доступные в системе"
    />

    <!-- Action Bar: Sort & Expandable Search -->
    <div v-if="!hideToolbar" class="library-toolbar" :class="{ 'search-active': isSearchOpen }">
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
        placeholder="Поиск исполнителей..."
        title="Поиск исполнителей"
        @input="onSearchInput"
        @clear="onSearchClear"
      />
    </div>

    <!-- Spotify-style virtual grid -->
    <VirtualGrid
      ref="virtualGridRef"
      type="artist"
      :fetchFn="fetchArtists"
      :pageSize="30"
      :skeletonCount="12"
      @click="goToArtist"
      @contextmenu="handleContextMenu"
    >
      <template #empty>
        <span class="empty-icon"><User :size="48" /></span>
        <template v-if="effectiveSearchQuery || localQuery">
          <h3>Артисты не найдены</h3>
          <p class="empty-subtext">По запросу «{{ localQuery || effectiveSearchQuery }}» среди исполнителей ничего не найдено</p>
          <button 
            type="button" 
            class="btn-global-search" 
            @click="goToGlobalSearch"
          >
            <Search :size="16" />
            <span>Искать в глобальном поиске</span>
          </button>
        </template>
        <p v-else>{{ scope === 'global' ? 'Нет артистов в коллекции' : 'Нет исполнителей' }}</p>
      </template>
    </VirtualGrid>
  </div>
</template>

<script setup>
import { ref, watch, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSort } from '@/composables'
import { useContextMenu } from '@/composables/useContextMenu'
import SortChips from '@/components/SortChips.vue'
import ExpandableSearch from '@/components/ui/ExpandableSearch.vue'
import VirtualGrid from '@/components/VirtualGrid.vue'
import InfoBanner from '@/components/InfoBanner.vue'
import api, { artistsApi } from '@/api/client'
import { User, Search } from 'lucide-vue-next'

// Universal context menu
const { openMenu } = useContextMenu()

const props = defineProps({
  searchQuery: {
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
const virtualGridRef = ref(null)

// Sort state (persisted to localStorage) - separate key per scope
const sortStorageKey = computed(() => 
  props.scope === 'global' ? 'global-artists-sort' : 'library-artists-sort'
)

const { 
  sortBy, 
  sortOrder, 
  currentOption, 
  nextSort, 
  toggleOrder 
} = useSort(sortStorageKey.value, 'artists', { sortBy: 'name', sortOrder: 'asc' })

// Fetch function for virtual grid
const fetchArtists = async ({ offset, limit }) => {
  const params = {
    offset,
    limit,
    sort_by: sortBy.value,
    sort_order: sortOrder.value
  }
  
  if (effectiveSearchQuery.value) {
    params.search = effectiveSearchQuery.value
  }
  
  // Use global or library endpoint based on scope
  const response = props.scope === 'global' 
    ? await artistsApi.getGlobal(params)
    : await artistsApi.getAll(params)
  
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
const goToArtist = (artist) => {
  const query = props.scope === 'global' ? { scope: 'global' } : {}
  router.push({ 
    path: `/artist/${encodeURIComponent(artist.name)}`,
    query
  })
}

// Context menu
const handleContextMenu = ({ item, event }) => {
  openMenu('artist', item, props.scope, event)
}

// Watch scope changes to reload
watch(() => props.scope, () => {
  virtualGridRef.value?.reset()
})

// Expose for parent
defineExpose({
  reset: () => virtualGridRef.value?.reset()
})
</script>

<style scoped>
.library-artists {
  padding-bottom: 20px;
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
