<template>
  <div class="user-profile-view">
    <!-- Loading State -->
    <div v-if="loading" class="loading-container">
      <div class="spinner"></div>
    </div>

    <!-- Private / Forbidden Profile -->
    <div v-else-if="isForbidden" class="empty-state private-profile">
      <div class="empty-icon"><Lock :size="56" /></div>
      <h2>Профиль скрыт</h2>
      <p>Пользователь ограничил доступ к своей медиатеке настройками приватности</p>
      <button class="btn-pill-secondary" @click="router.back()">
        Назад
      </button>
    </div>

    <!-- Error / Not Found -->
    <div v-else-if="error || !user" class="empty-state error-profile">
      <div class="empty-icon"><UserX :size="56" /></div>
      <h2>Пользователь не найден</h2>
      <p>{{ error || 'Не удалось загрузить данные пользователя' }}</p>
      <button class="btn-pill-secondary" @click="router.back()">
        Назад
      </button>
    </div>

    <!-- Normal Profile View -->
    <template v-else>
      <!-- Hero Card with integrated edge tabs & details panel -->
      <ProfileHeroCard
        class="profile-hero-wrapper"
        :user="user"
        :isSelf="isSelf"
        :isFollowing="isFollowing"
        :followLoading="followLoading"
        :userAvatar="userAvatar"
        :initials="userInitials"
        :avatarGradientStyle="avatarGradientStyle"
        :ambientGlowStyle="ambientGlowStyle"
        :scAccount="scAccount"
        :spAccount="spAccount"
        :activeTab="activeTab"
        :overviewAlbumsCount="user?.album_count || overviewAlbums.length"
        :scPlaylistsCount="scPlaylists.length"
        :scTracksCount="scTracks.length"
        :totalDuration="user?.total_duration || 0"
        :totalPlaylistTracks="totalPlaylistTracks"
        :maxPlaylistTrackCount="maxPlaylistTrackCount"
        :uniqueAlbumArtistsCount="uniqueAlbumArtistsCount"
        :totalAlbumTracksCount="totalAlbumTracksCount"
        @play="handleHeroPlay"
        @shuffle="handleHeroShuffle"
        @follow="toggleFollow"
        @edit="showEditProfileModal = true"
        @share="handleShare"
        @selectTab="selectTab"
        @createPlaylist="handleCreatePlaylist"
      />

      <!-- Active Background Imports Panel (when viewing self) -->
      <ProfileActiveImports
        v-if="isSelf"
        class="profile-imports-wrapper"
        @restore="handleRestoreJob"
        @cancel="handleCancelJob"
      />

      <!-- Overview Tab Content -->
      <div v-show="activeTab === 'overview'" class="tab-pane tab-pane-overview">
        <ProfileTabsOverview
          :user="user"
          :isSelf="isSelf"
          :loadingOverview="loadingOverview"
          :overviewTracks="overviewTracks"
          :overviewPlaylists="overviewPlaylists"
          :overviewAlbums="overviewAlbums"
          :scAccount="scAccount"
          :scPlaylists="scPlaylists"
          :scTracks="scTracks"
          :importingTrackUrl="importingTrackUrl"
          @selectTab="selectTab"
          @openScPlaylist="handleOpenScPlaylist"
          @quickPlay="handleQuickPlayExternalTrack"
          @quickAdd="handleQuickAddExternalTrack"
        />
      </div>

      <!-- Tracks Tab Content -->
      <div v-show="activeTab === 'tracks'" class="tab-pane tab-pane-bounded">
        <VirtualTrackList
          v-if="hasOpenedTracks || activeTab === 'tracks'"
          :key="'user-tracks-' + userId"
          ref="virtualTrackListRef"
          :fetchFn="fetchUserTracks"
          :pageSize="50"
          :skeletonCount="12"
          :showAlbum="true"
          :showAddToLibrary="true"
          menuContext="social"
          @click="handleTrackClick"
          @like="handleLikeTrack"
          @menu="handleTrackMenu"
          @download="handleDirectDownload"
          @addToLibrary="handleAddToLibrary"
        >
          <template #empty>
            <span class="empty-icon"><Music :size="48" /></span>
            <p>У пользователя нет треков в библиотеке</p>
          </template>
        </VirtualTrackList>
      </div>

      <!-- Playlists Tab Content -->
      <div v-show="activeTab === 'playlists'" class="tab-pane tab-pane-bounded">
        <VirtualGrid
          v-if="hasOpenedPlaylists || activeTab === 'playlists'"
          :key="'user-playlists-' + userId"
          ref="playlistsGridRef"
          type="playlist"
          :fetchFn="fetchUserPlaylists"
          :pageSize="30"
          :skeletonCount="8"
          @click="goToPlaylist"
          @play="shufflePlaylist"
          @contextmenu="handlePlaylistContextMenu"
        >
          <template #empty>
            <span class="empty-icon"><Folder :size="48" /></span>
            <p>Нет публичных плейлистов</p>
          </template>
        </VirtualGrid>
      </div>

      <!-- Albums Tab Content -->
      <div v-show="activeTab === 'albums'" class="tab-pane tab-pane-bounded">
        <VirtualGrid
          v-if="hasOpenedAlbums || activeTab === 'albums'"
          :key="'user-albums-' + userId"
          ref="albumsGridRef"
          type="album"
          :fetchFn="fetchUserAlbums"
          :pageSize="30"
          :skeletonCount="8"
          @click="goToAlbum"
          @play="shuffleAlbum"
          @contextmenu="handleAlbumContextMenu"
        >
          <template #empty>
            <span class="empty-icon"><Disc3 :size="48" /></span>
            <p>Нет альбомов в библиотеке</p>
          </template>
        </VirtualGrid>
      </div>

      <!-- SoundCloud Tab Content -->
      <div v-show="activeTab === 'soundcloud'" class="tab-pane tab-pane-bounded">
        <ProfileTabSoundCloud
          v-if="hasOpenedSoundCloud || activeTab === 'soundcloud'"
          ref="scTabRef"
          :userId="userId"
          :isSelf="isSelf"
          :scAccount="scAccount"
          :scPlaylists="scPlaylists"
          :scTracks="scTracks"
          :loadingScPlaylists="loadingScPlaylists"
          :loadingScTracks="loadingScTracks"
          :scTracksCursor="scTracksCursor"
          :loadingMoreScTracks="loadingMoreScTracks"
          :importingTrackUrl="importingTrackUrl"
          :isSyncingScPlaylist="isSyncingScPlaylist"
          @loadMore="loadMoreScTracks"
          @syncPlaylist="handleSyncScPlaylist"
          @quickPlay="handleQuickPlayExternalTrack"
          @quickAdd="handleQuickAddExternalTrack"
        />
      </div>
    </template>

    <!-- Edit Profile Modal -->
    <ProfileEditModal
      v-model="showEditProfileModal"
      :user="user"
      :userAvatar="userAvatar"
      :userId="userId"
      @saved="loadUserProfile(true)"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { usePlayerStore } from '@/stores/player'
