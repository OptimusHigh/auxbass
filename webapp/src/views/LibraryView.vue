<template>
  <div class="library-view">
    <!-- No channel AND no existing content - show onboarding setup prompt -->
    <div v-if="!authStore.hasChannel && !hasExistingContent" class="no-channel-prompt">
      <div class="prompt-icon">📚</div>
      <h2>Ваша библиотека</h2>
      <p>Подключите Telegram-канал, чтобы сохранять треки и создавать свою коллекцию музыки</p>
      <button class="setup-btn" @click="goToChannelSetup">
        Подключить канал
      </button>
    </div>

    <!-- Has channel OR has existing content - show library -->
    <template v-else>
      <!-- Channel Warning Banner if disconnected but user has tracks -->
      <div v-if="!authStore.hasChannel" class="channel-warning-banner" @click="goToChannelSetup">
        <span class="warning-icon">⚠️</span>
        <div class="warning-text">
          <strong>Связь с Telegram-каналом потеряна</strong>
          <span>{{ authStore.channelError || 'Бот удалён или не имеет прав администратора. Новые треки не бэкапятся.' }}</span>
        </div>
        <button class="warning-btn">Подключить</button>
      </div>
      <!-- Persistent Library Tabs Navigation (always visible across all tabs) -->
      <div class="library-persistent-nav" :class="{ 'search-active': isOverviewSearchOpen }">
        <!-- Desktop Section Title -->
        <h1 v-if="!isOverviewSearchOpen" class="library-desktop-title">
          {{ currentTabTitle }}
        </h1>

        <!-- Mobile Tabs Navigation Pills -->
        <div v-if="!isOverviewSearchOpen" class="library-tabs-pills">
          <button 
            v-for="t in tabs" 
            :key="t.id"
            class="library-tab-pill" 
            :class="{ active: currentTabId === t.id }"
            @click="setTab(t.id)"
          >
            <span>{{ t.label }}</span>
          </button>
        </div>

        <ExpandableSearch
          v-if="currentTabId === 'overview'"
          v-model="searchQuery"
          v-model:open="isOverviewSearchOpen"
          :placeholder="searchPlaceholder"
          title="Поиск по медиатеке"
          @input="debouncedSearch"
          @clear="handleClearSearch"
        />
      </div>

      <!-- Overview Dashboard View without search -->
      <div v-if="currentTabId === 'overview' && !debouncedQuery" class="overview-dashboard">
        <!-- Section: Плейлисты (Horizontal Scroll) -->
        <section v-if="loadingPlaylists" class="library-section">
          <div class="section-header">
            <div class="skeleton-section-title"></div>
          </div>
          <div class="horizontal-scroll">
            <div v-for="i in 5" :key="i" class="feed-card-skeleton">
              <div class="skeleton-feed-cover"></div>
              <div class="skeleton-feed-title"></div>
              <div class="skeleton-feed-sub"></div>
            </div>
          </div>
        </section>

        <section v-else-if="allPlaylists.length > 0" class="library-section">
          <div class="section-header">
            <h2 
              class="section-title clickable" 
              @click="setTab('playlists')"
              title="Перейти в плейлисты"
            >
              Плейлисты
            </h2>
            <div class="section-actions">
              <button class="section-link" @click="setTab('playlists')">Все {{ allPlaylists.length }}</button>
              <button 
                class="scroll-arrow-btn" 
                :disabled="!playlistsScroll.canScrollLeft.value"
                @click="playlistsScroll.scroll('left')"
                title="Назад"
                aria-label="Назад"
              >
                <ChevronLeft :size="16" />
              </button>
              <button 
                class="scroll-arrow-btn" 
                :disabled="!playlistsScroll.canScrollRight.value"
                @click="playlistsScroll.scroll('right')"
                title="Вперед"
                aria-label="Вперед"
              >
                <ChevronRight :size="16" />
              </button>
            </div>
          </div>
          <div 
            class="horizontal-scroll"
            :ref="playlistsScroll.containerRef"
          >
            <div 
              v-for="pl in allPlaylists.slice(0, 10)" 
              :key="pl.id" 
              class="feed-card"
              @click="goTo(`/playlist/${pl.id}`)"
              @contextmenu.prevent="openMenu('playlist', pl, 'library', $event)"
              v-longpress="(e) => openMenu('playlist', pl, 'library', e)"
            >
              <div class="feed-card-cover" :style="getPlaylistCoverStyle(pl)">
                <img 
                  v-if="pl.covers?.length" 
                  :src="getCoverUrl(pl.covers[0], CoverSize.MEDIUM)" 
                  alt="" 
                  loading="lazy"
                />
                <Folder v-else :size="32" />
                <button 
                  v-if="pl.track_count > 0" 
                  class="play-overlay" 
                  @click.stop="handlePlayPlaylist(pl)"
                  title="Слушать"
                >
                  <Play :size="18" fill="currentColor" />
                </button>
              </div>
              <div class="feed-card-title">{{ pl.name }}</div>
              <div class="feed-card-subtitle">{{ pl.track_count }} треков</div>
            </div>
          </div>
        </section>

        <!-- Section: Альбомы (Horizontal Scroll) -->
        <section v-if="loadingAlbums" class="library-section">
          <div class="section-header">
            <div class="skeleton-section-title"></div>
          </div>
          <div class="horizontal-scroll">
            <div v-for="i in 5" :key="i" class="feed-card-skeleton">
              <div class="skeleton-feed-cover"></div>
              <div class="skeleton-feed-title"></div>
              <div class="skeleton-feed-sub"></div>
            </div>
          </div>
        </section>

        <section v-else-if="overviewAlbums.length > 0" class="library-section">
          <div class="section-header">
            <h2 
              class="section-title clickable" 
              @click="setTab('albums')"
              title="Перейти в альбомы"
            >
              Альбомы
            </h2>
            <div class="section-actions">
              <button class="section-link" @click="setTab('albums')">Все {{ albumsTotal || overviewAlbums.length }}</button>
              <button 
                class="scroll-arrow-btn" 
                :disabled="!albumsScroll.canScrollLeft.value"
                @click="albumsScroll.scroll('left')"
                title="Назад"
                aria-label="Назад"
              >
                <ChevronLeft :size="16" />
              </button>
              <button 
                class="scroll-arrow-btn" 
                :disabled="!albumsScroll.canScrollRight.value"
                @click="albumsScroll.scroll('right')"
                title="Вперед"
                aria-label="Вперед"
              >
                <ChevronRight :size="16" />
              </button>
            </div>
          </div>
          <div 
            class="horizontal-scroll"
            :ref="albumsScroll.containerRef"
          >
            <div 
              v-for="album in overviewAlbums.slice(0, 10)" 
              :key="album.id" 
              class="feed-card"
              @click="goTo(`/album/${album.id}`)"
              @contextmenu.prevent="openMenu('album', album, 'library', $event)"
              v-longpress="(e) => openMenu('album', album, 'library', e)"
            >
              <div class="feed-card-cover">
                <img 
                  v-if="album.cover_url" 
                  :src="getCoverUrl(album.cover_url, CoverSize.MEDIUM)" 
                  alt="" 
                  loading="lazy"
                />
                <Disc3 v-else :size="32" />
                <button 
                  class="play-overlay" 
                  @click.stop="handlePlayAlbum(album)"
                  title="Слушать альбом"
                >
                  <Play :size="18" fill="currentColor" />
                </button>
              </div>
              <div class="feed-card-title">{{ album.name }}</div>
              <div class="feed-card-subtitle">{{ album.artist || 'Альбом' }}</div>
            </div>
          </div>
        </section>

        <!-- Section: Исполнители (Horizontal Scroll with round avatars) -->
        <section v-if="loadingArtists" class="library-section">
          <div class="section-header">
            <div class="skeleton-section-title"></div>
          </div>
          <div class="horizontal-scroll">
            <div v-for="i in 5" :key="i" class="feed-card-skeleton artist-card-skeleton">
              <div class="skeleton-feed-cover skeleton-circle"></div>
              <div class="skeleton-feed-title"></div>
            </div>
          </div>
        </section>

        <section v-else-if="overviewArtists.length > 0" class="library-section">
          <div class="section-header">
            <h2 
              class="section-title clickable" 
              @click="setTab('artists')"
              title="Перейти к артистам"
            >
              Исполнители
            </h2>
            <div class="section-actions">
              <button class="section-link" @click="setTab('artists')">Все {{ artistsTotal || overviewArtists.length }}</button>
              <button 
                class="scroll-arrow-btn" 
                :disabled="!artistsScroll.canScrollLeft.value"
                @click="artistsScroll.scroll('left')"
                title="Назад"
                aria-label="Назад"
              >
                <ChevronLeft :size="16" />
              </button>
              <button 
                class="scroll-arrow-btn" 
                :disabled="!artistsScroll.canScrollRight.value"
                @click="artistsScroll.scroll('right')"
                title="Вперед"
                aria-label="Вперед"
              >
                <ChevronRight :size="16" />
              </button>
            </div>
          </div>
          <div 
            class="horizontal-scroll"
            :ref="artistsScroll.containerRef"
          >
            <div 
              v-for="artist in overviewArtists.slice(0, 10)" 
              :key="artist.name" 
              class="feed-card artist-card"
              @click="goTo(`/artist/${encodeURIComponent(artist.name)}`)"
              @contextmenu.prevent="openMenu('artist', artist, 'library', $event)"
              v-longpress="(e) => openMenu('artist', artist, 'library', e)"
            >
              <div class="feed-card-cover artist-cover" :style="getArtistCoverStyle(artist)">
                <img 
                  v-if="artist.image_url" 
                  :src="getCoverUrl(artist.image_url, CoverSize.MEDIUM)" 
                  alt="" 
                  loading="lazy"
                />
                <span v-else class="artist-initials">{{ getArtistInitials(artist) }}</span>
              </div>
              <div class="feed-card-title">{{ artist.name }}</div>
              <div class="feed-card-subtitle">{{ artist.track_count }} треков</div>
            </div>
          </div>
        </section>

        <!-- Section: Треки (With Shuffle Button and TrackItems) -->
        <section class="library-section tracks-overview-section">
          <div class="section-header">
            <h2 
              class="section-title clickable" 
              @click="setTab('tracks')"
              title="Перейти во все треки"
            >
              Треки
            </h2>
            <button class="section-link" @click="setTab('tracks')">
              Все {{ totalTracksCount }}
            </button>
          </div>

          <!-- Shuffle Button Bar -->
          <div class="overview-actions-bar">
            <button 
              class="shuffle-all-btn" 
              @click="handleShuffleAll" 
              :disabled="totalTracksCount === 0 || shuffling"
            >
              <div v-if="shuffling" class="spinner small"></div>
              <Shuffle v-else :size="16" class="shuffle-icon" />
              <span class="shuffle-text">
                <template v-if="shuffling">Загрузка...</template>
                <template v-else-if="totalTracksCount > 0">Перемешать ({{ totalTracksCount }})</template>
                <template v-else>Перемешать</template>
              </span>
            </button>

            <button 
              class="import-btn" 
              @click="tasksStore.openImportModal()"
              title="Импорт музыки из SoundCloud / Spotify"
            >
              <CloudDownload :size="16" class="import-icon" />
              <span class="import-text">Импорт</span>
            </button>

            <button 
              class="overview-refresh-btn" 
              :class="{ 'is-refreshing': isOverviewRefreshing }"
              @click="handleForceRefresh"
              :disabled="isOverviewRefreshing"
              title="Обновить медиатеку"
              aria-label="Обновить медиатеку"
            >
              <RefreshCw :size="16" class="refresh-icon" :class="{ 'spin-anim': isOverviewRefreshing }" />
            </button>
          </div>

          <!-- Recent Tracks List -->
          <div v-if="loadingTracks && !overviewTracks.length" class="overview-tracks-loading">
            <div v-for="i in 6" :key="i" class="track-item-skeleton">
              <div class="skeleton-thumb"></div>
              <div class="skeleton-text">
                <div class="skeleton-line-title"></div>
                <div class="skeleton-line-sub"></div>
              </div>
            </div>
          </div>

          <div v-else-if="overviewTracks.length > 0" class="overview-tracks-list">
            <TrackItem
              v-for="track in overviewTracks.slice(0, 15)"
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

            <!-- View All Tracks Full Button -->
            <button 
              v-if="totalTracksCount > 15" 
              class="view-all-tracks-btn" 
              @click="setTab('tracks')"
            >
              <span>Смотреть все треки ({{ totalTracksCount }})</span>
              <ChevronRight :size="16" />
            </button>
          </div>

          <div v-else class="overview-empty-tracks">
            <Music :size="36" class="empty-icon" />
            <p>В вашей медиатеке пока нет треков</p>
          </div>
        </section>
      </div>

      <!-- Specific Tab or Search Active View -->
      <div v-else class="library-content">
        <component 
          :is="currentTabComponent" 
          :searchQuery="currentTabId === 'overview' ? debouncedQuery : searchQuery"
          :hideToolbar="currentTabId === 'overview'"
          @update:searchQuery="handleSubtabSearch"
        />
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, onActivated } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { useLibraryStore } from '@/stores/library'
import { usePlayerStore } from '@/stores/player'
import { useTasksStore } from '@/stores/tasks'
import { useContextMenu } from '@/composables/useContextMenu'
import { useDebouncedSearch, useTrackActions } from '@/composables'
import api from '@/api/client'
import { getCoverUrl, CoverSize } from '@/utils'
import LibraryTracks from '@/components/library/LibraryTracks.vue'
import LibraryAlbums from '@/components/library/LibraryAlbums.vue'
import LibraryArtists from '@/components/library/LibraryArtists.vue'
import LibraryPlaylists from '@/components/library/LibraryPlaylists.vue'
import TrackItem from '@/components/TrackItem.vue'
import ExpandableSearch from '@/components/ui/ExpandableSearch.vue'
import { 
  Play, 
  Music, 
  Folder, 
  Disc3, 
  Shuffle, 
  ChevronRight,
  ChevronLeft,
  CloudDownload,
  RefreshCw
} from 'lucide-vue-next'
import { useHorizontalScroll } from '@/composables/useHorizontalScroll'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const uiStore = useUIStore()
const libraryStore = useLibraryStore()
const playerStore = usePlayerStore()
const tasksStore = useTasksStore()
const { openMenu } = useContextMenu()
const { handleLikeTrack, handleDirectDownload, handleHdNotice } = useTrackActions()

