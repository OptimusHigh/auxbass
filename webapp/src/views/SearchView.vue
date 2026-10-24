<template>
  <div class="search-view">
    <!-- Search Bar -->
    <div class="search-bar-wrapper">
      <SearchBar
        v-model="searchQuery"
        placeholder="Поиск по трекам, тегам #, артистам, плейлистам..."
        :loading="isLoading"
        @input="debouncedSearch"
        @clear="handleClear"
      />
    </div>

    <!-- Filter chips (when query is active OR when a tab filter is active) -->
    <div v-if="searchQuery.trim() || activeFilter !== 'all'" class="search-type-chips">
      <button 
        v-for="chip in filterChips" 
        :key="chip.id"
        class="type-chip"
        :class="{ active: activeFilter === chip.id }"
        @click="setFilter(chip.id)"
      >
        <span>{{ chip.label }}</span>
        <span v-if="getChipBadge(chip.id)" class="chip-badge">{{ getChipBadge(chip.id) }}</span>
      </button>
    </div>

    <!-- Search Results Mode -->
    <div v-if="searchQuery.trim() || activeFilter === 'soundcloud' || activeFilter === 'spotify' || activeFilter === 'youtube'" class="search-results-container">
      <!-- Smart URL Preview Card (when search query is a music URL) -->
      <div v-if="isMusicUrl" class="url-preview-card">
        <!-- Resolving state -->
        <div v-if="isResolvingUrl" class="url-preview-loading">
          <RefreshCw class="spin-icon" :size="20" />
          <span>Распознаю ссылку и загружаю треклист...</span>
        </div>

        <!-- Error state -->
        <div v-else-if="urlResolveError" class="url-preview-error">
          <span>⚠️ {{ urlResolveError }}</span>
        </div>

        <!-- Resolved preview -->
        <div v-else-if="urlPreviewData" class="url-preview-content">
          <div class="url-preview-header">
            <span class="url-provider-badge" :class="urlPreviewData.provider">
              {{ urlPreviewData.provider.toUpperCase() }}
            </span>
            <span class="url-type-badge">
              {{ urlPreviewData.is_album ? '💿 Альбом' : (urlPreviewData.entity_type === 'playlist' ? '📁 Плейлист' : '🎵 Трек') }}
            </span>
          </div>

          <div class="url-preview-body">
            <img 
              v-if="urlPreviewData.cover_url" 
              :src="urlPreviewData.cover_url" 
              class="url-preview-cover" 
              alt="cover" 
            />
            <div class="url-preview-info">
              <h3 class="url-preview-title">{{ urlPreviewData.title }}</h3>
              <p class="url-preview-author">{{ urlPreviewData.author || 'Неизвестный исполнитель' }}</p>
              <div class="url-preview-meta">
                <span>{{ urlPreviewData.track_count }} {{ formatTrackCount(urlPreviewData.track_count) }}</span>
              </div>
            </div>
          </div>

          <!-- Actions -->
          <div class="url-preview-actions">
            <!-- Track action -->
            <template v-if="urlPreviewData.entity_type === 'track'">
              <button class="btn-url-primary" @click="handlePlayUrlTrack">
                <Play :size="16" />
                <span>Слушать</span>
              </button>
              <button class="btn-url-secondary" @click="handleAddUrlTrack">
                <Plus :size="16" />
                <span>В библиотеку</span>
              </button>
            </template>

            <!-- Album / Playlist action -->
            <template v-else>
              <button 
                class="btn-url-primary" 
                :disabled="isStartingUrlImport || (urlImportJob && urlImportJob.status === 'in_progress')"
                @click="handleStartUrlImport"
              >
                <CloudDownload :size="16" />
                <span v-if="urlImportJob && urlImportJob.status === 'in_progress'">
                  Импорт: {{ urlImportJob.processed_tracks }} / {{ urlImportJob.total_tracks }} ({{ urlImportJob.progress_percent }}%)
                </span>
                <span v-else-if="urlImportJob && urlImportJob.status === 'completed'">
                  ✓ Успешно импортирован
                </span>
                <span v-else>
                  {{ urlPreviewData.is_album ? 'Импортировать альбом' : 'Импортировать плейлист' }}
                </span>
              </button>
            </template>
          </div>
        </div>
      </div>

      <!-- Loading initial search results skeleton -->
      <div v-if="isInitialLoading" class="search-skeleton-list">
        <TrackSkeleton v-for="n in 8" :key="n" />
      </div>

      <template v-else>
        <!-- ==================== TAB: ALL ==================== -->
        <SearchTabAll 
          v-if="activeFilter === 'all'"
          :topTracks="topTracks"
          :totalTracksCount="totalTracksCount"
          :soundcloudResults="soundcloudResults"
          :spotifyResults="spotifyResults"
          :youtubeResults="youtubeResults"
          :artistsResults="artistsResults"
          :albumsResults="albumsResults"
          :playlistsResults="playlistsResults"
          :importingTrackUrl="importingTrackUrl"
          @switchFilter="activeFilter = $event"
          @playTrack="handlePlayTrack"
          @likeTrack="handleLikeTrack"
          @menu="(e, type, item) => openMenu(type, item, 'search', e)"
          @downloadTrack="handleDirectDownload"
          @hdNotice="handleHdNotice"
          @addToLibrary="handleAddToLibrary"
          @quickPlaySoundCloud="handleQuickPlaySoundCloud"
          @quickAddSoundCloud="handleQuickAddSoundCloud"
          @quickPlaySpotify="handleQuickPlaySpotify"
          @quickAddSpotify="handleQuickAddSpotify"
          @quickPlayYouTube="handleQuickPlayYouTube"
          @quickAddYouTube="handleQuickAddYouTube"
          @goToArtist="goToArtist"
          @goToAlbum="goToAlbum"
          @goToPlaylist="goToPlaylist"
        />

        <!-- ==================== TAB: TRACKS ==================== -->
        <SearchTabTracks
          v-else-if="activeFilter === 'tracks'"
          :isTracksSearching="isTracksSearching"
          :allTracksList="allTracksList"
          :libraryResults="libraryResults"
          :libraryTotal="libraryTotal"
          :hasMoreLibrary="hasMoreLibrary"
          :isLibraryLoadingMore="isLibraryLoadingMore"
          :friendsResults="friendsResults"
          :friendsTotal="friendsTotal"
          :hasMoreFriends="hasMoreFriends"
          :isFriendsLoadingMore="isFriendsLoadingMore"
          :isFriendsLoading="isFriendsLoading"
          :globalResults="globalResults"
          :globalTotal="globalTotal"
          :hasMoreGlobal="hasMoreGlobal"
          :isGlobalLoadingMore="isGlobalLoadingMore"
          :isGlobalLoading="isGlobalLoading"
          @playTrack="handlePlayTrack"
          @likeTrack="handleLikeTrack"
          @menu="(e, type, item) => openMenu(type, item, 'search', e)"
          @downloadTrack="handleDirectDownload"
          @hdNotice="handleHdNotice"
          @addToLibrary="handleAddToLibrary"
          @loadMoreLibrary="loadMoreLibrary"
          @loadMoreFriends="loadMoreFriends"
          @loadMoreGlobal="loadMoreGlobal"
        />

        <!-- ==================== TAB: ARTISTS ==================== -->
        <SearchTabArtists
          v-else-if="activeFilter === 'artists'"
          :artistsResults="artistsResults"
          :isArtistsSearching="isArtistsSearching"
          @goToArtist="goToArtist"
        />

        <!-- ==================== TAB: SOUNDCLOUD ==================== -->
        <SearchTabSoundCloud
          v-else-if="activeFilter === 'soundcloud'"
          :soundcloudResults="soundcloudResults"
          :searchQuery="searchQuery"
          :scSubTab="scSubTab"
          :isSoundCloudSearching="isSoundCloudSearching"
          :isLoadingMoreSoundCloud="isLoadingMoreSoundCloud"
          :importingTrackUrl="importingTrackUrl"
          :scAccount="scAccount"
          :scLikes="scLikes"
          :filteredScLikes="filteredScLikes"
          :scLikesCursor="scLikesCursor"
          :isScLikesLoading="isScLikesLoading"
          :isLoadingMoreScLikes="isLoadingMoreScLikes"
          :isSearchingDeeperLikes="isSearchingDeeperLikes"
          :isSyncingAllLikes="isSyncingAllLikes"
          :syncJobProgress="syncJobProgress"
          :unimportedLikesCount="unimportedLikesCount"

          :scTracks="scTracks"
          :scTracksCursor="scTracksCursor"
          :isScTracksLoading="isScTracksLoading"
          :isLoadingMoreScTracks="isLoadingMoreScTracks"
          :isSyncingAllTracks="isSyncingAllTracks"
          :unimportedTracksCount="unimportedTracksCount"

          :scPlaylists="scPlaylists"
          :isScPlaylistsLoading="isScPlaylistsLoading"
          :selectedScPlaylist="selectedScPlaylist"
          :scPlaylistTracks="scPlaylistTracks"
          :isScPlaylistTracksLoading="isScPlaylistTracksLoading"
          :isSyncingPlaylist="isSyncingPlaylist"

          @resetToAllSearch="resetToAllSearch"
          @setScSubTab="setScSubTab"
          @loadMoreSoundCloud="loadMoreSoundCloud"
          @quickPlaySoundCloud="handleQuickPlaySoundCloud"
          @quickAddSoundCloud="handleQuickAddSoundCloud"
          @handleSyncAllLikes="handleSyncAllLikes"
          @fetchScLikes="fetchScLikes"
          @clearSearchInput="clearSearchInput"
          @loadAllLikesUntilMatch="loadAllLikesUntilMatch"
          @loadMoreScLikes="loadMoreScLikes"
          @goToSettings="router.push('/settings')"

          @fetchScTracks="fetchScTracks"
          @loadMoreScTracks="loadMoreScTracks"
          @handleSyncAllTracks="handleSyncAllTracks"
          @fetchScPlaylists="fetchScPlaylists"
          @selectScPlaylist="handleSelectScPlaylist"
          @clearSelectedScPlaylist="handleClearSelectedScPlaylist"
          @handleSyncPlaylist="handleSyncPlaylist"
        />

        <!-- ==================== TAB: SPOTIFY ==================== -->
        <SearchTabSpotify
          v-else-if="activeFilter === 'spotify'"
          :spotifyResults="spotifyResults"
          :searchQuery="searchQuery"
          :spSubTab="spSubTab"
          :isSpotifySearching="isSpotifySearching"
          :isLoadingMoreSpotify="isLoadingMoreSpotify"
          :importingTrackUrl="importingTrackUrl"
          @resetToAllSearch="resetToAllSearch"
          @setSpSubTab="setSpSubTab"
          @loadMoreSpotify="loadMoreSpotify"
          @quickPlaySpotify="handleQuickPlaySpotify"
          @quickAddSpotify="handleQuickAddSpotify"
        />

        <!-- ==================== TAB: YOUTUBE ==================== -->
        <SearchTabYouTube
          v-else-if="activeFilter === 'youtube'"
          :youtubeResults="youtubeResults"
          :searchQuery="searchQuery"
          :isYouTubeSearching="isYouTubeSearching"
          :isLoadingMoreYouTube="isLoadingMoreYouTube"
          :importingTrackUrl="importingTrackUrl"
          @resetToAllSearch="resetToAllSearch"
          @loadMoreYouTube="loadMoreYouTube"
          @quickPlayYouTube="handleQuickPlayYouTube"
          @quickAddYouTube="handleQuickAddYouTube"
        />

        <!-- ==================== TAB: ALBUMS ==================== -->
        <SearchTabAlbums
          v-else-if="activeFilter === 'albums'"
          :albumsResults="albumsResults"
          :isAlbumsSearching="isAlbumsSearching"
          @goToAlbum="goToAlbum"
          @menu="(e, type, item) => openMenu(type, item, 'search', e)"
        />

        <!-- ==================== TAB: PLAYLISTS ==================== -->
        <SearchTabPlaylists
          v-else-if="activeFilter === 'playlists'"
          :playlistsResults="playlistsResults"
          :isPlaylistsSearching="isPlaylistsSearching"
          @goToPlaylist="goToPlaylist"
          @menu="(e, type, item) => openMenu(type, item, 'search', e)"
        />

        <!-- Global Empty Results (All categories empty) -->
        <NoResultsBox v-if="noResults && activeFilter === 'all'" :text="`Ничего не найдено по запросу «${searchQuery}»`" hint="Попробуйте ввести другой тег, название трека, исполнителя или плейлиста" />
      </template>
    </div>

    <!-- ==================== EXPLORE MODE: CLEAN SEARCH LANDING ==================== -->
    <SearchExploreTags
      v-else
      :tagScope="tagScope"
      :loadingTags="loadingTags"
      :tags="tags"
      :displayTags="displayTags"
      :recentSearches="recentSearches"
      :scAccount="scAccount"
      :spAccount="spAccount"
      @switchScope="switchScope"
      @tagClick="handleTagClick"
      @recentClick="handleRecentClick"
      @removeRecent="handleRemoveRecent"
      @clearRecent="handleClearRecent"
      @selectSource="setFilter"
    />

  </div>
