<template>
  <div class="friends-view">
    <!-- No channel - show setup prompt -->
    <div v-if="!authStore.hasChannel" class="no-channel-prompt">
      <div class="prompt-icon"><Users :size="52" /></div>
      <h2>Социальная сеть AuxBass</h2>
      <p>Подключите Telegram-канал, чтобы подписываться на других пользователей, слушать их медиатеки и следить за свежими треками друзей.</p>
      <button class="setup-btn" @click="goToChannelSetup">
        <Sparkles :size="16" />
        <span>Подключить канал</span>
      </button>
    </div>

    <!-- Has channel - show friends hub -->
    <template v-else>
      <!-- ═══ Social Hub Hero Header ═══ -->
      <div 
        v-if="authStore.user" 
        class="social-hub-header" 
        @click="goToMyProfile"
        title="Перейти в свой профиль"
      >
        <!-- Background Ambient Glow -->
        <div class="hub-ambient-glow"></div>

        <div class="hub-user-main">
          <!-- Avatar with glowing ring -->
          <div class="hub-avatar-wrapper">
            <div class="hub-avatar">
              <img 
                v-if="authStore.userAvatarUrl" 
                :src="authStore.userAvatarUrl" 
                class="hub-avatar-img" 
                alt="Avatar" 
              />
              <span v-else class="hub-avatar-initials">{{ getInitials(authStore.user) }}</span>
            </div>
            <div class="hub-avatar-ring"></div>
          </div>

          <!-- User info & quick stats -->
          <div class="hub-user-details">
            <div class="hub-user-name-line">
              <span class="hub-user-name">{{ authStore.userDisplayName }}</span>
              <span class="hub-self-badge">Вы</span>
            </div>

            <div class="hub-user-meta">
              <span v-if="authStore.user.username" class="hub-handle">@{{ authStore.user.username }}</span>
              <span v-if="authStore.user.username" class="meta-separator">•</span>
              <span class="hub-status-text">Мой публичный профиль</span>
            </div>

            <!-- Header Quick Stats Pills -->
            <div class="hub-stats-row" @click.stop>
              <button 
                class="hub-stat-chip" 
                :class="{ active: activeTab === 'following' }"
                @click="activeTab = 'following'"
                title="Посмотреть подписки"
              >
                <Users :size="12" class="chip-icon" />
                <span class="chip-num">{{ following.length }}</span>
                <span class="chip-label">подписок</span>
              </button>

              <button 
                class="hub-stat-chip"
                :class="{ active: activeTab === 'followers' }"
                @click="activeTab = 'followers'"
                title="Посмотреть подписчиков"
              >
                <UserCheck :size="12" class="chip-icon" />
                <span class="chip-num">{{ followers.length }}</span>
                <span class="chip-label">подписчиков</span>
              </button>

              <div v-if="mutualCount > 0" class="hub-stat-chip mutual-chip" title="Взаимные подписки">
                <Sparkles :size="12" class="chip-icon" />
                <span class="chip-num">{{ mutualCount }}</span>
                <span class="chip-label">взаимно</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Open Profile Action Button -->
        <div class="hub-action-btn">
          <span>Профиль</span>
          <ChevronRight :size="18" />
        </div>
      </div>

      <!-- ═══ Unified Tab Navigation ═══ -->
      <div class="neu-tab-bar friends-tabs">
        <button 
          class="neu-tab" 
          :class="{ active: activeTab === 'feed' }"
          @click="activeTab = 'feed'"
        >
          <Flame :size="16" />
          <span class="neu-tab-content" data-text="Лента">Лента</span>
        </button>

        <button 
          class="neu-tab" 
          :class="{ active: activeTab === 'following' }"
          @click="activeTab = 'following'"
        >
          <Users :size="16" />
          <span class="neu-tab-content" data-text="Подписки">
            Подписки
            <span v-if="following.length" class="tab-badge">{{ following.length }}</span>
          </span>
        </button>

        <button 
          class="neu-tab" 
          :class="{ active: activeTab === 'followers' }"
          @click="activeTab = 'followers'"
        >
          <User :size="16" />
          <span class="neu-tab-content" data-text="Подписчики">
            Подписчики
            <span v-if="followers.length" class="tab-badge">{{ followers.length }}</span>
          </span>
        </button>

        <button 
          v-if="canUseSocial"
          class="neu-tab" 
          :class="{ active: activeTab === 'search' }"
          @click="activeTab = 'search'"
        >
          <Search :size="16" />
          <span class="neu-tab-content" data-text="Поиск">Поиск</span>
        </button>
      </div>

      <!-- ══════════════════════════════════════════
           TAB 1: Activity Feed
           ══════════════════════════════════════════ -->
      <div v-show="activeTab === 'feed'" class="tab-content">
        <SocialFeed @navigate-tab="handleNavigateTab" />
      </div>

      <!-- ══════════════════════════════════════════
           TAB 2: Following List
           ══════════════════════════════════════════ -->
      <div v-show="activeTab === 'following'" class="tab-content">
        <!-- Control Toolbar: Filter, Sort & View Mode Switcher -->
        <div v-if="following.length > 0" class="social-toolbar">
          <div class="toolbar-search-box">
            <Search :size="15" class="search-input-icon" />
            <input 
              v-model="followingSearch" 
              type="text" 
              class="toolbar-search-input"
              placeholder="Фильтр по имени или @username..."
            />
            <button 
              v-if="followingSearch" 
              class="clear-search-btn" 
              @click="followingSearch = ''"
              title="Очистить"
            >
              <X :size="14" />
            </button>
          </div>

          <div class="toolbar-controls-right">
            <!-- Sort Filter -->
            <div class="sort-selector">
              <button 
                class="toolbar-btn sort-btn" 
                @click="cycleSort"
                :title="`Сортировка: ${sortLabel}`"
              >
                <ArrowUpDown :size="14" />
                <span class="sort-text">{{ sortLabel }}</span>
              </button>
            </div>

            <!-- View Switcher (Grid vs List) -->
            <div class="view-mode-toggle">
              <button 
                class="view-toggle-btn" 
                :class="{ active: viewMode === 'grid' }"
                @click="viewMode = 'grid'"
                title="Сетка карточек"
              >
                <LayoutGrid :size="15" />
              </button>
              <button 
                class="view-toggle-btn" 
                :class="{ active: viewMode === 'list' }"
                @click="viewMode = 'list'"
                title="Компактный список"
              >
                <List :size="15" />
              </button>
            </div>
          </div>
        </div>

        <!-- Loading State -->
        <div v-if="loading" class="loading-state">
          <div class="spinner"></div>
        </div>

        <!-- Empty: No following at all -->
        <div v-else-if="following.length === 0" class="empty-glass-card">
          <div class="empty-icon-glow">
            <Users :size="48" />
          </div>
          <h3>Вы пока ни на кого не подписаны</h3>
          <p>Подписывайтесь на других пользователей, чтобы видеть их новые треки в своей персональной ленте и слушать их плейлисты.</p>
          <button v-if="canUseSocial" class="btn-pill-primary" @click="activeTab = 'search'">
            <Search :size="15" />
            <span>Найти друзей</span>
          </button>
        </div>

        <!-- Empty: No matches after local filter -->
        <div v-else-if="displayedFollowing.length === 0" class="empty-glass-card">
          <div class="empty-icon-glow">
            <Search :size="42" />
          </div>
          <h3>Никого не найдено</h3>
          <p>Среди ваших подписок нет пользователей, соответствующих запросу «{{ followingSearch }}».</p>
          <button class="btn-pill-secondary" @click="followingSearch = ''">
            Сбросить фильтр
          </button>
        </div>

        <!-- Following Cards Grid / List -->
        <div v-else :class="['users-collection', `view-${viewMode}`]">
          <UserSocialCard
            v-for="user in displayedFollowing"
            :key="user.id"
            :user="user"
            :layout="viewMode"
            :isSelf="user.id === authStore.user?.id"
            :isMutual="mutualUserIds.has(user.id)"
            :isLoading="actionLoadingId === user.id"
            @click="viewUserProfile(user)"
            @play="handlePlayUserTracks(user)"
            @follow="followUser(user)"
            @unfollow="unfollowUser(user)"
          />
        </div>
      </div>

      <!-- ══════════════════════════════════════════
           TAB 3: Followers List
           ══════════════════════════════════════════ -->
      <div v-show="activeTab === 'followers'" class="tab-content">
        <!-- Control Toolbar: Filter, Sort & View Mode Switcher -->
        <div v-if="followers.length > 0" class="social-toolbar">
          <div class="toolbar-search-box">
            <Search :size="15" class="search-input-icon" />
            <input 
              v-model="followersSearch" 
              type="text" 
              class="toolbar-search-input"
              placeholder="Фильтр подписчиков..."
            />
            <button 
              v-if="followersSearch" 
              class="clear-search-btn" 
              @click="followersSearch = ''"
              title="Очистить"
            >
              <X :size="14" />
            </button>
          </div>

          <div class="toolbar-controls-right">
            <!-- Sort Filter -->
            <div class="sort-selector">
              <button 
                class="toolbar-btn sort-btn" 
                @click="cycleSort"
                :title="`Сортировка: ${sortLabel}`"
              >
                <ArrowUpDown :size="14" />
                <span class="sort-text">{{ sortLabel }}</span>
              </button>
            </div>

            <!-- View Switcher (Grid vs List) -->
            <div class="view-mode-toggle">
              <button 
                class="view-toggle-btn" 
                :class="{ active: viewMode === 'grid' }"
                @click="viewMode = 'grid'"
                title="Сетка карточек"
              >
                <LayoutGrid :size="15" />
              </button>
              <button 
                class="view-toggle-btn" 
                :class="{ active: viewMode === 'list' }"
                @click="viewMode = 'list'"
                title="Компактный список"
              >
                <List :size="15" />
              </button>
            </div>
          </div>
        </div>

        <!-- Loading State -->
        <div v-if="loading" class="loading-state">
          <div class="spinner"></div>
        </div>

        <!-- Empty: No followers yet -->
        <div v-else-if="followers.length === 0" class="empty-glass-card">
          <div class="empty-icon-glow">
            <User :size="48" />
          </div>
          <h3>У вас пока нет подписчиков</h3>
          <p>Делитесь ссылкой на свой публичный профиль с друзьями, загружайте интересные треки, и другие слушатели подпишутся на вашу волну!</p>
          <button class="btn-pill-primary" @click="goToMyProfile">
            <Sparkles :size="15" />
            <span>Открыть мой профиль</span>
          </button>
        </div>

        <!-- Empty: No matches after local filter -->
        <div v-else-if="displayedFollowers.length === 0" class="empty-glass-card">
          <div class="empty-icon-glow">
            <Search :size="42" />
          </div>
          <h3>Подписчик не найден</h3>
          <p>Среди ваших подписчиков нет совпадений по запросу «{{ followersSearch }}».</p>
          <button class="btn-pill-secondary" @click="followersSearch = ''">
            Сбросить фильтр
          </button>
        </div>

        <!-- Followers Cards Grid / List -->
        <div v-else :class="['users-collection', `view-${viewMode}`]">
          <UserSocialCard
            v-for="user in displayedFollowers"
            :key="user.id"
            :user="user"
            :layout="viewMode"
            :isSelf="user.id === authStore.user?.id"
            :isMutual="mutualUserIds.has(user.id)"
            :isLoading="actionLoadingId === user.id"
            @click="viewUserProfile(user)"
            @play="handlePlayUserTracks(user)"
            @follow="followUser(user)"
            @unfollow="unfollowUser(user)"
          />
        </div>
      </div>

      <!-- ══════════════════════════════════════════
           TAB 4: Search & Discover
           ══════════════════════════════════════════ -->
      <div v-show="activeTab === 'search'" class="tab-content">
        <!-- Search Input Bar -->
        <div class="search-input-wrapper">
          <div class="search-bar-inner">
            <Search :size="18" class="search-icon-active" />
            <input 
              v-model="searchQuery" 
              type="text" 
              class="search-text-input"
              placeholder="Введите никнейм или @username друга для поиска..."
              @input="debouncedSearch"
              autofocus
            />
            <button 
              v-if="searchQuery" 
              class="clear-search-btn" 
              @click="clearSearch"
              title="Очистить поиск"
            >
              <X :size="16" />
            </button>
          </div>
        </div>

        <!-- Searching Spinner -->
        <div v-if="searching" class="loading-state">
          <div class="spinner"></div>
        </div>

        <!-- Search Empty Results -->
        <div v-else-if="searchQuery.trim().length >= 2 && searchResults.length === 0" class="empty-glass-card">
          <div class="empty-icon-glow">
            <Search :size="46" />
          </div>
          <h3>Никого не нашлось</h3>
          <p>Пользователя с именем или юзернеймом «{{ searchQuery }}» не найдено в AuxBass.</p>
        </div>

        <!-- Search Results Collection -->
        <div v-else-if="searchResults.length > 0" class="search-results-section">
          <div class="results-header">
            <span class="results-count">Найдено: {{ searchResults.length }}</span>
          </div>

          <div :class="['users-collection', `view-${viewMode}`]">
            <UserSocialCard
              v-for="user in searchResults"
              :key="user.id"
              :user="user"
              :layout="viewMode"
              :isSelf="user.id === authStore.user?.id"
              :isMutual="mutualUserIds.has(user.id)"
              :isLoading="actionLoadingId === user.id"
              @click="viewUserProfile(user)"
              @play="handlePlayUserTracks(user)"
              @follow="followUser(user)"
              @unfollow="unfollowUser(user)"
            />
          </div>
        </div>

        <!-- Default Hint State when search query is empty -->
        <div v-else class="search-discovery-hint">
          <div class="discovery-icon-box">
            <Lightbulb :size="32" />
          </div>
          <h3>Найдите друзей и музыкантов</h3>
          <p>Вводите Telegram-юзернейм (например, @durov) или имя пользователя, чтобы находить интересные медиатеки и слушать музыку вместе.</p>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useAuthStore } from '@/stores/auth'