const isOverviewRefreshing = ref(false)

const handleForceRefresh = async () => {
  if (isOverviewRefreshing.value) return
  isOverviewRefreshing.value = true
  try {
    await libraryStore.refresh()
    await loadOverviewData(true)
    uiStore.toast?.success('Медиатека обновлена', 'Данные успешно синхронизированы')
  } catch (err) {
    console.error('Failed to force refresh library:', err)
  } finally {
    isOverviewRefreshing.value = false
  }
}

const handleImportFinished = () => {
  loadOverviewData(true)
  libraryStore.fetchPlaylists(true)
  if (uiStore.toast) {
    uiStore.toast.success('Импорт завершен', 'Музыка добавлена в библиотеку')
  }
}

const goToChannelSetup = () => {
  router.push('/settings#channel')
}

const hasExistingContent = computed(() => {
  return (libraryStore.tracks?.length > 0) || 
         (libraryStore.playlists?.length > 0) || 
         !!authStore.channelInfo ||
         !!authStore.channelError
})

// Available tabs
const tabs = [
  { id: 'overview', label: 'Все', placeholder: 'Название или исполнитель...' },
  { id: 'playlists', label: 'Плейлисты', component: LibraryPlaylists, placeholder: 'Поиск плейлистов...' },
  { id: 'albums', label: 'Альбомы', component: LibraryAlbums, placeholder: 'Поиск альбомов...' },
  { id: 'artists', label: 'Артисты', component: LibraryArtists, placeholder: 'Поиск исполнителей...' },
  { id: 'tracks', label: 'Треки', component: LibraryTracks, placeholder: 'Название или исполнитель...' },
]