import { useLibraryStore } from '@/stores/library'
import { useUIStore } from '@/stores/ui'
import { useTasksStore } from '@/stores/tasks'
import { useExternalAccountsStore } from '@/stores/externalAccounts'
import { useContextMenu } from '@/composables/useContextMenu'
import { useTrackActions, useShare } from '@/composables'
import { socialApi, playlistsApi, ingestionApi } from '@/api/client'
import apiCache from '@/utils/apiCache'
import VirtualTrackList from '@/components/VirtualTrackList.vue'
import VirtualGrid from '@/components/VirtualGrid.vue'
import { getInitials, computeAvatarGradient, computeAmbientGlow } from './profile/profileUtils'

// Sub-components
import ProfileHeroCard from './profile/ProfileHeroCard.vue'
import ProfileActiveImports from './profile/ProfileActiveImports.vue'
import ProfileTabsOverview from './profile/ProfileTabsOverview.vue'
import ProfileTabSoundCloud from './profile/ProfileTabSoundCloud.vue'
import ProfileEditModal from './profile/ProfileEditModal.vue'

import {
  Lock,
  UserX,
  Music,
  Folder,
  Disc3,
  Sparkles,
} from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const playerStore = usePlayerStore()
const libraryStore = useLibraryStore()
const uiStore = useUIStore()
const tasksStore = useTasksStore()
const externalAccountsStore = useExternalAccountsStore()
const { openMenu } = useContextMenu()
const { share } = useShare()

