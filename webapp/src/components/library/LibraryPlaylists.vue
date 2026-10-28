<template>
  <div class="library-playlists">
    <!-- Info banner for global scope -->
    <InfoBanner
      v-if="scope === 'global'"
      :icon="FileText"
      title="Общая коллекция плейлистов"
      description="Все плейлисты, доступные в системе"
    />

    <!-- Action Bar: Sort & Create Controls + Expandable Search -->
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

      <!-- Sort & Action controls (hidden when search is open) -->
      <div v-if="!isSearchOpen" class="toolbar-controls">
        <SortChips
          :currentOption="currentOption"
          :sortOrder="sortOrder"
          @next="onNextSort"
          @toggle-order="onToggleOrder"
        />
        <button v-if="scope === 'global'" class="btn-pill-primary" @click="showManageModal = true">
          <Plus :size="16" /> <span>Добавить</span>
        </button>
        <button v-else class="btn-pill-primary" @click="handleCreatePlaylist">
          <Plus :size="16" /> <span>Создать</span>
        </button>
        <div class="stats" v-if="virtualGridRef?.total">
          {{ virtualGridRef?.total }}
        </div>
      </div>

      <!-- Expandable Search -->
      <ExpandableSearch
        v-model="localQuery"
        v-model:open="isSearchOpen"
        placeholder="Поиск плейлистов..."
        title="Поиск плейлистов"
        @input="onSearchInput"
        @clear="onSearchClear"
      />
    </div>

    <!-- Spotify-style virtual grid -->
    <VirtualGrid
      ref="virtualGridRef"
      type="playlist"
      :fetchFn="fetchPlaylists"
      :pageSize="30"
      :skeletonCount="12"
      @click="goToPlaylist"
      @play="shufflePlaylist"
      @contextmenu="handleContextMenu"
    >
      <template #empty>
        <span class="empty-icon"><FileText :size="48" /></span>
        <template v-if="effectiveSearchQuery || localQuery">
          <h3>Плейлисты не найдены</h3>
          <p class="empty-subtext">По запросу «{{ localQuery || effectiveSearchQuery }}» в плейлистах ничего не найдено</p>
          <button 
            type="button" 
            class="btn-global-search" 
            @click="goToGlobalSearch"
          >
            <Search :size="16" />
            <span>Искать в глобальном поиске</span>
          </button>
        </template>
        <template v-else>
          <p>{{ scope === 'global' ? 'Нет публичных плейлистов' : 'У вас пока нет плейлистов' }}</p>
          <button v-if="scope !== 'global'" class="create-first-btn" @click="handleCreatePlaylist">
            Создать плейлист
          </button>
        </template>
      </template>
    </VirtualGrid>

    <!-- Create modal (library scope) -->
    <div v-if="showCreateModal" class="modal-overlay" @click.self="closeCreateModal">
      <div class="modal">
        <h2>Новый плейлист</h2>
        <input
          v-model="newPlaylistName"
          type="text"
          placeholder="Название плейлиста"
          ref="createNameInput"
          @keyup.enter="createPlaylist"
        />
        <div class="modal-actions">
          <button type="button" class="cancel-btn" @click="closeCreateModal">Отмена</button>
          <button 
            type="button"
            class="confirm-btn" 
            @click="createPlaylist"
            :disabled="!newPlaylistName.trim()"
          >
            Создать
          </button>
        </div>
      </div>
    </div>

    <!-- Manage modal (global scope) — toggle public status -->
    <div v-if="showManageModal" class="modal-overlay" @click.self="closeManageModal">
      <div class="modal manage-modal">
        <h2>Добавить в коллекции</h2>
        <p class="hint-text">Выберите плейлисты для отображения в общей коллекции</p>
        
        <div class="playlists-manage-list">
          <div 
            v-for="playlist in ownPlaylists" 
            :key="playlist.id"
            class="playlist-manage-item"
            @click="togglePlaylistStatus(playlist)"
          >
            <div class="playlist-manage-info">
              <div class="playlist-manage-cover">
                <img v-if="playlist.covers?.length" :src="getCoverUrl(playlist.covers[0], CoverSize.SMALL)" loading="lazy" alt="" />
                <div v-else class="playlist-manage-placeholder"><Music :size="20" /></div>
              </div>
              <div class="playlist-manage-text">
                <div class="playlist-manage-name">{{ playlist.name }}</div>
                <div class="playlist-manage-count">{{ playlist.track_count }} треков</div>
              </div>
            </div>
            <label class="checkbox-label compact">
              <input 
                type="checkbox" 
                :checked="playlist.is_public"
                @click.stop="togglePlaylistStatus(playlist)"
              />
            </label>
          </div>
        </div>
        
        <div class="modal-actions">
          <button class="cancel-btn" @click="closeManageModal">Закрыть</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted, onActivated } from 'vue'