// Current tab state synced with uiStore
const currentTabId = computed({
  get: () => uiStore.libraryTab || 'overview',
  set: (val) => uiStore.setLibraryTab(val)
})

const resetScrollToTop = () => {
  const mainContent = document.querySelector('.main-content')
  if (mainContent) {
    mainContent.scrollTop = 0
  }
}

const setTab = (tabId) => {
  if (currentTabId.value !== tabId) {
    handleClearSearch()
    isOverviewSearchOpen.value = false
    resetScrollToTop()
  }
  currentTabId.value = tabId
  const query = { ...route.query }
  if (tabId === 'overview') {
    delete query.tab
  } else {
    query.tab = tabId
  }
  router.replace({ path: '/library', query })
}

const applyRouteTab = () => {
  const tab = route.query.tab
  if (tab && typeof tab === 'string' && tabs.some(t => t.id === tab)) {
    uiStore.setLibraryTab(tab)
  }
}

const currentTab = computed(() => tabs.find(t => t.id === currentTabId.value) || tabs[0])

// Desktop section title
const currentTabTitle = computed(() => {
  switch (currentTabId.value) {
    case 'tracks':
      return 'Все треки'
    case 'playlists':
      return 'Плейлисты'
    case 'albums':
      return 'Альбомы'
    case 'artists':
      return 'Артисты'
    case 'overview':
    default:
      return 'Библиотека'
  }
})

