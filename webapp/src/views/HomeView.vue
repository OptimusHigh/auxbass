<template>
  <div class="home-view">
    <!-- Header Greeting -->
    <header class="home-greeting">
      <h1 class="greeting-title">{{ greetingText }}</h1>
      <p class="greeting-subtitle" v-if="authStore.user">
        Рады видеть вас, {{ userFirstName }}!
      </p>
    </header>

    <!-- Top Quick-Access Grid (Spotify 2x3 / 3x2) -->
    <!-- Skeletons while loading quick access -->
    <section v-if="loadingQuickAccess" class="quick-access-grid">
      <div v-for="i in 6" :key="i" class="quick-card quick-skeleton">
        <div class="skeleton-thumb"></div>
        <div class="skeleton-info">
          <div class="skeleton-line-title"></div>
          <div class="skeleton-line-sub"></div>
        </div>
      </div>
    </section>

    <!-- Real Quick-Access Cards -->
    <section v-else class="quick-access-grid">
      <!-- Liked Songs Tile (Always First) -->
      <div 
        class="quick-card liked-card" 
        @click="goTo('/liked')"
        @contextmenu.prevent="openMenu('liked', { name: 'Понравившиеся', track_count: likedCount }, 'home', $event)"
        v-longpress="(e) => openMenu('liked', { name: 'Понравившиеся', track_count: likedCount }, 'home', e)"
      >
        <div class="quick-card-cover liked-cover-gradient">
          <Heart :size="24" fill="currentColor" />
        </div>
        <div class="quick-card-info">
          <span class="quick-card-title">Понравившиеся</span>
          <span class="quick-card-meta">{{ formatTrackCount(likedCount) }}</span>
        </div>
        <button 
          v-if="likedCount > 0" 
          class="quick-play-btn" 
          @click.stop="handlePlayLiked"
          title="Слушать любимые"
        >
          <Play :size="18" fill="currentColor" />
        </button>
      </div>

      <!-- User Top Playlists -->
      <div 
        v-for="playlist in topPlaylists" 
        :key="playlist.id"
        class="quick-card"
        @click="goTo(`/playlist/${playlist.id}`)"
        @contextmenu.prevent="openMenu('playlist', playlist, 'home', $event)"
        v-longpress="(e) => openMenu('playlist', playlist, 'home', e)"
      >
        <div class="quick-card-cover" :style="getPlaylistCoverStyle(playlist)">
          <img 
            v-if="playlist.covers?.length" 
            :src="getCoverUrl(playlist.covers[0], CoverSize.MEDIUM)" 
            alt="" 
            loading="lazy"
          />
          <div v-else class="quick-vinyl-badge">
            <Music :size="20" />
          </div>
        </div>
        <div class="quick-card-info">
          <span class="quick-card-title">{{ playlist.name }}</span>
          <span class="quick-card-meta">{{ formatTrackCount(playlist.track_count) }}</span>
        </div>
        <button 
          v-if="playlist.track_count > 0" 
          class="quick-play-btn" 
          @click.stop="handlePlayPlaylist(playlist)"
          title="Слушать плейлист"
        >
          <Play :size="18" fill="currentColor" />
        </button>
      </div>

      <!-- Quick Add Playlist Tile if fewer than 5 playlists -->
      <div 
        v-if="topPlaylists.length < 5" 
        class="quick-card create-card" 
        @click="handleCreatePlaylist"
        @contextmenu.prevent="handleCreatePlaylist"
        v-longpress="handleCreatePlaylist"
      >
        <div class="quick-card-cover create-cover">
          <Plus :size="24" />
        </div>
        <div class="quick-card-info">
          <span class="quick-card-title">Создать плейлист</span>
          <span class="quick-card-meta">Новая подборка</span>
        </div>
      </div>
    </section>

    <!-- Section: Недавно прослушано (History) -->
    <!-- Skeleton when history is loading -->
    <section v-if="loadingHistory" class="home-section">
      <div class="section-header">
        <div class="skeleton-section-title"></div>
      </div>
      <div class="horizontal-scroll">
        <div v-for="i in 6" :key="i" class="feed-card-skeleton">
          <div class="skeleton-feed-cover"></div>
          <div class="skeleton-feed-title"></div>
          <div class="skeleton-feed-sub"></div>
        </div>
      </div>
    </section>

    <!-- Real History Section -->
    <section v-else-if="recentHistoryTracks.length > 0" class="home-section">
      <div class="section-header">
        <h2 class="section-title">Недавно прослушано</h2>
        <div class="section-actions">
          <button 
            class="scroll-arrow-btn" 
            :disabled="!historyScroll.canScrollLeft.value"
            @click="historyScroll.scroll('left')"
            title="Назад"
            aria-label="Назад"
          >
            <ChevronLeft :size="16" />
          </button>
          <button 
            class="scroll-arrow-btn" 
            :disabled="!historyScroll.canScrollRight.value"
            @click="historyScroll.scroll('right')"
            title="Вперед"
            aria-label="Вперед"
          >
            <ChevronRight :size="16" />
          </button>
        </div>
      </div>
      <div 
        class="horizontal-scroll"
        :ref="historyScroll.containerRef"
      >
        <div 
          v-for="track in recentHistoryTracks" 
          :key="track.id" 
          class="feed-card"
          @click="handlePlayTrack(track, recentHistoryTracks)"
          @contextmenu.prevent="openMenu('track', track, 'history', $event)"
          v-longpress="(e) => openMenu('track', track, 'history', e)"
        >
          <div class="feed-card-cover">
            <img 
              v-if="track.cover_url" 
              :src="getCoverUrl(track.cover_url, CoverSize.MEDIUM)" 
              alt=""
              loading="lazy"
            />
            <div v-else class="feed-card-placeholder">
              {{ track.title?.charAt(0) || '♪' }}
            </div>
            <button class="play-overlay" title="Слушать">
              <Play :size="18" fill="currentColor" />
            </button>
          </div>
          <div class="feed-card-title">{{ track.title }}</div>
          <div class="feed-card-subtitle">{{ track.artist || 'Неизвестен' }}</div>
        </div>
      </div>
    </section>

    <!-- Section: Ваши плейлисты -->
    <!-- Skeleton when playlists are loading -->
    <section v-if="loadingPlaylists" class="home-section">
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

    <!-- Real Playlists Section -->
    <section v-else-if="allPlaylists.length > 0" class="home-section">
      <div class="section-header">
        <h2 
          class="section-title clickable" 
          @click="goToLibraryPlaylists"
          title="Перейти в плейлисты"
        >
          Ваши плейлисты
        </h2>
        <div class="section-actions">
          <button class="section-link" @click="goToLibraryPlaylists" title="Показать все плейлисты">
            <span>Все</span>
            <ChevronRight :size="13" />
          </button>
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
          @contextmenu.prevent="openMenu('playlist', pl, 'home', $event)"
          v-longpress="(e) => openMenu('playlist', pl, 'home', e)"
        >
          <div class="feed-card-cover placeholder-vinyl" :style="getPlaylistCoverStyle(pl)">
            <img 
              v-if="pl.covers?.length" 
              :src="getCoverUrl(pl.covers[0], CoverSize.MEDIUM)" 
              alt=""
              loading="lazy"
            />
            <div v-else class="vinyl-disc-placeholder">
              <div class="vinyl-rings"></div>
              <div class="vinyl-spindle">
                <Music :size="20" />
              </div>
            </div>
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
          <div class="feed-card-subtitle">{{ formatTrackCount(pl.track_count) }}</div>
        </div>
      </div>
    </section>

    <!-- Section: Музыкальные открытия и жанры (Explore & Discovery) -->
    <HomeExploreSection />

    <!-- Section: Свежее в сообществе (Recent Uploads) -->
    <!-- Skeleton when uploads are loading -->
    <section v-if="loadingUploads" class="home-section">
      <div class="section-header">
        <div class="skeleton-section-title"></div>
      </div>
      <div class="horizontal-scroll">
        <div v-for="i in 6" :key="i" class="feed-card-skeleton">
          <div class="skeleton-feed-cover"></div>
          <div class="skeleton-feed-title"></div>
          <div class="skeleton-feed-sub"></div>
        </div>
      </div>
    </section>

    <!-- Real Recent Uploads Section -->
    <section v-else-if="recentUploads.length > 0" class="home-section">
      <div class="section-header">
        <h2 class="section-title">Новинки сообщества</h2>
        <div class="section-actions">
          <button 
            class="scroll-arrow-btn" 
            :disabled="!uploadsScroll.canScrollLeft.value"
            @click="uploadsScroll.scroll('left')"
            title="Назад"
            aria-label="Назад"
          >
            <ChevronLeft :size="16" />
          </button>
          <button 
            class="scroll-arrow-btn" 
            :disabled="!uploadsScroll.canScrollRight.value"
            @click="uploadsScroll.scroll('right')"
            title="Вперед"
            aria-label="Вперед"
          >
            <ChevronRight :size="16" />
          </button>
        </div>
      </div>
      <div 
        class="horizontal-scroll"
        :ref="uploadsScroll.containerRef"
      >
        <div 
          v-for="track in recentUploads.slice(0, 12)" 
          :key="track.id" 
          class="feed-card"
          @click="handlePlayTrack(track, recentUploads)"
          @contextmenu.prevent="openMenu('track', track, 'recent_uploads', $event)"
          v-longpress="(e) => openMenu('track', track, 'recent_uploads', e)"
        >
          <div class="feed-card-cover">
            <img 
              v-if="track.cover_url" 
              :src="getCoverUrl(track.cover_url, CoverSize.MEDIUM)" 
              alt=""
              loading="lazy"
            />
            <div v-else class="feed-card-placeholder">
              {{ track.title?.charAt(0) || '♪' }}
            </div>
            <button class="play-overlay" title="Слушать">
              <Play :size="18" fill="currentColor" />
            </button>
          </div>
          <div class="feed-card-title">{{ track.title }}</div>
          <div class="feed-card-subtitle">{{ track.artist || 'Неизвестен' }}</div>
        </div>
      </div>
    </section>

    <!-- Section: Рекомендации (Spotify Discovery Placeholder) -->
    <section class="home-section">
      <div class="discovery-banner">
        <div class="discovery-badge">
          <Sparkles :size="16" />
          <span>Персональная волна</span>
        </div>
        <h3 class="discovery-title">Умные рекомендации на основе вашего вкуса</h3>
        <p class="discovery-text">
          Слушайте треки, ставьте лайки и сохраняйте альбомы. Скоро здесь появится автоматически сгенерированная лента новинок под ваши предпочтения.
        </p>
        <button class="discovery-btn" @click="handleRandomMix">
          <Shuffle :size="16" />
          <span>Включить случайный микс</span>
        </button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, onActivated } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useLibraryStore } from '@/stores/library'
