<template>
  <!-- Loading skeleton for entire page -->
  <div class="artist-detail-view" v-if="loading && !artist">
    <!-- Artist header skeleton -->
    <div class="artist-header">
      <div class="artist-image skeleton-image"></div>
      <div class="artist-info">
        <div class="skeleton-title"></div>
        <div class="skeleton-meta"></div>
      </div>
    </div>

    <!-- Actions skeleton -->
    <div class="artist-actions">
      <div class="skeleton-buttons"></div>
    </div>

    <!-- Tracks section skeleton -->
    <section class="section">
      <div class="skeleton-section-title"></div>
      <div class="track-list">
        <TrackSkeleton v-for="i in 8" :key="i" />
      </div>
    </section>
  </div>

  <!-- Actual content -->
  <div class="artist-detail-view" v-else-if="artist">
    <!-- Unified Hero Header -->
    <div class="hero-header">
      <div class="hero-cover round artist-cover">
        <img v-if="artist.image_url" :src="getCoverUrl(artist.image_url, CoverSize.LARGE)" :alt="artist.name" />
        <div v-else class="cover-placeholder"><User :size="48" /></div>
      </div>
      <div class="hero-info">
        <h1 class="hero-title">{{ artist.name }}</h1>
        <p class="hero-meta">
          {{ artist.track_count }} треков • {{ artist.album_count }} альбомов
        </p>
        <span v-if="isGlobal" class="global-artist-badge"><Globe :size="12" /> Вся коллекция</span>
        <TagChips
          v-if="artist.tags?.length"
          :tags="artist.tags"
          :max="5"
          size="sm"
          :clickable="true"
          class="artist-tags"
          @tagClick="handleTagClick"
        />
      </div>
    </div>

    <!-- Unified Actions -->
    <div class="hero-actions" v-if="artist.track_count > 0">
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
      </div>
    </div>

    <!-- Albums section -->
    <section v-if="artist.albums?.length" class="section">
      <h2>Альбомы</h2>
      <div class="albums-row">
        <div
          v-for="album in artist.albums"
          :key="album.id"
          class="album-card"
          @click="goToAlbum(album)"
          @contextmenu.prevent="openMenu('album', album, 'artist', $event)"
          v-longpress="(e) => openMenu('album', album, 'artist', e)"
        >
          <div class="album-cover">
            <img v-if="album.cover_url" :src="getCoverUrl(album.cover_url, CoverSize.MEDIUM)" :alt="album.name" />
            <div v-else class="cover-placeholder"><Disc3 :size="24" /></div>
          </div>
          <div class="album-name">{{ album.name }}</div>
          <div class="album-year" v-if="album.release_date">
            {{ formatYear(album.release_date) }}
          </div>
        </div>
      </div>
    </section>

    <!-- Tracks section with virtual scrolling -->
    <section class="section tracks-section">
      <div class="section-header">
        <h2>Все треки</h2>
        <span v-if="tracksTotal > 0" class="tracks-count">{{ tracksTotal }}</span>
      </div>
      
      <!-- Virtual track list with Spotify-style skeleton loading -->
      <div class="virtual-tracks-container">
        <VirtualTrackList
          ref="virtualTrackListRef"
          :fetchFn="fetchArtistTracks"
          :pageSize="50"
          :skeletonCount="12"
          :showAlbum="true"
          :showAddToLibrary="isGlobal"
          menuContext="artist"
          @click="handleTrackClick"
          @like="handleLikeTrack"
          @menu="handleTrackMenu"
          @download="handleDirectDownload"
          @addToLibrary="handleAddToLibrary"
          @hdNotice="handleHdNotice"
          @update:total="tracksTotal = $event"
        >
          <template #empty>
            <span class="empty-icon"><Music :size="48" /></span>
            <p>Нет треков</p>
          </template>
        </VirtualTrackList>
      </div>
    </section>
  </div>

  <!-- Not found at all -->
  <div v-else class="not-in-library">
    <div class="not-in-library-content">
      <div class="icon"><User :size="48" /></div>
      <h2>{{ decodeURIComponent(route.params.name) }}</h2>
      <p>Исполнитель не найден</p>
      <button class="primary-btn" @click="router.push('/library?tab=artists')">
        Вернуться к артистам
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useLibraryStore } from '@/stores/library'
import { useUIStore } from '@/stores/ui'
import { useContextMenu } from '@/composables/useContextMenu'
import { useTrackActions, usePlaybackActions } from '@/composables'
import VirtualTrackList from '@/components/VirtualTrackList.vue'
import TagChips from '@/components/TagChips.vue'
import api, { playerApi } from '@/api/client'
import { User, Disc3, Globe, Music, Play, Pause, Shuffle } from 'lucide-vue-next'
import { getCoverUrl, CoverSize, formatDuration } from '@/utils'

