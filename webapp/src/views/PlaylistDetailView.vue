<template>
  <div class="playlist-detail-view" v-if="playlist">
    <!-- Unified Hero Header -->
    <div class="hero-header">
      <div class="hero-cover playlist-cover">
        <div class="cover-grid" :class="{ 'single-cover': coverImages.length === 1 }" v-if="coverImages.length">
          <img
            v-for="(cover, i) in coverImages"
            :key="i"
            :src="cover"
          />
        </div>
        <div v-else class="cover-placeholder"><Music :size="48" /></div>
      </div>
      <div class="hero-info">
        <h1 class="hero-title">{{ playlist.name }}</h1>
        <p class="hero-meta">
          <span>{{ playlist.track_count }} треков</span>
          <span v-if="playlist.is_public" class="public-badge"><Globe :size="14" /> Публичный</span>
          <span v-if="playlist.owner_name && !isOwner" class="owner-info" :class="{ 'clickable': !!playlist.owner_id }" @click="goToOwner">от {{ playlist.owner_name }}</span>
        </p>
        <div v-if="playlist.is_system" class="system-playlist-badge" style="margin-top:8px;display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:rgba(29,185,84,0.15);border:1px solid rgba(29,185,84,0.3);font-size:12px;color:#1db954;">Minecraft Media - sistemnyy playlist</div>
        <TagChips
          v-if="playlist.tags?.length"
          :tags="playlist.tags"
          :max="5"
          size="sm"
          :clickable="true"
          class="playlist-tags"
          @tagClick="handleTagClick"
        />
        <!-- Stale / unavailable tracks warning -->
        <div v-if="playlist.unavailable_track_count > 0" class="playlist-stale-banner">
          <AlertCircle :size="14" />
          <span v-if="isOwner">
            {{ playlist.unavailable_track_count }} {{ playlist.unavailable_track_count === 1 ? 'трек недоступен' : 'треков недоступно' }} в Telegram. Отправьте их боту повторно, чтобы вернуть звук.
          </span>
          <span v-else>
            {{ playlist.unavailable_track_count }} {{ playlist.unavailable_track_count === 1 ? 'трек устарел' : 'треков устарело' }} у автора и может пропускаться.
          </span>
        </div>
      </div>
    </div>

    <!-- Unified Actions with Expandable Search -->
    <div class="hero-actions" :class="{ 'search-active': isSearchOpen }">
      <div class="action-buttons" v-if="!isSearchOpen">
        <button 
          class="action-btn play-btn" 
          @click="togglePlay" 
          :disabled="!filteredTracks?.length" 
          :title="playButtonTitle"
        >
          <Pause v-if="isPlaying" :size="20" fill="currentColor" />
          <Play v-else :size="20" fill="currentColor" />
        </button>
        <button class="action-btn shuffle-btn" @click="shufflePlay" :disabled="isShuffling || !filteredTracks?.length" title="Перемешать">
          <Shuffle :size="18" />
        </button>
        <!-- Edit button for owner -->
        <button v-if="isOwner" class="action-btn edit-btn" @click="openEditModal" title="Редактировать">
          <Edit3 :size="18" />
        </button>
        <!-- Subscribe/Unsubscribe button for non-owner public playlists -->
        <button 
          v-else-if="playlist.is_public" 
          class="action-btn subscribe-action-btn"
          :class="{ subscribed: playlist.is_subscribed }"
          @click="toggleSubscription"
          :disabled="subscribing"
          :title="playlist.is_subscribed ? 'В медиатеке' : 'Добавить в медиатеку'"
        >
          <Check v-if="playlist.is_subscribed" :size="18" />
          <Plus v-else :size="18" />
        </button>
        <!-- Share button -->
        <button class="action-btn share-btn" @click="handleSharePlaylist" title="Поделиться">
          <Share2 :size="18" />
        </button>
        <!-- Pin button -->
        <button 
          class="action-btn pin-btn" 
          :class="{ active: uiStore.isPlaylistPinned(playlist.id) }"
          @click="togglePin" 
          :title="uiStore.isPlaylistPinned(playlist.id) ? 'Открепить от сайдбара' : 'Закрепить в сайдбаре'"
        >
          <Pin :size="18" :fill="uiStore.isPlaylistPinned(playlist.id) ? 'currentColor' : 'none'" />
        </button>
      </div>

      <ExpandableSearch
        v-if="playlist.tracks?.length > 5"
        v-model="searchQuery"
        v-model:open="isSearchOpen"
        placeholder="Поиск по плейлисту..."
        title="Поиск по плейлисту"
      />
    </div>

    <!-- Track list -->
    <div class="track-list" v-if="filteredTracks.length">
      <TrackItem
        v-for="(track, index) in filteredTracks"
        :key="track.id"
        :track="track"
        :isPlaying="playerStore.currentTrack?.id === track.id"
        :isLiked="track.is_liked"
        @click="playTrack(track, index)"
        @like="handleLikeTrack(track)"
        @menu="(e) => openMenu('track', track, `playlist:${playlist.id}`, e)"
        @download="handleDirectDownload(track)"
        @hdNotice="handleHdNotice"
      />
    </div>

    <!-- Empty search inside playlist -->
    <div v-else-if="searchQuery" class="empty-state search-empty">
      <span class="empty-icon"><Search :size="48" /></span>
      <p>Ничего не найдено по запросу «{{ searchQuery }}»</p>
      <button type="button" class="btn-global-search" @click="goToGlobalSearch">
        <Search :size="16" />
        <span>Искать в глобальном поиске</span>
      </button>
    </div>

    <!-- Empty playlist -->
    <div v-else class="empty-state">
      <span class="empty-icon"><Music :size="48" /></span>
      <p>Плейлист пуст</p>
      <button v-if="isOwner" class="empty-add-btn" @click="openEditModal">
        <Plus :size="18" />
        <span>Добавить треки</span>
      </button>
      <p v-else class="hint">В этом плейлисте пока нет треков</p>
    </div>

    <!-- Edit modal -->
    <EditPlaylistModal
      :show="showEditModal"
      :playlist="playlist"
      @close="showEditModal = false"
      @save="handleSavePlaylist"
      @delete="deletePlaylist"
      @update:tracks="handleTracksUpdate"
    />
  </div>

  <div v-else-if="loading" class="loading">
    <div class="spinner"></div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useAuthStore } from '@/stores/auth'