import { socialApi } from '@/api/client'
import SocialFeed from '@/components/social/SocialFeed.vue'
import UserSocialCard from '@/components/social/UserSocialCard.vue'
import { 
  Users, 
  User, 
  Search, 
  Flame, 
  Lightbulb, 
  ChevronRight,
  LayoutGrid,
  List,
  Sparkles,
  ArrowUpDown,
  X,
  UserCheck
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const playerStore = usePlayerStore()
const authStore = useAuthStore()

// Navigate to channel setup
const goToChannelSetup = () => {
  router.push({ name: 'settings', query: { section: 'channel' } })
}

const goToMyProfile = () => {
  if (authStore.user?.id) {
    router.push(`/user/${authStore.user.id}`)
  }
}

// Check if user can use social features
const canUseSocial = computed(() => authStore.hasChannel)

// Tab state (default to feed!)
const activeTab = ref(route.query.tab || 'feed')

// View mode: 'grid' or 'list' (load preference from localStorage)
const viewMode = ref(localStorage.getItem('auxbass_friends_view_mode') || 'grid')

watch(viewMode, (newVal) => {
  localStorage.setItem('auxbass_friends_view_mode', newVal)
})

// Sort mode: 'default' | 'tracks' | 'name'
const sortBy = ref('default')

const sortLabel = computed(() => {
  if (sortBy.value === 'tracks') return 'По трекам'
  if (sortBy.value === 'name') return 'По имени'
  return 'По умолчанию'
})

const cycleSort = () => {
  if (sortBy.value === 'default') sortBy.value = 'tracks'
  else if (sortBy.value === 'tracks') sortBy.value = 'name'
  else sortBy.value = 'default'
}

// Data
const following = ref([])
const followers = ref([])
const searchResults = ref([])
const loading = ref(false)
const searching = ref(false)
const actionLoadingId = ref(null)

const searchQuery = ref('')
const followingSearch = ref('')
const followersSearch = ref('')

// Debounce timer
let searchTimer = null

// Mutual follows detection
const mutualUserIds = computed(() => {
  const followerIds = new Set(followers.value.map(u => u.id))
  return new Set(following.value.filter(u => followerIds.has(u.id)).map(u => u.id))
})

const mutualCount = computed(() => mutualUserIds.value.size)

// Filter & Sort for Following
const displayedFollowing = computed(() => {
  let list = [...following.value]
  
  if (followingSearch.value.trim()) {
    const q = followingSearch.value.toLowerCase().trim()
    list = list.filter(u => 
      (u.display_name && u.display_name.toLowerCase().includes(q)) ||
      (u.username && u.username.toLowerCase().includes(q)) ||
      (u.custom_nickname && u.custom_nickname.toLowerCase().includes(q))
    )
  }

  if (sortBy.value === 'tracks') {
    list.sort((a, b) => (b.track_count || 0) - (a.track_count || 0))
  } else if (sortBy.value === 'name') {
    list.sort((a, b) => (a.display_name || '').localeCompare(b.display_name || '', 'ru'))
  }

  return list
})

// Filter & Sort for Followers
const displayedFollowers = computed(() => {
  let list = [...followers.value]
  
  if (followersSearch.value.trim()) {
    const q = followersSearch.value.toLowerCase().trim()
    list = list.filter(u => 
      (u.display_name && u.display_name.toLowerCase().includes(q)) ||
      (u.username && u.username.toLowerCase().includes(q)) ||
      (u.custom_nickname && u.custom_nickname.toLowerCase().includes(q))
    )
  }

  if (sortBy.value === 'tracks') {
    list.sort((a, b) => (b.track_count || 0) - (a.track_count || 0))
  } else if (sortBy.value === 'name') {
    list.sort((a, b) => (a.display_name || '').localeCompare(b.display_name || '', 'ru'))
  }

  return list
})

const getInitials = (user) => {
  if (user?.id === authStore.user?.id && authStore.userDisplayName) {
    return authStore.userDisplayName.charAt(0).toUpperCase()
  }
  if (!user) return '?'
  if (user.custom_nickname) return user.custom_nickname.charAt(0).toUpperCase()
  if (user.display_name) return user.display_name.charAt(0).toUpperCase()
  if (user.first_name) return user.first_name.charAt(0).toUpperCase()
  if (user.username) return user.username.charAt(0).toUpperCase()
  return '?'
}

const loadFollowing = async () => {
  loading.value = true
  try {
    const response = await socialApi.getFollowing()
    following.value = response.data?.items || []
  } catch (error) {
    console.error('Failed to load following:', error)
  } finally {
    loading.value = false
  }
}

const loadFollowers = async () => {
  loading.value = true
  try {
    const response = await socialApi.getFollowers()
    followers.value = response.data?.items || []
  } catch (error) {
    console.error('Failed to load followers:', error)
  } finally {
    loading.value = false
  }
}

const debouncedSearch = () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(searchUsers, 300)
}

