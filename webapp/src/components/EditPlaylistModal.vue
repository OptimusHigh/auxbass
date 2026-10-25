<template>
  <div v-if="show" class="modal-overlay" @click.self="$emit('close')">
    <div class="modal edit-playlist-modal">
      <!-- Header -->
      <div class="edit-header">
        <div class="edit-header-title">
          <h2>Редактирование</h2>
          <span v-if="isSystem" class="system-badge-pill" title="Системный плейлист Minecraft">
            <Lock :size="11" />
            <span>Системный</span>
          </span>
        </div>
        <button class="close-modal-btn" @click="$emit('close')"><X :size="20" /></button>
      </div>
      
      <!-- Playlist cover, name and settings -->
      <div class="edit-settings">
        <div class="cover-uploader">
          <div class="cover-preview-box" :class="{ 'is-locked-box': isSystem }">
            <img v-if="currentCoverUrl" :src="currentCoverUrl" alt="Cover" class="cover-preview-img" />
            <div v-else class="cover-placeholder-box">
              <ImageIcon :size="22" />
            </div>
            <div v-if="coverLoading" class="cover-spinner-overlay">
              <div class="search-spinner-inline"></div>
            </div>
            <div v-if="isSystem" class="cover-lock-watermark" title="Обложка защищена и формируется автоматически">
              <Lock :size="13" />
            </div>
          </div>
          <div v-if="isSystem" class="cover-system-badge" title="Обложка защищена">
            <span>Обложка</span>
          </div>
          <div v-else class="cover-action-buttons">
            <button type="button" class="cover-btn upload" @click="triggerFileInput" :disabled="coverLoading" title="Загрузить обложку">
              <Upload :size="13" />
              <span>{{ customCoverUrl ? 'Заменить' : 'Обложка' }}</span>
            </button>
            <button v-if="customCoverUrl" type="button" class="cover-btn remove" @click="removeCover" :disabled="coverLoading" title="Сбросить на коллаж">
              <Trash2 :size="13" />
            </button>
            <input
              ref="fileInputRef"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              class="hidden-cover-input"
              @change="handleCoverFileChange"
            />
          </div>
        </div>

        <div class="edit-details">
          <div class="edit-name-wrapper" :class="{ 'is-locked': isSystem }">
            <input
              v-model="name"
              type="text"
              placeholder="Название плейлиста"
              class="edit-name-input"
              :disabled="isSystem"
              :title="isSystem ? 'Имя системного плейлиста нельзя изменить' : ''"
            />
            <span v-if="isSystem" class="name-lock-badge" title="Имя защищено от изменений">
              <Lock :size="14" />
            </span>
          </div>
          <div class="edit-options">
            <label class="checkbox-label compact">
              <input type="checkbox" v-model="isPublic" />
              <span>Публичный</span>
            </label>
            <span v-if="isSystem" class="system-field-hint">
              Аватар и имя зафиксированы
            </span>
          </div>
        </div>
      </div>
      
      <!-- Search input -->
      <div class="search-input-wrapper">
        <SearchBar
          v-model="searchQuery"
          placeholder="Поиск треков для добавления..."
          :loading="searching"
          @input="debouncedSearch"
          @clear="clearSearch"
        />
      </div>
      
      <!-- Content area -->
      <div class="edit-content" ref="scrollContentRef">
        <!-- Search results -->
        <div v-if="searchQuery && librarySearchItems.length > 0" class="search-results">
          <!-- Section: My Library -->
          <div class="section-label">Моя библиотека ({{ librarySearchTotal }})</div>
          <TrackSearchItem
            v-for="track in librarySearchItems"
            :key="'lib-' + track.id"
            :track="track"
            :isInPlaylist="isTrackInPlaylist(track.id)"
            :isAdding="addingTrackId === track.id"
            :isRemoving="removingTrackId === track.id"
            @add="addTrack"
            @remove="removeTrack"
          />
          <!-- Infinite scroll trigger for library search -->
          <div ref="searchLoadTrigger" v-if="hasMoreSearchResults" class="load-trigger"></div>
          <TrackSkeleton v-for="i in searchSkeletonCount" :key="'search-skel-' + i" />
          <!-- Spacer for full scroll height of remaining library results -->
          <div
            v-if="librarySearchTotal - librarySearchItems.length > 0"
            class="scroll-spacer"
            :style="{ height: (librarySearchTotal - librarySearchItems.length) * 54 + 'px' }"
          ></div>
        </div>
        
        <div v-else-if="searchQuery && !searching" class="no-results search-empty-modal">
          <p>В медиатеке ничего не найдено по запросу «{{ searchQuery }}»</p>
          <button type="button" class="btn-global-search-modal" @click="goToGlobalSearch">
            <Search :size="14" />
            <span>Искать в глобальном поиске</span>
          </button>
        </div>
        
        <!-- Current playlist tracks -->
        <div v-else class="current-tracks">
          <div class="section-label">Треки в плейлисте ({{ tracks.length }})</div>
          <div v-if="tracks.length" class="tracks-editor">
            <EditableTrackItem
              v-for="(track, index) in tracks"
              :key="'edit-' + track.id"
              :track="track"
              :index="index"
              :isDragging="dragIndex === index"
              :isDragOver="dragOverIndex === index"
              @dragstart="handleDragStart($event, index)"
              @dragend="handleDragEnd"
              @dragover="handleDragOver($event, index)"
              @drop="onDrop($event, index)"
              @handleTouchStart="handleTouchStart($event, index)"
              @handleTouchMove="handleTouchMove"
              @handleTouchEnd="onTouchEnd"
              @remove="removeTrackFromList(track, index)"
            />
          </div>
          <div v-else class="empty-playlist-hint">
            <span><Music :size="32" /></span>
            <p>Плейлист пуст</p>
            <p class="hint">Найдите треки через поиск выше</p>
          </div>
        </div>
      </div>
      
      <!-- Footer -->
      <div class="edit-footer" :class="{ 'is-system-footer': isSystem }">
        <div v-if="isSystem" class="system-footer-note">
          <Lock :size="13" />
          <span>Системный плейлист</span>
        </div>
        <button 
          v-else
          class="delete-playlist-btn" 
          :class="{ 'confirm-state': deleteConfirmPending }"
          @click="handleDeleteClick"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
          </svg>
          <span class="delete-text">{{ deleteConfirmPending ? 'Точно удалить?' : 'Удалить плейлист' }}</span>
        </button>
        <button 
          class="save-btn" 
          @click="save"
          :disabled="(!name.trim() && !isSystem) || saving"
        >
          <span v-if="saving">Сохранение...</span>
          <span v-else>Сохранить</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useLibraryStore } from '@/stores/library'