// Universal context menu
const { openMenu } = useContextMenu()

const route = useRoute()
const router = useRouter()
const playerStore = usePlayerStore()
const libraryStore = useLibraryStore()
const uiStore = useUIStore()

// Unified track actions
const { handleDirectDownload, handleHdNotice, handleLikeTrack, handleAddToLibrary } = useTrackActions()

const artist = ref(null)
const loading = ref(true)
const notInLibrary = ref(false)
const tracksTotal = ref(0)
const virtualTrackListRef = ref(null)

// Get scope from query param
const scope = computed(() => route.query.scope || 'library')
const activeScope = ref(scope.value)
const isGlobal = computed(() => activeScope.value === 'global')

watch(scope, (newScope) => {
  activeScope.value = newScope
})

// Tracks pagination with virtual scroll
const artistName = computed(() => route.params.name ? decodeURIComponent(route.params.name) : null)

const fetchArtistTracks = async ({ offset, limit }) => {
  if (!artistName.value) return { items: [], total: 0 }
  
  const response = await api.get(`/artists/${encodeURIComponent(artistName.value)}/tracks`, {
    params: { offset, limit, scope: activeScope.value }
  })
  return response.data
}

const loadArtist = async () => {
  loading.value = true
  notInLibrary.value = false
  try {
    const name = decodeURIComponent(route.params.name)
    let currentScope = activeScope.value
    let response

    try {
      response = await api.get(`/artists/${encodeURIComponent(name)}/info`, { params: { scope: currentScope } })
    } catch (err) {
      // If artist not found in library (404), seamlessly fallback to global scope!
      if (err.response?.status === 404 && currentScope === 'library') {
        response = await api.get(`/artists/${encodeURIComponent(name)}/info`, { params: { scope: 'global' } })
        activeScope.value = 'global'
      } else {
        throw err
      }
    }

    artist.value = response.data
    // Reset virtual track list
    if (virtualTrackListRef.value) {
      virtualTrackListRef.value.reset()
    }
  } catch (error) {
    console.error('Failed to load artist:', error)
    artist.value = null
  } finally {
    loading.value = false
  }
}

// Handle track click from VirtualTrackList
const handleTrackClick = ({ track, index, allTracks }) => {
  const context = artist.value ? { type: 'artist', id: artist.value.id || artistName.value, name: artistName.value } : null
  playerStore.playTrack(track, allTracks, context)
}

// Handle track menu from VirtualTrackList
const handleTrackMenu = ({ track, index, event }) => {
  openMenu('track', track, 'artist', event)
}

// Unified playback actions - use shufflePlayFull for lazy loading all artist tracks
const { playAll, togglePlay, isPlaying, isCurrentContext, playButtonTitle, shufflePlayFull, isShuffling } = usePlaybackActions(
  () => virtualTrackListRef.value?.getLoadedTracks() || [],
  () => artist.value ? { type: 'artist', id: artist.value.id || artistName.value, name: artistName.value } : null
)

// Shuffle play handler using lazy loading
const shufflePlay = () => {
  if (artistName.value) {
    shufflePlayFull('artist', artistName.value)
  }
}

const goToAlbum = (album) => {
  const query = isGlobal.value ? { scope: 'global' } : {}
  router.push({ path: `/album/${album.id}`, query })
}

// Go to global scope (used when artist not in library)
const goToGlobal = () => {
  router.push({ 
    path: route.path, 
    query: { scope: 'global' } 
  })
}