// Unified actions
const { handleDirectDownload, handleLikeTrack, handleAddToLibrary } = useTrackActions()

const userId = computed(() => {
  const raw = route.params.id
  if (raw === 'me' || !raw) {
    return authStore.user?.id || 0
  }
  const parsed = Number(raw)
  return isNaN(parsed) ? (authStore.user?.id || 0) : parsed
})
const isSelf = computed(() => !!(authStore.user && authStore.user.id === userId.value))

// ─── Core State ───
const user = ref(null)
const loading = ref(true)
const error = ref(null)
const isForbidden = ref(false)
const isFollowing = ref(false)
const followLoading = ref(false)
const activeTab = ref('overview')
const hasOpenedTracks = ref(false)
const hasOpenedPlaylists = ref(false)
const hasOpenedAlbums = ref(false)
const hasOpenedSoundCloud = ref(false)
const showEditProfileModal = ref(false)

const markTabOpened = (tabKey) => {
  if (tabKey === 'tracks') hasOpenedTracks.value = true
  if (tabKey === 'playlists') hasOpenedPlaylists.value = true
  if (tabKey === 'albums') hasOpenedAlbums.value = true
  if (tabKey === 'soundcloud') hasOpenedSoundCloud.value = true
}

// ─── Overview Data ───
const overviewTracks = ref([])
const overviewPlaylists = ref([])
const overviewAlbums = ref([])
const loadingOverview = ref(false)

// ─── External Accounts ───
const externalAccounts = ref([])
const scAccount = computed(() => {
  const fromApi = externalAccounts.value.find(a => a.provider === 'soundcloud')
  if (fromApi) return fromApi
  if (isSelf.value && externalAccountsStore.isScConnected) {
    return externalAccountsStore.scAccount
  }
  return null
})
const spAccount = computed(() => {
  const fromApi = externalAccounts.value.find(a => a.provider === 'spotify')
  if (fromApi) return fromApi
  if (isSelf.value && externalAccountsStore.isSpConnected) {
    return externalAccountsStore.spAccount
  }
  return null
})

const scPlaylists = ref([])
const loadingScPlaylists = ref(false)
const scTracks = ref([])
const loadingScTracks = ref(false)
const scTracksCursor = ref(null)
const loadingMoreScTracks = ref(false)
const importingTrackUrl = ref(null)
const isSyncingScPlaylist = ref(false)

// ─── Computed Props for HeroCard ───
const userAvatar = computed(() => {
  if (isSelf.value && authStore.userAvatarUrl) {
    return authStore.userAvatarUrl
  }
  return user.value?.avatar_url || user.value?.custom_avatar_url || user.value?.photo_url || null
})

const userInitials = computed(() => getInitials(user.value, isSelf.value, authStore))
const avatarGradientStyle = computed(() => computeAvatarGradient(user.value?.display_name || user.value?.username))
const ambientGlowStyle = computed(() => computeAmbientGlow(user.value?.display_name || user.value?.username))

const totalPlaylistTracks = computed(() => {
  return overviewPlaylists.value.reduce((acc, pl) => acc + (pl.track_count || 0), 0)
})

const maxPlaylistTrackCount = computed(() => {
  if (!overviewPlaylists.value.length) return 0
  return Math.max(...overviewPlaylists.value.map(pl => pl.track_count || 0))
})

const uniqueAlbumArtistsCount = computed(() => {
  const artists = overviewAlbums.value.map(a => a.artist).filter(Boolean)
  return new Set(artists).size
})

const totalAlbumTracksCount = computed(() => {
  return overviewAlbums.value.reduce((acc, a) => acc + (a.track_count || a.total_tracks || 0), 0)
})

// ─── Refs ───
const virtualTrackListRef = ref(null)
const playlistsGridRef = ref(null)
const albumsGridRef = ref(null)
const scTabRef = ref(null)

const resetScrollToTop = () => {
  const mainContent = document.querySelector('.main-content')
  if (mainContent) {
    mainContent.scrollTop = 0
  }
}

const selectTab = async (tabKey) => {
  if (activeTab.value !== tabKey) {
    resetScrollToTop()
  }
  markTabOpened(tabKey)
  activeTab.value = tabKey
  await nextTick()
  window.dispatchEvent(new Event('resize'))
  if (tabKey === 'tracks') {
    virtualTrackListRef.value?.updateScroll?.()
  } else if (tabKey === 'playlists') {
    playlistsGridRef.value?.updateScroll?.()
  } else if (tabKey === 'albums') {
    albumsGridRef.value?.updateScroll?.()
  }
}