import { usePlayerStore } from '@/stores/player'
import { useUIStore } from '@/stores/ui'
import { useContextMenu } from '@/composables/useContextMenu'
import { getCoverUrl, CoverSize, formatTrackCount } from '@/utils'
import { 
  Heart, 
  Play, 
  Plus, 
  Music, 
  Sparkles, 
  Shuffle,
  ChevronLeft,
  ChevronRight
} from 'lucide-vue-next'
import { useHorizontalScroll } from '@/composables/useHorizontalScroll'
import HomeExploreSection from '@/components/home/HomeExploreSection.vue'

const router = useRouter()
const authStore = useAuthStore()
const libraryStore = useLibraryStore()
const playerStore = usePlayerStore()
const uiStore = useUIStore()
const { openMenu } = useContextMenu()

// Horizontal scroll managers for desktop carousels
const historyScroll = useHorizontalScroll()
const playlistsScroll = useHorizontalScroll()
const uploadsScroll = useHorizontalScroll()

// Independent async loading flags per section
const loadingQuickAccess = ref(!libraryStore.playlists?.length && !libraryStore.likedTracks?.length)
const loadingHistory = ref(!libraryStore.history?.length)
const loadingPlaylists = ref(!libraryStore.playlists?.length)
const loadingUploads = ref(!libraryStore.recentUploads?.length)