// Handle tag click: navigate to search
const handleTagClick = (tag) => {
  if (!tag) return
  const cleanTag = tag.replace(/^#/, '')
  router.push({ path: '/search', query: { tag: cleanTag } })
}

// Track actions (handleLikeTrack, handleAddToLibrary, handleDirectDownload, handleHdNotice) 
// are provided by useTrackActions composable above


const formatYear = (date) => {
  if (!date) return ''
  return new Date(date).getFullYear()
}

const onTrackAdded = () => {
  loadArtist()
}

onMounted(() => {
  loadArtist()
  window.addEventListener('track:added:library', onTrackAdded)
})

onUnmounted(() => {
  window.removeEventListener('track:added:library', onTrackAdded)
})

// Reload when scope changes
watch(scope, () => {
  loadArtist()
})

// Reload when route params change (for sidebar navigation)
watch(
  () => route.params.name,
  (newName, oldName) => {
    if (newName && newName !== oldName) {
      loadArtist()
    }
  }
)
</script>

<style scoped>
.artist-detail-view {
  padding: 16px;
}

.not-in-library {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: 16px;
}

.global-artist-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 12px;
  background: rgba(29, 185, 84, 0.12);
  color: var(--c-accent);
  border: 1px solid rgba(29, 185, 84, 0.25);
  margin-top: 6px;
  width: fit-content;
}

.artist-tags {
  margin-top: 8px;
}

.not-in-library-content {
  text-align: center;
}

.not-in-library-content .icon {
  font-size: 80px;
  margin-bottom: 16px;
  opacity: 0.5;
  color: var(--c-text-3);
  display: flex;
  justify-content: center;
}

.not-in-library-content h2 {
  font-size: 24px;
  font-weight: 700;
  color: var(--c-text-1);
  margin: 0 0 8px 0;
}

.not-in-library-content p {
  color: var(--c-text-2);
  margin: 0 0 24px 0;
}

.not-in-library-content .primary-btn {
  background: var(--c-accent);
  color: var(--c-accent-text, #000);
  border: none;
  border-radius: var(--r-full);
  padding: 12px 24px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: transform 0.2s, opacity 0.2s;
  box-shadow: 4px 4px 8px var(--sh-dark), 0 0 16px var(--c-accent-glow);
}

.not-in-library-content .primary-btn:hover {
  opacity: 0.95;
  transform: scale(1.02);
}

.play-btn svg.lucide-play {
  margin-left: 2px;
}

.section {
  margin-bottom: 32px;
}

.section h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--c-text-1);
  margin: 0 0 16px 0;
}

.albums-row {
  display: flex;
  gap: 16px;
  overflow-x: auto;
  padding-bottom: 8px;
  -webkit-overflow-scrolling: touch;
}

.albums-row::-webkit-scrollbar {
  height: 4px;
}

.albums-row::-webkit-scrollbar-thumb {
  background: var(--c-bg-4);
  border-radius: 2px;
}

.album-card {
  flex-shrink: 0;
  width: 140px;
  cursor: pointer;
}

.album-cover {
  width: 140px;
  height: 140px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--c-bg-3);
  margin-bottom: 8px;
}

.album-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 40px;
}

.album-name {
  color: var(--c-text-1);
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.album-year {
  color: var(--c-text-3);
  font-size: 12px;
}

/* Tracks section */
.tracks-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.section-header h2 {
  margin: 0;
}

.tracks-count {
  color: var(--c-text-3);
  font-size: 14px;
  font-weight: 500;
}

.virtual-tracks-container {
  flex: 1;
  min-height: 0;
  border-radius: var(--r-md);
  overflow: hidden;
}

.track-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* load-trigger, loading-more are in design-system.css */

.loading-more-tracks {
  margin-top: 8px;
}

.loading {
  display: flex;
  justify-content: center;
  padding: 48px;
}

/* spinner is in design-system.css */

/* Page skeleton styles */
.skeleton-image {
  background: var(--c-bg-3);
  animation: pulse 1.5s ease-in-out infinite;
}

.skeleton-title {
  height: 28px;
  width: 180px;
  background: var(--c-bg-3);
  border-radius: 4px;
  margin-bottom: 8px;
  animation: pulse 1.5s ease-in-out infinite;
}

.skeleton-meta {
  height: 16px;
  width: 140px;
  background: var(--c-bg-3);
  border-radius: 4px;
  animation: pulse 1.5s ease-in-out infinite;
  animation-delay: 0.1s;
}

.skeleton-buttons {
  width: 100px;
  height: 48px;
  background: var(--c-bg-3);
  border-radius: 24px;
  animation: pulse 1.5s ease-in-out infinite;
  animation-delay: 0.2s;
}

.skeleton-section-title {
  height: 20px;
  width: 100px;
  background: var(--c-bg-3);
  border-radius: 4px;
  margin-bottom: 16px;
  animation: pulse 1.5s ease-in-out infinite;
  animation-delay: 0.3s;
}

@keyframes pulse {
  0%, 100% {
    opacity: 0.4;
  }
  50% {
    opacity: 0.8;
  }
}
</style>
