<template>
  <div class="app spotify-theme" :class="appClasses">
    <!-- Auth checking state (only for sessions without local credentials) -->
    <div v-if="!authStore.isAuthenticated && authStore.loading && !authStore.initialized" class="auth-loading">
      <div class="auth-spinner"></div>
    </div>

    <!-- Main content with router -->
    <template v-else>
      <!-- Desktop Sidebar -->
      <Sidebar v-if="isDesktop && authStore.isAuthenticated" />

      <!-- Main Content Wrapper -->
      <div class="main-content-wrapper">
        <!-- Header for authenticated pages (mobile + desktop detail pages) -->
        <PageHeader 
          v-if="showHeader"
          :title="pageTitle"
          :showBack="showBackButton"
          @goBack="goBack"
        >
          <template v-if="route.name === 'library' && libraryTabId === 'overview'" #icon>
            <component :is="libraryIcon" :size="20" />
          </template>
          <template #actions>
            <button
              v-if="route.name === 'library' && libraryTabId === 'overview' && !isDesktop"
              class="header-action-btn"
              @click="tasksStore.openImportModal()"
              title="Импорт музыки из SoundCloud / Spotify"
              aria-label="Импорт музыки"
            >
              <CloudDownload :size="18" />
            </button>
            <button
              v-if="route.name === 'library' && libraryTabId === 'overview' && !isDesktop"
              class="header-action-btn"
              @click="openLibrarySearch"
              title="Поиск по медиатеке"
              aria-label="Поиск по медиатеке"
            >
              <Search :size="18" />
            </button>
            <button
              v-if="authStore.user && !isDesktop"
              class="header-profile-btn"
              :class="{ 'has-active-imports': tasksStore.hasActiveImports }"
              @click="showProfileMenu = true"
              @contextmenu.prevent="showProfileMenu = true"
              v-longpress="() => { showProfileMenu = true }"
              :title="tasksStore.hasActiveImports ? `Импорт: ${tasksStore.overallProgress}% (открыть меню профиля)` : 'Меню профиля'"
            >
              <div class="header-avatar-badge" :class="{ 'importing': tasksStore.hasActiveImports }">
                <img v-if="authStore.userAvatarUrl" :src="authStore.userAvatarUrl" class="header-avatar-img" />
                <span v-else>{{ userInitials }}</span>
                <div v-if="tasksStore.hasActiveImports" class="header-import-spinner-ring"></div>
              </div>
              <span v-if="tasksStore.hasActiveImports" class="header-import-pill">
                {{ tasksStore.overallProgress }}%
              </span>
            </button>
          </template>
        </PageHeader>

        <!-- Router view -->
        <main ref="scrollRef" class="main-content">
          <!-- Pull-to-refresh indicator -->
          <div
            v-if="pullDistance > 0 || isRefreshing"
            class="pull-to-refresh-indicator"
            :style="{ transform: `translateY(${isRefreshing ? 60 : pullDistance}px)` }"
          >
            <div class="ptr-spinner" :class="{ active: isRefreshing, ready: isPulling }">
              <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
            </div>
            <span class="ptr-text">{{ isRefreshing ? 'Обновление…' : isPulling ? 'Отпустите' : 'Потяните вниз' }}</span>
          </div>

          <router-view v-slot="{ Component }">
            <keep-alive :include="['HomeView', 'LibraryView', 'LikedTracksView', 'FriendsView', 'SearchView']">
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </main>

      </div>

      <!-- Mobile Footer (Player + Navigation) -->
      <MobileFooter
        v-if="showNav && !isDesktop"
        :showPlayer="!!playerStore.currentTrack"
        :currentTrack="playerStore.currentTrack"
        :isPlaying="playerStore.isPlaying"
        :loading="playerStore.loading"
        :progress="playerStore.progress"
        :duration="playerStore.duration"
        :buffered="playerStore.buffered"
        :isLiked="isCurrentTrackLiked"
        @expand-player="showFullPlayer = true"
        @toggle-play="playerStore.togglePlay()"
        @next-track="playerStore.next()"
        @prev-track="playerStore.prev()"
        @toggle-shuffle="playerStore.toggleShuffle()"
        @toggle-repeat="playerStore.toggleRepeat()"
        @like="handleToggleLike"
      />

      <!-- Desktop: Now Playing Sidebar -->
      <NowPlayingSidebar 
        v-if="isNowPlayingActive" 
        @goToUser="handleGoToUser"
      />

      <!-- Desktop: Bottom Player -->
      <DesktopPlayer 
        v-if="isDesktop && playerStore.currentTrack && authStore.isAuthenticated"
        @expand="showFullPlayer = true"
      />

      <!-- Full player modal - Desktop version -->
      <FullPlayerDesktop
        v-if="isDesktop"
        :show="showFullPlayer"
        :track="playerStore.currentTrack"
        :is-playing="playerStore.isPlaying"
        :loading="playerStore.loading"
        :progress="playerStore.progress"
        :duration="playerStore.duration"
        :buffered="playerStore.buffered"
        :volume="playerStore.volume"
        :is-muted="playerStore.isMuted"
        :shuffle="playerStore.shuffle"
        :repeat="playerStore.repeat"
        :is-liked="isCurrentTrackLiked"
        :upcoming-queue="upcomingTracks"
        :queue-length="playerStore.queue.length"
        :history-tracks="historyTracks"
        :hd-track-info="playerStore.hdTrackInfo"
        :lazy-shuffle-mode="playerStore.isLazyShuffleMode()"
        :lazy-shuffle-total="playerStore.lazyShuffleIds?.length || 0"
        :lazy-shuffle-index="playerStore.lazyShuffleIndex"
        :context-info="playerStore.lazyShuffleContext"
        @close="showFullPlayer = false"
        @toggle="playerStore.togglePlay()"
        @next="playerStore.next()"
        @prev="playerStore.prev()"
        @seek="playerStore.seek($event)"
        @setVolume="playerStore.setVolume($event)"
        @toggleMute="playerStore.toggleMute()"
        @toggleShuffle="playerStore.toggleShuffle()"
        @toggleRepeat="playerStore.toggleRepeat()"
        @playFromQueue="playerStore.playFromQueue($event)"
        @like="handleToggleLike"
      />

      <!-- Full player modal - Mobile version -->
      <Transition name="player-slide">
        <FullPlayer 
          v-if="showFullPlayer && !isDesktop"
          :track="playerStore.currentTrack"
          :is-playing="playerStore.isPlaying"
          :loading="playerStore.loading"
          :progress="playerStore.progress"
          :duration="playerStore.duration"
          :buffered="playerStore.buffered"
          :volume="playerStore.volume"
          :is-muted="playerStore.isMuted"
          :shuffle="playerStore.shuffle"
          :repeat="playerStore.repeat"
          :queue="playerStore.queue"
          :queue-index="playerStore.queueIndex"
          :shuffle-order="playerStore.shuffleOrder"
          :shuffle-index="playerStore.shuffleIndex"
          :is-liked="isCurrentTrackLiked"
          :lazy-shuffle-mode="playerStore.isLazyShuffleMode()"
          :lazy-shuffle-total="playerStore.lazyShuffleIds?.length || 0"
          :lazy-shuffle-index="playerStore.lazyShuffleIndex"
          @close="showFullPlayer = false"
          @toggle="playerStore.togglePlay()"
          @next="playerStore.next()"
          @prev="playerStore.prev()"
          @seek="playerStore.seek($event)"
          @setVolume="playerStore.setVolume($event)"
          @toggleMute="playerStore.toggleMute()"
          @toggleShuffle="playerStore.toggleShuffle()"
          @toggleRepeat="playerStore.toggleRepeat()"
          @removeFromQueue="playerStore.removeFromQueue($event)"
          @moveInQueue="playerStore.moveInQueue($event.from, $event.to)"
          @playFromQueue="playerStore.playFromQueue($event)"
          @like="handleToggleLike"
        />
      </Transition>
      
      <!-- Channel setup banner -->
      <ChannelBanner />
      
      <!-- Global context menu (universal for all element types) -->
      <ContextMenu />
      
      <!-- Network status banner (unified for offline, server down, reconnecting, maintenance) -->
      <NetworkBanner />
      
      <!-- Server update prompt banner -->
      <AppUpdatePrompt />
      
      <!-- PWA install banner & guide modal -->
      <PwaInstallBanner />
      <PwaInstallModal />
      <WelcomeGuideModal />

      <!-- Global toast notifications -->
      <ToastContainer />

      <!-- Global Ingestion Modals (accessible from any screen) -->
      <ExportifyImportModal 
        :show="tasksStore.showExportifyModal"
        @close="tasksStore.closeExportifyModal"
        @imported="libraryStore.fetchTracks({ refresh: true })"
      />

      <ImportModal
        :show="tasksStore.showImportModal"
        @close="tasksStore.closeImportModal"
        @imported="libraryStore.fetchTracks({ refresh: true })"
      />

      <!-- Share Modal -->
      <ShareModal />

      <!-- Channel Access Required Modal -->
      <Teleport to="body">
        <div v-if="channelAccessModal.show" class="channel-access-overlay" @click.self="channelAccessModal.show = false">
          <div class="channel-access-dialog">
            <div class="channel-access-icon">📢</div>
            <h3>Требуется доступ к каналу</h3>
            <p>
              Бот <strong v-if="channelAccessModal.botUsername">@{{ channelAccessModal.botUsername }}</strong>
              не имеет доступа к вашему каналу
              <strong v-if="channelAccessModal.channelTitle">«{{ channelAccessModal.channelTitle }}»</strong>.
            </p>
            <p class="channel-access-sub">
              Чтобы воспроизводить треки и обновлять ссылки, добавьте бота администратором в канал с правом публикации сообщений.
            </p>
            <div class="channel-access-actions">
              <button class="action-btn primary" @click="goToChannelSettings">
                Настройки канала
              </button>
              <button class="action-btn secondary" @click="channelAccessModal.show = false">
                Понятно
              </button>
            </div>
          </div>
        </div>
      </Teleport>

      <!-- Profile Context Menu -->
      <ProfileMenu v-if="authStore.isAuthenticated" v-model="showProfileMenu" placement="header" />
    </template>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, inject, watch, nextTick, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { usePlayerStore } from '@/stores/player'
