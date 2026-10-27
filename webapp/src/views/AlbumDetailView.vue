<template>
  <div class="album-detail-view" v-if="album">
    <!-- Unified Hero Header -->
    <div class="hero-header">
      <div class="hero-cover album-cover">
        <img v-if="album.cover_url" :src="getCoverUrl(album.cover_url, CoverSize.LARGE)" :alt="album.name" />
        <div v-else class="cover-placeholder"><Disc3 :size="48" /></div>
      </div>
      <div class="hero-info">
        <h1 class="hero-title">{{ album.name }}</h1>
        <!-- Artists (clickable, split into separate links) -->
        <div v-if="parsedAlbumArtists.length > 0" class="artists-container">
          <Users :size="16" class="artists-icon" />
          <span class="artists-links">
            <template v-for="(artist, index) in parsedAlbumArtists" :key="artist">
              <button 
                class="artist-link-inline"
                @click="goToArtistByName(artist)"
                @contextmenu.prevent="openMenu('artist', { name: artist }, 'album-header', $event)"
                v-longpress="(e) => openMenu('artist', { name: artist }, 'album-header', e)"
              >{{ artist }}</button>
              <span v-if="index < parsedAlbumArtists.length - 1" class="artist-separator">, </span>
            </template>
          </span>
        </div>
        <p v-else class="artist" @click="goToArtist" v-longpress="(e) => openMenu('artist', { name: album.artist }, 'album-header', e)" @contextmenu.prevent="openMenu('artist', { name: album.artist }, 'album-header', $event)">{{ album.artist }}</p>
        <p class="hero-meta">
          <span v-if="album.release_date">{{ formatYear(album.release_date) }} • </span>
          <span v-if="album.total_tracks">
            {{ Math.min(album.track_count, album.total_tracks) }}/{{ album.total_tracks }} треков
          </span>
          <span v-else>{{ album.track_count }} треков</span>
        </p>
        <!-- Album tags from enrichment -->
        <TagChips
          v-if="album.tags?.length"
          :tags="album.tags"
          :max="4"
          size="sm"
          :clickable="true"
          class="album-tags"
          @tagClick="handleTagClick"
        />
      </div>
    </div>

    <!-- Unified Actions -->
    <div class="hero-actions">
      <div class="action-buttons">
        <button 
          class="action-btn play-btn" 
          @click="togglePlay" 
          :title="playButtonTitle"
        >
          <Pause v-if="isPlaying" :size="20" fill="currentColor" />
          <Play v-else :size="20" fill="currentColor" />
        </button>
        <button class="action-btn shuffle-btn" @click="shufflePlay" :disabled="isShuffling" title="Перемешать">
          <Shuffle :size="18" />
        </button>
        <button class="action-btn share-btn" @click="handleShareAlbum" title="Поделиться">
          <Share2 :size="18" />
        </button>
      </div>
    </div>

    <!-- Track list - show full_tracklist if available, otherwise just user's tracks -->
    <div class="track-list">
      <!-- Full tracklist mode: shows all album tracks with availability status -->
      <template v-if="album.full_tracklist && album.full_tracklist.length">
        <div
          v-for="item in album.full_tracklist"
          :key="item.track_number"
          class="tracklist-item"
          :class="{ 
            'missing': !item.track, 
            'has-track': item.track,
            'not-in-library': item.track && !item.in_library,
            'playing': playerStore.currentTrack?.id === item.track_id
          }"
          @click="handleTracklistItemClick(item)"
          @contextmenu.prevent="item.track && openMenu('track', item.track, 'album', $event)"
          v-longpress="(e) => item.track && openMenu('track', item.track, 'album', e)"
        >
          <span class="track-number">{{ item.track_number }}</span>
          
          <div class="track-info">
            <span class="track-title" :class="{ 'missing-title': !item.track }">
              {{ item.title }}
            </span>
            <span 
              v-if="item.artist && item.artist !== album.artist" 
              class="track-artist clickable"
              @click.stop="goToArtistByName(item.artist)"
              title="Перейти к исполнителю"
            >
              {{ item.artist }}
            </span>
          </div>
          
          <span class="track-duration">{{ formatDuration(item.track?.duration || item.duration) }}</span>
          
          <!-- Add to library button for tracks not in user's library -->
          <button
            v-if="item.track && !item.in_library"
            class="add-library-btn"
            @click.stop="handleAddToLibraryFromTracklist(item)"
            title="Добавить в библиотеку"
          >
            <Plus :size="14" />
          </button>
          <!-- In library indicator -->
          <span v-else-if="item.track && item.in_library" class="in-library-indicator">
            <Check :size="14" />
          </span>
          <!-- Missing track button - track not in database -->
          <button
            v-else-if="!item.track"
            class="add-btn"
            @click.stop="handleMissingTrack(item)"
            title="Найти трек"
          >
            <Plus :size="14" />
          </button>
          <!-- Playing indicator -->
          <span v-if="playerStore.currentTrack?.id === item.track_id" class="playing-indicator">
            <Music :size="14" />
          </span>
        </div>
      </template>
      
      <!-- Simple mode: just user's tracks (fallback when no full_tracklist) -->
      <template v-else>
        <TrackItem
          v-for="(track, index) in album.tracks"
          :key="track.id"
          :track="track"
          :trackNumber="index + 1"
          :isPlaying="playerStore.currentTrack?.id === track.id"
          :isLiked="track.is_liked"
          :hideCover="true"
          :showAddToLibrary="isGlobal"
          :inLibrary="track.in_library"
          @click="playTrack(track, index)"
          @like="handleLikeTrack(track)"
          @addToLibrary="handleAddToLibrary(track)"
          @menu="(e) => openMenu('track', track, 'album', e)"
          @download="handleDirectDownload(track)"
          @hdNotice="handleHdNotice"
        />
      </template>
    </div>
    
    <!-- Missing track modal -->
    <div v-if="showMissingModal" class="modal-overlay" @click="closeMissingModal">
      <div class="modal-content" @click.stop>
        <h3>{{ missingTrackItem?.title }}</h3>
        
        <div v-if="searchingTrack" class="modal-loading">
          <div class="spinner"></div>
          <p>Ищем трек...</p>
        </div>
        
        <div v-else-if="foundTrack" class="modal-found">
          <p class="success-message">✅ {{ searchResult?.message }}</p>
          <div class="found-track-info">
            <strong>{{ foundTrack.title }}</strong>
            <span>{{ foundTrack.artist }}</span>
          </div>
          <button
            v-if="!searchResult?.in_library"
            class="primary-btn"
            @click="addFoundTrack"
            :disabled="addingTrack"
          >
            {{ addingTrack ? 'Добавляем...' : 'Добавить в библиотеку' }}
          </button>
          <button v-else class="secondary-btn" @click="closeMissingModal">
            Уже в библиотеке
          </button>
        </div>
        
        <div v-else class="modal-not-found">
          <p class="info-message">{{ searchResult?.message }}</p>
          <p class="hint">Скинь этот трек боту в Telegram, и он появится в библиотеке.</p>
        </div>
        
        <button class="close-btn" @click="closeMissingModal"><X :size="20" /></button>
      </div>
    </div>
  </div>

  <div v-else-if="loading" class="loading">
    <div class="spinner"></div>
  </div>

  <div v-else class="album-not-found">
    <div class="not-found-content">
      <Disc3 :size="48" class="not-found-icon" />
      <h2>Альбом не найден</h2>
      <p>Возможно, альбом был удалён или ссылка устарела</p>
      <button class="primary-btn" @click="router.push('/library?tab=albums')">
        Перейти в медиатеку
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useLibraryStore } from '@/stores/library'
import { useUIStore } from '@/stores/ui'
import { useContextMenu } from '@/composables/useContextMenu'
import { useTrackActions, usePlaybackActions, useTrackSync } from '@/composables'
import TrackItem from '@/components/TrackItem.vue'
import TagChips from '@/components/TagChips.vue'
import api from '@/api/client'
import { Disc3, Check, Music, X, Play, Pause, Shuffle, Plus, Users, Share2 } from 'lucide-vue-next'
import { splitArtists, getCoverUrl, CoverSize, formatDuration } from '@/utils/formatters'
import { useShare } from '@/composables/useShare'