import { useDragReorder } from '@/composables/useDragReorder'
import { useVirtualScroll } from '@/composables/useVirtualScroll'
import { playlistsApi } from '@/api/client'
import api from '@/api/client'
import TrackSearchItem from './TrackSearchItem.vue'
import EditableTrackItem from './EditableTrackItem.vue'
import TrackSkeleton from './TrackSkeleton.vue'
import SearchBar from './ui/SearchBar.vue'
import { X, Music, Upload, Trash2, Search, Image as ImageIcon, Lock } from 'lucide-vue-next'
import { getCoverUrl, CoverSize } from '@/utils'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  show: Boolean,
  playlist: Object
})

const emit = defineEmits(['close', 'save', 'delete', 'update:tracks'])

const playerStore = usePlayerStore()
const libraryStore = useLibraryStore()
const authStore = useAuthStore()
const uiStore = useUIStore()

// Form state
const name = ref('')
const isPublic = ref(false)
const tracks = ref([])
const saving = ref(false)

const isSystem = computed(() => Boolean(props.playlist?.is_system || props.playlist?.system_type === 'minecraft' || props.playlist?.system_type === 'minecraft_main'))

const scrollContentRef = ref(null)

// Delete confirmation (double-click pattern)
const deleteConfirmPending = ref(false)
let deleteConfirmTimer = null

const handleDeleteClick = () => {
  if (deleteConfirmPending.value) {
    // Second click — confirm deletion
    clearTimeout(deleteConfirmTimer)
    deleteConfirmPending.value = false
    emit('delete')
  } else {
    // First click — enter confirm state
    deleteConfirmPending.value = true
    deleteConfirmTimer = setTimeout(() => {
      deleteConfirmPending.value = false
    }, 3000)
  }
}