const clearSearch = () => {
  searchQuery.value = ''
  searchResults.value = []
}

const searchUsers = async () => {
  if (!searchQuery.value || searchQuery.value.trim().length < 2) {
    searchResults.value = []
    return
  }
  
  searching.value = true
  try {
    const response = await socialApi.searchUsers(searchQuery.value.trim())
    searchResults.value = response.data?.items || []
  } catch (error) {
    console.error('Search failed:', error)
  } finally {
    searching.value = false
  }
}

const followUser = async (user) => {
  if (!canUseSocial.value) {
    authStore.promptChannelSetup()
    return
  }

  actionLoadingId.value = user.id
  try {
    await socialApi.follow(user.id)
    user.is_following = true
    if (!following.value.find(u => u.id === user.id)) {
      following.value.unshift(user)
    }
  } catch (error) {
    if (error.response?.status === 403) {
      authStore.promptChannelSetup()
    } else {
      console.error('Failed to follow:', error)
    }
  } finally {
    actionLoadingId.value = null
  }
}

const unfollowUser = async (user) => {
  if (!canUseSocial.value) {
    authStore.promptChannelSetup()
    return
  }

  actionLoadingId.value = user.id
  try {
    await socialApi.unfollow(user.id)
    user.is_following = false
    following.value = following.value.filter(u => u.id !== user.id)
  } catch (error) {
    if (error.response?.status === 403) {
      authStore.promptChannelSetup()
    } else {
      console.error('Failed to unfollow:', error)
    }
  } finally {
    actionLoadingId.value = null
  }
}