import { useLibraryStore } from '@/stores/library'
import { useUIStore } from '@/stores/ui'
import { useTasksStore } from '@/stores/tasks'
import { useExternalAccountsStore } from '@/stores/externalAccounts'
import { useModals } from '@/composables/useModals'
import PageHeader from '@/components/PageHeader.vue'
import ToastContainer from '@/components/ToastContainer.vue'
import { MobileFooter } from '@/components/layout'
import { useNetworkMonitor } from '@/composables/useNetworkMonitor'
import { usePullToRefresh } from '@/composables/usePullToRefresh'
import { usePwaInstall } from '@/composables/usePwaInstall'
import { useWelcomeGuide } from '@/composables/useWelcomeGuide'
import { initAppUpdateListeners } from '@/composables/useAppUpdate'
import { Library, CloudDownload, Search } from 'lucide-vue-next'
import { tracksApi } from '@/api/client'
import { useContextMenu } from '@/composables/useContextMenu'

// Heavy and conditional components loaded asynchronously on demand
const FullPlayer = defineAsyncComponent(() => import('@/components/FullPlayer.vue'))
const FullPlayerDesktop = defineAsyncComponent(() => import('@/components/desktop/FullPlayerDesktop.vue'))
const Sidebar = defineAsyncComponent(() => import('@/components/desktop/Sidebar.vue'))
const DesktopPlayer = defineAsyncComponent(() => import('@/components/desktop/DesktopPlayer.vue'))
const NowPlayingSidebar = defineAsyncComponent(() => import('@/components/desktop/NowPlayingSidebar.vue'))
const ContextMenu = defineAsyncComponent(() => import('@/components/ContextMenu.vue'))
const ProfileMenu = defineAsyncComponent(() => import('@/components/layout/ProfileMenu.vue'))
const ShareModal = defineAsyncComponent(() => import('@/components/ShareModal.vue'))
const PwaInstallModal = defineAsyncComponent(() => import('@/components/PwaInstallModal.vue'))
const PwaInstallBanner = defineAsyncComponent(() => import('@/components/PwaInstallBanner.vue'))
const WelcomeGuideModal = defineAsyncComponent(() => import('@/components/WelcomeGuideModal.vue'))
const AppUpdatePrompt = defineAsyncComponent(() => import('@/components/AppUpdatePrompt.vue'))
const ChannelBanner = defineAsyncComponent(() => import('@/components/ChannelBanner.vue'))
const NetworkBanner = defineAsyncComponent(() => import('@/components/NetworkBanner.vue'))
const ExportifyImportModal = defineAsyncComponent(() => import('@/components/ExportifyImportModal.vue'))
const ImportModal = defineAsyncComponent(() => import('@/components/ImportModal.vue'))

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const playerStore = usePlayerStore()
const libraryStore = useLibraryStore()
const uiStore = useUIStore()
const tasksStore = useTasksStore()
const externalAccountsStore = useExternalAccountsStore()
const telegram = inject('telegram')
const networkMonitor = useNetworkMonitor()
const pwaInstall = usePwaInstall()
const welcomeGuide = useWelcomeGuide()
const showProfileMenu = ref(false)