watch(activeTab, async (newTab) => {
  markTabOpened(newTab)
  await nextTick()
  window.dispatchEvent(new Event('resize'))
  if (newTab === 'tracks') {
    virtualTrackListRef.value?.updateScroll?.()
  } else if (newTab === 'playlists') {
    playlistsGridRef.value?.updateScroll?.()
  } else if (newTab === 'albums') {
    albumsGridRef.value?.updateScroll?.()
  }
})

// ─── Data Loading ───
const loadOverviewData = async (id) => {
  if (!id) return
  loadingOverview.value = true
  try {
    const [tracksRes, playlistsRes, albumsRes] = await Promise.allSettled([
      socialApi.getUserLibrary(id, { page: 1, per_page: 8 }),
      playlistsApi.getUserPlaylists(id),
      socialApi.getUserAlbums(id, { page: 1, per_page: 20 })
    ])

    if (tracksRes.status === 'fulfilled') {
      overviewTracks.value = tracksRes.value.data?.items || (Array.isArray(tracksRes.value.data) ? tracksRes.value.data : [])
    }
    if (playlistsRes.status === 'fulfilled') {
      overviewPlaylists.value = playlistsRes.value.data?.items || (Array.isArray(playlistsRes.value.data) ? playlistsRes.value.data : [])
    }
    if (albumsRes.status === 'fulfilled') {
      overviewAlbums.value = albumsRes.value.data?.items || (Array.isArray(albumsRes.value.data) ? albumsRes.value.data : [])
    }
  } catch (err) {
    console.error('Failed to load overview data:', err)
  } finally {
    loadingOverview.value = false
  }
}

const loadExternalAccounts = async (id) => {
  if (!id) return
  try {
    if (isSelf.value) {
      externalAccountsStore.fetchSoundCloud()
      externalAccountsStore.fetchSpotify()
    }
    const res = await socialApi.getUserExternalAccounts(id)
    const accounts = Array.isArray(res.data) ? res.data : (res.data?.accounts || [])
    externalAccounts.value = accounts

    const sc = accounts.find(a => a.provider === 'soundcloud') || (isSelf.value && externalAccountsStore.isScConnected ? externalAccountsStore.scAccount : null)
    if (sc) {
      if (sc.show_playlists || isSelf.value) loadScPlaylists(id)
      if (sc.show_tracks || isSelf.value) loadScTracks(id, true)
    }
  } catch (err) {
    console.error('Failed to load user external accounts:', err)
  }
}

const loadScPlaylists = async (id) => {
  loadingScPlaylists.value = true
  try {
    const res = await socialApi.getUserExternalPlaylists(id, 'soundcloud')
    scPlaylists.value = res.data?.items || []
  } catch (err) {
    console.error('Failed to load SC playlists:', err)
    scPlaylists.value = []
  } finally {
    loadingScPlaylists.value = false
  }
}

const loadScTracks = async (id, reset = true) => {
  loadingScTracks.value = true
  if (reset) scTracksCursor.value = null
  try {
    const res = await socialApi.getUserExternalTracks(id, 'soundcloud', { limit: 40 })
    scTracks.value = res.data?.items || []
    scTracksCursor.value = res.data?.next_cursor || null
  } catch (err) {
    console.error('Failed to load SC tracks:', err)
    scTracks.value = []
  } finally {
    loadingScTracks.value = false
  }
}

const loadMoreScTracks = async () => {
  if (!scTracksCursor.value || loadingMoreScTracks.value) return
  loadingMoreScTracks.value = true
  try {
    const res = await socialApi.getUserExternalTracks(userId.value, 'soundcloud', {
      cursor: scTracksCursor.value,
      limit: 40,
    })
    const more = res.data?.items || []
    scTracks.value = [...scTracks.value, ...more]
    scTracksCursor.value = res.data?.next_cursor || null
  } catch (err) {
    console.error('Failed to load more SC tracks:', err)
  } finally {
    loadingMoreScTracks.value = false
  }
}

