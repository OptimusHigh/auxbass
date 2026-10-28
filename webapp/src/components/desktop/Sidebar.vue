<template>
  <!-- 1. Collapsed Rail Sidebar (Shown in grid when isSidebarCollapsed is true) -->
  <aside v-if="uiStore.isSidebarCollapsed" class="sidebar sidebar-wrapper rail-mode">
    <div class="sidebar-scroll">
      <!-- App logo / Expand button at top -->
      <div class="rail-header">
        <button 
          class="rail-logo-btn" 
          @click="handleRailToggleClick" 
          :title="uiStore.isAutoCollapsed ? 'Открыть меню (Ctrl+B)' : 'Развернуть сайдбар (Ctrl+B)'"
          aria-label="Развернуть сайдбар"
        >
          <div class="rail-logo-icon">
            <svg class="rail-logo-note" width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
            </svg>
            <ChevronRight class="rail-logo-chevron" :size="20" stroke-width="2.5" />
          </div>
        </button>
      </div>

      <!-- Main Navigation Icons -->
      <nav class="rail-nav">
        <router-link to="/" class="rail-nav-item" :class="{ active: isActiveExact('/') }" title="Главная">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
            <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
          </svg>
          <div class="rail-active-indicator"></div>
        </router-link>

        <router-link 
          to="/search" 
          class="rail-nav-item" 
          :class="{ active: isActive('/search') }"
          @click="onSearchClick"
          title="Поиск"
        >
          <Search :size="22" />
          <div class="rail-active-indicator"></div>
        </router-link>

        <div 
          class="rail-nav-item clickable" 
          :class="{ active: route.name === 'library' }"
          @click="goToLibraryTab('overview')"
          title="Медиатека"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m16 6 4 14M12 6v14M8 8v12M4 4v16"></path>
          </svg>
          <div class="rail-active-indicator"></div>
        </div>

        <router-link 
          to="/liked" 
          class="rail-nav-item liked-rail-item" 
          :class="{ active: isActive('/liked') }"
          title="Любимые треки"
        >
          <Heart :size="20" :fill="isActive('/liked') ? 'currentColor' : 'none'" />
          <span v-if="likedCount > 0" class="rail-badge">{{ formatRailCount(likedCount) }}</span>
          <div class="rail-active-indicator"></div>
        </router-link>

        <router-link 
          to="/friends" 
          class="rail-nav-item" 
          :class="{ active: isActive('/friends') }"
          title="Подписки"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
            <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/>
          </svg>
          <div class="rail-active-indicator"></div>
        </router-link>

        <router-link 
          to="/settings#import" 
          class="rail-nav-item" 
          :class="{ active: isImportActive }"
          @click="handleImportClick"
          title="Импорт"
        >
          <Upload :size="20" />
          <div class="rail-active-indicator"></div>
        </router-link>

        <!-- SoundCloud Rail Item -->
        <div 
          v-if="externalAccountsStore.isScConnected"
          class="rail-nav-item clickable"
          :class="{ active: isSoundCloudActive }"
          @click="goToSoundCloud"
          :title="`SoundCloud (@${externalAccountsStore.scAccount?.username || 'me'})`"
        >
          <Radio :size="20" />
          <div class="rail-active-indicator"></div>
        </div>

        <!-- Spotify Last / Recent Imports Rail Item -->
        <div 
          v-for="file in externalAccountsStore.recentSpotifyImports.slice(0, 2)"
          :key="`rail-${file.file_id || file.id}`"
          class="rail-nav-item clickable"
          :class="{ active: isFileImportActive(file) }"
          @click="openSpotifyImportFile(file)"
          :title="`Импорт Spotify: ${file.filename} (${file.total_tracks} треков)`"
        >
          <FileSpreadsheet :size="20" />
          <div class="rail-active-indicator"></div>
        </div>
      </nav>

      <!-- Divider -->
      <div class="rail-divider"></div>

      <!-- Playlists Mini Section -->
      <div v-if="displayedPlaylists.length > 0" class="rail-playlists">
        <div 
          v-for="playlist in displayedPlaylists.slice(0, 5)" 
          :key="playlist.id"
          class="rail-playlist-thumb"
          :class="{ 
            active: $route.params.id == playlist.id && $route.name === 'playlist-detail',
            'is-playing': isPlaylistPlaying(playlist.id),
            'is-pinned': playlist.isPinned
          }"
          @click="$router.push(`/playlist/${playlist.id}`)"
          @contextmenu.prevent="openMenu('playlist', playlist, 'sidebar', $event)"
          :title="playlist.name + (playlist.isPinned ? ' (Закреплен)' : '') + (isPlaylistPlaying(playlist.id) ? ' • Играет сейчас' : '')"
        >
          <img v-if="playlist.covers?.length" :src="getCoverUrl(playlist.covers[0], CoverSize.SMALL)" alt="" />
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <path d="M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18c-.31-.11-.65-.18-1-.18-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z"/>
          </svg>
          <div v-if="playlist.isPinned" class="rail-pin-dot" title="Закреплен"></div>
          <div v-if="isPlaylistPlaying(playlist.id)" class="rail-playing-ring"></div>
        </div>
      </div>

      <!-- Rail Footer -->
      <div class="rail-footer">
        <div 
          v-if="authStore.user" 
          class="rail-avatar clickable" 
          :class="{ active: showProfileMenu, 'has-active-imports': tasksStore.hasActiveImports }"
          @click="showProfileMenu = !showProfileMenu"
          @contextmenu.prevent="showProfileMenu = true"
          v-longpress="() => { showProfileMenu = true }"
          title="Мой профиль"
        >
          <img v-if="authStore.userAvatarUrl" :src="authStore.userAvatarUrl" class="sidebar-avatar-img" />
          <template v-else>{{ userInitials }}</template>
          <div v-if="tasksStore.hasActiveImports" class="rail-import-ring"></div>
        </div>
        <router-link 
          to="/downloaded" 
          class="rail-footer-btn saved-btn" 
          :class="{ active: isActive('/downloaded') }"
          :title="cachedTracksCount > 0 ? `Сохранённые треки (${cachedTracksCount})` : 'Сохранённые треки'"
        >
          <FolderDown :size="18" />
          <span v-if="cachedTracksCount > 0" class="rail-badge offline-badge">{{ formatRailCount(cachedTracksCount) }}</span>
          <div class="rail-active-indicator"></div>
        </router-link>
        <router-link 
          to="/settings#profile" 
          class="rail-footer-btn settings-btn" 
          :class="{ active: isSettingsActive }"
          @click="handleSettingsClick"
          title="Настройки"
        >
          <Settings :size="18" />
          <div class="rail-active-indicator"></div>
        </router-link>
      </div>
    </div>
  </aside>

  <!-- 2. Full Sidebar (Shown in grid when isSidebarCollapsed is false) -->
  <aside v-else class="sidebar sidebar-wrapper full-mode">
    <div class="sidebar-scroll">
      <!-- Logo & Header Actions -->
      <div class="sidebar-logo">
        <button 
          class="sidebar-logo-btn" 
          @click="uiStore.setSidebarCollapsed(true, true)" 
          title="Свернуть сайдбар (Ctrl+B)"
          aria-label="Свернуть сайдбар"
        >
          <div class="logo-icon">
            <svg class="logo-note" width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
            </svg>
            <ChevronLeft class="logo-chevron" :size="20" stroke-width="2.5" />
          </div>
        </button>
        <span 
          class="logo-text clickable-title" 
          @click="uiStore.setSidebarCollapsed(true, true)" 
          title="Свернуть сайдбар (Ctrl+B)"
        >
          {{ authStore.appName || 'auxbassbot' }}
        </span>

        <div 
          v-if="authStore.user" 
          class="header-avatar clickable" 
          @click="goToMyProfile"
          title="Мой профиль"
        >
          <span class="header-avatar-badge">
            <img v-if="authStore.userAvatarUrl" :src="authStore.userAvatarUrl" class="sidebar-avatar-img" />
            <template v-else>{{ userInitials }}</template>
          </span>
        </div>
      </div>

      <!-- Main Navigation -->
      <nav class="sidebar-nav">
        <router-link to="/" class="nav-item" :class="{ active: isActiveExact('/') }">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
            <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
          </svg>
          <span>Главная</span>
        </router-link>

        <router-link 
          to="/search" 
          class="nav-item" 
          :class="{ active: isActive('/search') }"
          @click="onSearchClick"
        >
          <Search :size="22" />
          <span>Поиск</span>
        </router-link>

        <!-- Library Root -->
        <div 
          class="nav-item clickable" 
          :class="{ active: route.name === 'library' && (!route.query.tab || route.query.tab === 'overview') }"
          @click="goToLibraryTab('overview')"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m16 6 4 14M12 6v14M8 8v12M4 4v16"></path>
          </svg>
          <span>Библиотека</span>
        </div>

        <!-- Library Sub-links -->
        <div class="sidebar-subnav">
          <div
            class="nav-subitem clickable"
            :class="{ active: route.name === 'library' && route.query.tab === 'tracks' }"
            @click="goToLibraryTab('tracks')"
            title="Все треки медиатеки"
          >
            <Music :size="15" />
            <span>Все треки</span>
            <span v-if="libraryStore.total || libraryStore.tracks?.length" class="sub-count">{{ libraryStore.total || libraryStore.tracks.length }}</span>
          </div>

          <div
            class="nav-subitem clickable"
            :class="{ active: route.name === 'library' && route.query.tab === 'playlists' }"
            @click="goToLibraryTab('playlists')"
            title="Плейлисты"
          >
            <ListMusic :size="15" />
            <span>Плейлисты</span>
          </div>

          <div
            class="nav-subitem clickable"
            :class="{ active: route.name === 'library' && route.query.tab === 'artists' }"
            @click="goToLibraryTab('artists')"
            title="Исполнители"
          >
            <Mic2 :size="15" />
            <span>Артисты</span>
          </div>

          <div
            class="nav-subitem clickable"
            :class="{ active: route.name === 'library' && route.query.tab === 'albums' }"
            @click="goToLibraryTab('albums')"
            title="Альбомы"
          >
            <Disc3 :size="15" />
            <span>Альбомы</span>
          </div>
        </div>

        <!-- Liked Tracks / Любимые треки -->
        <router-link 
          to="/liked" 
          class="nav-item liked-highlight-item" 
          :class="{ active: isActive('/liked') }"
          title="Любимые треки"
        >
          <Heart :size="20" :fill="isActive('/liked') ? 'currentColor' : 'none'" />
          <span>Любимые треки</span>
          <span v-if="likedCount > 0" class="nav-count">{{ likedCount }}</span>
        </router-link>

        <router-link to="/friends" class="nav-item" :class="{ active: isActive('/friends') }">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
            <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/>
          </svg>
          <span>Подписки</span>
        </router-link>

        <!-- Import Root Item -->
        <router-link 
          to="/settings#import" 
          class="nav-item" 
          :class="{ active: isImportActive }"
          @click="handleImportClick"
          title="Импорт"
        >
          <Upload :size="20" />
          <span>Импорт</span>
        </router-link>

        <!-- Import Sub-links -->
        <div v-if="hasImportSubitems" class="sidebar-subnav">
          <!-- SoundCloud Subitem -->
          <div 
            v-if="externalAccountsStore.isScConnected"
            class="nav-subitem clickable" 
            :class="{ active: isSoundCloudActive }"
            @click="goToSoundCloud"
            :title="`SoundCloud (@${externalAccountsStore.scAccount?.username || 'me'})`"
          >
            <Radio :size="15" />
            <span>SoundCloud</span>
            <span v-if="externalAccountsStore.scAccount?.likes_count" class="sub-count" title="Количество лайков">
              {{ formatRailCount(externalAccountsStore.scAccount.likes_count) }}
            </span>
          </div>

          <!-- Spotify Recent Imports -->
          <div 
            v-for="file in externalAccountsStore.recentSpotifyImports" 
            :key="file.file_id || file.id"
            class="nav-subitem import-file-subitem clickable" 
            :class="{ active: isFileImportActive(file) }"
            @click="openSpotifyImportFile(file)"
            :title="`Открыть импорт: ${file.filename} (${file.total_tracks} треков)`"
          >
            <FileSpreadsheet :size="15" class="subitem-icon" />
            <span class="file-name-subitem">{{ formatImportFilename(file.filename) }}</span>
            <span v-if="file.total_tracks" class="sub-count" title="Количество треков">{{ file.total_tracks }}</span>
            <button 
              class="subitem-delete-btn"
              @click.stop="promptDeleteImportFile(file)"
              title="Удалить файл импорта"
              aria-label="Удалить файл импорта"
            >
              <Trash2 :size="12" />
            </button>
          </div>
        </div>
      </nav>

      <!-- Playlists Section -->
      <div v-if="displayedPlaylists.length > 0" class="sidebar-section playlists-section">
        <div class="section-header clickable" @click="goToPersonalPlaylists">
          <span>Плейлисты</span>
          <span class="section-count">{{ userPlaylists.length }}</span>
        </div>

        <div class="playlists-list">
          <div 
            v-for="playlist in displayedPlaylists" 
            :key="playlist.id"
            class="nav-item playlist-item"
            :class="{ 
              active: $route.params.id == playlist.id && $route.name === 'playlist-detail',
              'is-playing': isPlaylistPlaying(playlist.id),
              'is-pinned': playlist.isPinned
            }"
            @click="$router.push(`/playlist/${playlist.id}`)"
            @contextmenu.prevent="openMenu('playlist', playlist, 'sidebar', $event)"
          >
            <div class="playlist-cover" :style="getPlaylistCoverStyle(playlist)">
              <img v-if="playlist.covers?.length" :src="getCoverUrl(playlist.covers[0], CoverSize.SMALL)" alt="" />
              <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18c-.31-.11-.65-.18-1-.18-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z"/>
              </svg>
              <!-- Live Equalizer overlay when playing -->
              <div v-if="isPlaylistPlaying(playlist.id)" class="cover-playing-overlay">
                <span class="mini-eq-bar bar-1"></span>
                <span class="mini-eq-bar bar-2"></span>
                <span class="mini-eq-bar bar-3"></span>
              </div>
            </div>
            <div class="playlist-info">
              <div class="playlist-name-row">
                <span class="playlist-name" :title="playlist.name">{{ playlist.name }}</span>
                <Pin v-if="playlist.isPinned" class="playlist-pin-icon" :size="11" />
              </div>
              <span class="playlist-subtitle" :class="{ 'playing-text': isPlaylistPlaying(playlist.id) }">
                {{ getPlaylistSubtitle(playlist) }}
              </span>
            </div>
          </div>
          
          <div 
            v-if="hasMorePlaylists"
            class="nav-item show-more-btn"
            @click="goToPersonalPlaylists"
          >
            <span class="show-more-text">Показать все {{ userPlaylists.length }}</span>
          </div>
        </div>
      </div>

      <!-- User Section (bottom) -->
      <div class="sidebar-footer">
        <div 
          class="user-info clickable" 
          :class="{ active: showProfileMenu, 'has-active-imports': tasksStore.hasActiveImports }"
          @click="showProfileMenu = !showProfileMenu" 
          @contextmenu.prevent="showProfileMenu = true"
          v-longpress="() => { showProfileMenu = true }"
          :title="tasksStore.hasActiveImports ? `Импорт: ${tasksStore.overallProgress}% (открыть меню профиля)` : 'Меню профиля'"
        >
          <div class="user-avatar-wrap">
            <div class="user-avatar" :class="{ 'importing': tasksStore.hasActiveImports }">
              <img v-if="authStore.userAvatarUrl" :src="authStore.userAvatarUrl" class="sidebar-avatar-img" />
              <template v-else>{{ userInitials }}</template>
            </div>
            <div v-if="tasksStore.hasActiveImports" class="avatar-import-ring"></div>
          </div>
          <div class="user-info-text">
            <span class="user-name">{{ userName }}</span>
            <span v-if="tasksStore.hasActiveImports" class="user-import-indicator">
              <span class="import-pulsing-dot"></span>
              <span class="import-label">Импорт {{ tasksStore.overallProgress }}%</span>
            </span>
          </div>
        </div>
        <router-link 
          to="/downloaded" 
          class="footer-btn saved-btn" 
          :class="{ active: isActive('/downloaded') }"
          :title="cachedTracksCount > 0 ? `Сохранённые треки (${cachedTracksCount})` : 'Сохранённые треки'"
        >
          <FolderDown :size="18" />
          <span v-if="cachedTracksCount > 0" class="footer-badge">{{ formatRailCount(cachedTracksCount) }}</span>
        </router-link>
        <router-link 
          to="/settings#profile" 
          class="footer-btn settings-btn" 
          :class="{ active: isSettingsActive }"
          @click="handleSettingsClick"
          title="Настройки"
        >
          <Settings :size="18" />
        </router-link>
      </div>
    </div>

  </aside>

  <!-- Context Menu for Profile -->
  <ProfileMenu v-model="showProfileMenu" placement="sidebar" />
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useLibraryStore } from '@/stores/library'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { usePlayerStore } from '@/stores/player'
import { useTasksStore } from '@/stores/tasks'
import { useContextMenu } from '@/composables/useContextMenu'
import { getCoverUrl, CoverSize, getPlaylistCoverStyle } from '@/utils'
import {
  FolderDown,
  Upload,
  Music,
  Mic2,
  Disc3,
  ListMusic,
  Search,
  Heart,
  ChevronRight,
  ChevronLeft,
  Settings,
  FileSpreadsheet,
  Cloud,
  Radio,
  Trash2,
  Pin
} from 'lucide-vue-next'
import { getCacheStats } from '@/utils/audioCacheDb'
import { useExternalAccountsStore } from '@/stores/externalAccounts'
import ProfileMenu from '@/components/layout/ProfileMenu.vue'