const router = useRouter()
const searchQuery = ref('')

// Infinite scroll for library search results
const {
  items: librarySearchItems,
  total: librarySearchTotal,
  hasMore: hasMoreSearchResults,
  loading: librarySearchLoading,
  loadTriggerRef: searchLoadTrigger,
  loadingSkeletonCount: searchSkeletonCount,
  reset: resetLibrarySearch,
  clear: clearLibrarySearch,
} = useVirtualScroll({
  fetchFn: async ({ offset, limit }) => {
    const page = Math.floor(offset / limit) + 1
    const res = await api.get('/library', {
      params: { search: searchQuery.value, per_page: limit, page }
    })
    return { items: res.data.items || [], total: res.data.total || 0 }
  },
  limit: 30,
  immediate: false,
  scrollContainer: scrollContentRef,
  skeletonCount: 4
})

// Computed search state
const searching = computed(() => librarySearchLoading.value)

// Debounced search: library via virtual scroll
let searchDebounceTimer = null
const debouncedSearch = () => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(async () => {
    const query = searchQuery.value.trim()
    if (!query) {
      clearLibrarySearch()
      return
    }
    await resetLibrarySearch()
  }, 300)
}

const clearSearch = () => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchQuery.value = ''
  clearLibrarySearch()
}

const goToGlobalSearch = () => {
  const q = searchQuery.value.trim()
  emit('close')
  if (q) {
    router.push({ path: '/search', query: { q } })
  }
}

const addingTrackId = ref(null)
const removingTrackId = ref(null)

// Reorder & Touch handlers
const onTracksReorder = async (reordered) => {
  tracks.value = reordered
  emit('update:tracks', reordered)
  try {
    await playlistsApi.reorder(props.playlist.id, reordered.map(t => t.id))
  } catch (error) {
    console.error('Failed to reorder tracks:', error)
    uiStore.toast.error('Ошибка', 'Не удалось сохранить порядок треков')
  }
}

const {
  dragIndex,
  dragOverIndex,
  handleDragStart,
  handleDragEnd,
  handleDragOver,
  handleDrop,
  handleTouchStart,
  handleTouchMove,
  handleTouchEnd
} = useDragReorder()

const onDrop = async (event, toIndex) => {
  const reordered = await handleDrop(event, toIndex, tracks.value)
  if (reordered) {
    await onTracksReorder(reordered)
  }
}

const onTouchEnd = async () => {
  const reordered = await handleTouchEnd(tracks.value)
  if (reordered) {
    await onTracksReorder(reordered)
  }
}

// Cover management
const customCoverUrl = ref(null)
const coverLoading = ref(false)
const fileInputRef = ref(null)

const currentCoverUrl = computed(() => {
  if (customCoverUrl.value) {
    return getCoverUrl(customCoverUrl.value, CoverSize.MEDIUM)
  }
  if (props.playlist?.custom_cover_url) {
    return getCoverUrl(props.playlist.custom_cover_url, CoverSize.MEDIUM)
  }
  if (props.playlist?.cover_url) {
    return getCoverUrl(props.playlist.cover_url, CoverSize.MEDIUM)
  }
  if (props.playlist?.covers?.length) {
    return getCoverUrl(props.playlist.covers[0], CoverSize.MEDIUM)
  }
  return null
})

const triggerFileInput = () => {
  if (isSystem.value) return
  fileInputRef.value?.click()
}

const handleCoverFileChange = async (e) => {
  if (isSystem.value) return
  const file = e.target.files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) {
    uiStore.toast.error('Ошибка', 'Выберите файл изображения (JPG, PNG, WebP)')
    return
  }
  coverLoading.value = true
  try {
    const res = await playlistsApi.uploadCover(props.playlist.id, file)
    customCoverUrl.value = res.data.custom_cover_url
    await libraryStore.notifyPlaylistChange(props.playlist.id)
    uiStore.toast.success('Успешно', 'Обложка плейлиста обновлена')
  } catch (error) {
    console.error('Failed to upload cover:', error)
    uiStore.toast.error('Ошибка', 'Не удалось загрузить обложку')
  } finally {
    coverLoading.value = false
    if (fileInputRef.value) fileInputRef.value.value = ''
  }
}