const loadUserProfile = async (bypassCache = false) => {
  const id = userId.value
  if (!id) {
    if (authStore.loading || !authStore.initialized) return
    error.value = 'Пользователь не найден'
    loading.value = false
    return
  }
  if (!user.value) loading.value = true
  error.value = null
  isForbidden.value = false

  try {
    const shouldBypass = bypassCache || isSelf.value
    const res = await socialApi.getUser(id, {}, { bypassCache: shouldBypass })
    user.value = res.data
    isFollowing.value = !!res.data.is_following
    loadOverviewData(id)
    loadExternalAccounts(id)
  } catch (err) {
    if (err.response?.status === 403) {
      isForbidden.value = true
    } else if (err.response?.status === 404) {
      error.value = 'Пользователь не существует'
    } else {
      error.value = 'Ошибка загрузки профиля'
    }
  } finally {
    loading.value = false
  }
}

// ─── Actions ───
const toggleFollow = async () => {
  if (!authStore.hasChannel) {
    authStore.promptChannelSetup()
    return
  }
  if (!user.value || followLoading.value) return

  followLoading.value = true
  try {
    if (isFollowing.value) {
      await socialApi.unfollow(user.value.id)
      isFollowing.value = false
      if (user.value.followers_count > 0) user.value.followers_count--
    } else {
      await socialApi.follow(user.value.id)
      isFollowing.value = true
      user.value.followers_count++
    }
  } catch (err) {
    if (err.response?.status === 403) {
      authStore.promptChannelSetup()
    } else {
      console.error('Failed to toggle follow:', err)
    }
  } finally {
    followLoading.value = false
  }
}

const handleShare = () => {
  if (!user.value) return
  share({
    type: 'user',
    id: user.value.id,
    title: user.value.display_name,
    text: `Посмотри медиатеку ${user.value.display_name} в TG Player!`,
  })
}

const handlePlayUserLibrary = async () => {
  if (overviewTracks.value?.length > 0) {
    playerStore.play(overviewTracks.value[0], overviewTracks.value)
    uiStore.toast.success('Воспроизведение', `Играет медиатека ${user.value.display_name}`)
  } else {
    try {
      const res = await socialApi.getUserLibrary(userId.value, { page: 1, per_page: 50 })
      const tracks = res.data?.items || []
      if (tracks.length > 0) {
        playerStore.play(tracks[0], tracks)
        uiStore.toast.success('Воспроизведение', `Играет медиатека ${user.value.display_name}`)
      } else {
        uiStore.toast.info('Пусто', 'У пользователя нет доступных треков')
      }
    } catch (e) {
      console.error('Failed to play user library:', e)
    }
  }
}

const handleShuffleUserLibrary = async () => {
  try {
    await playerStore.playShuffleAll('user_library', userId.value, user.value?.display_name)
    uiStore.toast.success('Перемешивание', `Играет медиатека ${user.value?.display_name || ''}`)
  } catch (e) {
    console.error('Failed to shuffle user library:', e)
  }
}

const handleHeroPlay = () => {
  if (activeTab.value === 'soundcloud' && scTracks.value?.length > 0) {
    handleQuickPlayExternalTrack(scTracks.value[0])
  } else {
    handlePlayUserLibrary()
  }
}

const handleHeroShuffle = () => {
  if (activeTab.value === 'soundcloud' && scTracks.value?.length > 0) {
    const randomIndex = Math.floor(Math.random() * scTracks.value.length)
    handleQuickPlayExternalTrack(scTracks.value[randomIndex])
  } else {
    handleShuffleUserLibrary()
  }
}

const handleCreatePlaylist = () => {
  router.push('/library?tab=playlists')
}

// ─── Track/Playlist/Album Fetch & Actions ───
const fetchUserTracks = async ({ offset, limit, signal }) => {
  const page = Math.floor(offset / limit) + 1
  try {
    const res = await socialApi.getUserLibrary(userId.value, { page, per_page: limit }, { signal })
    return { items: res.data.items || [], total: res.data.total || 0 }
  } catch (err) {
    if (err?.name === 'AbortError' || err?.code === 'ERR_CANCELED') {
      throw err
    }
    console.error('Failed to fetch user tracks:', err)
    return { items: [], total: 0 }
  }
}