const handlePlayUserTracks = async (user) => {
  try {
    const res = await socialApi.getUserLibrary(user.id, { per_page: 50 })
    const tracks = res.data?.items || []
    if (tracks.length > 0) {
      playerStore.playTrack(tracks[0], tracks)
    }
  } catch (e) {
    console.error('Failed to play user tracks:', e)
  }
}

const viewUserProfile = (user) => {
  if (user?.id) {
    router.push(`/user/${user.id}`)
  }
}

const handleNavigateTab = (tabName) => {
  activeTab.value = tabName
}

// Load data on tab change
watch(activeTab, (tab) => {
  if (tab === 'following') {
    loadFollowing()
  } else if (tab === 'followers') {
    loadFollowers()
  }
})

onMounted(() => {
  // Always load following and followers in background so counts & badges are ready
  loadFollowing()
  loadFollowers()
  
  if (route.query.viewUser) {
    router.replace(`/user/${route.query.viewUser}`)
  }
  
  window.addEventListener('reset-view-state', handleResetState)
})

onUnmounted(() => {
  window.removeEventListener('reset-view-state', handleResetState)
})

const handleResetState = (event) => {
  if (event.detail.route === '/friends') {
    activeTab.value = 'feed'
    searchQuery.value = ''
    searchResults.value = []
    followingSearch.value = ''
    followersSearch.value = ''
    loadFollowing()
    loadFollowers()
  }
}
</script>