const removeCover = async () => {
  if (isSystem.value) return
  coverLoading.value = true
  try {
    await playlistsApi.deleteCover(props.playlist.id)
    customCoverUrl.value = null
    await libraryStore.notifyPlaylistChange(props.playlist.id)
    uiStore.toast.success('Успешно', 'Обложка сброшена на коллаж треков')
  } catch (error) {
    console.error('Failed to delete cover:', error)
    uiStore.toast.error('Ошибка', 'Не удалось удалить обложку')
  } finally {
    coverLoading.value = false
  }
}

// Watch for playlist changes
watch(() => props.playlist, (pl) => {
  if (pl && !props.show) {
    name.value = pl.name || ''
    isPublic.value = pl.is_public || false
    customCoverUrl.value = pl.custom_cover_url || null
    tracks.value = [...(pl.tracks || [])]
  }
}, { immediate: true })

watch(() => props.show, (show) => {
  if (show && props.playlist) {
    name.value = props.playlist.name || ''
    isPublic.value = props.playlist.is_public || false
    customCoverUrl.value = props.playlist.custom_cover_url || null
    tracks.value = [...(props.playlist.tracks || [])]
  }
  if (!show) {
    clearSearch()
    clearLibrarySearch()
    deleteConfirmPending.value = false
    if (deleteConfirmTimer) clearTimeout(deleteConfirmTimer)
  }
})

// Track management
const isTrackInPlaylist = (trackId) => tracks.value.some(t => t.id === trackId)

const addTrack = async (track) => {
  if (addingTrackId.value) return
  addingTrackId.value = track.id
  try {
    await playlistsApi.addTrack(props.playlist.id, track.id)
    tracks.value.push(track)
    emit('update:tracks', tracks.value)
    await libraryStore.notifyPlaylistChange(props.playlist.id)
    uiStore.toast.success('Добавлено', `Трек "${track.title || 'Без названия'}" добавлен`)
  } catch (error) {
    console.error('Failed to add track:', error)
    const msg = error.response?.data?.detail || 'Не удалось добавить трек'
    uiStore.toast.error('Ошибка', msg)
  } finally {
    addingTrackId.value = null
  }
}

const removeTrack = async (track) => {
  if (removingTrackId.value) return
  removingTrackId.value = track.id
  try {
    await playlistsApi.removeTrack(props.playlist.id, track.id)
    tracks.value = tracks.value.filter(t => t.id !== track.id)
    emit('update:tracks', tracks.value)
    await libraryStore.notifyPlaylistChange(props.playlist.id)
    uiStore.toast.success('Удалено', 'Трек убран из плейлиста')
  } catch (error) {
    console.error('Failed to remove track:', error)
    uiStore.toast.error('Ошибка', 'Не удалось удалить трек')
  } finally {
    removingTrackId.value = null
  }
}

const removeTrackFromList = async (track, index) => {
  try {
    await playlistsApi.removeTrack(props.playlist.id, track.id)
    const idx = tracks.value.findIndex(t => t.id === track.id)
    if (idx !== -1) tracks.value.splice(idx, 1)
    emit('update:tracks', tracks.value)
    await libraryStore.notifyPlaylistChange(props.playlist.id)
    uiStore.toast.success('Удалено', 'Трек убран из плейлиста')
  } catch (error) {
    console.error('Failed to remove track:', error)
    uiStore.toast.error('Ошибка', 'Не удалось удалить трек')
  }
}

// Save
const save = async () => {
  const targetName = isSystem.value ? (props.playlist?.name || name.value.trim()) : name.value.trim()
  if (!targetName || saving.value) return
  saving.value = true
  try {
    const payload = {
      is_public: isPublic.value
    }
    if (!isSystem.value) {
      payload.name = targetName
    } else {
      payload.name = props.playlist?.name || targetName
    }

    const response = await libraryStore.updatePlaylist(props.playlist.id, payload)

    if (tracks.value.length > 0) {
      await playlistsApi.reorder(props.playlist.id, tracks.value.map(t => t.id))
    }
    
    emit('save', { 
      name: payload.name, 
      isPublic: isPublic.value, 
      covers: response?.covers || [],
      customCoverUrl: customCoverUrl.value,
      tracks: [...tracks.value]
    })
  } catch (error) {
    console.error('Failed to save:', error)
    uiStore.toast.error('Ошибка', 'Не удалось сохранить плейлист')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal, 1200);
  padding: 16px;
}