const route = useRoute()
const router = useRouter()
const libraryStore = useLibraryStore()
const authStore = useAuthStore()
const uiStore = useUIStore()
const playerStore = usePlayerStore()
const tasksStore = useTasksStore()
const externalAccountsStore = useExternalAccountsStore()
const showProfileMenu = ref(false)
const cachedTracksCount = ref(0)

onMounted(() => {
  tasksStore.checkRecentJobs()
})

const handleRailToggleClick = () => {
  uiStore.setSidebarCollapsed(false, true)
}

const onSearchClick = () => {
  if (route.path === '/search') {
    window.dispatchEvent(new CustomEvent('nav-tab-click', { detail: { route: '/search' } }))
  }
}

const formatRailCount = (count) => {
  if (!count) return ''
  if (count > 999) return `${(count / 1000).toFixed(0)}k`
  if (count > 99) return '99+'
  return count.toString()
}

const updateCachedStats = async () => {
  try {
    const stats = await getCacheStats()
    cachedTracksCount.value = stats?.trackCount || 0
  } catch (_) {}
}

// Universal context menu
const { openMenu } = useContextMenu()

// Stats
const likedCount = computed(() => libraryStore.likedTracks?.length || 0)

// Playlists Smart List
const playlists = computed(() => libraryStore.playlists || [])
const userPlaylists = computed(() => playlists.value)