<style scoped>
.friends-view {
  padding: 16px;
  max-width: 1040px;
  margin: 0 auto;
  width: 100%;
}

/* ══════════════════════════════════════════════
   SOCIAL HUB HERO HEADER
   ══════════════════════════════════════════════ */
.social-hub-header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 24px;
  background: var(--c-bg-2);
  border-radius: var(--r-2xl);
  border: 1px solid rgba(255, 255, 255, 0.05);
  box-shadow: 
    4px 6px 18px var(--sh-dark),
    -2px -2px 6px var(--sh-light);
  margin-bottom: 22px;
  cursor: pointer;
  /* overflow: hidden REMOVED — was clipping box-shadow and hub-ambient-glow */
  /* Ambient glow child is absolutely positioned; use border-radius for visual clip */
  isolation: isolate;
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}

.social-hub-header:hover {
  background: var(--c-bg-3);
  border-color: rgba(34, 197, 94, 0.3);
  transform: translateY(-2px);
  box-shadow: 
    6px 10px 24px var(--sh-dark),
    0 0 20px rgba(34, 197, 94, 0.1);
}

.social-hub-header:active {
  transform: scale(0.995);
}

/* Ambient glow spotlight behind the avatar */
.hub-ambient-glow {
  position: absolute;
  top: -50px;
  left: -20px;
  width: 180px;
  height: 180px;
  background: radial-gradient(circle, rgba(34, 197, 94, 0.22) 0%, transparent 70%);
  pointer-events: none;
  filter: blur(20px);
  /* Prevent glow from protruding beyond card corners via parent isolation+border-radius */
  border-radius: inherit;
}