</template>

<script setup>
import SearchTabAll from '@/components/search/SearchTabAll.vue'
import SearchTabTracks from '@/components/search/SearchTabTracks.vue'
import SearchTabSoundCloud from '@/components/search/SearchTabSoundCloud.vue'
import SearchTabSpotify from '@/components/search/SearchTabSpotify.vue'
import SearchTabYouTube from '@/components/search/SearchTabYouTube.vue'
import SearchTabArtists from '@/components/search/SearchTabArtists.vue'
import SearchTabAlbums from '@/components/search/SearchTabAlbums.vue'
import SearchTabPlaylists from '@/components/search/SearchTabPlaylists.vue'
import SearchExploreTags from '@/components/search/SearchExploreTags.vue'
import { ref, computed, watch, onMounted, onUnmounted, onActivated } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useLibraryStore } from '@/stores/library'
import { usePlayerStore } from '@/stores/player'
import { useUIStore } from '@/stores/ui'
import { useTasksStore } from '@/stores/tasks'
import { useExternalAccountsStore } from '@/stores/externalAccounts'
import { 
  useContextMenu, 
  useDebouncedSearch, 
  useTrackSearch, 
  useTrackActions, 
  useTrackSync 
} from '@/composables'
import api, { tracksApi, artistsApi, albumsApi, playlistsApi, ingestionApi } from '@/api/client'
import SearchBar from '@/components/ui/SearchBar.vue'
import TrackItem from '@/components/TrackItem.vue'
import ExternalTrackItem from '@/components/ExternalTrackItem.vue'
import TrackSkeleton from '@/components/TrackSkeleton.vue'
import NoResultsBox from '@/components/NoResultsBox.vue'
import { getCoverUrl, CoverSize, formatDuration } from '@/utils'
import { 
  Music, 
  Hash, 
  Play, 
  Users, 
  Disc3,
  Globe, 
  Folder, 
  ArrowRight,
  ArrowLeft,
  Plus,
  Heart,
  RefreshCw,
  Check,
  CloudDownload,
  ExternalLink,
  Radio,
  Settings,
  FileSpreadsheet,
  Upload,
  Clock
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const libraryStore = useLibraryStore()
const playerStore = usePlayerStore()
const uiStore = useUIStore()
const tasksStore = useTasksStore()
const externalAccountsStore = useExternalAccountsStore()
const { openMenu } = useContextMenu()
const { handleDirectDownload, handleHdNotice, handleLikeTrack } = useTrackActions()

// Input & Debounce
const { 
  query: searchQuery, 
  debouncedQuery, 
  search: debouncedSearch, 
  clear: clearSearchInput, 
  setQuery 
} = useDebouncedSearch(null, 350)

const activeFilter = ref('all')

// ─── 3-Tier Track Search Composable ───
const {
  searchQuery: trackSearchQuery,
  libraryResults,
  friendsResults,
  globalResults,
  isSearching: isTracksSearching,
  isFriendsLoading,
  isGlobalLoading,
  isFriendsLoadingMore,
  isGlobalLoadingMore,
  libraryPage,
  libraryTotal,
  friendsTotal,
  globalTotal,
  hasMoreLibrary,
  hasMoreFriends,
  hasMoreGlobal,
  isLibraryLoadingMore,
  allResults: allTracksList,
  loadMoreLibrary,
  loadMoreFriends,
  loadMoreGlobal,
  clearSearch: clearTrackSearch,
  search: executeTrackSearch
} = useTrackSearch({ perPage: 40 })

// Auto-sync reactive track models
useTrackSync(libraryResults, { isLibraryList: true })
useTrackSync(friendsResults)
useTrackSync(globalResults)

// ─── Artists, Albums & Playlists Search State ───
const artistsResults = ref([])
const albumsResults = ref([])
const playlistsResults = ref([])
const isArtistsSearching = ref(false)
const isAlbumsSearching = ref(false)
const isPlaylistsSearching = ref(false)

// ─── Smart URL Detection & Preview State ───
const MUSIC_URL_REGEX = /^https?:\/\/(?:(?:m|www|music)\.)?(?:soundcloud\.com|on\.soundcloud\.com|open\.spotify\.com|spotify\.link|youtube\.com|youtu\.be)\/.+/i

const isMusicUrl = computed(() => {
  const q = (searchQuery.value || '').trim()
  return MUSIC_URL_REGEX.test(q)
})

const urlPreviewData = ref(null)
const isResolvingUrl = ref(false)
const urlResolveError = ref(null)
const isStartingUrlImport = ref(false)
const urlImportJob = ref(null)

const resolveMusicUrl = async (url) => {
  isResolvingUrl.value = true
  urlResolveError.value = null
  urlPreviewData.value = null
  urlImportJob.value = null
  try {
    const res = await ingestionApi.preview(url)
    urlPreviewData.value = res.data
  } catch (err) {
    console.error('Failed to preview URL:', err)
    urlResolveError.value = err.response?.data?.detail || 'Не удалось получить информацию по ссылке'
  } finally {
    isResolvingUrl.value = false
  }
}

const handleStartUrlImport = async () => {
  if (!urlPreviewData.value || isStartingUrlImport.value) return
  const data = urlPreviewData.value
  isStartingUrlImport.value = true

  try {
    const isContainer = data.is_album || data.entity_type === 'playlist' || data.entity_type === 'album'
    const res = await ingestionApi.start(
      data.url,
      null,
      null,
      data.title,
      isContainer,
      data.title,
      data.cover_url
    )
    const job = res.data
    urlImportJob.value = job
    if (job) {
      tasksStore.registerJob(job, {
        type: 'import',
        title: `${data.is_album ? 'Альбом' : 'Плейлист'}: ${data.title}`,
      })
      uiStore.toast?.success('Импорт запущен', `Начался импорт «${data.title}» (${data.track_count} треков)`)

      const pollInterval = setInterval(async () => {
        try {
          const jobRes = await ingestionApi.getJob(job.id)
          urlImportJob.value = jobRes.data
          if (['completed', 'failed', 'cancelled'].includes(jobRes.data.status)) {
            clearInterval(pollInterval)
            isStartingUrlImport.value = false
            if (jobRes.data.status === 'completed') {
              libraryStore.fetchTracks({ refresh: true })
              uiStore.toast?.success('Готово', `«${data.title}» успешно добавлен в библиотеку!`)
            }
          }
        } catch {
          clearInterval(pollInterval)
          isStartingUrlImport.value = false
        }
      }, 2000)
    }
  } catch (err) {
    console.error('Failed to start import:', err)
    uiStore.toast?.error('Ошибка', err.response?.data?.detail || 'Не удалось запустить импорт')
    isStartingUrlImport.value = false
  }
}

const handlePlayUrlTrack = async () => {
  if (!urlPreviewData.value) return
  const data = urlPreviewData.value
  try {
    const res = await ingestionApi.quickImport({
      url: data.url,
      title: data.title,
      artist: data.author,
      duration: data.tracks?.[0]?.duration,
      cover_url: data.cover_url,
      preview_only: true,
    })
    const track = res.data?.track
    if (track) {
      playerStore.playTrack(track, [track], 0)
    }
  } catch (err) {
    uiStore.toast?.error('Ошибка', 'Не удалось воспроизвести трек')
  }
}

const handleAddUrlTrack = async () => {
  if (!urlPreviewData.value) return
  const data = urlPreviewData.value
  try {
    await ingestionApi.quickImport({
      url: data.url,
      title: data.title,
      artist: data.author,
      duration: data.tracks?.[0]?.duration,
      cover_url: data.cover_url,
      add_to_library: true,
      preview_only: false,
    })
    libraryStore.fetchTracks({ refresh: true })
    uiStore.toast?.success('Добавлено', `Трек «${data.title}» добавлен в медиатеку`)
  } catch (err) {
    uiStore.toast?.error('Ошибка', 'Не удалось добавить трек')
  }
}

// ─── SoundCloud External Search & Likes State ───
const soundcloudResults = ref([])
const isSoundCloudSearching = ref(false)
const isLoadingMoreSoundCloud = ref(false)
const importingTrackUrl = ref(null)

const scSubTab = ref('search') // 'search' | 'likes'
const scAccount = computed(() => externalAccountsStore.scAccount)
const isScAccountLoading = computed(() => externalAccountsStore.loadingSc)
const scLikes = ref([])
const isScLikesLoading = ref(false)
const scLikesCursor = ref(null)
const isLoadingMoreScLikes = ref(false)
const isSyncingAllLikes = ref(false)
const syncJobProgress = ref(null)

const fetchScAccountForSearch = async (force = false) => {
  try {
    await externalAccountsStore.fetchSoundCloud(force)
  } catch (e) {
    console.error('Failed to get SC account:', e)
  }
}

const fetchScLikes = async (reset = true) => {
  if (isScLikesLoading.value) return
  isScLikesLoading.value = true
  try {
    if (reset) {
      scLikesCursor.value = null
    }
    const res = await ingestionApi.getSoundCloudLikes({ limit: 40 })
    scLikes.value = res.data?.items || []
    scLikesCursor.value = res.data?.next_cursor || null
    if (res.data?.account) {
      externalAccountsStore.setSoundCloudAccount(res.data.account)
    }
  } catch (e) {
    console.error('Failed to fetch SC likes:', e)
    if (e.response?.status !== 404) {
      uiStore.toast?.error('Ошибка', e.response?.data?.detail || 'Не удалось загрузить лайки')
    }
  } finally {
    isScLikesLoading.value = false
  }
}

const loadMoreScLikes = async () => {
  if (!scLikesCursor.value || isLoadingMoreScLikes.value) return
  isLoadingMoreScLikes.value = true
  try {
    const res = await ingestionApi.getSoundCloudLikes({ cursor: scLikesCursor.value, limit: 40 })
    const more = res.data?.items || []
    scLikes.value = [...scLikes.value, ...more]
    scLikesCursor.value = res.data?.next_cursor || null
  } catch (e) {
    console.error('Failed to load more likes:', e)
  } finally {
    isLoadingMoreScLikes.value = false
  }
}

const switchToLikesTab = () => {
  setScSubTab('likes')
}

// ─── SoundCloud User Tracks State ───
const scTracks = ref([])
const isScTracksLoading = ref(false)
const scTracksCursor = ref(null)
const isLoadingMoreScTracks = ref(false)
const isSyncingAllTracks = ref(false)

const unimportedTracksCount = computed(() => {
  return scTracks.value.filter(t => !t.in_library).length
})

const fetchScTracks = async (reset = true) => {
  if (isScTracksLoading.value) return
  isScTracksLoading.value = true
  try {
    if (reset) {
      scTracksCursor.value = null
    }
    const res = await ingestionApi.getSoundCloudTracks({ limit: 40 })
    scTracks.value = res.data?.items || []
    scTracksCursor.value = res.data?.next_cursor || null
    if (res.data?.account) {
      externalAccountsStore.setSoundCloudAccount(res.data.account)
    }
  } catch (e) {
    console.error('Failed to fetch SC tracks:', e)
    if (e.response?.status !== 404) {
      uiStore.toast?.error('Ошибка', e.response?.data?.detail || 'Не удалось загрузить треки')
    }
  } finally {
    isScTracksLoading.value = false
  }
}

const loadMoreScTracks = async () => {
  if (!scTracksCursor.value || isLoadingMoreScTracks.value) return
  isLoadingMoreScTracks.value = true
  try {
    const res = await ingestionApi.getSoundCloudTracks({ cursor: scTracksCursor.value, limit: 40 })
    const more = res.data?.items || []
    scTracks.value = [...scTracks.value, ...more]
    scTracksCursor.value = res.data?.next_cursor || null
  } catch (e) {
    console.error('Failed to load more SC tracks:', e)
  } finally {
    isLoadingMoreScTracks.value = false
  }
}

const handleSyncAllTracks = async () => {
  if (isSyncingAllTracks.value) return
  const toImport = scTracks.value.filter(t => !t.in_library)
  if (toImport.length === 0) {
    uiStore.toast?.info('Синхронизация', 'Все ваши авторские треки уже в медиатеке!')
    return
  }

  isSyncingAllTracks.value = true
  try {
    const urls = toImport.map(t => t.url)
    const tracks = toImport.map(t => ({
      url: t.url,
      title: t.title,
      artist: t.artist,
      duration: t.duration,
      cover_url: t.cover_url,
      genre: t.genre,
      tags: t.tags,
      extra: {
        is_soundcloud: true,
        genre: t.genre,
        tags: t.tags,
      }
    }))
    const res = await ingestionApi.start(urls[0], urls, tracks, `SoundCloud Tracks (${toImport.length})`)
    const jobId = res.data?.id
    if (res.data) {
      tasksStore.registerJob(res.data, {
        type: 'import',
        title: `SoundCloud Tracks (${toImport.length})`,
      })
    }
    uiStore.toast?.success('Синхронизация', `Запущен импорт ${urls.length} треков в медиатеку и Telegram-канал`)

    const pollInterval = setInterval(async () => {
      try {
        const jobRes = await ingestionApi.getJob(jobId)
        const job = jobRes.data
        syncJobProgress.value = job
        if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
          clearInterval(pollInterval)
          isSyncingAllTracks.value = false
          syncJobProgress.value = null
          await fetchScTracks(true)
          libraryStore.fetchTracks({ refresh: true })
          if (job.status === 'completed') {
            uiStore.toast?.success('Готово', `Синхронизировано треков: ${job.processed_tracks}`)
          }
        }
      } catch (err) {
        clearInterval(pollInterval)
        isSyncingAllTracks.value = false
        syncJobProgress.value = null
      }
    }, 2000)
  } catch (e) {
    console.error('Failed to start sync job:', e)
    uiStore.toast?.error('Ошибка', 'Не удалось запустить синхронизацию')
    isSyncingAllTracks.value = false
  }
}