const currentTabComponent = computed(() => {
  if (currentTabId.value === 'overview') {
    // If user is searching while on overview, render LibraryTracks with the query
    if (debouncedQuery.value) {
      return LibraryTracks
    }
    return null
  }
  return currentTab.value?.component || LibraryTracks
})

const searchPlaceholder = computed(() => currentTab.value.placeholder)

// Debounced search using composable
const { query: searchQuery, debouncedQuery, search: debouncedSearch, clear: clearSearch, setQuery } = useDebouncedSearch()

const isOverviewSearchOpen = ref(false)

const handleSubtabSearch = (query) => {
  // When a subtab updates its search query, sync searchQuery without triggering parent debounce echo
  if (currentTabId.value !== 'overview') {
    searchQuery.value = query || ''
  }
}

// Overview Data State
const loadingPlaylists = ref(false)
const loadingAlbums = ref(false)
const loadingArtists = ref(false)
const loadingTracks = ref(false)
const shuffling = ref(false)

const overviewAlbums = ref([])
const albumsTotal = ref(0)

// Horizontal scroll managers for carousels
const playlistsScroll = useHorizontalScroll()
const albumsScroll = useHorizontalScroll()
const artistsScroll = useHorizontalScroll()

const allPlaylists = computed(() => libraryStore.playlists || [])
const overviewArtists = computed(() => libraryStore.artists || [])
const artistsTotal = computed(() => libraryStore.artistsTotal || overviewArtists.value.length)
const overviewTracks = computed(() => libraryStore.tracks || [])
const totalTracksCount = computed(() => libraryStore.total || overviewTracks.value.length)

// Navigation helpers
const goTo = (path) => {
  router.push(path)
}

const handlePlayPlaylist = async (playlist) => {
  try {
    await playerStore.playShuffleAll('playlist', playlist.id)
  } catch (e) {
    console.error('Failed to play playlist:', e)
  }
}