// Greeting by time of day
const greetingText = computed(() => {
  const hour = new Date().getHours()
  if (hour >= 5 && hour < 12) return 'Доброе утро'
  if (hour >= 12 && hour < 18) return 'Добрый день'
  if (hour >= 18 && hour < 23) return 'Добрый вечер'
  return 'Доброй ночи'
})

const userFirstName = computed(() => {
  const u = authStore.user
  if (!u) return ''
  return u.first_name || u.username || ''
})

const likedCount = computed(() => libraryStore.likedTracks?.length || 0)
const allPlaylists = computed(() => libraryStore.playlists || [])
const topPlaylists = computed(() => allPlaylists.value.slice(0, 5))
const recentHistoryTracks = computed(() => libraryStore.history || [])
const recentUploads = computed(() => libraryStore.recentUploads || [])

const goTo = (path) => {
  router.push(path)
}

const goToLibraryPlaylists = () => {
  uiStore.setLibraryTab('playlists')
  router.push('/library')
}

const handlePlayLiked = async () => {
  if (libraryStore.likedTracks?.length > 0) {
    playerStore.playTrack(libraryStore.likedTracks[0], libraryStore.likedTracks, 0)
  }
}

const handlePlayPlaylist = async (playlist) => {
  try {
    await playerStore.playShuffleAll('playlist', playlist.id)
  } catch (e) {
    console.error('Failed to play playlist:', e)
  }
}