import { useLibraryStore } from '@/stores/library'
import { useUIStore } from '@/stores/ui'
import { useContextMenu } from '@/composables/useContextMenu'
import { useTrackActions, usePlaybackActions, useTrackSync, useShare } from '@/composables'
import TrackItem from '@/components/TrackItem.vue'
import TagChips from '@/components/TagChips.vue'
import EditPlaylistModal from '@/components/EditPlaylistModal.vue'
import ExpandableSearch from '@/components/ui/ExpandableSearch.vue'
import api, { playlistsApi } from '@/api/client'
import apiCache from '@/utils/apiCache'
import { Music, Check, Plus, Globe, Play, Pause, Shuffle, Edit3, Share2, Search, Pin, AlertCircle } from 'lucide-vue-next'
import { getCoverUrl, CoverSize } from '@/utils'

// Universal context menu
const { openMenu } = useContextMenu()

const route = useRoute()
const router = useRouter()
const playerStore = usePlayerStore()
const authStore = useAuthStore()
const libraryStore = useLibraryStore()
const uiStore = useUIStore()

// Unified track actions
const { handleDirectDownload, handleHdNotice, handleLikeTrack } = useTrackActions()
const { share } = useShare()

const handleSharePlaylist = () => {
  if (!playlist.value) return
  const trackCount = playlist.value.tracks?.length || playlist.value.tracks_count || 0
  share({
    type: 'playlist',
    id: playlist.value.id,
    title: playlist.value.name,
    subtitle: trackCount ? `${trackCount} треков` : 'Плейлист',
    coverUrl: playlist.value.custom_cover_url || (playlist.value.tracks?.[0]?.cover_url || ''),
    text: `Послушай плейлист «${playlist.value.name}» в TG Player!`,
  })
}

const goToOwner = () => {
  if (playlist.value?.owner_id) {
    router.push(`/user/${playlist.value.owner_id}`)
  }
}

// State
const playlist = ref(null)
const loading = ref(true)
const searchQuery = ref('')
const isSearchOpen = ref(false)

const goToGlobalSearch = () => {
  const q = searchQuery.value.trim()
  if (q) {
    router.push({ path: '/search', query: { q } })
  }
}