.edit-playlist-modal {
  background: var(--c-bg-3);
  border-radius: 16px;
  height: 85vh;
  max-height: 700px;
  min-height: 400px;
  width: calc(100% - 32px);
  max-width: 480px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Mobile fullscreen mode */
@media (max-width: 480px) {
  .modal-overlay {
    padding: 0;
  }
  
  .edit-playlist-modal {
    width: 100%;
    height: 100%;
    max-height: none;
    min-height: 100%;
    border-radius: 0;
  }
}

/* Small height screens - compact mode */
@media (max-height: 600px) {
  .modal-overlay {
    padding: 0;
  }
  
  .edit-playlist-modal {
    height: 100%;
    max-height: none;
    min-height: 100%;
    border-radius: 0;
  }
}

@media (min-width: 600px) {
  .edit-playlist-modal {
    width: 85vw;
    max-width: 520px;
  }
}

@media (min-width: 900px) {
  .edit-playlist-modal {
    width: 50vw;
    max-width: 760px;
    height: 80vh;
    max-height: 750px;
  }
}

.edit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 20px 16px;
  border-bottom: 1px solid var(--c-bg-4);
}

.edit-header-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.system-badge-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 999px;
  background: rgba(29, 185, 84, 0.12);
  color: var(--c-accent);
  border: 1px solid rgba(29, 185, 84, 0.25);
}

.close-modal-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--c-bg-4);
  border: none;
  color: var(--c-text-2);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.edit-settings {
  padding: 16px 20px;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 16px;
  background: var(--c-bg-2);
  border-bottom: 1px solid var(--c-bg-4);
}

.cover-uploader {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.cover-preview-box {
  width: 64px;
  height: 64px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--c-bg-3);
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--c-bg-4);
}

.cover-preview-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-placeholder-box {
  color: var(--c-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
}

.cover-spinner-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
}

.cover-action-buttons {
  display: flex;
  align-items: center;
  gap: 4px;
}

.cover-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 6px;
  border: 1px solid var(--c-bg-4);
  background: var(--c-bg-3);
  color: var(--c-text-1);
  cursor: pointer;
  transition: background 0.15s;
}

.cover-btn:hover:not(:disabled) {
  background: var(--c-bg-4);
}

.cover-btn.remove {
  color: #ff5555;
  padding: 4px 6px;
}

.hidden-cover-input {
  display: none;
}

.cover-preview-box.is-locked-box {
  border-color: rgba(255, 255, 255, 0.08);
}

.cover-lock-watermark {
  position: absolute;
  bottom: 3px;
  right: 3px;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  background: rgba(13, 13, 13, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-accent);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.cover-system-badge {
  font-size: 11px;
  color: var(--c-text-3);
  font-weight: 500;
  text-align: center;
}

.edit-details {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.edit-name-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
}

.edit-name-input {
  width: 100%;
  padding: 10px 14px;
  background: var(--c-bg-1);
  border: 1px solid var(--c-bg-4);
  border-radius: 8px;
  color: var(--c-text-1);
  font-size: 15px;
  transition: all 0.2s ease;
}

.edit-name-wrapper.is-locked .edit-name-input {
  padding-right: 36px;
  background: var(--c-bg-1);
  opacity: 0.85;
  border-color: rgba(255, 255, 255, 0.04);
  color: var(--c-text-2);
  cursor: not-allowed;
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0,0,0,0.5)), inset -1px -1px 2px var(--sh-inset-light, rgba(255,255,255,0.03));
}