// Pull-to-refresh — bound to .main-content via scrollRef
const { scrollRef, pullDistance, isPulling, isRefreshing } = usePullToRefresh(
  () => libraryStore.refresh(),
  { telegram }
)

const { showFullPlayer } = useModals(telegram)
const { isOpen: isContextMenuOpen, closeMenu: closeContextMenu } = useContextMenu()

// Scroll to top helper for tab clicks and resets
const scrollToContentTop = () => {
  if (scrollRef.value) {
    scrollRef.value.scrollTo({ top: 0, behavior: 'smooth' })
  }
  const scrollContainers = document.querySelectorAll(
    '.main-content, .main-content-wrapper, .library-view, .collections-view, .layout-content, .virtual-track-list'
  )
  scrollContainers.forEach((el) => {
    if (el && el.scrollTop > 0) {
      el.scrollTo({ top: 0, behavior: 'smooth' })
    }
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
  if (document.documentElement) {
    document.documentElement.scrollTo({ top: 0, behavior: 'smooth' })
  }
}

const handleLibraryTabClick = (tabId) => {
  uiStore.setLibraryTab(tabId)
  nextTick(() => {
    scrollToContentTop()
  })
}

// Responsive detection & auto-collapse calculation
// Responsive detection & adaptive sidebar state management
// Desktop layout is >= 768px, Mobile layout is < 768px
const isDesktop = ref(window.innerWidth >= 768)

const updateLayoutState = () => {
  const width = window.innerWidth
  isDesktop.value = width >= 768

  if (!isDesktop.value) {
    return
  }

  // GEOMETRIC BOUNDARY RULES (Center space NEVER drops below 600px):
  // 1. Screens >= 1200px (Wide desktop):
  //    Both sidebars fit comfortably (280px + >=600px center + 320px = >=1200px).
  if (width >= 1200) {
    const isLeftCollapsed = uiStore.userCollapsedPreference === true
    uiStore.setSidebarCollapsed(isLeftCollapsed, false)

    const isRightVisible = uiStore.userNowPlayingPreference !== false
    uiStore.setNowPlayingSidebar(isRightVisible, false)
    uiStore.isAutoCollapsed = false
    uiStore.isRightAutoHidden = false
  }
  // 2. Screens < 1200px (Narrow desktop / Laptop / Tablet):
  //    280px left + 320px right = 600px sidebars, which would crush center!
  //    Therefore, Full Left Sidebar and Right Sidebar are MUTUALLY EXCLUSIVE:
  else {
    if (uiStore.userNowPlayingPreference === true && uiStore.lastActiveSidebar === 'right') {
      // User explicitly wants Now Playing panel open on laptop:
      // Left sidebar MUST yield to rail (72px) so center stays spacious
      uiStore.setNowPlayingSidebar(true, false)
      uiStore.setSidebarCollapsed(true, false)
      uiStore.isAutoCollapsed = true
      uiStore.isRightAutoHidden = false
    } else {
      // Default: Right sidebar closes first, Left sidebar stays full (or rail if user preferred)!
      uiStore.setNowPlayingSidebar(false, false)
      uiStore.isRightAutoHidden = (uiStore.userNowPlayingPreference === true)
      const isLeftCollapsed = uiStore.userCollapsedPreference === true
      uiStore.setSidebarCollapsed(isLeftCollapsed, false)
      uiStore.isAutoCollapsed = false
    }
  }
}

const handleWindowResize = () => {
  updateLayoutState()
}

// Watch track presence changes to update layout when playback starts or stops
watch(() => !!playerStore.currentTrack, () => {
  if (isDesktop.value) {
    updateLayoutState()
  }
})

// Keyboard shortcuts: Ctrl+B / Cmd+B to toggle left sidebar, Ctrl+J to toggle right sidebar
const handleGlobalKeyDown = (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b' && isDesktop.value) {
    e.preventDefault()
    uiStore.toggleSidebarCollapse()
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'j' && isDesktop.value && playerStore.currentTrack) {
    e.preventDefault()
    uiStore.toggleNowPlayingSidebar()
  }
}

const isNowPlayingActive = computed(() => {
  return isDesktop.value && !!playerStore.currentTrack && authStore.isAuthenticated && uiStore.isNowPlayingSidebarVisible
})

// App classes for layout
const appClasses = computed(() => ({
  'has-player': !!playerStore.currentTrack,
  'desktop-layout': isDesktop.value && authStore.isAuthenticated,
  'has-now-playing': isNowPlayingActive.value,
  'sidebar-collapsed': isDesktop.value && authStore.isAuthenticated && uiStore.isSidebarCollapsed
}))

// Computed property for like state based on libraryStore.likedTracks + currentTrack
const isCurrentTrackLiked = computed(() => {
  const track = playerStore.currentTrack
  if (!track?.id) return false
  if (libraryStore.isTrackLiked(track.id)) return true
  return track.is_liked === true
})

// Navigation visibility
const showNav = computed(() => {
  return authStore.isAuthenticated && route.name !== 'login'
})

const showHeader = computed(() => {
  // On desktop, show header only for drill-down detail pages (not main navigation pages)
  if (isDesktop.value) {
    const detailRoutes = ['album-detail', 'artist-detail', 'playlist-detail']
    return authStore.isAuthenticated && detailRoutes.includes(route.name)
  }
  // On mobile, show header for all authenticated pages except login
  const noHeaderRoutes = ['login']
  return authStore.isAuthenticated && !noHeaderRoutes.includes(route.name)
})

const showBackButton = computed(() => {
  if (route.name === 'search' && (route.query.tab || route.query.mode)) {
    return true
  }
  // Library subtabs need a back button on mobile (header shows back + title)
  if (route.name === 'library' && libraryTabId.value !== 'overview') {
    return true
  }
  // Main navigation tabs do not need a back button
  const mainNavRoutes = ['home', 'library', 'search', 'friends', 'liked', 'settings']
  return !mainNavRoutes.includes(route.name)
})

const pageTitle = computed(() => {
  if (route.name === 'home') {
    return 'Главная'
  }
  if (route.name === 'search') {
    if (route.query.tab === 'soundcloud') {
      return route.query.mode === 'likes' ? 'SoundCloud • Лайки' : 'SoundCloud'
    }
    if (route.query.tab === 'spotify') {
      return route.query.mode === 'likes' ? 'Spotify • Лайки' : (route.query.mode === 'exportify' ? 'Spotify • Exportify' : 'Spotify')
    }
    return 'Поиск'
  }
  // Для страницы библиотеки заголовок зависит от активного подраздела
  if (route.name === 'library') {
    const libraryTitles = {
      tracks: 'Все треки',
      playlists: 'Плейлисты',
      albums: 'Альбомы',
      artists: 'Артисты',
      overview: 'Медиатека',
    }
    return libraryTitles[libraryTabId.value] || 'Медиатека'
  }
  
  const titles = {
    collections: 'Коллекции',
    albums: 'Альбомы',
    'album-detail': route.params?.name || 'Альбом',
    artists: 'Исполнители',
    'artist-detail': decodeURIComponent(route.params?.name || 'Артист'),
    playlists: 'Плейлисты',
    'playlist-detail': 'Плейлист',
    friends: 'Кенты',
    'user-profile': 'Профиль',
    settings: 'Настройки',
    liked: 'Любимое',
    downloaded: 'Скачанное',
    'my-profile': 'Профиль',
  }
  return titles[route.name] || ''
})

const libraryIcon = computed(() => Library)

const libraryTabId = computed(() => uiStore.libraryTab || 'overview')

const openLibrarySearch = () => {
  router.push({ path: '/library', query: { tab: 'tracks' } })
}

const userInitials = computed(() => {
  const name = authStore.userDisplayName
  if (name) return name.charAt(0).toUpperCase()
  const u = authStore.user
  if (!u) return '?'
  if (u.first_name) return u.first_name.charAt(0).toUpperCase()
  if (u.username) return u.username.charAt(0).toUpperCase()
  return '?'
})

// Queue computeds for desktop player
const upcomingTracks = computed(() => {
  if (playerStore.isLazyShuffleMode && playerStore.isLazyShuffleMode()) {
    return playerStore.lazyUpcomingTracks || []
  }
  if (playerStore.shuffle) {
    // In shuffle mode, use shuffle order
    const currentShuffleIdx = playerStore.shuffleIndex
    return playerStore.shuffleOrder
      .slice(currentShuffleIdx + 1)
      .map(idx => playerStore.queue[idx])
      .filter(t => t)
  } else {
    // In normal mode, use queue order
    return playerStore.queue.slice(playerStore.queueIndex + 1)
  }
})

const historyTracks = computed(() => {
  if (playerStore.shuffle) {
    // In shuffle mode, get previous tracks from shuffle order
    const currentShuffleIdx = playerStore.shuffleIndex
    return playerStore.shuffleOrder
      .slice(0, currentShuffleIdx)
      .map(idx => playerStore.queue[idx])
      .filter(t => t)
      .reverse()
  } else {
    // In normal mode, get previous tracks
    return playerStore.queue.slice(0, playerStore.queueIndex).reverse()
  }
})

const goBack = () => {
  if (route.name === 'library' && uiStore.libraryTab !== 'overview') {
    uiStore.setLibraryTab('overview')
    router.replace({ path: '/library', query: {} })
    return
  }
  if (route.name === 'search' && (route.query.tab || route.query.mode)) {
    if (window.history.length > 1) {
      router.back()
    } else {
      router.replace({ path: '/search', query: {} })
    }
    return
  }
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/')
  }
}