const filteredTracks = computed(() => {
  const list = playlist.value?.tracks || []
  if (!searchQuery.value.trim()) return list
  const q = searchQuery.value.trim().toLowerCase().replace(/^#/, '')
  return list.filter(t => 
    t.title?.toLowerCase().includes(q) || 
    t.artist?.toLowerCase().includes(q) ||
    (t.tags && t.tags.some(tag => (typeof tag === 'string' ? tag : tag?.tag)?.toLowerCase().includes(q)))
  )
})

// Sync playlist tracks with global track events
useTrackSync(() => playlist.value?.tracks)
const showEditModal = ref(false)
const subscribing = ref(false)

// Unified playback actions - use shufflePlayFull for lazy loading all playlist tracks
const { playAll, togglePlay, isPlaying, isCurrentContext, playButtonTitle, shufflePlayFull, isShuffling, playTrack } = usePlaybackActions(
  () => filteredTracks.value?.length ? filteredTracks.value : playlist.value?.tracks,
  () => playlist.value ? { type: 'playlist', id: playlist.value.id, name: playlist.value.name } : null
)

const togglePin = () => {
  if (!playlist.value?.id) return
  const isPinned = uiStore.togglePinPlaylist(playlist.value.id)
  if (isPinned) {
    uiStore.toast.success('Закреплено', 'Плейлист закреплен в сайдбаре')
  } else {
    uiStore.toast.info('Откреплено', 'Плейлист откреплен от сайдбара')
  }
}

// Shuffle play handler using lazy loading
const shufflePlay = () => {
  if (playlist.value?.id) {
    shufflePlayFull('playlist', playlist.value.id)
  }
}

// Computed
const isOwner = computed(() => {
  if (!playlist.value || !authStore.user) return true
  return playlist.value.owner_id === authStore.user.id || !playlist.value.owner_id
})

const coverImages = computed(() => {
  if (playlist.value?.custom_cover_url) {
    return [getCoverUrl(playlist.value.custom_cover_url, CoverSize.LARGE)]
  }
  // Use covers array from API (track covers collage, up to 4)
  if (playlist.value?.covers?.length) {
    // Use large size for single cover, medium for multi-cover grid
    const size = playlist.value.covers.length === 1 ? CoverSize.LARGE : CoverSize.MEDIUM
    return playlist.value.covers.map(url => getCoverUrl(url, size))
  }
  // Fallback to track covers for collage (use medium size for grid)
  if (!playlist.value?.tracks) return []
  return playlist.value.tracks.filter(t => t.cover_url).slice(0, 4).map(t => getCoverUrl(t.cover_url, CoverSize.MEDIUM))
})

// Data loading
const loadPlaylist = async (force = false) => {
  if (!route.params.id) return
  loading.value = true
  try {
    const response = await api.get(`/playlists/${route.params.id}`, {
      bypassCache: Boolean(force)
    })
    playlist.value = response.data
  } catch (error) {
    console.error('Failed to load playlist:', error)
  } finally {
    loading.value = false
  }
}

// Edit modal handlers
const openEditModal = () => {
  if (playlist.value?.is_system) { uiStore.toast.error('Zaschischeno', 'Sistemnyy playlist nelzya pereimenovat'); return }
  showEditModal.value = true
}

const handleSavePlaylist = async ({ name, isPublic, covers, tracks: savedTracks }) => {
  playlist.value.name = name
  playlist.value.is_public = isPublic
  if (savedTracks) {
    playlist.value.tracks = savedTracks
    playlist.value.track_count = savedTracks.length
  }
  
  // Update covers array (track collage covers from save response)
  if (covers?.length) {
    playlist.value.covers = covers
  }
  
  showEditModal.value = false
  uiStore.toast.success('Сохранено', 'Плейлист обновлён')

  // Notify entire app (sidebar, grids, cache) AFTER modal is closed
  await libraryStore.notifyPlaylistChange(playlist.value.id)
}

const handleTracksUpdate = (tracks) => {
  playlist.value.tracks = tracks
  playlist.value.track_count = tracks.length
}

const deletePlaylist = async () => {
  if (playlist.value?.is_system) { uiStore.toast.error('Zaschischeno', 'Sistemnyy playlist Minecraft Media nelzya udalit'); return }
  try {
    await libraryStore.deletePlaylist(playlist.value.id)
    uiStore.toast.success('Удалено', 'Плейлист удален')
    router.push('/library?tab=playlists')
  } catch (error) {
    console.error('Failed to delete playlist:', error)
    uiStore.toast.error('Ошибка', 'Не удалось удалить плейлист')
  }
}

// Subscription
const toggleSubscription = async () => {
  if (subscribing.value || !playlist.value) return
  if (!playlist.value.is_subscribed && !authStore.requireChannel('подписки на плейлист')) {
    return
  }
  subscribing.value = true
  const prevSubscribed = !!playlist.value.is_subscribed
  const targetSubscribed = !prevSubscribed

  // 1. Optimistic update: instantly flip button state in UI
  playlist.value.is_subscribed = targetSubscribed

  try {
    if (prevSubscribed) {
      // Unsubscribe
      await playlistsApi.unsubscribe(playlist.value.id)
      uiStore.toast.success('Удалено', 'Плейлист убран из медиатеки')
    } else {
      // Subscribe
      await playlistsApi.subscribe(playlist.value.id)
      uiStore.toast.success('Добавлено', 'Плейлист добавлен в медиатеку')
    }
  } catch (error) {
    const errorMsg = error.response?.data?.detail || ''
    if (errorMsg.includes('Already subscribed')) {
      playlist.value.is_subscribed = true
      uiStore.toast.info('В медиатеке', 'Плейлист уже добавлен')
    } else if (errorMsg.includes('Not subscribed')) {
      playlist.value.is_subscribed = false
      uiStore.toast.info('Удалено', 'Плейлист не в медиатеке')
    } else {
      // Revert optimistic update on unexpected error
      playlist.value.is_subscribed = prevSubscribed
      console.error('Failed to toggle subscription:', error)
      uiStore.toast.error('Ошибка', errorMsg || 'Не удалось обновить статус подписки')
    }
  } finally {
    subscribing.value = false
    // Explicitly invalidate cache and notify the whole app
    apiCache.delete(`/playlists/${playlist.value.id}`)
    await libraryStore.notifyPlaylistChange(playlist.value.id)
  }
}

// Handle tag click: navigate to search
const handleTagClick = (tag) => {
  if (!tag) return
  const cleanTag = tag.replace(/^#/, '')
  router.push({ path: '/search', query: { tag: cleanTag } })
}

const onPlaylistChanged = (e) => {
  if (showEditModal.value) return // Prevent clobbering active modal editing state
  const changedId = e?.detail?.playlistId
  if (!changedId || String(changedId) === String(route.params.id) || (playlist.value && String(changedId) === String(playlist.value.id))) {
    loadPlaylist(true)
  }
}

// Load on mount & listen for global changes
onMounted(() => {
  loadPlaylist(true)
  window.addEventListener('playlist:changed', onPlaylistChanged)
})

onUnmounted(() => {
  window.removeEventListener('playlist:changed', onPlaylistChanged)
})

// Reload when route params change (for sidebar navigation)
watch(
  () => route.params.id,
  (newId, oldId) => {
    if (newId && newId !== oldId) {
      loadPlaylist()
    }
  }
)
</script>

<style scoped>
.playlist-detail-view {
  padding: 16px;
}

.cover-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  width: 100%;
  height: 100%;
}

.cover-grid.single-cover {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
}

.cover-grid img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.hero-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
  min-height: 44px;
}