const handlePlayAlbum = async (album) => {
  try {
    const response = await api.get(`/albums/${album.id}`)
    const albumData = response.data
    const tracks = albumData.full_tracklist?.filter(t => t.in_library) || albumData.tracks || []
    if (tracks.length > 0) {
      playerStore.playTrack(tracks[0], tracks, { type: 'album', id: album.id, name: album.name || albumData.name })
    }
  } catch (e) {
    console.error('Failed to play album:', e)
  }
}

const playTrack = (track) => {
  if (playerStore.shuffle) {
    playerStore.playShuffleAll('library', null, null, { startingTrack: track })
  } else {
    const list = overviewTracks.value
    const idx = list.findIndex(t => t.id === track.id)
    playerStore.playTrack(track, list, { type: 'library' }, idx >= 0 ? idx : 0)
  }
}

const handleShuffleAll = async () => {
  shuffling.value = true
  try {
    await playerStore.playShuffleAll('library')
  } catch (e) {
    console.error('Failed to shuffle library:', e)
  } finally {
    shuffling.value = false
  }
}

const handleCreatePlaylist = async () => {
  if (!authStore.hasChannel) {
    authStore.promptChannelSetup()
    return
  }
  const name = prompt('Название нового плейлиста:')
  if (!name || !name.trim()) return
  const pl = await libraryStore.createPlaylist(name.trim())
  if (pl?.id) {
    router.push(`/playlist/${pl.id}`)
  }
}

// Cover styles
const getPlaylistCoverStyle = (playlist) => {
  if (playlist.covers?.length) return {}
  const str = playlist.name || 'Playlist'
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash)
  }
  const hue = Math.abs(hash % 360)
  return {
    background: `linear-gradient(135deg, hsl(${hue}, 55%, 35%) 0%, hsl(${(hue + 40) % 360}, 45%, 25%) 100%)`
  }
}

const getArtistInitials = (artist) => {
  const name = artist?.name || ''
  return name.slice(0, 2).toUpperCase() || '♪'
}

const getArtistCoverStyle = (artist) => {
  const str = artist?.name || 'Artist'
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash)
  }
  const hue = Math.abs(hash % 360)
  return {
    background: `linear-gradient(135deg, hsl(${hue}, 50%, 30%) 0%, hsl(${(hue + 50) % 360}, 40%, 20%) 100%)`
  }
}

// Fetch Overview Data
const loadOverviewData = async (force = false) => {
  // 1. Playlists (SWR: show cached immediately, revalidate in background)
  loadingPlaylists.value = !libraryStore.playlists?.length
  libraryStore.fetchPlaylists(force).finally(() => {
    loadingPlaylists.value = false
  })

  // 2. Liked
  libraryStore.fetchLikedTracks()

  // 3. Tracks
  loadingTracks.value = !libraryStore.tracks?.length
  libraryStore.fetchTracks({ limit: 20, refresh: force, bypassCache: force }).finally(() => {
    loadingTracks.value = false
  })

  // 4. Artists (Preview for overview: only first 10)
  loadingArtists.value = !overviewArtists.value?.length
  libraryStore.fetchArtists({ limit: 10, bypassCache: force }).finally(() => {
    loadingArtists.value = false
  })

  // 5. Albums
  loadingAlbums.value = !overviewAlbums.value?.length
  try {
    const response = await api.get('/albums', { params: { limit: 12 }, bypassCache: force })
    overviewAlbums.value = response.data?.items || []
    albumsTotal.value = response.data?.total || overviewAlbums.value.length
  } catch (e) {
    console.error('Failed to load overview albums:', e)
  } finally {
    loadingAlbums.value = false
  }
}

// Sync search query from route or external trigger
const applyRouteSearch = (queryOverride) => {
  const queryParam = queryOverride || route.query.search || route.query.q
  if (queryParam && typeof queryParam === 'string') {
    uiStore.setLibraryTab('tracks')
    setQuery(queryParam, true)
  }
}

const handleAppSearch = (event) => {
  const query = event.detail?.query
  if (query) {
    uiStore.setLibraryTab('tracks')
    setQuery(query, true)
  }
}

const handleClearSearch = () => {
  clearSearch()
  if (route.query.search || route.query.q) {
    router.replace({ path: '/library', query: {} })
  }
}

// Watch route search query param changes
watch(
  () => route.query.search || route.query.q,
  (newVal) => {
    if (newVal && typeof newVal === 'string') {
      if (searchQuery.value !== newVal) {
        uiStore.setLibraryTab('tracks')
        setQuery(newVal, true)
      }
    }
  }
)

// Watch route query tab param changes
watch(
  () => route.query.tab,
  (newTab) => {
    if (newTab && typeof newTab === 'string' && tabs.some(t => t.id === newTab)) {
      if (currentTabId.value !== newTab) {
        handleClearSearch()
        isOverviewSearchOpen.value = false
        resetScrollToTop()
        currentTabId.value = newTab
      }
    } else if (!newTab && currentTabId.value !== 'overview') {
      handleClearSearch()
      isOverviewSearchOpen.value = false
      resetScrollToTop()
      currentTabId.value = 'overview'
    }
  }
)