// ─── SoundCloud Playlists State ───
const scPlaylists = ref([])
const isScPlaylistsLoading = ref(false)
const selectedScPlaylist = ref(null)
const scPlaylistTracks = ref([])
const isScPlaylistTracksLoading = ref(false)
const isSyncingPlaylist = ref(false)

const fetchScPlaylists = async (type = 'all') => {
  if (isScPlaylistsLoading.value) return
  isScPlaylistsLoading.value = true
  try {
    const res = await ingestionApi.getSoundCloudPlaylists({ playlist_type: type })
    scPlaylists.value = res.data?.items || []
    if (res.data?.account) {
      externalAccountsStore.setSoundCloudAccount(res.data.account)
    }
  } catch (e) {
    console.error('Failed to fetch SC playlists:', e)
    if (e.response?.status !== 404) {
      uiStore.toast?.error('Ошибка', e.response?.data?.detail || 'Не удалось загрузить плейлисты')
    }
  } finally {
    isScPlaylistsLoading.value = false
  }
}

const handleSelectScPlaylist = async (pl) => {
  selectedScPlaylist.value = pl
  scPlaylistTracks.value = []
  isScPlaylistTracksLoading.value = true
  try {
    const res = await ingestionApi.getSoundCloudPlaylistTracks(pl.id)
    scPlaylistTracks.value = res.data?.tracks || []
  } catch (e) {
    console.error('Failed to fetch playlist tracks:', e)
    uiStore.toast?.error('Ошибка', 'Не удалось загрузить треки плейлиста')
  } finally {
    isScPlaylistTracksLoading.value = false
  }
}