// === Mobile Back Gesture & Overlays Coordinator ===
let isPoppingModalHistory = false

// Synchronize browser history with FullPlayer sheet so Android/iOS back swipe closes it smoothly
watch(showFullPlayer, (isOpen, wasOpen) => {
  if (isOpen && !wasOpen) {
    if (!isPoppingModalHistory) {
      const currentState = window.history.state || {}
      window.history.pushState({ ...currentState, modal: 'fullplayer' }, '', window.location.href)
    }
  } else if (!isOpen && wasOpen) {
    // If closed programmatically (close button or swipe down gesture), pop history entry
    if (!isPoppingModalHistory && window.history.state?.modal === 'fullplayer') {
      isPoppingModalHistory = true
      window.history.back()
    }
  }
})

// === Channel Access Required Modal State ===
const channelAccessModal = ref({
  show: false,
  channelTitle: '',
  botUsername: ''
})

// Unified dismissal of topmost overlay: returns true if an overlay was consumed
const dismissTopOverlay = () => {
  // 1. Context menu
  if (isContextMenuOpen?.value) {
    closeContextMenu()
    return true
  }
  // 2. Profile menu
  if (showProfileMenu.value) {
    showProfileMenu.value = false
    return true
  }
  // 3. Exportify import modal
  if (tasksStore.showExportifyModal) {
    tasksStore.closeExportifyModal()
    return true
  }
  // 4. Ingestion import modal
  if (tasksStore.showImportModal) {
    tasksStore.closeImportModal()
    return true
  }
  // 5. Channel access modal
  if (channelAccessModal.value?.show) {
    channelAccessModal.value.show = false
    return true
  }
  // 6. Full player sheet
  if (showFullPlayer.value) {
    isPoppingModalHistory = true
    showFullPlayer.value = false
    nextTick(() => {
      isPoppingModalHistory = false
    })
    return true
  }
  return false
}