// Handle reset state
const handleResetState = (event) => {
  if (event.detail.route === '/library' || event.detail.route === '/') {
    handleClearSearch()
    currentTabId.value = 'overview'
  }
}

onMounted(() => {
  applyRouteTab()
  applyRouteSearch()
  loadOverviewData(true)
  window.addEventListener('reset-view-state', handleResetState)
  window.addEventListener('app-search', handleAppSearch)
  window.addEventListener('playlist:changed', onPlaylistChanged)
})

const onPlaylistChanged = () => {
  loadOverviewData(true)
}

onActivated(() => {
  applyRouteTab()
  applyRouteSearch()
  loadOverviewData(true)
})

onUnmounted(() => {
  window.removeEventListener('reset-view-state', handleResetState)
  window.removeEventListener('app-search', handleAppSearch)
  window.removeEventListener('playlist:changed', onPlaylistChanged)
})
</script>

<style scoped>
.library-view {
  --library-nav-height: 58px;
  padding: 0 0 32px var(--content-padding, 16px);
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}

@media (min-width: 768px) {
  .library-view {
    padding: 0 0 32px var(--content-padding, 24px);
  }
}

@media (min-width: 1024px) {
  .library-view {
    --library-nav-height: 0px;
  }
}

/* ─── Persistent Library Tabs Navigation ─── */
.library-persistent-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
  padding-right: var(--content-padding, 16px);
  box-sizing: border-box;
  /* ─── STICKY HEADER FIX: flush to ceiling without parent padding gap ─── */
  position: sticky;
  top: 0;
  z-index: 26;
  margin-left: calc(-1 * var(--content-padding, 16px));
  padding-left: var(--content-padding, 16px);
  padding-top: 14px;
  padding-bottom: 10px;
  margin-bottom: 16px;
  background: rgba(14, 16, 16, 0.88);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  width: calc(100% + var(--content-padding, 16px));
}

@media (min-width: 768px) {
  .library-persistent-nav {
    margin-left: calc(-1 * var(--content-padding, 24px));
    padding-left: var(--content-padding, 24px);
    padding-right: var(--content-padding, 24px);
    width: calc(100% + var(--content-padding, 24px));
  }
}

@media (min-width: 1024px) {
  .library-persistent-nav {
    position: relative;
    top: auto;
    background: transparent;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    border-bottom: none;
    padding-top: 16px;
    padding-bottom: 12px;
    margin-bottom: 12px;
  }
}

.library-persistent-nav.search-active {
  justify-content: stretch;
}

.library-desktop-title {
  display: none;
}