const handleClearSelectedScPlaylist = () => {
  selectedScPlaylist.value = null
  scPlaylistTracks.value = []
}

const handleSyncPlaylist = async (playlist) => {
  if (isSyncingPlaylist.value) return

  // If tracks are not loaded yet, fetch them first
  let tracksToSync = scPlaylistTracks.value
  if (!tracksToSync || tracksToSync.length === 0) {
    try {
      const res = await ingestionApi.getSoundCloudPlaylistTracks(playlist.id)
      tracksToSync = res.data?.tracks || []
      scPlaylistTracks.value = tracksToSync
    } catch (e) {
      uiStore.toast?.error('Ошибка', 'Не удалось получить список треков для синхронизации')
      return
    }
  }

  if (tracksToSync.length === 0) {
    uiStore.toast?.info('Синхронизация', 'В этом плейлисте нет треков для синхронизации')
    return
  }

  isSyncingPlaylist.value = true
  try {
    const urls = tracksToSync.map(t => t.url)
    const tracksPayload = tracksToSync.map(t => ({
      url: t.url,
      title: t.title,
      artist: t.artist,
      duration: t.duration,
      cover_url: t.cover_url || playlist.artwork_url,
      genre: t.genre,
      tags: t.tags,
      extra: {
        is_soundcloud: true,
        playlist_title: playlist.title,
      }
    }))

    const res = await ingestionApi.start(
      playlist.permalink_url || urls[0],
      urls,
      tracksPayload,
      playlist.title,
      false,                // create_playlist = false (в библиотеку без создания плейлиста)
      null,                 // playlist_name
      playlist.artwork_url  // cover_url
    )
    const jobId = res.data?.id
    if (res.data) {
      tasksStore.registerJob(res.data, {
        type: 'import',
        title: `Плейлист: ${playlist.title}`,
      })
    }
    uiStore.toast?.success('Синхронизация', `Запущен импорт плейлиста «${playlist.title}» (${urls.length} треков)`)

    const pollInterval = setInterval(async () => {
      try {
        const jobRes = await ingestionApi.getJob(jobId)
        const job = jobRes.data
        syncJobProgress.value = job
        if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
          clearInterval(pollInterval)
          isSyncingPlaylist.value = false
          syncJobProgress.value = null
          if (selectedScPlaylist.value?.id === playlist.id) {
            await handleSelectScPlaylist(playlist)
          }
          libraryStore.fetchTracks({ refresh: true })
          if (job.status === 'completed') {
            uiStore.toast?.success('Готово', `Плейлист «${playlist.title}» успешно синхронизирован!`)
          }
        }
      } catch (err) {
        clearInterval(pollInterval)
        isSyncingPlaylist.value = false
        syncJobProgress.value = null
      }
    }, 2000)
  } catch (e) {
    console.error('Failed to sync playlist:', e)
    uiStore.toast?.error('Ошибка', 'Не удалось запустить синхронизацию плейлиста')
    isSyncingPlaylist.value = false
  }
}

const isSearchingDeeperLikes = ref(false)

function isCloseMatch(term, text) {
  if (!term || !text) return false
  if (text.includes(term)) return true

  // Word-by-word comparison
  const words = text.split(/[\s\-_|,./()\[\]]+/).filter(w => w.length >= 4)
  for (const word of words) {
    if (word.includes(term) || term.includes(word)) return true
    if (term.length >= 5 && Math.abs(term.length - word.length) <= 2) {
      let prev = []
      for (let j = 0; j <= word.length; j++) prev[j] = j
      for (let i = 1; i <= term.length; i++) {
        const curr = [i]
        for (let j = 1; j <= word.length; j++) {
          const cost = term[i - 1] === word[j - 1] ? 0 : 1
          curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        }
        prev = curr
      }
      const maxDist = term.length >= 8 ? 2 : 1
      if (prev[word.length] <= maxDist) return true
    }
  }
  return false
}

const filteredScLikes = computed(() => {
  const q = searchQuery.value?.trim().toLowerCase()
  if (!q) return scLikes.value

  const terms = q.split(/\s+/).filter(Boolean)

  return scLikes.value.filter(track => {
    const title = (track.title || '').toLowerCase()
    const artist = (track.artist || '').toLowerCase()
    const album = (track.album || '').toLowerCase()
    const combined = `${artist} ${title} ${album}`

    return terms.every(term => isCloseMatch(term, combined))
  })
})

const unimportedLikesCount = computed(() => {
  const targetList = searchQuery.value?.trim() ? filteredScLikes.value : scLikes.value
  return targetList.filter(t => !t.in_library).length
})

const loadAllLikesUntilMatch = async () => {
  if (isSearchingDeeperLikes.value || !scLikesCursor.value) return
  isSearchingDeeperLikes.value = true
  try {
    let pages = 0
    while (scLikesCursor.value && pages < 6) {
      pages++
      const res = await ingestionApi.getSoundCloudLikes({ cursor: scLikesCursor.value, limit: 50 })
      const more = res.data?.items || []
      if (more.length === 0) break
      scLikes.value = [...scLikes.value, ...more]
      scLikesCursor.value = res.data?.next_cursor || null
      if (filteredScLikes.value.length > 0 || !scLikesCursor.value) {
        break
      }
    }
    if (filteredScLikes.value.length > 0) {
      uiStore.toast?.success('Найдено', `Найдено треков в лайках: ${filteredScLikes.value.length}`)
    } else if (!scLikesCursor.value) {
      uiStore.toast?.info('Поиск завершён', 'Проверены все доступные лайки вашего профиля')
    }
  } catch (e) {
    console.error('Failed to load deeper SoundCloud likes:', e)
    uiStore.toast?.error('Ошибка', 'Не удалось загрузить следующие страницы лайков')
  } finally {
    isSearchingDeeperLikes.value = false
  }
}

const handleSyncAllLikes = async () => {
  if (isSyncingAllLikes.value) return
  const targetList = searchQuery.value?.trim() ? filteredScLikes.value : scLikes.value
  const toImport = targetList.filter(t => !t.in_library)
  if (toImport.length === 0) {
    uiStore.toast?.info('Синхронизация', 'Все треки из лайков уже в вашей медиатеке!')
    return
  }

  isSyncingAllLikes.value = true
  try {
    const urls = toImport.map(t => t.url)
    const tracks = toImport.map(t => ({
      url: t.url,
      title: t.title,
      artist: t.artist,
      duration: t.duration,
      cover_url: t.cover_url,
      genre: t.genre,
      tags: t.tags,
      extra: {
        is_soundcloud: true,
        liked_at: t.liked_at,
        genre: t.genre,
        tags: t.tags,
      }
    }))
    const res = await ingestionApi.start(urls[0], urls, tracks, `SoundCloud Likes (${toImport.length})`)
    const jobId = res.data?.id
    if (res.data) {
      tasksStore.registerJob(res.data, {
        type: 'import',
        title: `SoundCloud Likes (${toImport.length})`,
      })
    }
    uiStore.toast?.success('Синхронизация', `Запущен импорт ${urls.length} треков в медиатеку и Telegram-канал`)

    const pollInterval = setInterval(async () => {
      try {
        const jobRes = await ingestionApi.getJob(jobId)
        const job = jobRes.data
        syncJobProgress.value = job
        if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
          clearInterval(pollInterval)
          isSyncingAllLikes.value = false
          syncJobProgress.value = null
          await fetchScLikes(true)
          libraryStore.fetchTracks({ refresh: true })
          if (job.status === 'completed') {
            uiStore.toast?.success('Готово', `Синхронизировано треков: ${job.processed_tracks}`)
          }
        }
      } catch (err) {
        clearInterval(pollInterval)
        isSyncingAllLikes.value = false
        syncJobProgress.value = null
      }
    }, 2000)
  } catch (e) {
    console.error('Failed to start sync job:', e)
    uiStore.toast?.error('Ошибка', 'Не удалось запустить синхронизацию')
    isSyncingAllLikes.value = false
  }
}

let scAbortController = null
let scSearchSeq = 0

