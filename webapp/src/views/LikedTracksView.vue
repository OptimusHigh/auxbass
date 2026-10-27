<template>
  <div class="liked-tracks-view">
    <!-- No channel AND no existing content - show setup prompt -->
    <div v-if="!authStore.hasChannel && !hasExistingContent" class="no-channel-prompt">
      <div class="prompt-icon">
        <Heart :size="48" />
      </div>
      <h2>Понравившиеся</h2>
      <p>Подключите Telegram-канал, чтобы сохранять понравившиеся треки</p>
      <button class="setup-btn" @click="goToChannelSetup">
        Подключить канал
      </button>
    </div>

    <template v-else>
      <!-- Channel Warning Banner if disconnected but user has tracks -->
      <div v-if="!authStore.hasChannel" class="channel-warning-banner" @click="goToChannelSetup">
        <span class="warning-icon">⚠️</span>
        <div class="warning-text">
          <strong>Связь с Telegram-каналом потеряна</strong>
          <span>{{ authStore.channelError || 'Бот удалён или не имеет прав администратора. Новые лайки не будут сохранены в канал.' }}</span>
        </div>
        <button class="warning-btn">Подключить</button>
      </div>
      <!-- Unified Hero Header -->
      <div class="hero-header">
        <div class="hero-cover liked-cover">
          <Heart :size="48" class="liked-icon" />
        </div>
        <div class="hero-info">
          <h1 class="hero-title">Понравившиеся</h1>
          <p class="hero-meta">{{ tracks.length }} треков</p>
        </div>
      </div>

      <!-- Unified Actions with Expandable Search in one row -->
      <div class="hero-actions" :class="{ 'search-active': isSearchOpen }" v-if="tracks.length">
        <div class="action-buttons" v-if="!isSearchOpen">
          <button 
            class="action-btn play-btn" 
            @click="togglePlay" 
            :title="playButtonTitle"
          >
            <Pause v-if="isPlaying" :size="20" fill="currentColor" />
            <Play v-else :size="20" fill="currentColor" />
          </button>
          <button class="action-btn shuffle-btn" @click="handleShuffle" :disabled="isShuffling || !sortedTracks.length" title="Перемешать">
            <Shuffle :size="18" />
          </button>
        </div>

        <ExpandableSearch
          v-if="tracks.length > 5"
          v-model="searchQuery"
          v-model:open="isSearchOpen"
          placeholder="Поиск по понравившимся..."
          title="Поиск по понравившимся"
        />
      </div>

      <!-- Loading -->
      <div v-if="loading" class="loading">
        <div class="spinner"></div>
      </div>

      <!-- Track list -->
      <div class="track-list" v-else-if="sortedTracks.length">
        <TrackItem
          v-for="(track, index) in sortedTracks"
          :key="track.id"
          :track="track"
          :isPlaying="playerStore.currentTrack?.id === track.id"
          :isLiked="true"
          @click="playTrack(track, index)"
          @like="unlikeTrack(track)"
          @menu="(e) => openMenu('track', track, 'liked', e)"
          @download="handleDirectDownload(track)"
          @hdNotice="handleHdNotice"
        />
      </div>

      <!-- Empty search state -->
      <div v-else-if="searchQuery" class="empty-state search-empty">
        <div class="empty-icon">
          <Search :size="48" />
        </div>
        <p>Ничего не найдено по запросу «{{ searchQuery }}»</p>
        <button type="button" class="btn-global-search" @click="goToGlobalSearch">
          <Search :size="16" />
          <span>Искать в глобальном поиске</span>
        </button>
      </div>

      <!-- Empty state -->
      <div v-else class="empty-state">
        <div class="empty-icon">
          <Heart :size="48" />
        </div>
        <p>Нет понравившихся треков</p>
        <p class="hint">Нажмите ♡ на треке, чтобы добавить</p>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { useRouter } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useLibraryStore } from '@/stores/library'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { useContextMenu } from '@/composables/useContextMenu'
import { useTrackActions, usePlaybackActions, useTrackSync } from '@/composables'
import TrackItem from '@/components/TrackItem.vue'
import ExpandableSearch from '@/components/ui/ExpandableSearch.vue'
import { Heart, Play, Pause, Shuffle, Search } from 'lucide-vue-next'

// Universal context menu
const { openMenu } = useContextMenu()

const router = useRouter()
const playerStore = usePlayerStore()
const libraryStore = useLibraryStore()
const authStore = useAuthStore()
const uiStore = useUIStore()

// Unified track actions
const { handleDirectDownload, handleHdNotice } = useTrackActions()

const goToChannelSetup = () => {
  router.push('/settings#channel')
}

const hasExistingContent = computed(() => {
  return (tracks.value?.length > 0) || !!authStore.channelInfo || !!authStore.channelError
})

const tracks = computed(() => libraryStore.likedTracks)
const loading = ref(true)
const searchQuery = ref('')
const isSearchOpen = ref(false)

const goToGlobalSearch = () => {
  const q = searchQuery.value.trim()
  if (q) {
    router.push({ path: '/search', query: { q } })
  }
}

// Sync liked tracks with global track events
useTrackSync(() => libraryStore.likedTracks)

const sortedTracks = computed(() => {
  let list = [...tracks.value].sort((a, b) => {
    const dateA = new Date(a.liked_at || 0)
    const dateB = new Date(b.liked_at || 0)
    return dateB - dateA
  })
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase().replace(/^#/, '')
    list = list.filter(t => 
      t.title?.toLowerCase().includes(q) || 
      t.artist?.toLowerCase().includes(q) ||
      (t.tags && t.tags.some(tag => (typeof tag === 'string' ? tag : tag?.tag)?.toLowerCase().includes(q)))
    )
  }
  return list
})

// Unified playback actions
const { playAll, togglePlay, isPlaying, isCurrentContext, playButtonTitle, shufflePlay, shufflePlayFull, isShuffling, playTrack } = usePlaybackActions(
  sortedTracks,
  () => ({ type: 'liked', name: 'Любимые треки' })
)

const handleShuffle = () => {
  shufflePlayFull('liked')
}

const unlikeTrack = async (track) => {
  try {
    await libraryStore.toggleLike(track.id, true)
  } catch (error) {
    console.error('Failed to unlike track:', error)
  }
}

onMounted(async () => {
  loading.value = true
  await libraryStore.fetchLikedTracks()
  loading.value = false
})

onActivated(async () => {
  await libraryStore.fetchLikedTracks()
})
</script>

<style scoped>
.liked-tracks-view {
  padding: 16px;
}

.hero-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  min-height: 44px;
}

.hero-actions.search-active {
  justify-content: stretch;
}

.liked-cover {
  background: linear-gradient(135deg, #2a151b 0%, #181818 100%);
  border: 1px solid rgba(255, 69, 100, 0.2);
  box-shadow: 
    6px 6px 14px var(--sh-dark),
    -3px -3px 8px var(--sh-light),
    0 0 24px rgba(255, 69, 100, 0.15);
}

.liked-icon {
  color: #ff4564;
  filter: drop-shadow(0 0 10px rgba(255, 69, 100, 0.6));
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

.empty-state .empty-icon {
  display: flex;
  justify-content: center;
  margin-bottom: 16px;
  color: rgba(255, 69, 100, 0.4);
}

.empty-state p {
  margin: 0 0 8px 0;
}

.empty-state .hint {
  font-size: 13px;
  color: var(--c-text-3);
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