const handleTrackClick = (payload, index) => {
  const track = payload?.track || payload
  const allTracks = payload?.allTracks || overviewTracks.value || [track]
  const context = { type: 'user_library', id: userId.value, name: user.value?.display_name }
  if (playerStore.shuffle) {
    playerStore.playShuffleAll('user_library', userId.value, user.value?.display_name, { startingTrack: track })
  } else {
    playerStore.play(track, allTracks, context)
  }
}

const handleTrackMenu = (payload, index, event) => {
  if (payload && payload.track) {
    openMenu('track', payload.track, payload.context || 'social', payload.event)
    return
  }
  openMenu('track', payload, 'social', event)
}

const fetchUserPlaylists = async ({ offset, limit }) => {
  try {
    const res = await playlistsApi.getUserPlaylists(userId.value)
    const all = res.data.items || []
    return { items: all.slice(offset, offset + limit), total: all.length }
  } catch (err) {
    console.error('Failed to fetch user playlists:', err)
    return { items: [], total: 0 }
  }
}

const goToPlaylist = (playlist) => {
  router.push(`/playlist/${playlist.id}`)
}

const shufflePlaylist = async (playlist) => {
  await playerStore.playShuffleAll('playlist', playlist.id, playlist.name)
}

const handlePlaylistContextMenu = (payload, event) => {
  if (payload && payload.item) {
    openMenu('playlist', payload.item, 'social', payload.event)
    return
  }
  openMenu('playlist', payload, 'social', event)
}

const fetchUserAlbums = async ({ offset, limit }) => {
  const page = Math.floor(offset / limit) + 1
  try {
    const res = await socialApi.getUserAlbums(userId.value, { page, per_page: limit })
    return { items: res.data.items || [], total: res.data.total || 0 }
  } catch (err) {
    console.error('Failed to fetch user albums:', err)
    return { items: [], total: 0 }
  }
}

const goToAlbum = (album) => {
  router.push(`/album/${album.id}`)
}

const shuffleAlbum = async (album) => {
  await playerStore.playShuffleAll('album', album.id, album.name)
}

const handleAlbumContextMenu = (payload, event) => {
  if (payload && payload.item) {
    openMenu('album', payload.item, 'social', payload.event)
    return
  }
  openMenu('album', payload, 'social', event)
}

// ─── External Track Handlers ───
const handleQuickPlayExternalTrack = async (item) => {
  if (importingTrackUrl.value) return
  importingTrackUrl.value = item.url

  try {
    const res = await ingestionApi.quickImport({
      url: item.url,
      title: item.title,
      artist: item.artist,
      duration: item.duration,
      cover_url: item.cover_url,
      genre: item.genre,
      tags: item.tags,
      add_to_library: false,
    })

    const track = res.data?.track
    if (track) {
      if (track.in_library) item.in_library = true
      item.already_in_tg = true
      item.track_id = track.id
      playerStore.play(track, [track])
    }
  } catch (e) {
    console.error('Failed to quick play external track:', e)
    const errorMsg = e.response?.data?.detail || 'Не удалось загрузить трек'
    uiStore.toast?.error('Ошибка воспроизведения', errorMsg)
  } finally {
    importingTrackUrl.value = null
  }
}

const handleQuickAddExternalTrack = (item) => {
  tasksStore.enqueueTrack(item, 'soundcloud')
}

const handleOpenScPlaylist = async (pl) => {
  await selectTab('soundcloud')
  await nextTick()
  scTabRef.value?.openScPlaylist?.(pl)
}

// ─── Import/Ingestion Handlers ───
const handleRestoreJob = (jobId) => {
  tasksStore.restoreJob(jobId)
}

const handleCancelJob = (jobId) => {
  tasksStore.cancelJob(jobId)
}