const handlePlayTrack = (track, list) => {
  const idx = list.findIndex(t => t.id === track.id)
  playerStore.playTrack(track, list, idx >= 0 ? idx : 0)
}

const handleRandomMix = async () => {
  try {
    await playerStore.playShuffleAll('library')
  } catch (e) {
    console.error('Failed to start random mix:', e)
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

const getPlaylistCoverStyle = (playlist) => {
  if (playlist.covers?.length) return {}
  const str = playlist.name || 'Playlist'
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash)
  }
  const hue = Math.abs(hash % 360)
  return {
    background: `radial-gradient(circle at 35% 35%, hsl(${hue}, 30%, 20%) 0%, hsl(${(hue + 25) % 360}, 22%, 12%) 65%, #0e0e12 100%)`,
    boxShadow: 'inset 0 0 24px rgba(0, 0, 0, 0.7), inset 0 0 0 1px rgba(255, 255, 255, 0.05)'
  }
}

const onPlaylistChanged = () => {
  libraryStore.fetchPlaylists(true).finally(() => {
    loadingPlaylists.value = false
    loadingQuickAccess.value = false
  })
}

const onTrackAdded = () => {
  libraryStore.fetchRecentUploads(15).finally(() => {
    loadingUploads.value = false
  })
  libraryStore.fetchPlaylists(true)
}

const onTrackChangedOrRemoved = () => {
  libraryStore.fetchRecentUploads(15).finally(() => {
    loadingUploads.value = false
  })
}

onMounted(() => {
  window.addEventListener('playlist:changed', onPlaylistChanged)
  window.addEventListener('track:added:library', onTrackAdded)
  window.addEventListener('track:removed', onTrackChangedOrRemoved)
  window.addEventListener('track:removed:library', onTrackChangedOrRemoved)
  window.addEventListener('track:changed', onTrackChangedOrRemoved)

  // SWR: If store already has cached data from localStorage, immediately show it (no skeletons)
  loadingPlaylists.value = !libraryStore.playlists?.length
  loadingQuickAccess.value = !libraryStore.playlists?.length && !libraryStore.likedTracks?.length
  loadingHistory.value = !libraryStore.history?.length
  loadingUploads.value = !libraryStore.recentUploads?.length

  // ALWAYS revalidate in background so changes appear without relogin or cookie clearing
  libraryStore.fetchPlaylists(true).finally(() => {
    loadingPlaylists.value = false
    loadingQuickAccess.value = false
  })
  libraryStore.fetchLikedTracks().finally(() => {
    loadingQuickAccess.value = false
  })
  libraryStore.fetchHistory(20).finally(() => {
    loadingHistory.value = false
  })
  libraryStore.fetchRecentUploads(15).finally(() => {
    loadingUploads.value = false
  })
})

onActivated(() => {
  // Always refresh playlists, liked tracks, and recent uploads when returning to Home tab
  libraryStore.fetchPlaylists(true)
  libraryStore.fetchLikedTracks()
  libraryStore.fetchRecentUploads(15)
})

onUnmounted(() => {
  window.removeEventListener('playlist:changed', onPlaylistChanged)
  window.removeEventListener('track:added:library', onTrackAdded)
  window.removeEventListener('track:removed', onTrackChangedOrRemoved)
  window.removeEventListener('track:removed:library', onTrackChangedOrRemoved)
  window.removeEventListener('track:changed', onTrackChangedOrRemoved)
})
</script>