// Window popstate listener (fired by mobile system edge-swipe back, browser back button, Android back)
const handleWindowPopState = () => {
  // If we triggered history.back() programmatically when closing an overlay, ignore this popstate
  if (isPoppingModalHistory) {
    isPoppingModalHistory = false
    return
  }

  // If any overlay was open, consume the back gesture and close only that overlay
  if (dismissTopOverlay()) {
    return
  }
}

// === Native Telegram BackButton Integration ===
const syncTelegramBackButton = () => {
  const tgBackButton = telegram?.BackButton || window.Telegram?.WebApp?.BackButton
  if (!tgBackButton) return

  const hasOverlay = showFullPlayer.value || 
    isContextMenuOpen?.value || 
    showProfileMenu.value || 
    tasksStore.showExportifyModal || 
    tasksStore.showImportModal || 
    channelAccessModal.value?.show

  if (hasOverlay || showBackButton.value) {
    try {
      tgBackButton.show()
    } catch (_) {}
  } else {
    try {
      tgBackButton.hide()
    } catch (_) {}
  }
}

watch([
  showBackButton, 
  showFullPlayer, 
  () => isContextMenuOpen?.value, 
  showProfileMenu,
  () => tasksStore.showExportifyModal,
  () => tasksStore.showImportModal,
  () => channelAccessModal.value?.show
], () => {
  syncTelegramBackButton()
}, { immediate: true })

const handleTelegramBackClick = () => {
  if (dismissTopOverlay()) {
    return
  }
  if (showBackButton.value) {
    goBack()
  }
}

// Toggle like for current track
const handleToggleLike = async () => {
  if (playerStore.currentTrack?.id) {
    const current = isCurrentTrackLiked.value
    await libraryStore.toggleLike(playerStore.currentTrack.id, current)
  }
}

// Handle navigation to user profile
const handleGoToUser = (user) => {
  if (!user?.id) return
  showFullPlayer.value = false
  // Navigate to friends page and pass user info via query
  router.push({ 
    name: 'friends',
    query: { viewUser: user.id }
  })
}