.hub-user-main {
  display: flex;
  align-items: center;
  gap: 18px;
  min-width: 0;
  flex: 1;
}

.hub-avatar-wrapper {
  position: relative;
  flex-shrink: 0;
}

.hub-avatar {
  width: 60px;
  height: 60px;
  border-radius: var(--r-full);
  background: linear-gradient(135deg, var(--c-accent), #3b82f6, #8b5cf6);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
  border: 2px solid rgba(255, 255, 255, 0.1);
  position: relative;
  z-index: 1;
}

.hub-avatar-ring {
  position: absolute;
  inset: -3px;
  border-radius: var(--r-full);
  border: 2px solid var(--c-accent);
  opacity: 0.5;
  filter: drop-shadow(0 0 6px var(--c-accent-glow));
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.social-hub-header:hover .hub-avatar-ring {
  opacity: 0.9;
  transform: scale(1.04);
}

.hub-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.hub-avatar-initials {
  font-size: 22px;
  font-weight: 800;
  color: #fff;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.4);
}

.hub-user-details {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.hub-user-name-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.hub-user-name {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hub-self-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: var(--r-full);
  background: var(--c-accent);
  color: #fff;
  line-height: 1;
  box-shadow: 0 0 8px var(--c-accent-glow);
}

.hub-user-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--c-text-2);
}

.hub-handle {
  color: var(--c-text-3);
  font-weight: 500;
}

.meta-separator {
  color: var(--c-text-3);
  font-size: 10px;
}

.hub-status-text {
  color: var(--c-text-2);
}

/* Hub Quick Stats Row */
.hub-stats-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.hub-stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: var(--c-bg-1);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: var(--r-full);
  font-size: 12px;
  color: var(--c-text-2);
  cursor: pointer;
  transition: all 0.15s ease;
}