<style scoped>
.home-view {
  padding: var(--content-padding, 16px) 0 32px var(--content-padding, 16px);
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}

/* Greeting */
.home-greeting {
  margin-bottom: 24px;
  max-width: var(--content-max-width, 1400px);
  padding-right: var(--content-padding, 16px);
  box-sizing: border-box;
}

.greeting-title {
  font-size: 28px;
  font-weight: 800;
  color: var(--c-text-1, #fff);
  letter-spacing: -0.025em;
  margin: 0;
}

@media (min-width: 1024px) {
  .greeting-title {
    font-size: 32px;
  }
}

.greeting-subtitle {
  font-size: 14px;
  color: var(--c-text-3, rgba(255, 255, 255, 0.5));
  margin-top: 4px;
  font-weight: 500;
}

/* Quick Access Grid (Spotify style) */
.quick-access-grid {
  display: grid;
  grid-template-columns: var(--quick-grid-cols, repeat(2, 1fr));
  gap: 10px;
  margin-bottom: 28px;
  max-width: var(--content-max-width, 1400px);
  margin-right: var(--content-padding, 16px);
  box-sizing: border-box;
}

@media (min-width: 768px) {
  .quick-access-grid {
    gap: 12px;
    margin-bottom: 36px;
    margin-right: var(--content-padding, 24px);
  }
}

.quick-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--c-bg-2, #1A1A1A);
  border: 1px solid rgba(255, 255, 255, 0.035);
  border-radius: var(--r-md, 12px);
  box-shadow: 3px 3px 8px var(--sh-dark, rgba(0, 0, 0, 0.4)), -1px -1px 3px var(--sh-light, rgba(255, 255, 255, 0.02));
  overflow: hidden;
  height: 58px;
  cursor: pointer;
  position: relative;
  transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
  user-select: none;
}

@media (min-width: 1024px) {
  .quick-card {
    height: 64px;
  }
}

.quick-card:hover {
  background: var(--c-bg-3, #222222);
  border-color: rgba(255, 255, 255, 0.08);
  transform: translateY(-2px);
  box-shadow: 5px 6px 14px var(--sh-dark, rgba(0, 0, 0, 0.6)), -2px -2px 6px var(--sh-light, rgba(255, 255, 255, 0.03));
}

.quick-card:active {
  transform: scale(0.98);
  box-shadow: inset 2px 2px 5px var(--sh-inset-dark, rgba(0, 0, 0, 0.5));
}

.quick-card-cover {
  width: 58px;
  height: 58px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--c-bg-3, #222);
  color: rgba(255, 255, 255, 0.7);
  overflow: hidden;
  position: relative;
}

@media (min-width: 1024px) {
  .quick-card-cover {
    width: 64px;
    height: 64px;
  }
}

.quick-vinyl-badge {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.75);
}

.quick-card-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.liked-cover-gradient {
  background: linear-gradient(135deg, #581c87 0%, #7e22ce 50%, #9333ea 100%);
  color: #fff;
  box-shadow: inset 0 0 16px rgba(255, 255, 255, 0.25);
}

.create-cover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--c-accent, #1db954);
}

.quick-card-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding-right: 12px;
}

.quick-card-title {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--c-text-1, #fff);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (min-width: 1024px) {
  .quick-card-title {
    font-size: 14.5px;
  }
}

.quick-card-meta {
  font-size: 11px;
  color: var(--c-text-3, rgba(255, 255, 255, 0.45));
  margin-top: 2px;
  font-weight: 500;
}

@media (min-width: 1024px) {
  .quick-card-meta {
    font-size: 12px;
  }
}