// Handle auth:logout event (triggered by API interceptor on 401)
const handleAuthLogout = () => {
  authStore.logout()
  playerStore.stop()
  // Force full page reload to clear all store states only if not already on the login page
  if (window.location.pathname !== '/login' && !window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

// Apply UI scale to documentElement (root)
const applyUIScale = () => {
  const scale = Number(playerStore.uiScale) || 1.0
  // Clear any residual zoom on body
  document.body.style.zoom = ''
  // Apply zoom to documentElement for full-viewport scaling without gaps or overflow clipping
  document.documentElement.style.zoom = scale
  document.documentElement.style.setProperty('--ui-scale', String(scale))
  updateLayoutState()
  window.dispatchEvent(new Event('resize'))
}

// Watch for scale changes
watch(
  () => playerStore.uiScale,
  () => applyUIScale(),
  { immediate: true }
)

// === Player error toast handlers (hoisted for cleanup in onUnmounted) ===
const handlePlayerError = (e) => {
  const { type, track, message, errorCode } = e.detail || {}
  const trackTitle = track?.title || 'Трек'
  
  switch (type) {
    case 'cascade_error':
      uiStore.toast.error('Воспроизведение остановлено', 'Слишком много ошибок подряд. Попробуйте позже.')
      break
    case 'audio_error':
      if (errorCode === 2) {
        uiStore.toast.warning('Ошибка сети', `Не удалось загрузить «${trackTitle}», переключаем...`)
      } else if (errorCode === 3) {
        uiStore.toast.warning('Ошибка декодирования', `Не удалось декодировать «${trackTitle}»`)
      } else {
        uiStore.toast.warning('Ошибка аудио', `Проблема с «${trackTitle}», переключаем...`)
      }
      break
    case 'stall_timeout':
      uiStore.toast.warning('Медленная загрузка', `Трек «${trackTitle}» не загружается — проблемы с сетью`)
      break
    case 'not_found':
      uiStore.toast.error('Трек не найден', `«${trackTitle}» больше недоступен`)
      break
    case 'playback_error':
      uiStore.toast.warning('Ошибка воспроизведения', message || `Проблема с «${trackTitle}»`)
      break
    case 'offline_unavailable':
      uiStore.toast.info('Оффлайн-режим', `«${trackTitle}» не сохранён для оффлайн-прослушивания`)
      break
    case 'auth_expired':
      // Silent — will auto-skip, not worth showing toast
      break
  }
}

const handleStallRecovered = (e) => {
  const { track, attempt } = e.detail || {}
  if (attempt > 1) {
    uiStore.toast.info('Восстановлено', `Воспроизведение «${track?.title || 'Трек'}» восстановлено`)
  }
}

const handleNetworkRecovered = () => {
  uiStore.toast.success('Сеть восстановлена', 'Соединение восстановлено')
}

const handleChannelAccessRequired = (e) => {
  const detail = e.detail || {}
  channelAccessModal.value = {
    show: true,
    channelTitle: detail.channel_title || authStore.channelInfo?.channel_title || '',
    botUsername: detail.bot_username || authStore.botUsername || ''
  }
}

const goToChannelSettings = () => {
  channelAccessModal.value.show = false
  router.push('/settings#channel')
}

// Initialize auth on mount
onMounted(async () => {
  // Apply initial scale
  applyUIScale()

  // Deep linking helper
  const handleStartParams = async () => {
    try {
      const urlParams = new URLSearchParams(window.location.search)
      const startParam = 
        telegram?.initDataUnsafe?.start_param || 
        window.Telegram?.WebApp?.initDataUnsafe?.start_param || 
        urlParams.get('startapp') || 
        urlParams.get('tgWebAppStartParam')
      
      if (!startParam) return

      console.log('[DeepLink] Processing start_param:', startParam)

      if (startParam.startsWith('user_')) {
        const uid = startParam.replace('user_', '')
        if (uid) {
          router.push(`/user/${uid}`)
        }
      } else if (startParam.startsWith('playlist_')) {
        const pid = startParam.replace('playlist_', '')
        if (pid) {
          router.push(`/playlist/${pid}`)
        }
      } else if (startParam.startsWith('album_')) {
        const aid = startParam.replace('album_', '')
        if (aid) {
          router.push(`/album/${aid}`)
        }
      } else if (startParam === 'minecraft_media' || startParam === 'minecraft_media_guide') {
        // Minecraft deep-link: open system playlist (ensure it exists via auth ensure)
        try {
          const { playlistsApi } = await import('@/api')
          // Fetch my playlists and find system one
          const resp = await playlistsApi.getAll()
          const items = resp.data?.items || resp.data || []
          const sys = items.find(pl => pl.is_system)
          if (sys) {
            router.push(`/playlist/${sys.id}`)
          } else {
            // Fallback: try to get system playlist via minecraft endpoint (requires auth, but we are authenticated)
            // If not found, just go home where playlist will appear after ensure
            router.push('/')
          }
        } catch (e) {
          console.error('[DeepLink] minecraft_media failed:', e)
          router.push('/')
        }
      } else if (startParam.startsWith('track_')) {
        const tid = Number(startParam.replace('track_', ''))
        if (tid) {
          try {
            const resp = await tracksApi.getOne(tid)
            if (resp.data) {
              await playerStore.play(resp.data)
            }
          } catch (e) {
            console.error('[DeepLink] Failed to load track:', e)
          }
        }
      }
    } catch (err) {
      console.error('[DeepLink] Error handling start parameter:', err)
    }
  }
  
  // Initialize PWA installation check & banner schedule
  pwaInstall.init()
  
  // Add resize & keyboard listeners for responsive detection and shortcuts
  window.addEventListener('resize', handleWindowResize)
  window.addEventListener('keydown', handleGlobalKeyDown)
  updateLayoutState()
  
  // Listen for auth:logout events from API interceptor
  window.addEventListener('auth:logout', handleAuthLogout)
  
  // Initialize auth in background if not already initialized
  if (authStore.isAuthenticated && !authStore.initialized) {
    authStore.initialize().catch(err => console.warn('[App] Background auth initialize error:', err))
  }

  // Defer secondary background checks so they don't compete with initial view data rendering
  const scheduleSecondaryInit = typeof window !== 'undefined' && window.requestIdleCallback
    ? window.requestIdleCallback
    : (cb) => setTimeout(cb, 1500)

  scheduleSecondaryInit(() => {
    if (authStore.isAuthenticated) {
      externalAccountsStore.fetchSoundCloud().catch(() => {})
      externalAccountsStore.fetchLastSpotifyImport().catch(() => {})
      tasksStore.checkRecentJobs().catch(() => {})
      libraryStore.startSyncPolling()
    }
  })

  window.addEventListener('focus', handleWindowFocus)

  // Restore player state if available (persisted queue, track, position) without blocking UI
  if (playerStore.hasSavedState() && !playerStore.currentTrack && !playerStore.isPlaying) {
    playerStore.restoreState()
  }

  // Handle deep link / start_param navigation
  await handleStartParams()
  
  // Trigger onboarding guide if eligible
  try {
    const rawParam = telegram?.initDataUnsafe?.start_param || ''
    const urlParams = new URLSearchParams(window.location.search)
    const urlStartApp = urlParams.get('startapp') || urlParams.get('tgWebAppStartParam') || ''
    const effectiveParam = (rawParam || urlStartApp).toLowerCase().trim()

    const isExplicitMc = 
      effectiveParam === 'minecraft_media_guide' ||
      effectiveParam === 'minecraft_media' || 
      window.location.search.toLowerCase().includes('startapp=minecraft') ||
      window.location.search.toLowerCase().includes('minecraft_media')

    const isExplicitDefault = 
      effectiveParam === 'guide' || 
      effectiveParam === 'meloman_guide' || 
      effectiveParam === 'default_guide' ||
      urlParams.has('guide') ||
      window.location.search.toLowerCase().includes('startapp=guide') ||
      window.location.search.toLowerCase().includes('guide=1') ||
      window.location.search.toLowerCase().includes('guide=default')

    // Detect if user account was created or reset recently (within last 30 minutes)
    let isFreshUser = false
    if (authStore.user?.created_at) {
      const createdMs = new Date(authStore.user.created_at).getTime()
      if (!isNaN(createdMs) && (Date.now() - createdMs) < 30 * 60 * 1000) {
        if (!sessionStorage.getItem('tg_fresh_guide_shown')) {
          isFreshUser = true
          sessionStorage.setItem('tg_fresh_guide_shown', 'true')
        }
      }
    }

    if (isExplicitMc) {
      setTimeout(() => welcomeGuide.openGuide('minecraft'), 300)
    } else if (isExplicitDefault) {
      setTimeout(() => welcomeGuide.openGuide('default'), 300)
    } else if (isFreshUser || (!welcomeGuide.hasSeenGuide('default') && !window.location.search.includes('no_guide'))) {
      setTimeout(() => welcomeGuide.openGuide('default'), 700)
    }
  } catch (err) {
    console.warn('[App] Welcome guide init error:', err)
  }

  // === Start network monitoring & update tracking ===
  networkMonitor.startMonitoring()
  initAppUpdateListeners()
  
  // === Player event listeners ===
  window.addEventListener('player:error', handlePlayerError)
  window.addEventListener('player:stall-recovered', handleStallRecovered)
  window.addEventListener('player:network-recovered', handleNetworkRecovered)
  window.addEventListener('player:channel-access-required', handleChannelAccessRequired)
  
  // Handle unavailable tracks - show notification with helpful message
  playerStore.setOnTrackUnavailable((track, message, isLargeFile) => {
    if (isLargeFile) {
      const sizeMB = track.file_size ? (track.file_size / 1024 / 1024).toFixed(1) : '20+'
      console.warn(`[Player] Track too large for streaming: ${sizeMB} MB`)
      uiStore.toast.warning(
        'Большой файл',
        `Трек (${sizeMB} MB) слишком большой для стриминга. Используйте кнопку скачивания.`
      )
    } else {
      console.warn('[Player] Track unavailable:', message)
      // Check if it's HD format error
      if (message && (message.includes('HD') || message.includes('FLAC') || message.includes('WAV') || message.includes('высокого качества'))) {
        uiStore.toast.warning(
          'Только HD',
          'Этот трек доступен только в HD качестве. Используйте кнопку скачивания.'
        )
      } else {
        uiStore.toast.error(
          'Трек недоступен',
          message || 'Не удалось воспроизвести трек'
        )
      }
    }
  })

  // Hook up native Telegram BackButton
  const tgBackButton = telegram?.BackButton || window.Telegram?.WebApp?.BackButton
  if (tgBackButton) {
    try {
      tgBackButton.onClick(handleTelegramBackClick)
      syncTelegramBackButton()
    } catch (_) {}
  }

  // Hook up popstate for mobile back edge-swipe
  window.addEventListener('popstate', handleWindowPopState)
})

const handleWindowFocus = () => {
  if (authStore.isAuthenticated) {
    tasksStore.checkRecentJobs()
    libraryStore.checkSyncState()
  }
}

// Watch auth state to load external accounts & last import when user logs in
watch(
  () => authStore.isAuthenticated,
  (isAuth) => {
    if (isAuth) {
      externalAccountsStore.fetchSoundCloud()
      externalAccountsStore.fetchLastSpotifyImport()
      tasksStore.checkRecentJobs()
      libraryStore.startSyncPolling()
    } else {
      libraryStore.stopSyncPolling()
    }
  }
)

onUnmounted(() => {
  window.removeEventListener('focus', handleWindowFocus)
  window.removeEventListener('resize', handleWindowResize)
  window.removeEventListener('keydown', handleGlobalKeyDown)
  window.removeEventListener('auth:logout', handleAuthLogout)
  window.removeEventListener('player:error', handlePlayerError)
  window.removeEventListener('player:stall-recovered', handleStallRecovered)
  window.removeEventListener('player:network-recovered', handleNetworkRecovered)
  window.removeEventListener('player:channel-access-required', handleChannelAccessRequired)
  window.removeEventListener('popstate', handleWindowPopState)
  networkMonitor.stopMonitoring()
  libraryStore.stopSyncPolling()

  // Clean up Telegram BackButton listener
  const tgBackButton = telegram?.BackButton || window.Telegram?.WebApp?.BackButton
  if (tgBackButton) {
    try {
      tgBackButton.offClick(handleTelegramBackClick)
    } catch (_) {}
  }

  // Reset zoom on unmount
  document.body.style.zoom = ''
  document.documentElement.style.zoom = ''
  document.documentElement.style.removeProperty('--ui-scale')
})
</script>

<style>
/* App.vue layout styles — tokens are in design-system.css */
/* Global resets (*, html, body) are in style.css — not duplicated here */

.app {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  max-height: 100%;
  min-height: 100%;
  overflow: hidden;
}

/* Desktop Layout - CSS Grid */
.app.desktop-layout {
  display: grid;
  grid-template-columns: var(--sidebar-width) minmax(440px, 1fr);
  grid-template-rows: 1fr auto;
  grid-template-areas:
    "sidebar main"
    "player player";
  height: 100%;
  min-height: 100%;
  max-height: 100%;
  width: 100%;
  overflow: hidden;
  position: relative;
  transition: grid-template-columns 0.24s cubic-bezier(0.16, 1, 0.3, 1);

  /* Adaptive content tokens — cascade to child views */
  --content-padding: 24px;
  --content-max-width: 1400px;
  --card-min-width: 160px;
  --quick-grid-cols: repeat(3, 1fr);
}

.app.desktop-layout.sidebar-collapsed {
  --sidebar-width: var(--sidebar-collapsed-width, 72px);
  --content-max-width: 1600px;
}

.app.desktop-layout.has-now-playing {
  grid-template-columns: var(--sidebar-width) minmax(440px, 1fr) var(--now-playing-width);
  grid-template-areas:
    "sidebar main nowplaying"
    "player player player";
  --content-padding: 16px;
  --content-max-width: 1200px;
  --card-min-width: 136px;
  --quick-grid-cols: repeat(2, 1fr);
}

.app.desktop-layout.sidebar-collapsed.has-now-playing {
  --content-padding: 20px;
  --content-max-width: 1400px;
  --card-min-width: 148px;
  --quick-grid-cols: repeat(3, 1fr);
}

@media (max-width: 850px) {
  .app.desktop-layout.has-now-playing {
    grid-template-columns: var(--sidebar-width) minmax(0, 1fr) var(--now-playing-width);
  }
  .app.desktop-layout.has-now-playing .main-content-wrapper {
    min-width: 0;
  }
}

.app.desktop-layout :deep(.sidebar) {
  grid-area: sidebar;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  position: relative;
  z-index: 5;
}

.app.desktop-layout .main-content-wrapper {
  grid-area: main;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100%;
  min-height: 0;
  min-width: 440px;
  background-color: var(--c-bg-0);
  position: relative;
  z-index: 1;
}

.app.desktop-layout :deep(.now-playing-sidebar) {
  grid-area: nowplaying;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  position: relative;
  z-index: 5;
}

.app.desktop-layout :deep(.desktop-player) {
  grid-area: player;
  width: 100%;
  flex-shrink: 0;
  position: sticky;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 200;
}

/* Mobile layout wrapper */
.main-content-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  min-width: 0;
  background-color: var(--c-bg-0);
}

.main-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  min-height: 0;
  min-width: 0;
  position: relative;
  scrollbar-gutter: stable;
  background-color: var(--c-bg-0);
}