// Universal context menu
const { openMenu } = useContextMenu()
const { openShare } = useShare()

const route = useRoute()
const router = useRouter()
const playerStore = usePlayerStore()
const libraryStore = useLibraryStore()
const uiStore = useUIStore()

// Unified track actions
const { handleDirectDownload, handleHdNotice, handleLikeTrack, handleAddToLibrary } = useTrackActions()

const handleShareAlbum = () => {
  if (!album.value) return
  openShare({
    type: 'album',
    id: album.value.id,
    title: album.value.title || album.value.name,
    subtitle: album.value.artist || 'Альбом',
    coverUrl: album.value.cover_url || '',
    text: `Послушай альбом «${album.value.title || album.value.name}» в TG Player!`,
  })
}

const album = ref(null)
const loading = ref(true)

// Sync album tracks with global track events
useTrackSync(() => album.value?.tracks)
// Also sync tracks inside full_tracklist entries
useTrackSync(() => {
  const fl = album.value?.full_tracklist
  if (!fl) return null
  return fl.filter(item => item.track).map(item => item.track)
})

// Scope from query
const scope = computed(() => route.query.scope || 'library')
const notFound = ref(false)
const activeScope = ref(scope.value)
const isGlobal = computed(() => activeScope.value === 'global')

// Sync activeScope with route query scope
watch(scope, (newScope) => {
  activeScope.value = newScope
})