.quick-play-btn {
  position: absolute;
  right: 10px;
  top: 50%;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: var(--c-accent, #1db954);
  color: #000;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  opacity: 0;
  pointer-events: none;
  transform: translateY(-50%) scale(0.85);
  transition: all 0.2s cubic-bezier(0.2, 0.8, 0.2, 1);
  z-index: 2;
}

.quick-card:hover .quick-play-btn {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(-50%) scale(1);
}

.quick-play-btn:hover {
  transform: translateY(-50%) scale(1.08) !important;
  background: #1ed760;
}

@media (max-width: 768px) {
  .quick-play-btn {
    width: 32px;
    height: 32px;
    right: 8px;
  }
}

/* Sections */
.home-section {
  margin-bottom: 28px;
  width: 100%;
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

.section-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-text-1, #fff);
  letter-spacing: -0.01em;
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
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--c-bg-2, #1A1A1A);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: 2px 2px 6px var(--sh-dark, rgba(0, 0, 0, 0.45)), -1px -1px 3px var(--sh-light, rgba(255, 255, 255, 0.02));
  color: var(--c-text-1, #fff);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.2, 0, 0, 1);
  padding: 0;
  user-select: none;
}

.scroll-arrow-btn:hover:not(:disabled) {
  background: var(--c-bg-3, #222222);
  border-color: rgba(255, 255, 255, 0.1);
  transform: scale(1.06);
  box-shadow: 3px 3px 8px var(--sh-dark, rgba(0, 0, 0, 0.55)), -1px -1px 4px var(--sh-light, rgba(255, 255, 255, 0.03));
  color: var(--c-accent, #1db954);
}

.scroll-arrow-btn:active:not(:disabled) {
  transform: scale(0.94);
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0, 0, 0, 0.5)), inset -1px -1px 2px var(--sh-inset-light, rgba(255, 255, 255, 0.02));
}

.scroll-arrow-btn:disabled {
  opacity: 0.2;
  cursor: default;
  pointer-events: none;
  box-shadow: none;
}

@media (max-width: 768px) {
  .scroll-arrow-btn {
    display: none;
  }
}

.section-link {
  background: var(--c-bg-2, #1A1A1A);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: 2px 2px 5px var(--sh-dark, rgba(0, 0, 0, 0.45)), -1px -1px 3px var(--sh-light, rgba(255, 255, 255, 0.02));
  border-radius: var(--r-full, 9999px);
  padding: 4px 10px 4px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--c-text-2, #B0B0B0);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  transition: all 0.18s cubic-bezier(0.2, 0, 0, 1);
  user-select: none;
}

.section-link:hover {
  background: var(--c-bg-3, #222222);
  border-color: rgba(255, 255, 255, 0.1);
  color: var(--c-accent, #1db954);
  transform: translateY(-1px);
  box-shadow: 3px 3px 8px var(--sh-dark, rgba(0, 0, 0, 0.55)), -1px -1px 3px var(--sh-light, rgba(255, 255, 255, 0.03));
}

.section-link:active {
  transform: scale(0.96);
  box-shadow: inset 1px 1px 3px var(--sh-inset-dark, rgba(0, 0, 0, 0.4));
}

/* Horizontal Scroll */
.horizontal-scroll {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  padding-bottom: 8px;
  padding-right: var(--content-padding, 24px);
  width: 100%;
  box-sizing: border-box;
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
  transition: scrollbar-color 0.2s ease;
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

/* Feed Cards */
.feed-card {
  width: var(--card-min-width, 132px);
  flex-shrink: 0;
  cursor: pointer;
  display: flex;
  flex-direction: column;
}

@media (min-width: 1024px) {
  .feed-card {
    width: var(--card-min-width, 160px);
  }
}

.feed-card-cover {
  width: var(--card-min-width, 132px);
  height: var(--card-min-width, 132px);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.05);
  overflow: hidden;
  position: relative;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.25s ease;
}

@media (min-width: 1024px) {
  .feed-card-cover {
    border-radius: 12px;
    margin-bottom: 10px;
  }

  .feed-card:hover .feed-card-cover {
    transform: translateY(-4px);
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.55);
  }
}

.feed-card-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.feed-card-placeholder {
  font-size: 38px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.25);
}

/* Vinyl placeholder disc for playlists/tracks without artwork */
.vinyl-disc-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.vinyl-rings {
  position: absolute;
  inset: 6px;
  border-radius: 50%;
  background: repeating-radial-gradient(
    circle,
    transparent,
    transparent 5px,
    rgba(255, 255, 255, 0.03) 6px,
    rgba(255, 255, 255, 0.03) 7px
  );
  box-shadow: inset 0 0 16px rgba(0, 0, 0, 0.6);
  pointer-events: none;
}

.vinyl-spindle {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: radial-gradient(circle at 40% 40%, rgba(35, 35, 42, 0.95) 0%, rgba(16, 16, 20, 0.98) 100%);
  border: 1px solid rgba(255, 255, 255, 0.09);
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.5), inset 0 1px 2px rgba(255, 255, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-text-2, #B0B0B0);
  z-index: 1;
  transition: transform 0.2s ease, color 0.2s ease;
}

.feed-card:hover .vinyl-spindle {
  transform: scale(1.06);
  color: var(--c-accent, #1db954);
}

.feed-card .play-overlay {
  position: absolute;
  bottom: 8px;
  right: 8px;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: var(--c-accent, #1db954);
  color: #000;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transform: translateY(6px);
  transition: all 0.2s ease;
  box-shadow: 0 6px 14px rgba(0, 0, 0, 0.5);
}

@media (min-width: 1024px) {
  .feed-card .play-overlay {
    width: 42px;
    height: 42px;
    bottom: 10px;
    right: 10px;
  }
}

.feed-card:hover .play-overlay {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0);
}

@media (hover: none), (max-width: 768px) {
  .feed-card .play-overlay {
    opacity: 0.95;
    pointer-events: auto;
    transform: translateY(0);
  }
}

.feed-card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--c-text-1, #fff);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}

@media (min-width: 1024px) {
  .feed-card-title {
    font-size: 14px;
    margin-top: 2px;
  }
}

.feed-card-subtitle {
  font-size: 11px;
  color: var(--c-text-3, rgba(255, 255, 255, 0.5));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}

@media (min-width: 1024px) {
  .feed-card-subtitle {
    font-size: 12.5px;
  }
}

/* Discovery Banner */
.discovery-banner {
  background: linear-gradient(135deg, rgba(30, 27, 75, 0.8) 0%, rgba(15, 23, 42, 0.9) 100%);
  border: 1px solid rgba(139, 92, 246, 0.25);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4), inset 0 0 24px rgba(139, 92, 246, 0.08);
  max-width: var(--content-max-width, 1400px);
  margin-right: var(--content-padding, 16px);
  box-sizing: border-box;
}

@media (min-width: 768px) {
  .discovery-banner {
    margin-right: var(--content-padding, 24px);
  }
}

.discovery-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #a78bfa;
  margin-bottom: 8px;
}

.discovery-title {
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  margin-bottom: 6px;
}

.discovery-text {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.7);
  line-height: 1.4;
  margin-bottom: 14px;
}

.discovery-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(139, 92, 246, 0.2);
  border: 1px solid rgba(139, 92, 246, 0.4);
  color: #fff;
  padding: 8px 16px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
}