.hero-actions.search-active {
  justify-content: stretch;
}

.play-btn svg.lucide-play {
  margin-left: 2px;
}

.track-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.empty-state {
  text-align: center;
  padding: 48px 16px;
  color: var(--c-text-2);
}

.empty-icon {
  font-size: 48px;
  display: block;
  margin-bottom: 16px;
}

.empty-add-btn {
  margin-top: 16px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 20px;
  background: var(--c-accent);
  color: #fff;
  border: none;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.1s;
}

.empty-add-btn:active {
  transform: scale(0.97);
}

.empty-add-btn:hover {
  opacity: 0.9;
}

.hint {
  color: var(--c-text-3);
  font-size: 14px;
  margin-top: 8px;
}

.public-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--c-accent);
  font-size: 13px;
  font-weight: 500;
}

.owner-info {
  font-size: 13px;
  color: var(--c-text-3);
}
.owner-info.clickable {
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.owner-info.clickable:hover {
  color: var(--c-accent);
}


.playlist-tags {
  margin-top: 8px;
}

.playlist-stale-banner {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 6px 12px;
  background: rgba(234, 179, 8, 0.12);
  border: 1px solid rgba(234, 179, 8, 0.25);
  border-radius: 8px;
  color: #eab308;
  font-size: 12px;
  line-height: 1.4;
}
</style>