// Parse album artists into separate names
const parsedAlbumArtists = computed(() => {
  if (!album.value?.artist) return []
  return splitArtists(album.value.artist)
})

// Missing track modal state
const showMissingModal = ref(false)
const missingTrackItem = ref(null)
const searchingTrack = ref(false)
const searchResult = ref(null)
const foundTrack = ref(null)
const addingTrack = ref(false)

// Get playable tracks - all tracks that have a track object (exist in database)
const playableTracks = computed(() => {
  if (album.value?.full_tracklist) {
    // All tracks with track object are playable (both library and global mode)
    return album.value.full_tracklist
      .filter(item => item.track)
      .map(item => item.track)
  }
  return album.value?.tracks || []
})

const loadAlbum = async () => {
  loading.value = true
  notFound.value = false
  try {
    let currentScope = activeScope.value
    let response
    try {
      response = await api.get(`/albums/${route.params.id}`, { params: { scope: currentScope } })
      // If album returned with 0 tracks in library scope, attempt global to load full tracklist
      if (currentScope === 'library' && (!response.data?.tracks?.length && !response.data?.full_tracklist)) {
        const globalRes = await api.get(`/albums/${route.params.id}`, { params: { scope: 'global' } }).catch(() => null)
        if (globalRes?.data?.tracks?.length || globalRes?.data?.full_tracklist) {
          response = globalRes
          activeScope.value = 'global'
        }
      }
    } catch (err) {
      if (err.response?.status === 404 && currentScope === 'library') {
        response = await api.get(`/albums/${route.params.id}`, { params: { scope: 'global' } })
        activeScope.value = 'global'
      } else {
        throw err
      }
    }
    album.value = response.data
  } catch (err) {
    console.error('Failed to load album:', err)
    notFound.value = true
    album.value = null
  } finally {
    loading.value = false
  }
}

// Unified playback actions - use shufflePlayFull for lazy loading all album tracks
const { playAll, togglePlay, isPlaying, isCurrentContext, playButtonTitle, shufflePlayFull, isShuffling, playTrack } = usePlaybackActions(
  playableTracks,
  () => album.value ? { type: 'album', id: album.value.id, name: album.value.name } : null
)

// Shuffle play handler using lazy loading
const shufflePlay = () => {
  if (album.value?.id) {
    shufflePlayFull('album', album.value.id)
  }
}

const playTrackItem = (item) => {
  if (item.track) {
    const context = album.value ? { type: 'album', id: album.value.id, name: album.value.name } : null
    playerStore.playTrack(item.track, playableTracks.value, context)
  }
}

// Handle click on tracklist item
const handleTracklistItemClick = (item) => {
  if (item.track) {
    // Track exists in database - play it (works for both library and global mode)
    playTrackItem(item)
  } else {
    // Track not in database - show search modal to help user find/add it
    handleMissingTrack(item)
  }
}