/* Pull-to-refresh indicator */
.pull-to-refresh-indicator {
  position: absolute;
  top: -60px;
  left: 0;
  right: 0;
  height: 60px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: var(--c-text-2);
  font-size: 12px;
  z-index: 10;
  pointer-events: none;
  transition: none;
  will-change: transform;
}

.ptr-spinner {
  width: 24px;
  height: 24px;
  color: var(--c-text-2);
  transition: color 0.15s;
}

.ptr-spinner.ready {
  color: var(--c-accent);
}

.ptr-spinner.active {
  color: var(--c-accent);
  animation: spin 0.8s linear infinite;
}

.ptr-text {
  font-size: 11px;
  opacity: 0.7;
}

/* Desktop: content padding — views manage their own content padding */
.app.desktop-layout .main-content {
  padding: 0 0 20px;
}

.auth-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 100%;
}

.auth-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--c-bg-4);
  border-top-color: var(--c-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* @keyframes spin — defined in design-system.css */

/* Desktop adjustments */
@media (min-width: 768px) {
  .app.desktop-layout :deep(.track-item) {
    border-radius: 8px;
    margin-bottom: 2px;
  }
  
  .app.desktop-layout :deep(.track-item:hover) {
    background: rgba(255, 255, 255, 0.1);
  }
}

/* Full player mobile slide transition */
.player-slide-enter-active {
  transition: transform 0.35s cubic-bezier(0.32, 0.72, 0, 1), opacity 0.25s ease;
}

.player-slide-leave-active {
  transition: transform 0.28s cubic-bezier(0.32, 0.72, 0, 1), opacity 0.2s ease;
}

.player-slide-enter-from,
.player-slide-leave-to {
  transform: translateY(100%);
  opacity: 0.6;
}

.player-slide-enter-to,
.player-slide-leave-from {
  transform: translateY(0);
  opacity: 1;
}

@media (min-width: 1440px) {
  .app.desktop-layout {
    --content-padding: 40px;
  }
  .app.desktop-layout.has-now-playing {
    --content-padding: 24px;
  }
  .app.desktop-layout.sidebar-collapsed.has-now-playing {
    --content-padding: 32px;
  }
}

.header-profile-btn {
  background: transparent;
  border: none;
  padding: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.header-action-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.04);
  background: var(--c-bg-2, #1a1a1a);
  color: var(--c-text-2, #b0b0b0);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  box-shadow: 3px 3px 6px var(--sh-dark, rgba(0, 0, 0, 0.4)), -2px -2px 5px var(--sh-light, rgba(255, 255, 255, 0.03));
  transition: all 0.15s ease;
}

.header-action-btn:hover {
  color: var(--c-accent, #1db954);
  transform: scale(1.05);
}

.header-action-btn:active {
  transform: scale(0.95);
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0, 0, 0, 0.5)), inset -2px -2px 4px var(--sh-inset-light, rgba(255, 255, 255, 0.02));
}

.header-avatar-badge {
  position: relative;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--c-accent), #8b5cf6);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
  transition: transform 0.15s ease;
  overflow: hidden;
}