.hub-stat-chip:hover {
  background: var(--c-bg-4);
  color: var(--c-text-1);
  border-color: rgba(255, 255, 255, 0.1);
}

.hub-stat-chip.active {
  background: rgba(34, 197, 94, 0.14);
  border-color: rgba(34, 197, 94, 0.35);
  color: var(--c-accent);
}

.chip-icon {
  color: var(--c-accent);
}

.chip-num {
  font-weight: 700;
  color: var(--c-text-1);
}

.chip-label {
  color: var(--c-text-3);
}

.mutual-chip {
  background: rgba(59, 130, 246, 0.12);
  border-color: rgba(59, 130, 246, 0.25);
  cursor: default;
}

.mutual-chip .chip-icon {
  color: #60a5fa;
}

.mutual-chip .chip-num {
  color: #93c5fd;
}

/* Hub Action Button */
.hub-action-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: var(--r-full);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
  color: var(--c-text-2);
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
  transition: all 0.2s ease;
}

.social-hub-header:hover .hub-action-btn {
  background: var(--c-accent);
  border-color: var(--c-accent);
  color: #fff;
  box-shadow: 0 0 12px var(--c-accent-glow);
}

/* ══════════════════════════════════════════════
   TABS NAVIGATION
   ══════════════════════════════════════════════ */
.friends-tabs {
  margin-bottom: 20px;
  /* Extra vertical padding: prevents glow on active .neu-tab from being clipped by overflow-x scroll */
  padding: 8px 2px;
  margin-top: -8px;
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  margin-left: 6px;
  font-size: 11px;
  font-weight: 700;
  border-radius: var(--r-full);
  background: rgba(255, 255, 255, 0.08);
  color: var(--c-text-2);
  transition: all 0.2s ease;
}

.neu-tab.active .tab-badge {
  background: var(--c-accent);
  color: #fff;
  box-shadow: 0 0 8px var(--c-accent-glow);
}

/* ══════════════════════════════════════════════
   TOOLBAR: FILTER, SORT & VIEW SWITCHER
   ══════════════════════════════════════════════ */
.social-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.toolbar-search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 240px;
  padding: 8px 14px;
  background: var(--c-bg-2);
  border-radius: var(--r-full);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: inset 1px 1px 3px var(--sh-dark);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.toolbar-search-box:focus-within {
  border-color: rgba(34, 197, 94, 0.4);
  box-shadow: inset 1px 1px 3px var(--sh-dark), 0 0 10px rgba(34, 197, 94, 0.15);
}

.search-input-icon {
  color: var(--c-text-3);
  flex-shrink: 0;
}

.toolbar-search-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--c-text-1);
  font-size: 13px;
}

.toolbar-search-input::placeholder {
  color: var(--c-text-3);
}

.clear-search-btn {
  background: transparent;
  border: none;
  color: var(--c-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 2px;
  border-radius: var(--r-full);
  transition: color 0.15s ease;
}

.clear-search-btn:hover {
  color: var(--c-text-1);
}

.toolbar-controls-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.toolbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  background: var(--c-bg-2);
  border: 1px solid rgba(255, 255, 255, 0.04);
  border-radius: var(--r-full);
  color: var(--c-text-2);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 2px 2px 5px var(--sh-dark);
  transition: all 0.15s ease;
}

.toolbar-btn:hover {
  background: var(--c-bg-3);
  color: var(--c-text-1);
}

.view-mode-toggle {
  display: flex;
  align-items: center;
  background: var(--c-bg-2);
  border: 1px solid rgba(255, 255, 255, 0.04);
  border-radius: var(--r-full);
  padding: 3px;
  box-shadow: 2px 2px 5px var(--sh-dark);
}

.view-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-full);
  background: transparent;
  border: none;
  color: var(--c-text-3);
  cursor: pointer;
  transition: all 0.15s ease;
}

.view-toggle-btn:hover {
  color: var(--c-text-1);
}

.view-toggle-btn.active {
  background: var(--c-bg-4);
  color: var(--c-accent);
  box-shadow: 0 0 8px rgba(34, 197, 94, 0.2);
}

/* ══════════════════════════════════════════════
   USERS COLLECTION (GRID & LIST)
   ══════════════════════════════════════════════ */