// Add track to library from tracklist (global mode)
const handleAddToLibraryFromTracklist = async (item) => {
  if (!item.track_id) return
  const success = await libraryStore.addToLibrary(item.track_id)
  if (success) {
    item.in_library = true
  }
}

// Handle clicking on missing track
const handleMissingTrack = async (item) => {
  missingTrackItem.value = item
  showMissingModal.value = true
  searchingTrack.value = true
  searchResult.value = null
  foundTrack.value = null
  
  try {
    const response = await api.post(`/albums/${album.value.id}/find-track`, null, {
      params: {
        title: item.title,
        artist: item.artist || album.value.artist
      }
    })
    searchResult.value = response.data
    if (response.data.found && response.data.track) {
      foundTrack.value = response.data.track
    }
  } catch (error) {
    searchResult.value = {
      found: false,
      message: 'Ошибка поиска. Попробуй скинуть трек боту.'
    }
  } finally {
    searchingTrack.value = false
  }
}

const addFoundTrack = async () => {
  if (!searchResult.value?.track_id) return
  
  addingTrack.value = true
  try {
    await api.post(`/albums/${album.value.id}/add-track/${searchResult.value.track_id}`)
    // Reload album to reflect changes
    await loadAlbum()
    closeMissingModal()
  } catch (error) {
    console.error('Failed to add track:', error)
  } finally {
    addingTrack.value = false
  }
}

const closeMissingModal = () => {
  showMissingModal.value = false
  missingTrackItem.value = null
  searchResult.value = null
  foundTrack.value = null
}

const goToArtist = () => {
  const query = isGlobal.value ? { scope: 'global' } : {}
  router.push({ path: `/artist/${encodeURIComponent(album.value.artist)}`, query })
}

// Navigate to specific artist by name
const goToArtistByName = (artistName) => {
  if (artistName) {
    const query = isGlobal.value ? { scope: 'global' } : {}
    router.push({ path: `/artist/${encodeURIComponent(artistName)}`, query })
  }
}

// Handle tag click: navigate to search
const handleTagClick = (tag) => {
  if (!tag) return
  const cleanTag = tag.replace(/^#/, '')
  router.push({ path: '/search', query: { tag: cleanTag } })
}


const formatYear = (date) => {
  if (!date) return ''
  return new Date(date).getFullYear()
}

const onTrackAdded = () => {
  loadAlbum()
}

onMounted(() => {
  loadAlbum()
  window.addEventListener('track:added:library', onTrackAdded)
})

onUnmounted(() => {
  window.removeEventListener('track:added:library', onTrackAdded)
})

// Reload when route params change (for sidebar navigation)
watch(
  () => route.params.id,
  (newId, oldId) => {
    if (newId && newId !== oldId) {
      loadAlbum()
    }
  }
)
</script>

<style scoped>
.album-detail-view {
  padding: 16px;
}

.artist {
  color: var(--c-text-2);
  margin: 0 0 4px 0;
  cursor: pointer;
}

.artist:hover {
  text-decoration: underline;
}

/* Artists Container - for split artists */
.artists-container {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 4px 0;
}

.artists-icon {
  flex-shrink: 0;
  opacity: 0.7;
  color: var(--c-text-2);
}

.artists-links {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0;
}

.artist-link-inline {
  background: none;
  border: none;
  color: var(--c-text-2);
  font-size: inherit;
  cursor: pointer;
  padding: 0;
  transition: color 0.2s ease;
}

.artist-link-inline:hover {
  color: var(--c-accent);
  text-decoration: underline;
}

.artist-separator {
  color: var(--c-text-3);
}

.album-tags {
  margin-top: 8px;
}

.play-btn svg.lucide-play {
  margin-left: 2px;
}

.track-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* Full tracklist item styles (matching TrackItem.vue) */
.tracklist-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin: 4px 0;
  border-radius: var(--r-md);
  cursor: pointer;
  transition: all 0.15s ease;
  background: transparent;
}

.tracklist-item:hover {
  background: var(--c-bg-3);
}

.tracklist-item:active {
  transform: scale(0.98);
}

.tracklist-item.playing {
  background: var(--c-bg-3);
  box-shadow: 
    inset 2px 2px 4px var(--sh-inset-dark),
    inset -1px -1px 3px var(--sh-inset-light);
}

.tracklist-item.playing .track-title {
  color: var(--c-accent);
}