// Check if a playlist is currently playing
const isPlaylistPlaying = (playlistId) => {
  return playerStore.isPlaying && playerStore.currentPlaylistId === Number(playlistId)
}

// Compute the smart order of playlists:
// 1. Pinned (in user's pin order)
// 2. Recently played (by lastPlayedAt desc)
// 3. Fallback: remaining playlists sorted by updated_at or created_at desc
const sortedPlaylists = computed(() => {
  const all = [...userPlaylists.value]
  if (!all.length) return []

  const byId = new Map(all.map(p => [Number(p.id), p]))
  const result = []
  const seenIds = new Set()

  // 1. Pinned playlists (preserve pinned order)
  const pinnedIds = uiStore.pinnedPlaylistIds || []
  for (const pid of pinnedIds) {
    const numId = Number(pid)
    const p = byId.get(numId)
    if (p && !seenIds.has(numId)) {
      result.push({ ...p, isPinned: true, isRecent: false })
      seenIds.add(numId)
    }
  }

  // 2. Recently played playlists (ordered by lastPlayedAt DESC)
  const recents = playerStore.recentPlaylists || []
  for (const item of recents) {
    const numId = Number(item.id)
    const p = byId.get(numId)
    if (p && !seenIds.has(numId)) {
      result.push({ ...p, isPinned: false, isRecent: true, lastPlayedAt: item.lastPlayedAt })
      seenIds.add(numId)
    }
  }

  // 3. Fallback: remaining playlists sorted by updated_at or created_at DESC
  const remaining = all.filter(p => !seenIds.has(Number(p.id)))
  remaining.sort((a, b) => {
    const dateA = new Date(a.updated_at || a.created_at || 0).getTime()
    const dateB = new Date(b.updated_at || b.created_at || 0).getTime()
    return dateB - dateA
  })

  for (const p of remaining) {
    result.push({ ...p, isPinned: false, isRecent: false })
  }

  return result
})