.name-lock-badge {
  position: absolute;
  right: 12px;
  color: var(--c-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.system-field-hint {
  font-size: 12px;
  color: var(--c-text-3);
  margin-left: auto;
}

.edit-name-input:focus {
  outline: none;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--c-text-2);
  font-size: 14px;
  cursor: pointer;
}

.checkbox-label.compact input[type="checkbox"] {
  appearance: none;
  width: 36px;
  height: 20px;
  background: var(--c-bg-4);
  border-radius: 10px;
  position: relative;
  cursor: pointer;
}

.checkbox-label.compact input[type="checkbox"]::before {
  content: '';
  position: absolute;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: white;
  top: 2px;
  left: 2px;
  transition: transform 0.2s;
}

.checkbox-label.compact input[type="checkbox"]:checked {
  background: var(--c-accent);
}

.checkbox-label.compact input[type="checkbox"]:checked::before {
  transform: translateX(16px);
}

.search-input-wrapper {
  padding: 16px 20px;
}

.search-input-wrapper :deep(.search-bar) {
  margin-bottom: 0;
}

.edit-content {
  flex: 1;
  overflow-y: auto;
  padding: 0 20px;
}

.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-text-3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
  padding-top: 8px;
}

.search-results, .current-tracks {
  padding-bottom: 16px;
}

.no-results {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  color: var(--c-text-3);
}

.search-empty-modal {
  flex-direction: column;
  gap: 12px;
  text-align: center;
  font-size: 13px;
}

.btn-global-search-modal {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-radius: 18px;
  background: rgba(29, 185, 84, 0.15);
  border: 1px solid rgba(29, 185, 84, 0.35);
  color: var(--c-accent, #1db954);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-global-search-modal:hover {
  background: var(--c-accent, #1db954);
  color: #000;
  transform: translateY(-1px);
}

.empty-playlist-hint {
  text-align: center;
  padding: 32px 16px;
  color: var(--c-text-3);
}

.empty-playlist-hint span {
  font-size: 36px;
  display: block;
  margin-bottom: 12px;
}

.empty-playlist-hint p { margin: 0; }
.empty-playlist-hint .hint { font-size: 13px; margin-top: 4px; }

.edit-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-top: 1px solid var(--c-bg-4);
  background: var(--c-bg-3);
}

.system-footer-note {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--c-text-3);
}

.delete-playlist-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  background: transparent;
  border: 1px solid var(--c-text-3);
  border-radius: 8px;
  color: var(--c-text-2);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.delete-playlist-btn:hover {
  border-color: var(--c-error);
  color: var(--c-error);
}

.delete-playlist-btn.confirm-state {
  border-color: var(--c-error);
  color: #fff;
  background: var(--c-error);
  animation: pulse-danger 0.3s ease;
}

@keyframes pulse-danger {
  0% { transform: scale(1); }
  50% { transform: scale(1.05); }
  100% { transform: scale(1); }
}

.save-btn {
  padding: 12px 24px;
  background: var(--c-accent);
  border: none;
  border-radius: 8px;
  color: #000;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  margin-left: auto;
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* @keyframes spin — defined in design-system.css */

/* Mobile compact styles */
@media (max-width: 480px) {
  .edit-header {
    padding: 12px 12px 10px;
  }
  
  .edit-header h2 {
    font-size: 16px;
  }
  
  .edit-settings {
    padding: 12px;
    margin: 0 12px;
    gap: 10px;
  }
  
  .search-input-wrapper {
    padding: 10px 12px;
  }
  
  .edit-content {
    padding: 0 12px;
  }
  
  .edit-footer {
    padding: 12px;
    gap: 8px;
  }
  
  .delete-playlist-btn {
    padding: 8px 10px;
    font-size: 12px;
  }
  
  .save-btn {
    padding: 10px 18px;
    font-size: 13px;
  }
}

/* Very small screens (old phones) */
@media (max-width: 360px) {
  .edit-settings {
    flex-wrap: wrap;
    padding: 10px;
    margin: 0 8px;
  }
  
  .edit-name-input {
    order: 2;
    width: 100%;
    flex: none;
  }
  
  .edit-options {
    order: 3;
    margin-left: auto;
  }
  
  .edit-header {
    padding: 10px 8px;
  }
  
  .edit-content {
    padding: 0 8px;
  }
  
  .edit-footer {
    padding: 10px 8px;
  }
  
  .delete-playlist-btn .delete-text {
    display: none;
  }
}
</style>