.tracklist-item.missing {
  opacity: 0.5;
}

.tracklist-item.missing:hover {
  opacity: 0.8;
}

.track-number {
  width: 28px;
  text-align: center;
  color: var(--c-text-3);
  font-size: 14px;
  font-weight: 500;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.tracklist-item.playing .track-number {
  color: var(--c-accent);
}

.track-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.track-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.track-title.missing-title {
  color: var(--c-text-3);
}

.track-artist {
  font-size: 12px;
  color: var(--c-text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.track-artist.clickable {
  cursor: pointer;
  transition: color 0.15s ease;
}

.track-artist.clickable:hover {
  color: var(--c-accent);
  text-decoration: underline;
}

.album-not-found {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: 24px;
  text-align: center;
}

.not-found-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 360px;
}

.not-found-icon {
  color: var(--c-text-3);
  opacity: 0.6;
}

.not-found-content h2 {
  font-size: 20px;
  font-weight: 700;
  color: var(--c-text-1);
}

.not-found-content p {
  font-size: 14px;
  color: var(--c-text-3);
  margin-bottom: 8px;
}

.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  background: var(--c-accent);
  color: #000;
  font-size: 14px;
  font-weight: 600;
  border-radius: 20px;
  border: none;
  cursor: pointer;
  transition: transform 0.15s ease, opacity 0.15s ease;
}

.primary-btn:hover {
  transform: translateY(-1px);
  opacity: 0.95;
}

.primary-btn:active {
  transform: scale(0.97);
}

.track-duration {
  color: var(--c-text-3);
  font-size: 12px;
  font-weight: 500;
  min-width: 40px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.add-btn,
.add-library-btn {
  width: 28px;
  height: 28px;
  border-radius: var(--r-full);
  background: var(--c-accent);
  color: var(--c-accent-text, #000);
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: transform 0.15s ease;
  box-shadow: 2px 2px 4px var(--sh-dark);
}

.add-btn:hover,
.add-library-btn:hover {
  transform: scale(1.1);
}

.in-library-indicator {
  color: var(--c-accent);
  width: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.playing-indicator {
  color: var(--c-accent);
  width: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* Modal styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal, 1200);
  padding: 16px;
}

.modal-content {
  background: var(--c-bg-2);
  border-radius: var(--r-xl);
  padding: 24px;
  max-width: 400px;
  width: 100%;
  position: relative;
  box-shadow: 12px 12px 24px var(--sh-dark);
  border: 1px solid rgba(255, 255, 255, 0.04);
}

.modal-content h3 {
  margin: 0 0 16px 0;
  font-size: 18px;
  padding-right: 24px;
  color: var(--c-text-1);
}

.modal-loading {
  text-align: center;
  padding: 24px 0;
}

.modal-loading p {
  margin-top: 12px;
  color: var(--c-text-2);
}

.modal-found,
.modal-not-found {
  padding: 8px 0;
}

.success-message {
  color: var(--c-accent);
  margin-bottom: 12px;
}

.info-message {
  color: var(--c-text-2);
  margin-bottom: 8px;
}

.hint {
  color: var(--c-text-3);
  font-size: 13px;
}

.found-track-info {
  background: var(--c-bg-0);
  padding: 12px;
  border-radius: var(--r-md);
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.found-track-info strong {
  color: var(--c-text-1);
}

.found-track-info span {
  color: var(--c-text-2);
  font-size: 14px;
}

.primary-btn {
  width: 100%;
  padding: 12px;
  background: var(--c-accent);
  color: var(--c-accent-text, #000);
  border: none;
  border-radius: var(--r-full);
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.primary-btn:hover {
  background: var(--c-accent-light);
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.secondary-btn {
  width: 100%;
  padding: 12px;
  background: var(--c-bg-3);
  color: var(--c-text-1);
  border: none;
  border-radius: var(--r-full);
  cursor: pointer;
}

.close-btn {
  position: absolute;
  top: 16px;
  right: 16px;
  background: none;
  border: none;
  color: var(--c-text-3);
  cursor: pointer;
}

.close-btn:hover {
  color: var(--c-text-1);
}

.loading {
  display: flex;
  justify-content: center;
  padding: 48px;
}
</style>