.header-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}

.header-avatar-badge.importing {
  box-shadow: 0 0 10px rgba(29, 185, 84, 0.4);
}

.header-import-spinner-ring {
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 2px solid transparent;
  border-top-color: var(--c-accent, #1db954);
  border-right-color: #38bdf8;
  animation: header-avatar-spin 1.2s linear infinite;
  pointer-events: none;
}

@keyframes header-avatar-spin {
  to { transform: rotate(360deg); }
}

.header-import-pill {
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: rgba(29, 185, 84, 0.2);
  border: 1px solid rgba(29, 185, 84, 0.4);
  padding: 2px 7px;
  border-radius: 10px;
  animation: pill-pulse 2s ease-in-out infinite;
}

@keyframes pill-pulse {
  0%, 100% { opacity: 0.9; }
  50% { opacity: 0.6; }
}

.header-profile-btn:hover .header-avatar-badge {
  transform: scale(1.06);
}

/* ── Channel Access Required Modal ── */
.channel-access-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(6px);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.channel-access-dialog {
  background: var(--bg-card, #18181b);
  border: 1px solid var(--border-color, rgba(255, 255, 255, 0.1));
  border-radius: 16px;
  max-width: 420px;
  width: 100%;
  padding: 24px;
  text-align: center;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
}

.channel-access-icon {
  font-size: 40px;
  margin-bottom: 12px;
}

.channel-access-dialog h3 {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 10px;
  color: var(--text-primary, #fff);
}

.channel-access-dialog p {
  font-size: 14px;
  color: var(--text-secondary, #a1a1aa);
  line-height: 1.5;
  margin-bottom: 8px;
}

.channel-access-dialog p strong {
  color: var(--c-accent, #1db954);
}

.channel-access-sub {
  font-size: 13px !important;
  opacity: 0.8;
  margin-bottom: 20px !important;
}

.channel-access-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}

.channel-access-actions .action-btn {
  padding: 10px 18px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  border: none;
}

.channel-access-actions .action-btn.primary {
  background: var(--c-accent, #1db954);
  color: #000;
}

.channel-access-actions .action-btn.secondary {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}
</style>