import { useRouter } from 'vue-router'
import { useLibraryStore } from '@/stores/library'
import { useAuthStore } from '@/stores/auth'
import { usePlayerStore } from '@/stores/player'
import { useUIStore } from '@/stores/ui'
import { useSort } from '@/composables'
import { useContextMenu } from '@/composables/useContextMenu'
import SortChips from '@/components/SortChips.vue'
import ExpandableSearch from '@/components/ui/ExpandableSearch.vue'
import VirtualGrid from '@/components/VirtualGrid.vue'
import InfoBanner from '@/components/InfoBanner.vue'
import api from '@/api/client'
import { Plus, FileText, Music, Search, ChevronLeft } from 'lucide-vue-next'
import { getCoverUrl, CoverSize } from '@/utils'

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
const libraryStore = useLibraryStore()
const authStore = useAuthStore()
const playerStore = usePlayerStore()
const uiStore = useUIStore()
const virtualGridRef = ref(null)

// Sort state (persisted to localStorage) - separate key per scope
const sortStorageKey = computed(() => 
  props.scope === 'global' ? 'global-playlists-sort' : 'library-playlists-sort'
)

const { 
  sortBy, 
  sortOrder, 
  currentOption, 
  nextSort, 
  toggleOrder 
} = useSort(sortStorageKey.value, 'playlists', { sortBy: 'created_at', sortOrder: 'desc' })

// Liked tracks count (library scope only)
const likedCount = ref(libraryStore.likedTracks?.length || 0)

watch(() => libraryStore.likedTracks?.length, (len) => {
  likedCount.value = len || 0
  virtualGridRef.value?.patchItem('liked', { track_count: likedCount.value })
})

// Create modal state
const showCreateModal = ref(false)
const newPlaylistName = ref('')
const createNameInput = ref(null)

// Manage modal state (global scope)
const showManageModal = ref(false)
const ownPlaylists = ref([])