const DISPLAY_LIMIT = 8
const displayedPlaylists = computed(() => {
  return sortedPlaylists.value.slice(0, DISPLAY_LIMIT)
})

const hasMorePlaylists = computed(() => {
  return userPlaylists.value.length > DISPLAY_LIMIT
})

const formatTrackCount = (count) => {
  const n = count || 0
  const rem10 = n % 10
  const rem100 = n % 100
  if (rem10 === 1 && rem100 !== 11) return `${n} трек`
  if (rem10 >= 2 && rem10 <= 4 && (rem100 < 10 || rem100 >= 20)) return `${n} трека`
  return `${n} треков`
}

const formatTimeAgo = (timestamp) => {
  if (!timestamp) return 'Недавно'
  const diff = Date.now() - timestamp
  const minutes = Math.floor(diff / (1000 * 60))
  if (minutes < 5) return 'Только что'
  if (minutes < 60) return `${minutes}м назад`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}ч назад`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'Вчера'
  if (days < 7) return `${days}д назад`
  return 'Недавно'
}

const getPlaylistSubtitle = (playlist) => {
  if (isPlaylistPlaying(playlist.id)) {
    return 'Играет сейчас'
  }
  const countStr = formatTrackCount(playlist.track_count)
  if (playlist.isPinned) {
    return `Закреплен • ${countStr}`
  }
  if (playlist.isRecent && playlist.lastPlayedAt) {
    const timeAgo = formatTimeAgo(playlist.lastPlayedAt)
    return `${timeAgo} • ${countStr}`
  }
  return countStr
}

// Navigation
const goToLibraryTab = (tab) => {
  uiStore.setLibraryTab(tab)
  if (tab === 'overview') {
    router.push('/library')
  } else {
    router.push({ path: '/library', query: { tab } })
  }
}

const goToPersonalPlaylists = () => {
  goToLibraryTab('playlists')
}

// User info
const userName = computed(() => {
  return authStore.userDisplayName || 'User'
})

const userInitials = computed(() => {
  const name = userName.value || 'U'
  return name.substring(0, 2).toUpperCase()
})

// Route matching
const isActive = (path) => {
  return route.path.startsWith(path)
}

const isActiveExact = (path) => {
  return route.path === path
}

// Settings vs Import active section matching
const isImportActive = computed(() => {
  return route.path === '/settings' && uiStore.settingsSection === 'import'
})

const isSettingsActive = computed(() => {
  return route.path === '/settings' && uiStore.settingsSection === 'settings'
})

const handleImportClick = (e) => {
  if (route.path === '/settings') {
    e.preventDefault()
    const el = document.getElementById('import') || document.querySelector('.settings-view')
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    if (route.hash !== '#import') {
      router.replace({ path: '/settings', hash: '#import' })
    }
  }
}

const handleSettingsClick = (e) => {
  if (route.path === '/settings') {
    e.preventDefault()
    const el = document.getElementById('profile')
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    if (route.hash !== '#profile') {
      router.replace({ path: '/settings', hash: '#profile' })
    }
  }
}

const goToMyProfile = () => {
  if (authStore.user?.id) {
    router.push(`/user/${authStore.user.id}`)
  }
}

const onPlaylistChanged = () => {
  libraryStore.fetchPlaylists(true)
}

const isSoundCloudActive = computed(() => {
  return route.path === '/search' && route.query.tab === 'soundcloud'
})

const goToSoundCloud = () => {
  router.push({ path: '/search', query: { tab: 'soundcloud', mode: 'likes' } })
}

const hasImportSubitems = computed(() => {
  return externalAccountsStore.isScConnected || (externalAccountsStore.recentSpotifyImports && externalAccountsStore.recentSpotifyImports.length > 0)
})

const formatImportFilename = (name) => {
  if (!name) return 'Spotify CSV'
  return name.replace(/\.csv$/i, '').replace(/[_-]/g, ' ')
}

const openSpotifyImportFile = (file) => {
  tasksStore.openExportifyModal({ fileId: file.file_id, filename: file.filename, loadLastSaved: true })
}

const isFileImportActive = (file) => {
  if (!tasksStore.showExportifyModal) return false
  const activeFileId = tasksStore.exportifyModalOptions?.fileId
  if (activeFileId) return activeFileId === file.file_id
  return externalAccountsStore.lastSpotifyImport?.file_id === file.file_id
}

const promptDeleteImportFile = async (file) => {
  const name = formatImportFilename(file.filename)
  if (confirm(`Удалить файл импорта «${name}» из истории?`)) {
    try {
      await externalAccountsStore.deleteSpotifyImport(file.file_id || file.id)
      uiStore.toast?.success('Удалено', `Файл импорта «${name}» удалён`)
      if (tasksStore.showExportifyModal && tasksStore.exportifyModalOptions?.fileId === file.file_id) {
        tasksStore.closeExportifyModal()
      }
    } catch (err) {
      console.error('Failed to delete import file:', err)
      uiStore.toast?.error('Ошибка', 'Не удалось удалить файл импорта')
    }
  }
}

const openLastSpotifyImport = () => {
  tasksStore.openExportifyModal({ loadLastSaved: true })
}

onMounted(() => {
  window.addEventListener('playlist:changed', onPlaylistChanged)
  window.addEventListener('cache-updated', updateCachedStats)
  updateCachedStats()
  if (!libraryStore.likedTracks?.length) {
    libraryStore.fetchLikedTracks()
  }
  externalAccountsStore.fetchSoundCloud()
  externalAccountsStore.fetchLastSpotifyImport()
})

onUnmounted(() => {
  window.removeEventListener('playlist:changed', onPlaylistChanged)
  window.removeEventListener('cache-updated', updateCachedStats)
})
</script>

<style scoped src="./Sidebar.css"></style>