.users-collection.view-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 16px;
}

.users-collection.view-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* ══════════════════════════════════════════════
   SEARCH TAB
   ══════════════════════════════════════════════ */
.search-input-wrapper {
  margin-bottom: 20px;
}

.search-bar-inner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  background: var(--c-bg-2);
  border-radius: var(--r-full);
  border: 1px solid rgba(255, 255, 255, 0.06);
  box-shadow: 
    3px 3px 8px var(--sh-dark),
    -2px -2px 4px var(--sh-light);
  transition: all 0.2s ease;
}

.search-bar-inner:focus-within {
  border-color: var(--c-accent);
  box-shadow: 
    3px 3px 10px var(--sh-dark),
    0 0 14px var(--c-accent-glow);
}

.search-icon-active {
  color: var(--c-accent);
  flex-shrink: 0;
}

.search-text-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 15px;
  color: var(--c-text-1);
}

.search-text-input::placeholder {
  color: var(--c-text-3);
}

.results-header {
  margin-bottom: 14px;
}

.results-count {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-2);
}

.search-discovery-hint {
  text-align: center;
  padding: 60px 24px;
  color: var(--c-text-2);
}

.discovery-icon-box {
  width: 68px;
  height: 68px;
  border-radius: var(--r-full);
  background: var(--c-bg-2);
  border: 1px solid rgba(255, 255, 255, 0.04);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
  color: var(--c-accent);
  box-shadow: 
    3px 3px 10px var(--sh-dark),
    0 0 16px rgba(34, 197, 94, 0.15);
}

.search-discovery-hint h3 {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-text-1);
  margin: 0 0 8px;
}

.search-discovery-hint p {
  font-size: 14px;
  color: var(--c-text-2);
  max-width: 440px;
  margin: 0 auto;
  line-height: 1.5;
}

/* ══════════════════════════════════════════════
   EMPTY STATES & LOADING
   ══════════════════════════════════════════════ */
.empty-glass-card {
  text-align: center;
  padding: 56px 24px;
  background: var(--c-bg-2);
  border-radius: var(--r-2xl);
  border: 1px solid rgba(255, 255, 255, 0.03);
  box-shadow: 
    4px 6px 16px var(--sh-dark),
    -2px -2px 5px var(--sh-light);
  margin-top: 8px;
}

.empty-icon-glow {
  width: 72px;
  height: 72px;
  border-radius: var(--r-full);
  background: var(--c-bg-3);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 18px;
  color: var(--c-accent);
  box-shadow: 
    inset 2px 2px 5px var(--sh-dark),
    inset -1px -1px 3px var(--sh-light);
}

.empty-glass-card h3 {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-text-1);
  margin: 0 0 8px;
}

.empty-glass-card p {
  font-size: 14px;
  color: var(--c-text-2);
  max-width: 420px;
  margin: 0 auto 22px;
  line-height: 1.5;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 50px 0;
}

/* ══════════════════════════════════════════════
   NO CHANNEL SETUP PROMPT
   ══════════════════════════════════════════════ */
.no-channel-prompt {
  text-align: center;
  padding: 64px 24px;
  background: var(--c-bg-2);
  border-radius: var(--r-2xl);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: 
    4px 6px 18px var(--sh-dark),
    -2px -2px 6px var(--sh-light);
}

.prompt-icon {
  color: var(--c-accent);
  margin-bottom: 16px;
  filter: drop-shadow(0 0 14px var(--c-accent-glow));
}

.setup-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
  padding: 10px 24px;
  border-radius: var(--r-full);
  background: var(--c-accent);
  color: #fff;
  border: none;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 
    2px 2px 8px var(--sh-dark),
    0 0 12px var(--c-accent-glow);
  transition: all 0.18s ease;
}

.setup-btn:hover {
  background: var(--c-accent-light);
  transform: translateY(-1px);
  box-shadow: 
    2px 4px 12px var(--sh-dark),
    0 0 16px var(--c-accent-glow);
}

/* Responsive queries */
@media (max-width: 640px) {
  .social-hub-header {
    flex-direction: column;
    align-items: flex-start;
    padding: 16px;
  }

  .hub-action-btn {
    align-self: flex-end;
  }

  .users-collection.view-grid {
    grid-template-columns: 1fr;
  }
}
</style>