.library-tabs-pills {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
  scroll-padding-inline: 4px;
  padding-block: 6px;
  margin-block: -6px;
  padding-inline: 2px 12px;
  margin-inline: 0;
  mask-image: linear-gradient(to right, #000 0%, #000 calc(100% - 24px), transparent 100%);
  -webkit-mask-image: linear-gradient(to right, #000 0%, #000 calc(100% - 24px), transparent 100%);
}

.library-tabs-pills::-webkit-scrollbar {
  display: none;
}

.library-persistent-nav :deep(.expandable-search-container) {
  flex-shrink: 0;
  margin-left: auto;
}

@media (min-width: 1024px) {
  .library-tabs-pills {
    display: none;
  }

  .library-desktop-title {
    display: block;
    font-size: 28px;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: var(--c-text-1, #fff);
    margin: 0;
    line-height: 1.2;
    padding-top: 8px;
    margin-bottom: 8px;
  }

  .subtab-header {
    display: none;
  }
}

.library-tab-pill {
  padding: 8px 16px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.08);
  color: var(--c-text-2, rgba(255, 255, 255, 0.75));
  border: 1px solid rgba(255, 255, 255, 0.06);
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
  flex-shrink: 0;
  user-select: none;
}

.library-tab-pill:hover {
  background: rgba(255, 255, 255, 0.14);
  color: var(--c-text-1, #fff);
  transform: translateY(-1px);
}

.library-tab-pill:active {
  transform: scale(0.96);
}

.library-tab-pill.active {
  background: var(--c-accent, #1db954);
  color: #000;
  border-color: var(--c-accent, #1db954);
  font-weight: 700;
  box-shadow: 0 2px 12px rgba(29, 185, 84, 0.35);
}

/* ─── Sections ─── */
.library-section {
  margin-bottom: 30px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  width: 100%;
  padding-right: var(--content-padding, 16px);
  box-sizing: border-box;
}

@media (min-width: 768px) {
  .section-header {
    padding-right: var(--content-padding, 24px);
  }
}

.tracks-overview-section .section-header {
  padding-right: 0;
}

.tracks-overview-section,
.library-content {
  padding-right: var(--content-padding, 16px);
  box-sizing: border-box;
  width: 100%;
}

@media (min-width: 768px) {
  .tracks-overview-section,
  .library-content {
    padding-right: var(--content-padding, 24px);
  }
}

/* ─── Controls Bar Sticky Coordination ─── */
:deep(.library-toolbar) {
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
  :deep(.library-toolbar) {
    padding: 12px 24px;
    margin-inline: -24px;
  }
}

@media (min-width: 1024px) {
  :deep(.library-toolbar) {
    top: 0;
  }
}

.section-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-text-1, #fff);
  letter-spacing: -0.01em;
  margin: 0;
}

.section-title.clickable {
  cursor: pointer;
  transition: color 0.15s ease;
  user-select: none;
}

.section-title.clickable:hover {
  color: var(--c-accent, #1db954);
}

.section-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.scroll-arrow-btn {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.06);
  color: var(--c-text-1, #fff);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
  padding: 0;
  user-select: none;
}

.scroll-arrow-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.12);
  transform: scale(1.06);
}

.scroll-arrow-btn:active:not(:disabled) {
  transform: scale(0.94);
  background: rgba(255, 255, 255, 0.22);
}

.scroll-arrow-btn:disabled {
  opacity: 0.2;
  cursor: default;
  pointer-events: none;
}

@media (max-width: 768px) {
  .scroll-arrow-btn {
    display: none;
  }
}

.section-link {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-3, rgba(255, 255, 255, 0.5));
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  transition: all 0.2s;
}

.section-link:hover {
  color: var(--c-accent, #1db954);
}

/* ─── Horizontal Scroll ─── */
.horizontal-scroll {
  display: flex;
  gap: 14px;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 8px;
  padding-right: var(--content-padding, 16px);
  width: 100%;
  box-sizing: border-box;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
  transition: scrollbar-color 0.2s ease;
}

@media (min-width: 768px) {
  .horizontal-scroll {
    padding-right: var(--content-padding, 24px);
  }
}

.horizontal-scroll:hover {
  scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
}

.horizontal-scroll::-webkit-scrollbar {
  height: 5px;
}

.horizontal-scroll::-webkit-scrollbar-track {
  background: transparent;
}

.horizontal-scroll::-webkit-scrollbar-thumb {
  background: transparent;
  border-radius: 3px;
  transition: background 0.2s ease;
}

.horizontal-scroll:hover::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
}

.horizontal-scroll::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.38);
}

.feed-card {
  flex: 0 0 var(--card-min-width, 136px);
  width: var(--card-min-width, 136px);
  cursor: pointer;
  scroll-snap-align: start;
  user-select: none;
  transition: transform 0.2s;
}

@media (min-width: 768px) {
  .feed-card {
    flex: 0 0 var(--card-min-width, 156px);
    width: var(--card-min-width, 156px);
  }
}

.feed-card:hover {
  transform: translateY(-2px);
}

.feed-card:active {
  transform: scale(0.97);
}

.feed-card-cover {
  width: 100%;
  height: auto;
  aspect-ratio: 1 / 1;
  border-radius: 8px;
  overflow: hidden;
  position: relative;
  background: var(--c-bg-2, #181818);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.4);
  padding: 0;
  border: none;
}

.feed-card-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s;
}

.feed-card:hover .feed-card-cover img {
  transform: scale(1.04);
}

.play-overlay {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--c-accent, #1db954);
  color: #000;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  opacity: 0;
  transform: translateY(6px);
  transition: all 0.2s ease;
  cursor: pointer;
}

.feed-card:hover .play-overlay {
  opacity: 1;
  transform: translateY(0);
}

@media (max-width: 767px) {
  .play-overlay {
    opacity: 0.9;
    transform: translateY(0);
    width: 32px;
    height: 32px;
  }
}

.feed-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-1, #fff);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}

.feed-card-subtitle {
  font-size: 12px;
  color: var(--c-text-3, rgba(255, 255, 255, 0.5));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ─── Artist Specific Card ─── */
.artist-card .artist-cover {
  border-radius: 50%;
}

.artist-initials {
  font-size: 24px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.8);
}

.artist-card .feed-card-title,
.artist-card .feed-card-subtitle {
  text-align: center;
}

/* ─── Tracks Section ─── */
.overview-actions-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.import-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #fff;
  border-radius: 24px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
}

.import-btn:hover {
  background: rgba(255, 85, 0, 0.15);
  border-color: rgba(255, 85, 0, 0.4);
  color: #ff7733;
  transform: translateY(-1px);
}

.import-icon {
  color: #ff5500;
}

.overview-refresh-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: var(--c-text-2, rgba(255, 255, 255, 0.7));
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
  flex-shrink: 0;
}

.overview-refresh-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.25);
  transform: translateY(-1px) scale(1.05);
}

.overview-refresh-btn:active:not(:disabled) {
  transform: scale(0.95);
}

.overview-refresh-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.overview-refresh-btn .spin-anim {
  animation: spin 1s linear infinite;
}