const handleSyncScPlaylist = async (playlist) => {
  if (isSyncingScPlaylist.value) return

  let tracksToSync = scTabRef.value?.scPlaylistTracks || []
  if (!tracksToSync || tracksToSync.length === 0) {
    try {
      const res = await socialApi.getUserExternalPlaylistTracks(userId.value, playlist.id)
      tracksToSync = res.data?.tracks || []
    } catch (e) {
      uiStore.toast?.error('Ошибка', 'Не удалось получить треки плейлиста')
      return
    }
  }

  if (tracksToSync.length === 0) {
    uiStore.toast?.info('Синхронизация', 'В этом плейлисте нет треков для синхронизации')
    return
  }

  isSyncingScPlaylist.value = true
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
      false,
      null,
      playlist.artwork_url
    )

    if (res.data) {
      tasksStore.registerJob(res.data, {
        type: 'import',
        title: `Плейлист: ${playlist.title}`,
      })
    }
    uiStore.toast?.success('Синхронизация', `Запущен импорт плейлиста «${playlist.title}» (${urls.length} треков)`)

    const jobId = res.data?.id
    const pollInterval = setInterval(async () => {
      try {
        const jobRes = await ingestionApi.getJob(jobId)
        const job = jobRes.data
        if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
          clearInterval(pollInterval)
          isSyncingScPlaylist.value = false
          if (job.status === 'completed') {
            uiStore.toast?.success('Готово', `Плейлист «${playlist.title}» успешно синхронизирован!`)
            libraryStore.fetchTracks({ refresh: true })
            loadOverviewData(userId.value)
          }
        }
      } catch (err) {
        clearInterval(pollInterval)
        isSyncingScPlaylist.value = false
      }
    }, 2000)
  } catch (e) {
    console.error('Failed to sync SC playlist from profile:', e)
    uiStore.toast?.error('Ошибка', 'Не удалось запустить синхронизацию плейлиста')
    isSyncingScPlaylist.value = false
  }
}

// ─── Watchers ───
watch(
  () => route.params.id,
  (newId) => {
    if (newId && route.name === 'user-profile') {
      activeTab.value = 'overview'
      hasOpenedTracks.value = false
      hasOpenedPlaylists.value = false
      hasOpenedAlbums.value = false
      hasOpenedSoundCloud.value = false
      externalAccounts.value = []
      scPlaylists.value = []
      scTracks.value = []
      resetScrollToTop()
      loadUserProfile(true)
    }
  }
)

watch(
  () => authStore.user,
  (newUser) => {
    if (newUser && isSelf.value) {
      if (user.value) {
        user.value.custom_nickname = newUser.custom_nickname
        user.value.custom_avatar_url = newUser.custom_avatar_url
        user.value.avatar_url = newUser.custom_avatar_url || newUser.photo_url || user.value.avatar_url
        user.value.display_name = authStore.userDisplayName
        user.value.hide_telegram_id = newUser.hide_telegram_id
      }
      loadUserProfile(true)
    }
  },
  { deep: true }
)

watch(isSelf, (self) => {
  if (self) {
    tasksStore.checkRecentJobs()
  }
})

onMounted(() => {
  loadUserProfile(true)
  if (isSelf.value) {
    tasksStore.checkRecentJobs()
  }
})
</script>

<style scoped>
.user-profile-view {
  padding: 24px 0 48px var(--content-padding, 24px);
  width: 100%;
  max-width: 100%;
  min-height: calc(100vh - 120px);
  box-sizing: border-box;
  container-type: inline-size;
  container-name: userprofile;
}

@media (min-width: 1024px) {
  .user-profile-view {
    padding: 24px 0 48px var(--content-padding, 32px);
  }
}

.profile-hero-wrapper,
.profile-imports-wrapper,
.tab-pane-bounded,
.user-tabs-bar {
  max-width: var(--content-max-width, 1400px);
  margin-right: var(--content-padding, 24px);
  box-sizing: border-box;
}

@media (min-width: 1024px) {
  .profile-hero-wrapper,
  .profile-imports-wrapper,
  .tab-pane-bounded,
  .user-tabs-bar {
    margin-right: var(--content-padding, 32px);
  }
}

.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 80px 0;
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: var(--content-max-width, 1400px);
  margin-right: var(--content-padding, 24px);
  box-sizing: border-box;
}

@media (min-width: 1024px) {
  .empty-state {
    margin-right: var(--content-padding, 32px);
  }
}

.empty-icon {
  color: var(--c-accent);
  opacity: 0.8;
  margin-bottom: 8px;
}