const searchSoundCloud = async (query, limit = 30) => {
  const cleanQ = query.replace(/^#/, '').trim()
  if (scAbortController) {
    scAbortController.abort()
  }
  if (!cleanQ || cleanQ.length < 2) {
    scSearchSeq++
    soundcloudResults.value = []
    isSoundCloudSearching.value = false
    return
  }

  scAbortController = new AbortController()
  const currentSignal = scAbortController.signal
  const currentSeq = ++scSearchSeq

  isSoundCloudSearching.value = true
  try {
    const res = await ingestionApi.search(cleanQ, 'soundcloud', limit, { signal: currentSignal })
    if (currentSeq !== scSearchSeq || currentSignal.aborted) return
    soundcloudResults.value = res.data || []
  } catch (e) {
    if (e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError') return
    console.error('Failed to search SoundCloud:', e)
    if (currentSeq === scSearchSeq) {
      soundcloudResults.value = []
    }
  } finally {
    if (currentSeq === scSearchSeq) {
      isSoundCloudSearching.value = false
    }
  }
}

const loadMoreSoundCloud = async () => {
  const cleanQ = searchQuery.value.replace(/^#/, '').trim()
  if (!cleanQ || isLoadingMoreSoundCloud.value) return
  isLoadingMoreSoundCloud.value = true
  try {
    const targetLimit = Math.min(60, soundcloudResults.value.length + 30)
    const res = await ingestionApi.search(cleanQ, 'soundcloud', targetLimit)
    soundcloudResults.value = res.data || []
  } catch (e) {
    console.error('Failed to load more from SoundCloud:', e)
  } finally {
    isLoadingMoreSoundCloud.value = false
  }
}

const handleQuickPlaySoundCloud = async (scTrack) => {
  if (importingTrackUrl.value) return
  importingTrackUrl.value = scTrack.url

  try {
    const res = await ingestionApi.quickImport({
      url: scTrack.url,
      title: scTrack.title,
      artist: scTrack.artist,
      duration: scTrack.duration,
      cover_url: scTrack.cover_url,
      genre: scTrack.genre,
      tags: scTrack.tags,
      add_to_library: false,
      preview_only: true,
    })

    const track = res.data?.track
    if (track) {
      if (track.in_library) {
        scTrack.in_library = true
      }
      scTrack.is_chunk = track.is_chunk
      scTrack.already_in_tg = !track.is_chunk
      scTrack.track_id = track.id
      playerStore.playTrack(track, [track], 0)
    }
  } catch (e) {
    console.error('Failed to quick play track:', e)
    const errorMsg = e.response?.data?.detail || 'Не удалось загрузить трек'
    uiStore.toast?.error('Ошибка воспроизведения', errorMsg)
  } finally {
    importingTrackUrl.value = null
  }
}

const isTrackInLibrary = (item) => {
  if (!item) return false
  return item.in_library || tasksStore.isTrackCompleted(item.url)
}

const handleQuickAddSoundCloud = (scTrack) => {
  tasksStore.enqueueTrack(scTrack, 'soundcloud')
}

// ─── Spotify External Search & Likes State ───
const spotifyResults = ref([])
const isSpotifySearching = ref(false)
const isLoadingMoreSpotify = ref(false)

const spSubTab = ref('search') // 'search' | 'exportify'
const spAccount = computed(() => externalAccountsStore.spAccount)
const isSpAccountLoading = computed(() => externalAccountsStore.loadingSp)

const fetchSpAccountForSearch = async (force = false) => {
  try {
    await externalAccountsStore.fetchSpotify(force)
  } catch (e) {
    console.error('Failed to get Spotify account:', e)
  }
}

const fetchSpLikes = async (reset = true) => {
  if (isSpLikesLoading.value) return
  isSpLikesLoading.value = true
  try {
    if (reset) {
      spLikesCursor.value = null
    }
    const res = await ingestionApi.getSpotifyLikes({ limit: 40 })
    spLikes.value = res.data?.items || []
    spLikesCursor.value = res.data?.next_cursor || null
    if (res.data?.account) {
      externalAccountsStore.setSpotifyAccount(res.data.account)
    }
  } catch (e) {
    console.error('Failed to fetch Spotify likes:', e)
    if (e.response?.status !== 404) {
      uiStore.toast?.error('Ошибка', e.response?.data?.detail || 'Не удалось загрузить лайки')
    }
  } finally {
    isSpLikesLoading.value = false
  }
}

const loadMoreSpLikes = async () => {
  if (!spLikesCursor.value || isLoadingMoreSpLikes.value) return
  isLoadingMoreSpLikes.value = true
  try {
    const res = await ingestionApi.getSpotifyLikes({ cursor: spLikesCursor.value, limit: 40 })
    const more = res.data?.items || []
    spLikes.value = [...spLikes.value, ...more]
    spLikesCursor.value = res.data?.next_cursor || null
  } catch (e) {
    console.error('Failed to load more Spotify likes:', e)
  } finally {
    isLoadingMoreSpLikes.value = false
  }
}

const switchToSpotifyLikesTab = () => {
  spSubTab.value = 'likes'
  if (spLikes.value.length === 0 && spAccount.value?.connected) {
    fetchSpLikes(true)
  }
}

const unimportedSpLikesCount = computed(() => {
  return spLikes.value.filter(t => !t.in_library).length
})

const handleSyncAllSpLikes = async () => {
  if (isSyncingAllSpLikes.value) return
  const toImport = spLikes.value.filter(t => !t.in_library)
  if (toImport.length === 0) {
    uiStore.toast?.info('Синхронизация', 'Все треки из лайков уже в вашей медиатеке!')
    return
  }

  isSyncingAllSpLikes.value = true
  try {
    const urls = toImport.map(t => t.url)
    const res = await ingestionApi.start(urls[0], urls)
    const jobId = res.data?.id
    if (res.data) {
      tasksStore.registerJob(res.data, {
        type: 'exportify',
        title: `Spotify Likes (${toImport.length})`,
      })
    }
    uiStore.toast?.success('Синхронизация', `Запущен импорт ${urls.length} треков Spotify в медиатеку и Telegram-канал`)

    const pollInterval = setInterval(async () => {
      try {
        const jobRes = await ingestionApi.getJob(jobId)
        const job = jobRes.data
        syncSpJobProgress.value = job
        if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
          clearInterval(pollInterval)
          isSyncingAllSpLikes.value = false
          syncSpJobProgress.value = null
          await fetchSpLikes(true)
          libraryStore.fetchTracks({ refresh: true })
          if (job.status === 'completed') {
            uiStore.toast?.success('Готово', `Синхронизировано со Spotify: ${job.processed_tracks}`)
          }
        }
      } catch (err) {
        clearInterval(pollInterval)
        isSyncingAllSpLikes.value = false
        syncSpJobProgress.value = null
      }
    }, 2000)
  } catch (e) {
    console.error('Failed to start Spotify sync job:', e)
    uiStore.toast?.error('Ошибка', 'Не удалось запустить синхронизацию')
    isSyncingAllSpLikes.value = false
  }
}

let spAbortController = null
let spSearchSeq = 0

const searchSpotify = async (query, limit = 30) => {
  const cleanQ = query.replace(/^#/, '').trim()
  if (spAbortController) {
    spAbortController.abort()
  }
  if (!cleanQ || cleanQ.length < 2) {
    spSearchSeq++
    spotifyResults.value = []
    isSpotifySearching.value = false
    return
  }

  spAbortController = new AbortController()
  const currentSignal = spAbortController.signal
  const currentSeq = ++spSearchSeq

  isSpotifySearching.value = true
  try {
    const res = await ingestionApi.search(cleanQ, 'spotify', limit, { signal: currentSignal })
    if (currentSeq !== spSearchSeq || currentSignal.aborted) return
    spotifyResults.value = res.data || []
  } catch (e) {
    if (e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError') return
    console.error('Failed to search Spotify:', e)
    if (currentSeq === spSearchSeq) {
      spotifyResults.value = []
    }
  } finally {
    if (currentSeq === spSearchSeq) {
      isSpotifySearching.value = false
    }
  }
}

const loadMoreSpotify = async () => {
  const cleanQ = searchQuery.value.replace(/^#/, '').trim()
  if (!cleanQ || isLoadingMoreSpotify.value) return
  isLoadingMoreSpotify.value = true
  try {
    const targetLimit = Math.min(60, spotifyResults.value.length + 30)
    const res = await ingestionApi.search(cleanQ, 'spotify', targetLimit)
    spotifyResults.value = res.data || []
  } catch (e) {
    console.error('Failed to load more from Spotify:', e)
  } finally {
    isLoadingMoreSpotify.value = false
  }
}

const handleQuickPlaySpotify = async (spTrack) => {
  if (importingTrackUrl.value) return
  importingTrackUrl.value = spTrack.url

  try {
    const res = await ingestionApi.quickImport({
      url: spTrack.url,
      title: spTrack.title,
      artist: spTrack.artist,
      duration: spTrack.duration,
      cover_url: spTrack.cover_url,
      add_to_library: false,
      preview_only: true,
    })

    const track = res.data?.track
    if (track) {
      if (track.in_library) {
        spTrack.in_library = true
      }
      spTrack.is_chunk = track.is_chunk
      spTrack.already_in_tg = !track.is_chunk
      spTrack.track_id = track.id
      playerStore.playTrack(track, [track], 0)
    }
  } catch (e) {
    console.error('Failed to quick play Spotify track:', e)
    const errorMsg = e.response?.data?.detail || 'Не удалось загрузить трек'
    uiStore.toast?.error('Ошибка воспроизведения', errorMsg)
  } finally {
    importingTrackUrl.value = null
  }
}

const handleQuickAddSpotify = (spTrack) => {
  tasksStore.enqueueTrack(spTrack, 'spotify')
}

// ─── YouTube Music External Search State ───
const youtubeResults = ref([])
const isYouTubeSearching = ref(false)
const isLoadingMoreYouTube = ref(false)

let ytAbortController = null
let ytSearchSeq = 0

const searchYouTube = async (query, limit = 30) => {
  const cleanQ = query.replace(/^#/, '').trim()
  if (ytAbortController) {
    ytAbortController.abort()
  }
  if (!cleanQ || cleanQ.length < 2) {
    ytSearchSeq++
    youtubeResults.value = []
    isYouTubeSearching.value = false
    return
  }

  ytAbortController = new AbortController()
  const currentSignal = ytAbortController.signal
  const currentSeq = ++ytSearchSeq

  isYouTubeSearching.value = true
  try {
    const res = await ingestionApi.search(cleanQ, 'youtube', limit, { signal: currentSignal })
    if (currentSeq !== ytSearchSeq || currentSignal.aborted) return
    youtubeResults.value = res.data || []
  } catch (e) {
    if (e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError') return
    console.error('Failed to search YouTube:', e)
    if (currentSeq === ytSearchSeq) {
      youtubeResults.value = []
    }
  } finally {
    if (currentSeq === ytSearchSeq) {
      isYouTubeSearching.value = false
    }
  }
}

const loadMoreYouTube = async () => {
  const cleanQ = searchQuery.value.replace(/^#/, '').trim()
  if (!cleanQ || isLoadingMoreYouTube.value) return
  isLoadingMoreYouTube.value = true
  try {
    const targetLimit = Math.min(60, youtubeResults.value.length + 30)
    const res = await ingestionApi.search(cleanQ, 'youtube', targetLimit)
    youtubeResults.value = res.data || []
  } catch (e) {
    console.error('Failed to load more from YouTube:', e)
  } finally {
    isLoadingMoreYouTube.value = false
  }
}

const handleQuickPlayYouTube = async (ytTrack) => {
  if (importingTrackUrl.value) return
  importingTrackUrl.value = ytTrack.url

  try {
    const res = await ingestionApi.quickImport({
      url: ytTrack.url,
      title: ytTrack.title,
      artist: ytTrack.artist,
      duration: ytTrack.duration,
      cover_url: ytTrack.cover_url,
      add_to_library: false,
      preview_only: true,
    })

    const track = res.data?.track
    if (track) {
      if (track.in_library) {
        ytTrack.in_library = true
      }
      ytTrack.is_chunk = track.is_chunk
      ytTrack.already_in_tg = !track.is_chunk
      ytTrack.track_id = track.id
      playerStore.playTrack(track, [track], 0)
    }
  } catch (e) {
    console.error('Failed to quick play YouTube track:', e)
    const errorMsg = e.response?.data?.detail || 'Не удалось загрузить трек'
    uiStore.toast?.error('Ошибка воспроизведения', errorMsg)
  } finally {
    importingTrackUrl.value = null
  }
}

const handleQuickAddYouTube = (ytTrack) => {
  tasksStore.enqueueTrack(ytTrack, 'youtube')
}

// Local database search loading state (fast, < 150ms)
const isLocalSearching = computed(() => {
  return isTracksSearching.value || isArtistsSearching.value || isAlbumsSearching.value || isPlaylistsSearching.value
})

// Combined search loading state:
// When viewing SoundCloud or Spotify or YouTube tab, reflect external loading state.
// Otherwise reflect local search state so search input spinner never freezes for 3-5s.
const isLoading = computed(() => {
  if (isResolvingUrl.value) return true
  if (activeFilter.value === 'soundcloud') return isSoundCloudSearching.value
  if (activeFilter.value === 'spotify') return isSpotifySearching.value
  if (activeFilter.value === 'youtube') return isYouTubeSearching.value
  return isLocalSearching.value
})

// Skeletons are only shown while local database search is executing and no results are shown yet
const isInitialLoading = computed(() => {
  if (isMusicUrl.value) return false
  return isLocalSearching.value && 
         allTracksList.value.length === 0 && 
         artistsResults.value.length === 0 && 
         albumsResults.value.length === 0 && 
         playlistsResults.value.length === 0
})

// Dynamic Tags state
const tags = ref([])
const loadingTags = ref(false)
const tagScope = ref('library')

const filterChips = [
  { id: 'all', label: 'Все' },
  { id: 'tracks', label: 'Треки' },
  { id: 'soundcloud', label: 'SoundCloud' },
  { id: 'spotify', label: 'Spotify' },
  { id: 'youtube', label: 'YouTube Music' },
  { id: 'artists', label: 'Артисты' },
  { id: 'albums', label: 'Альбомы' },
  { id: 'playlists', label: 'Плейлисты' },
]

const totalTracksCount = computed(() => {
  const sum = (libraryTotal.value || 0) + (friendsTotal.value || 0) + (globalTotal.value || 0)
  return sum || allTracksList.value.length
})

const getChipBadge = (chipId) => {
  if (chipId === 'tracks') {
    return totalTracksCount.value > 0 ? totalTracksCount.value : null
  }
  if (chipId === 'soundcloud') {
    if (scSubTab.value === 'likes') {
      if (searchQuery.value?.trim()) {
        return filteredScLikes.value.length
      }
      return scLikes.value.length > 0 ? scLikes.value.length : (scAccount.value?.likes_count || null)
    }
    if (scSubTab.value === 'tracks') {
      return scTracks.value.length > 0 ? scTracks.value.length : (scAccount.value?.tracks_count || null)
    }
    if (scSubTab.value === 'playlists') {
      return scPlaylists.value.length > 0 ? scPlaylists.value.length : null
    }
    return soundcloudResults.value.length > 0 ? soundcloudResults.value.length : (scAccount.value?.connected ? '★' : null)
  }
  if (chipId === 'spotify') {
    if (spSubTab.value === 'likes' && spLikes.value.length > 0) {
      return spLikes.value.length
    }
    return spotifyResults.value.length > 0 ? spotifyResults.value.length : (spAccount.value?.connected ? '★' : null)
  }
  if (chipId === 'youtube') {
    return youtubeResults.value.length > 0 ? youtubeResults.value.length : null
  }
  if (chipId === 'artists') {
    return artistsResults.value.length > 0 ? artistsResults.value.length : null
  }
  if (chipId === 'albums') {
    return albumsResults.value.length > 0 ? albumsResults.value.length : null
  }
  if (chipId === 'playlists') {
    return playlistsResults.value.length > 0 ? playlistsResults.value.length : null
  }
  return null
}

// Top tracks preview for the "All" tab (max 8 items across library, friends, global)
const topTracks = computed(() => {
  const list = []
  const seen = new Set()
  for (const group of [libraryResults.value, friendsResults.value, globalResults.value]) {
    for (const t of group) {
      if (!seen.has(t.id)) {
        seen.add(t.id)
        list.push(t)
        if (list.length >= 8) return list
      }
    }
  }
  return list
})

const noResults = computed(() => {
  if (isLoading.value || isFriendsLoading.value || isGlobalLoading.value) return false
  if (!searchQuery.value?.trim()) return false
  if (activeFilter.value === 'all') {
    return topTracks.value.length === 0 && 
           artistsResults.value.length === 0 && 
           albumsResults.value.length === 0 && 
           playlistsResults.value.length === 0 && 
           soundcloudResults.value.length === 0 && 
           spotifyResults.value.length === 0 &&
           youtubeResults.value.length === 0
  }
  return false
})

// Fallback presets if tags API returns empty
const fallbackPresets = [
  { name: 'phonk', track_count: 0 },
  { name: 'dnb', track_count: 0 },
  { name: 'lo-fi', track_count: 0 },
  { name: 'rock', track_count: 0 },
  { name: 'ambient', track_count: 0 },
  { name: 'hiphop', track_count: 0 },
  { name: 'synthwave', track_count: 0 },
  { name: 'chill', track_count: 0 },
  { name: 'nightdrive', track_count: 0 },
  { name: 'indie', track_count: 0 },
  { name: 'electronic', track_count: 0 },
  { name: 'workout', track_count: 0 },
]

const displayTags = computed(() => {
  if (tags.value.length > 0) return tags.value
  return fallbackPresets
})

// Deterministic vibrant HSL gradient generator for tags
const getTagGradient = (name) => {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  const h1 = Math.abs(hash % 360)
  const h2 = (h1 + 38) % 360
  return `linear-gradient(135deg, hsl(${h1}, 75%, 40%) 0%, hsl(${h2}, 80%, 25%) 100%)`
}

const formatTrackCount = (count) => {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod100 >= 11 && mod100 <= 19) return 'треков'
  if (mod10 === 1) return 'трек'
  if (mod10 >= 2 && mod10 <= 4) return 'трека'
  return 'треков'
}

const getArtistInitials = (artist) => {
  const name = artist?.name || artist?.artist || ''
  return name.slice(0, 2).toUpperCase() || '♪'
}

const getArtistCoverStyle = (artist) => {
  const str = artist?.name || artist?.artist || 'Artist'
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash)
  }
  const hue = Math.abs(hash % 360)
  return {
    background: `linear-gradient(135deg, hsl(${hue}, 50%, 30%) 0%, hsl(${(hue + 50) % 360}, 40%, 20%) 100%)`
  }
}

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

const loadTags = async () => {
  loadingTags.value = true
  try {
    const resp = await tracksApi.getTags(tagScope.value, 40)
    const list = resp.data || []
    if (list.length > 0) {
      tags.value = list
    } else if (tagScope.value === 'library') {
      const globalResp = await tracksApi.getTags('global', 40)
      tags.value = globalResp.data || []
    }
  } catch (e) {
    console.error('Failed to load tags:', e)
  } finally {
    loadingTags.value = false
  }
}

const switchScope = async (scope) => {
  if (tagScope.value === scope) return
  tagScope.value = scope
  tags.value = []
  await loadTags()
}

// ─── Search Execution ───
const goToAlbum = (albumId) => {
  router.push(`/album/${albumId}`)
}

let metaAbortController = null
let metaSearchSeq = 0

const searchArtistsAndPlaylists = async (query) => {
  const cleanQ = query.replace(/^#/, '').trim()
  if (metaAbortController) {
    metaAbortController.abort()
  }
  if (!cleanQ) {
    metaSearchSeq++
    artistsResults.value = []
    albumsResults.value = []
    playlistsResults.value = []
    isArtistsSearching.value = false
    isAlbumsSearching.value = false
    isPlaylistsSearching.value = false
    return
  }

  metaAbortController = new AbortController()
  const currentSignal = metaAbortController.signal
  const currentSeq = ++metaSearchSeq

  isArtistsSearching.value = true
  isAlbumsSearching.value = true
  isPlaylistsSearching.value = true

  try {
    const [artistsRes, albumsRes, myPlaylistsRes, globalPlaylistsRes] = await Promise.allSettled([
      artistsApi.getGlobal({ search: cleanQ, limit: 30 }, { signal: currentSignal }),
      albumsApi.getGlobal({ search: cleanQ, limit: 30 }, { signal: currentSignal }),
      playlistsApi.getAll({ search: cleanQ, limit: 20 }, { signal: currentSignal }),
      playlistsApi.getGlobal({ search: cleanQ, limit: 20 }, { signal: currentSignal })
    ])

    if (currentSeq !== metaSearchSeq || currentSignal.aborted) return

    // 1. Artists
    if (artistsRes.status === 'fulfilled') {
      artistsResults.value = artistsRes.value.data?.items || []
    } else {
      const all = libraryStore.artists || []
      artistsResults.value = all.filter(a => (a.name || a.artist || '').toLowerCase().includes(cleanQ.toLowerCase()))
    }

    // 2. Albums
    if (albumsRes.status === 'fulfilled') {
      albumsResults.value = albumsRes.value.data?.items || []
    } else {
      albumsResults.value = []
    }

    // 3. Playlists (Personal + Global)
    const myItems = myPlaylistsRes.status === 'fulfilled' ? (myPlaylistsRes.value.data?.items || myPlaylistsRes.value.data || []) : []
    const globalItems = globalPlaylistsRes.status === 'fulfilled' ? (globalPlaylistsRes.value.data?.items || globalPlaylistsRes.value.data || []) : []

    const seen = new Set()
    const combined = []
    for (const pl of [...myItems, ...globalItems]) {
      if (pl?.id && !seen.has(pl.id)) {
        seen.add(pl.id)
        combined.push(pl)
      }
    }
    if (combined.length > 0 || (myPlaylistsRes.status === 'fulfilled' && globalPlaylistsRes.status === 'fulfilled')) {
      playlistsResults.value = combined
    } else {
      const all = libraryStore.playlists || []
      playlistsResults.value = all.filter(p => (p.name || '').toLowerCase().includes(cleanQ.toLowerCase()))
    }
  } finally {
    if (currentSeq === metaSearchSeq) {
      isArtistsSearching.value = false
      isAlbumsSearching.value = false
      isPlaylistsSearching.value = false
    }
  }
}

const performSearch = (q) => {
  const query = (q || '').trim()
  if (query) {
    if (MUSIC_URL_REGEX.test(query)) {
      resolveMusicUrl(query)
      clearTrackSearch()
      artistsResults.value = []
      albumsResults.value = []
      playlistsResults.value = []
      soundcloudResults.value = []
      spotifyResults.value = []
      youtubeResults.value = []
      saveRecentSearch(query)
      return
    } else {
      urlPreviewData.value = null
      urlResolveError.value = null
    }

    const isTagSearch = query.startsWith('#')
    trackSearchQuery.value = query
    executeTrackSearch()
    searchArtistsAndPlaylists(query)
    saveRecentSearch(query)

    // Hashtag queries search within catalog tags and should never trigger external web scraping
    if (!isTagSearch) {
      const isOverview = activeFilter.value === 'all'
      // Request 5 items for overview tab so external scraper returns fast; full 30 when in dedicated tab
      const externalLimit = isOverview ? 5 : 30
      if (activeFilter.value === 'all' || activeFilter.value === 'soundcloud') {
        searchSoundCloud(query, externalLimit)
      }
      if (activeFilter.value === 'all' || activeFilter.value === 'spotify') {
        searchSpotify(query, externalLimit)
      }
      if (activeFilter.value === 'all' || activeFilter.value === 'youtube') {
        searchYouTube(query, externalLimit)
      }
    } else {
      soundcloudResults.value = []
      spotifyResults.value = []
      youtubeResults.value = []
    }
  } else {
    clearTrackSearch()
    artistsResults.value = []
    albumsResults.value = []
    playlistsResults.value = []
    soundcloudResults.value = []
    spotifyResults.value = []
    youtubeResults.value = []
  }
}

// Watch debounced query from user typing
watch(debouncedQuery, (newVal) => {
  performSearch(newVal)
})

// On-demand external search when switching to dedicated SoundCloud, Spotify or YouTube tabs
watch(activeFilter, (newFilter) => {
  const query = (searchQuery.value || '').trim()
  if (!query || query.startsWith('#')) return
  if (newFilter === 'soundcloud' && soundcloudResults.value.length < 30) {
    searchSoundCloud(query, 30)
  } else if (newFilter === 'spotify' && spotifyResults.value.length < 30) {
    searchSpotify(query, 30)
  } else if (newFilter === 'youtube' && youtubeResults.value.length < 30) {
    searchYouTube(query, 30)
  }
})

const handleClear = () => {
  if (metaAbortController) metaAbortController.abort()
  if (scAbortController) scAbortController.abort()
  if (spAbortController) spAbortController.abort()
  if (ytAbortController) ytAbortController.abort()
  metaSearchSeq++
  scSearchSeq++
  spSearchSeq++
  ytSearchSeq++
  urlPreviewData.value = null
  urlResolveError.value = null
  urlImportJob.value = null
  isResolvingUrl.value = false
  clearSearchInput()
  clearTrackSearch()
  artistsResults.value = []
  albumsResults.value = []
  playlistsResults.value = []
  soundcloudResults.value = []
  spotifyResults.value = []
  youtubeResults.value = []
  if (tags.value.length === 0) {
    loadTags()
  }
  if (route.query.q || route.query.search || route.query.tag || route.query.tab || route.query.mode) {
    router.replace({ path: '/search', query: {} })
  }
}

// ─── Playback & Track Actions ───
const handlePlayTrack = (track, list, index) => {
  playerStore.playTrack(track, list || allTracksList.value, index >= 0 ? index : 0)
}


const handleAddToLibrary = async (track) => {
  try {
    const success = await libraryStore.addToLibrary(track.id)
    if (success) {
      track.in_library = true
      uiStore.toast.success('Добавлено', 'Трек добавлен в вашу библиотеку')
    }
  } catch (e) {
    uiStore.toast.error('Ошибка', 'Не удалось добавить трек')
  }
}

const goToArtist = (name) => {
  if (name) router.push(`/artist/${encodeURIComponent(name)}`)
}

const goToPlaylist = (id) => {
  if (id) router.push(`/playlist/${id}`)
}

// ─── Recent Searches Management ───
const RECENT_SEARCHES_KEY = 'auxbass_recent_searches'
const recentSearches = ref([])

const loadRecentSearches = () => {
  try {
    const raw = localStorage.getItem(RECENT_SEARCHES_KEY)
    if (raw) {
      recentSearches.value = JSON.parse(raw)
    }
  } catch (e) {
    console.error('Failed to load recent searches:', e)
  }
}

const saveRecentSearch = (term) => {
  const clean = (term || '').trim()
  if (!clean || clean.length < 2) return
  const current = recentSearches.value.filter(s => s.toLowerCase() !== clean.toLowerCase())
  const updated = [clean, ...current].slice(0, 10)
  recentSearches.value = updated
  try {
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(updated))
  } catch (e) {
    console.error('Failed to save recent search:', e)
  }
}

const handleRemoveRecent = (term) => {
  recentSearches.value = recentSearches.value.filter(s => s !== term)
  try {
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(recentSearches.value))
  } catch (e) {
    console.error('Failed to remove recent search:', e)
  }
}

const handleClearRecent = () => {
  recentSearches.value = []
  try {
    localStorage.removeItem(RECENT_SEARCHES_KEY)
  } catch (e) {
    console.error('Failed to clear recent searches:', e)
  }
}

const handleRecentClick = (term) => {
  setQuery(term, true)
}

const handleTagClick = (tagName) => {
  saveRecentSearch(`#${tagName}`)
  setQuery(`#${tagName}`, true)
}

const handlePlayTagMix = async (tagName) => {
  setQuery(`#${tagName}`, true)
  try {
    // 1. Try playing from library mix first
    const libRes = await api.get('/library', { params: { search: `#${tagName}`, per_page: 5 } }).catch(() => null)
    if (libRes?.data?.items?.length) {
      await playerStore.playShuffleAll('library', null, null, { search: `#${tagName}` })
      return
    }

    // 2. Otherwise play from global results for this tag
    const globalRes = await tracksApi.getGlobal({ search: `#${tagName}`, per_page: 30 })
    const items = globalRes.data?.items || []
    if (items.length > 0) {
      playerStore.playTrack(items[0], items, 0)
    } else {
      uiStore.toast.info('Тег', `По тегу #${tagName} треков пока нет`)
    }
  } catch (e) {
    console.error('Failed to play tag mix:', e)
  }
}

// ─── Filter & Sub-Tab Navigation ───
const setFilter = (chipId) => {
  activeFilter.value = chipId
  const newQuery = { ...route.query }
  if (chipId === 'soundcloud') {
    newQuery.tab = 'soundcloud'
    newQuery.mode = scSubTab.value || 'search'
    router.replace({ path: '/search', query: newQuery })
  } else if (chipId === 'spotify') {
    newQuery.tab = 'spotify'
    newQuery.mode = spSubTab.value || 'search'
    router.replace({ path: '/search', query: newQuery })
  } else if (chipId === 'youtube') {
    newQuery.tab = 'youtube'
    delete newQuery.mode
    router.replace({ path: '/search', query: newQuery })
  } else {
    delete newQuery.tab
    delete newQuery.mode
    router.replace({ path: '/search', query: newQuery })
  }
}

const resetToAllSearch = () => {
  activeFilter.value = 'all'
  const newQuery = { ...route.query }
  delete newQuery.tab
  delete newQuery.mode
  router.replace({ path: '/search', query: newQuery })
}

const setScSubTab = (mode) => {
  scSubTab.value = mode
  if (mode === 'likes') {
    if (scLikes.value.length === 0 && scAccount.value?.connected) {
      fetchScLikes(true)
    }
  } else if (mode === 'tracks') {
    if (scTracks.value.length === 0 && scAccount.value?.connected) {
      fetchScTracks(true)
    }
  } else if (mode === 'playlists') {
    if (scPlaylists.value.length === 0 && scAccount.value?.connected) {
      fetchScPlaylists()
    }
  }
  const newQuery = { ...route.query, tab: 'soundcloud', mode }
  router.replace({ path: '/search', query: newQuery })
}

const setSpSubTab = (mode) => {
  spSubTab.value = mode
  const newQuery = { ...route.query, tab: 'spotify', mode }
  router.replace({ path: '/search', query: newQuery })
}

// ─── Route Synchronization ───
const applyRouteQuery = () => {
  const tagParam = route.query.tag
  const queryParam = route.query.q || route.query.search
  if (route.query.tab === 'soundcloud') {
    activeFilter.value = 'soundcloud'
    if (route.query.mode === 'likes') {
      setScSubTab('likes')
    } else if (route.query.mode === 'tracks') {
      setScSubTab('tracks')
    } else if (route.query.mode === 'playlists') {
      setScSubTab('playlists')
    } else {
      scSubTab.value = 'search'
    }
  } else if (route.query.tab === 'spotify') {
    activeFilter.value = 'spotify'
    if (route.query.mode === 'likes') {
      spSubTab.value = 'likes'
      if (spLikes.value.length === 0) {
        fetchSpLikes()
      }
    } else if (route.query.mode === 'exportify') {
      spSubTab.value = 'exportify'
    } else {
      spSubTab.value = 'search'
    }
  } else if (route.query.tab === 'youtube') {
    activeFilter.value = 'youtube'
  } else if (!route.query.tab) {
    // When navigated to /search without a tab parameter, reset any external provider filter
    if (activeFilter.value === 'soundcloud' || activeFilter.value === 'spotify' || activeFilter.value === 'youtube') {
      activeFilter.value = 'all'
    }
  }

  if (tagParam && typeof tagParam === 'string') {
    const formatted = tagParam.startsWith('#') ? tagParam : `#${tagParam}`
    setQuery(formatted, true)
  } else if (queryParam && typeof queryParam === 'string') {
    setQuery(queryParam, true)
  }
}

const handleResetState = (event) => {
  if (event.detail?.route === '/search') {
    handleClear()
    activeFilter.value = 'all'
    if (Object.keys(route.query).length > 0) {
      router.replace({ path: '/search', query: {} })
    }
  }
}

onMounted(() => {
  loadRecentSearches()
  const hasInitialQuery = Boolean(route.query.tag || route.query.q || route.query.search)
  if (!hasInitialQuery) {
    loadTags()
  }
  fetchScAccountForSearch()
  fetchSpAccountForSearch()
  applyRouteQuery()
  window.addEventListener('reset-view-state', handleResetState)
})

// Watch route query params for reactive updates (e.g. from tag clicks, browser navigation, tab switches)
watch(
  () => [route.query.tag, route.query.q, route.query.search, route.query.tab, route.query.mode],
  () => {
    applyRouteQuery()
  }
)

onActivated(() => {
  applyRouteQuery()
  fetchScAccountForSearch()
  fetchSpAccountForSearch()
})

onUnmounted(() => {
  if (metaAbortController) metaAbortController.abort()
  if (scAbortController) scAbortController.abort()
  if (spAbortController) spAbortController.abort()
  if (ytAbortController) ytAbortController.abort()
  window.removeEventListener('reset-view-state', handleResetState)
})
</script>

<style scoped>
.search-view {
  padding: 12px 16px 32px;
  max-width: 1200px;
  margin: 0 auto;
}

.search-bar-wrapper {
  margin-bottom: 12px;
}

/* Type filter chips */
.search-type-chips {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
  padding-bottom: 8px;
  margin-bottom: 16px;
}

.search-type-chips::-webkit-scrollbar {
  display: none;
}

.type-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.05);
  color: var(--c-text-2, rgba(255, 255, 255, 0.8));
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: all 0.18s ease;
  font-family: inherit;
}

.type-chip.active {
  background: var(--c-accent, #1db954);
  color: #000;
  border-color: transparent;
}

.chip-badge {
  font-size: 11px;
  background: rgba(0, 0, 0, 0.2);
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 700;
}

.type-chip.active .chip-badge {
  background: rgba(0, 0, 0, 0.25);
  color: #000;
}

.search-results-container {
  min-height: 200px;
}

.search-skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* ─── Smart URL Preview Card ─── */
.url-preview-card {
  background: rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  padding: 16px;
  margin-bottom: 20px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
  animation: fadeIn 0.25s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

.url-preview-loading {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--c-text-2, rgba(255, 255, 255, 0.7));
  font-size: 14px;
  padding: 12px 4px;
}

.spin-icon {
  animation: spin 1s linear infinite;
  color: var(--c-accent, #1db954);
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.url-preview-error {
  color: #ff6b6b;
  font-size: 14px;
  padding: 8px 4px;
}

.url-preview-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.url-provider-badge {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.5px;
  padding: 3px 8px;
  border-radius: 6px;
}

.url-provider-badge.spotify {
  background: rgba(29, 185, 84, 0.2);
  color: #1ed760;
  border: 1px solid rgba(29, 185, 84, 0.4);
}

.url-provider-badge.soundcloud {
  background: rgba(255, 85, 0, 0.2);
  color: #ff5500;
  border: 1px solid rgba(255, 85, 0, 0.4);
}

.url-provider-badge.youtube {
  background: rgba(255, 0, 0, 0.2);
  color: #ff4e4e;
  border: 1px solid rgba(255, 0, 0, 0.4);
}

.url-type-badge {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-text-2, rgba(255, 255, 255, 0.75));
  background: rgba(255, 255, 255, 0.08);
  padding: 3px 10px;
  border-radius: 6px;
}

.url-preview-body {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-bottom: 14px;
}

.url-preview-cover {
  width: 72px;
  height: 72px;
  border-radius: 12px;
  object-fit: cover;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  flex-shrink: 0;
}

.url-preview-info {
  min-width: 0;
  flex: 1;
}

.url-preview-title {
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  margin: 0 0 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.url-preview-author {
  font-size: 13px;
  color: var(--c-text-2, rgba(255, 255, 255, 0.7));
  margin: 0 0 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.url-preview-meta {
  font-size: 12px;
  color: var(--c-text-3, rgba(255, 255, 255, 0.5));
}

.url-preview-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.btn-url-primary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  border-radius: 10px;
  background: var(--c-accent, #1db954);
  color: #000;
  font-size: 13px;
  font-weight: 700;
  border: none;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}

.btn-url-primary:hover:not(:disabled) {
  filter: brightness(1.1);
  transform: translateY(-1px);
}

.btn-url-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-url-secondary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  border: 1px solid rgba(255, 255, 255, 0.15);
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}

.btn-url-secondary:hover {
  background: rgba(255, 255, 255, 0.18);
}
</style>