.discovery-btn:hover {
  background: rgba(139, 92, 246, 0.35);
  transform: translateY(-1px);
}

.discovery-btn:active {
  transform: scale(0.97);
}

/* =========================================
   Skeletons (Neumorphic Async Loaders)
   ========================================= */
.quick-skeleton {
  cursor: default;
  pointer-events: none;
}

.skeleton-thumb {
  width: 56px;
  height: 56px;
  background: rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
  animation: pulse 1.5s ease-in-out infinite;
}

.skeleton-info {
  flex: 1;
  padding: 0 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skeleton-line-title {
  height: 13px;
  width: 75%;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  animation: pulse 1.5s ease-in-out infinite;
  animation-delay: 0.1s;
}

.skeleton-line-sub {
  height: 10px;
  width: 45%;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.05);
  animation: pulse 1.5s ease-in-out infinite;
  animation-delay: 0.2s;
}

.skeleton-section-title {
  height: 18px;
  width: 140px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  animation: pulse 1.5s ease-in-out infinite;
}

.feed-card-skeleton {
  width: 128px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skeleton-feed-cover {
  width: 128px;
  height: 128px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  animation: pulse 1.5s ease-in-out infinite;
}

.skeleton-feed-title {
  height: 13px;
  width: 80%;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  animation: pulse 1.5s ease-in-out infinite;
  animation-delay: 0.1s;
}

.skeleton-feed-sub {
  height: 10px;
  width: 50%;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.05);
  animation: pulse 1.5s ease-in-out infinite;
  animation-delay: 0.2s;
}

@keyframes pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 0.8; }
}
</style>