.shuffle-all-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  background: linear-gradient(135deg, var(--c-accent, #1db954) 0%, #158a3e 100%);
  color: #000;
  border: none;
  border-radius: 24px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
  box-shadow: 0 4px 14px rgba(29, 185, 84, 0.35);
}

.shuffle-all-btn:hover:not(:disabled) {
  transform: translateY(-1px) scale(1.02);
  box-shadow: 0 6px 18px rgba(29, 185, 84, 0.45);
}

.shuffle-all-btn:active:not(:disabled) {
  transform: scale(0.97);
}

.shuffle-all-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.overview-tracks-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.view-all-tracks-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 12px;
  margin-top: 10px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  color: var(--c-text-2, rgba(255, 255, 255, 0.8));
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.view-all-tracks-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--c-text-1, #fff);
  border-color: rgba(255, 255, 255, 0.15);
}

.overview-empty-tracks {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  color: var(--c-text-3, rgba(255, 255, 255, 0.4));
  font-size: 14px;
  gap: 8px;
}

/* ─── Skeletons ─── */
.skeleton-thumb {
  width: 56px;
  height: 56px;
  background: rgba(255, 255, 255, 0.08);
  animation: pulse-shimmer 1.5s infinite;
}

.skeleton-line-title {
  height: 12px;
  width: 70%;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  animation: pulse-shimmer 1.5s infinite;
}

.skeleton-line-sub {
  height: 10px;
  width: 40%;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 4px;
  animation: pulse-shimmer 1.5s infinite;
}

.skeleton-section-title {
  width: 120px;
  height: 18px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  animation: pulse-shimmer 1.5s infinite;
}

.feed-card-skeleton {
  flex: 0 0 var(--card-min-width, 136px);
  width: var(--card-min-width, 136px);
}

@media (min-width: 768px) {
  .feed-card-skeleton {
    flex: 0 0 var(--card-min-width, 156px);
    width: var(--card-min-width, 156px);
  }
}

.skeleton-feed-cover {
  width: 100%;
  height: auto;
  aspect-ratio: 1 / 1;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  margin-bottom: 8px;
  animation: pulse-shimmer 1.5s infinite;
}

.skeleton-circle {
  border-radius: 50% !important;
}

.skeleton-feed-title {
  height: 12px;
  width: 80%;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  margin-bottom: 4px;
  animation: pulse-shimmer 1.5s infinite;
}

.skeleton-feed-sub {
  height: 10px;
  width: 50%;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 4px;
  animation: pulse-shimmer 1.5s infinite;
}

.track-item-skeleton {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px;
}

.track-item-skeleton .skeleton-thumb {
  width: 44px;
  height: 44px;
  border-radius: 6px;
  flex-shrink: 0;
}

.track-item-skeleton .skeleton-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

@keyframes pulse-shimmer {
  0% { opacity: 0.5; }
  50% { opacity: 0.9; }
  100% { opacity: 0.5; }
}



/* ─── No channel prompt ─── */
.no-channel-prompt {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  text-align: center;
  padding: 32px 24px;
}

.no-channel-prompt .prompt-icon {
  font-size: 64px;
  margin-bottom: 16px;
}

.no-channel-prompt h2 {
  color: var(--c-text-1);
  font-size: 24px;
  font-weight: 700;
  margin: 0 0 12px 0;
}

.no-channel-prompt p {
  color: var(--c-text-2);
  font-size: 15px;
  line-height: 1.5;
  margin: 0 0 24px 0;
  max-width: 300px;
}

.no-channel-prompt .setup-btn {
  background: linear-gradient(135deg, var(--c-accent) 0%, #00c853 100%);
  border: none;
  border-radius: 24px;
  color: #000;
  font-size: 16px;
  font-weight: 600;
  padding: 14px 32px;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.no-channel-prompt .setup-btn:hover {
  transform: scale(1.02);
  box-shadow: 0 4px 16px rgba(0, 230, 118, 0.3);
}

/* ─── Channel Warning Banner ─── */
.channel-warning-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.35);
  border-radius: 12px;
  padding: 12px 16px;
  margin-bottom: 16px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}

.channel-warning-banner:hover {
  background: rgba(245, 158, 11, 0.18);
  border-color: rgba(245, 158, 11, 0.5);
}

.channel-warning-banner .warning-icon {
  font-size: 22px;
  flex-shrink: 0;
}

.channel-warning-banner .warning-text {
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 2px;
  min-width: 0;
}

.channel-warning-banner .warning-text strong {
  font-size: 14px;
  color: #fbbf24;
  font-weight: 600;
}

.channel-warning-banner .warning-text span {
  font-size: 12px;
  color: var(--c-text-2, #a1a1aa);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.channel-warning-banner .warning-btn {
  background: #f59e0b;
  color: #000;
  border: none;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 12px;
  flex-shrink: 0;
  cursor: pointer;
  transition: transform 0.15s;
}

.channel-warning-banner .warning-btn:hover {
  transform: scale(1.04);
}
</style>