// Fetch function for virtual grid
const fetchPlaylists = async ({ offset, limit }) => {
  const isLib = props.scope === 'library'
  const likedTitle = 'Понравившиеся'
  const q = (effectiveSearchQuery.value || '').trim().toLowerCase()
  const matchesSearch = !q || likedTitle.toLowerCase().includes(q) || 'liked'.includes(q)
  const showLikedCard = isLib && matchesSearch

  const params = { 
    sort_by: sortBy.value,
    sort_order: sortOrder.value
  }
  if (effectiveSearchQuery.value) {
    params.search = effectiveSearchQuery.value
  }
  const endpoint = props.scope === 'global' ? '/playlists/global' : '/playlists'

  if (!showLikedCard) {
    const response = await api.get(endpoint, { 
      params: { ...params, offset, limit },
      bypassCache: isLib
    })
    return response.data
  }

  const likedItem = {
    id: 'liked',
    is_liked: true,
    name: likedTitle,
    track_count: likedCount.value,
    is_owner: false,
    is_public: false,
  }

  if (offset === 0) {
    const apiLimit = Math.max(1, limit - 1)
    const response = await api.get(endpoint, { 
      params: { ...params, offset: 0, limit: apiLimit },
      bypassCache: isLib
    })
    const items = [likedItem, ...(response.data?.items || [])]
    const apiTotal = response.data?.total ?? response.data?.items?.length ?? 0
    return {
      items,
      total: apiTotal + 1
    }
  } else {
    const apiOffset = offset - 1
    const response = await api.get(endpoint, { 
      params: { ...params, offset: apiOffset, limit },
      bypassCache: isLib
    })
    const apiTotal = response.data?.total ?? response.data?.items?.length ?? 0
    return {
      items: response.data?.items || [],
      total: apiTotal + 1
    }
  }
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
const goToPlaylist = (playlist) => {
  if (playlist.id === 'liked' || playlist.is_liked) {
    router.push('/liked')
    return
  }
  router.push(`/playlist/${playlist.id}`)
}

const goToLiked = () => {
  router.push('/liked')
}

// Context menu
const handleContextMenu = ({ item, event }) => {
  if (item.id === 'liked' || item.is_liked) {
    openMenu('liked', { name: 'Понравившиеся', track_count: likedCount.value }, props.scope, event)
    return
  }
  openMenu('playlist', item, props.scope, event)
}

// Shuffle playlist
const shufflePlaylist = async (playlist) => {
  if (playlist.id === 'liked' || playlist.is_liked) {
    try {
      await playerStore.playShuffleAll('library')
    } catch (error) {
      console.error('Failed to shuffle liked tracks:', error)
    }
    return
  }
  try {
    await playerStore.playShuffleAll('playlist', playlist.id)
  } catch (error) {
    console.error('Failed to shuffle playlist:', error)
  }
}

// === Create modal (library scope) ===
const handleCreatePlaylist = () => {
  if (!authStore.hasChannel) {
    authStore.promptChannelSetup()
    return
  }
  showCreateModal.value = true
}

const closeCreateModal = () => {
  showCreateModal.value = false
  newPlaylistName.value = ''
}

const createPlaylist = async () => {
  if (!newPlaylistName.value.trim()) return
  try {
    const result = await libraryStore.createPlaylist(
      newPlaylistName.value.trim(),
      '',
      props.scope === 'global'
    )
    closeCreateModal()
    if (result?.id) {
      router.push(`/playlist/${result.id}`)
    }
  } catch (error) {
    console.error('Failed to create playlist:', error)
  }
}

watch(showCreateModal, (val) => {
  if (val) nextTick(() => createNameInput.value?.focus())
})

// === Manage modal (global scope) ===
const loadOwnPlaylists = async () => {
  try {
    const response = await api.get('/playlists/manage/all')
    ownPlaylists.value = response.data.items || response.data || []
  } catch (error) {
    console.error('Failed to load own playlists:', error)
  }
}

const closeManageModal = () => {
  showManageModal.value = false
}

const togglePlaylistStatus = async (playlist) => {
  try {
    const newStatus = !playlist.is_public
    await libraryStore.updatePlaylist(playlist.id, { is_public: newStatus })
    playlist.is_public = newStatus
    
    uiStore.toast.success('Сохранено', `Плейлист ${newStatus ? 'добавлен в коллекции' : 'удален из коллекций'}`)
  } catch (error) {
    console.error('Failed to toggle playlist status:', error)
    uiStore.toast.error('Ошибка', 'Не удалось обновить статус')
  }
}

watch(showManageModal, (val) => {
  if (val) loadOwnPlaylists()
})

// Load liked count for library scope
const loadLikedCount = async () => {
  if (props.scope !== 'library') return
  try {
    await libraryStore.fetchLikedTracks()
    likedCount.value = libraryStore.likedTracks?.length || 0
  } catch (e) {
    console.error('Failed to load liked count:', e)
  }
}

// Listen for playlist:changed events to auto-refresh the grid
const onPlaylistChanged = () => {
  loadLikedCount()
  virtualGridRef.value?.reset()
}

onMounted(() => {
  loadLikedCount()
  window.addEventListener('playlist:changed', onPlaylistChanged)
})

onActivated(() => {
  loadLikedCount()
  virtualGridRef.value?.reset()
})

onUnmounted(() => {
  window.removeEventListener('playlist:changed', onPlaylistChanged)
})

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
.library-playlists {
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

@media (max-width: 767px) {
  .toolbar-title {
    display: none;
  }
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

/* Create first button */
.create-first-btn {
  margin-top: 16px;
  background: var(--c-accent);
  color: #000;
  border: none;
  border-radius: 20px;
  padding: 12px 24px;
  font-weight: 600;
  cursor: pointer;
}

/* Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal, 1200);
  padding: 16px;
}

.modal {
  background: var(--c-bg-3);
  border-radius: 16px;
  padding: 24px;
  width: 100%;
  max-width: 360px;
}

.modal h2 {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 20px;
  color: var(--c-text-1);
}

.modal input {
  width: 100%;
  padding: 14px 16px;
  background: var(--c-bg-1);
  border: 1px solid var(--c-bg-4);
  border-radius: 10px;
  color: var(--c-text-1);
  font-size: 16px;
  margin-bottom: 20px;
}

.modal input::placeholder {
  color: var(--c-text-3);
}

.modal input:focus {
  outline: none;
  border-color: var(--c-accent);
}

.modal-actions {
  display: flex;
  gap: 12px;
}

.cancel-btn {
  flex: 1;
  padding: 12px;
  border-radius: 10px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--c-bg-4);
  background: transparent;
  color: var(--c-text-1);
}

.confirm-btn {
  flex: 1;
  padding: 12px;
  background: var(--c-accent);
  border: none;
  border-radius: 10px;
  color: #000;
  font-weight: 600;
  cursor: pointer;
}

.confirm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Manage modal */
.manage-modal {
  max-width: 480px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
}

.hint-text {
  font-size: 12px;
  color: var(--c-text-3, var(--c-text-3));
  margin-bottom: 16px;
}

.playlists-manage-list {
  flex: 1;
  overflow-y: auto;
  margin: 16px 0;
  max-height: 50vh;
}

.playlist-manage-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background: var(--c-bg-3, var(--c-bg-3));
  border-radius: 12px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: background 0.2s;
}

.playlist-manage-item:hover {
  background: var(--c-bg-4, var(--c-bg-4));
}

.playlist-manage-info {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.playlist-manage-cover {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--c-bg-1, var(--c-bg-1));
  flex-shrink: 0;
}

.playlist-manage-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.playlist-manage-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-text-3, var(--c-text-3));
}

.playlist-manage-text {
  flex: 1;
  min-width: 0;
}

.playlist-manage-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--c-text-1, var(--c-text-1));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.playlist-manage-count {
  font-size: 13px;
  color: var(--c-text-3, var(--c-text-3));
  margin-top: 2px;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--c-text-2, var(--c-text-2));
  font-size: 14px;
  margin-bottom: 12px;
  cursor: pointer;
  user-select: none;
}

.checkbox-label input[type="checkbox"] {
  appearance: none;
  -webkit-appearance: none;
  width: 44px;
  height: 24px;
  background: var(--c-bg-3, var(--c-bg-3));
  border-radius: 12px;
  position: relative;
  cursor: pointer;
  transition: background 0.2s;
}

.checkbox-label input[type="checkbox"]::before {
  content: '';
  position: absolute;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: white;
  top: 2px;
  left: 2px;
  transition: transform 0.2s;
}

.checkbox-label input[type="checkbox"]:checked {
  background: var(--c-accent);
}

.checkbox-label input[type="checkbox"]:checked::before {
  transform: translateX(20px);
}

.checkbox-label.compact {
  margin-bottom: 0;
  gap: 0;
}
</style>