.empty-state h2 {
  font-size: 20px;
  font-weight: 700;
  color: var(--c-text-1, #fff);
}

.empty-state p {
  color: var(--c-text-2);
  font-size: 14px;
  max-width: 320px;
}

.btn-pill-secondary {
  padding: 10px 24px;
  border-radius: var(--r-full, 9999px);
  background: var(--c-bg-3, #222);
  color: var(--c-text-1, #fff);
  border: none;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  margin-top: 8px;
}

/* Tab Bar */
.user-tabs-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
  padding-bottom: 4px;
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

.user-tabs-bar::-webkit-scrollbar {
  display: none;
}

.user-tabs-bar::after {
  content: '';
  flex-shrink: 0;
  width: 8px;
}

.user-tab-btn {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  gap: 8px;
  height: 42px;
  padding: 0 20px;
  border-radius: 9999px;
  background: var(--c-bg-2, #181818);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--c-text-2, rgba(255, 255, 255, 0.7));
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
  transition: all 0.2s cubic-bezier(0.2, 0.8, 0.2, 1);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  flex-shrink: 0;
}

.user-tab-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--c-text-1, #fff);
  border-color: rgba(255, 255, 255, 0.15);
  transform: translateY(-1px);
}

.user-tab-btn.active {
  background: var(--c-accent, #1db954);
  color: #000;
  font-weight: 700;
  border-color: var(--c-accent, #1db954);
  box-shadow: 0 4px 16px rgba(29, 185, 84, 0.4);
}

.user-tab-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.1);
  color: inherit;
  transition: background 0.2s;
}

.user-tab-btn.active .user-tab-badge {
  background: rgba(0, 0, 0, 0.18);
  color: #000;
}

.tab-pane {
  min-height: 200px;
  width: 100%;
  max-width: 100%;
  min-width: 0;
}

/* SC/SP Tab Variants */
.sc-badge-inline {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #ff5500;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  border-radius: 4px;
  padding: 1px 5px;
  line-height: 1.2;
  letter-spacing: 0.5px;
}

.user-tab-btn.sc-tab-btn {
  border-color: rgba(255, 85, 0, 0.25);
}

.user-tab-btn.sc-tab-btn.active {
  background: #ff5500;
  color: #fff;
  border-color: #ff5500;
  box-shadow: 0 4px 16px rgba(255, 85, 0, 0.4);
}

.sc-badge-num {
  background: rgba(255, 85, 0, 0.2) !important;
  color: #ff7733 !important;
}

.user-tab-btn.sc-tab-btn.active .sc-badge-num {
  background: rgba(0, 0, 0, 0.25) !important;
  color: #fff !important;
}

.sp-badge-num {
  background: rgba(29, 185, 84, 0.2) !important;
  color: #1db954 !important;
}

.user-tab-btn.sp-tab-btn.active .sp-badge-num {
  background: rgba(0, 0, 0, 0.25) !important;
  color: #000 !important;
}

/* Responsive */
@media (max-width: 768px) {
  .user-profile-view {
    padding: 16px 0 calc(var(--player-height, 70px) + var(--nav-height, 64px) + 32px) var(--content-padding, 16px);
    max-width: 100%;
  }

  .profile-hero-wrapper,
  .profile-imports-wrapper,
  .tab-pane-bounded,
  .empty-state {
    margin-right: var(--content-padding, 16px);
  }

  .user-tabs-bar {
    gap: 8px;
    margin-bottom: 20px;
    padding-bottom: 6px;
    margin-left: -16px;
    margin-right: -16px;
    padding-left: 16px;
    padding-right: 16px;
    width: calc(100% + 32px);
    max-width: calc(100% + 32px);
  }

  .user-tab-btn {
    height: 38px;
    padding: 0 14px;
    font-size: 13px;
  }

  .user-tab-badge {
    font-size: 10px;
    padding: 1px 6px;
  }
}

@media (max-width: 480px) {
  .user-profile-view {
    padding: 14px 0 calc(var(--player-height, 70px) + var(--nav-height, 64px) + 32px) var(--content-padding, 14px);
  }

  .profile-hero-wrapper,
  .profile-imports-wrapper,
  .tab-pane-bounded,
  .empty-state {
    margin-right: var(--content-padding, 14px);
  }

  .user-tabs-bar {
    margin-left: -14px;
    margin-right: -14px;
    padding-left: 14px;
    padding-right: 14px;
    width: calc(100% + 28px);
    max-width: calc(100% + 28px);
    gap: 6px;
  }

  .user-tab-btn {
    height: 36px;
    padding: 0 12px;
    font-size: 12.5px;
    gap: 6px;
  }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
