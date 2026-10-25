<template>
  <Teleport to="body">
    <Transition name="fade">
      <div 
        v-if="isOpen" 
        class="menu-overlay" 
        :class="{ desktop: isDesktop }" 
        @click="handleOverlayClick"
        @touchstart.passive="handleOverlayTouchStart"
        @contextmenu.prevent="handleOverlayContextMenu"
      >
        <Transition :name="isDesktop ? 'scale' : 'slide-up'">
          <div 
            v-if="isOpen" 
            ref="menuSheet"
            class="menu-sheet" 
            :class="{ desktop: isDesktop }"
            :style="isDesktop && adjustedPosition.x ? { left: adjustedPosition.x + 'px', top: adjustedPosition.y + 'px' } : {}"
            @click.stop
            @contextmenu.stop.prevent
          >
            <!-- Mobile Subview: Artists Selection -->
            <template v-if="!isDesktop && mobileSubmenu === 'artists'">
              <div class="menu-header mobile-sub-header">
                <button class="menu-back-btn" @click="mobileSubmenu = null">
                  <ChevronLeft :size="18" />
                  <span>Назад</span>
                </button>
                <div class="menu-info">
                  <div class="menu-title">Исполнители</div>
                  <div class="menu-subtitle">{{ title }}</div>
                </div>
                <button class="menu-close" @click="closeMenu">
                  <X :size="20" />
                </button>
              </div>
              <div class="menu-items">
                <button 
                  v-for="artist in currentSubmenuArtists" 
                  :key="artist"
                  class="menu-item"
                  @click="goToSpecificArtist(artist)"
                >
                  <User :size="18" />
                  <span>{{ artist }}</span>
                </button>
              </div>
            </template>

            <!-- Mobile Subview: Share Options -->
            <template v-else-if="!isDesktop && mobileSubmenu === 'share'">
              <div class="menu-header mobile-sub-header">
                <button class="menu-back-btn" @click="mobileSubmenu = null">
                  <ChevronLeft :size="18" />
                  <span>Назад</span>
                </button>
                <div class="menu-info">
                  <div class="menu-title">Поделиться</div>
                  <div class="menu-subtitle">{{ title }}</div>
                </div>
                <button class="menu-close" @click="closeMenu">
                  <X :size="20" />
                </button>
              </div>
              <div class="menu-items">
                <button class="menu-item" @click="handleShareAction('copy')">
                  <Link2 :size="18" />
                  <span>{{ shareCopyLabel }}</span>
                </button>
                <button class="menu-item" @click="handleShareAction('inline')">
                  <AtSign :size="18" />
                  <span>Скопировать инлайн-запрос</span>
                </button>
                <button class="menu-item" @click="handleShareAction('telegram')">
                  <Send :size="18" />
                  <span>Отправить в чат Telegram</span>
                </button>
                <button class="menu-item" @click="handleShareAction('download')">
                  <CloudDownload :size="18" />
                  <span>Скачать в Telegram</span>
                </button>
              </div>
            </template>

            <!-- Main Menu View -->
            <template v-else>
              <!-- Header -->
              <div class="menu-header">
                <div class="menu-cover" :style="coverStyle">
                  <component :is="coverIcon" v-if="!hasCover" :size="20" />
                </div>
                <div class="menu-info">
                  <div class="menu-title">{{ title }}</div>
                  <div class="menu-subtitle">{{ subtitle }}</div>
                </div>
                <button class="menu-close" @click="closeMenu">
                  <X :size="20" />
                </button>
              </div>

              <!-- Tags (for tracks with enrichment tags) -->
              <TagChips
                v-if="menuType === 'track' && menuData?.tags?.length"
                :tags="menuData.tags"
                :max="5"
                :clickable="true"
                size="sm"
                class="menu-tags"
                @tagClick="handleTagClick"
              />

              <!-- Menu Items -->
              <div class="menu-items">
                <!-- ═══ TRACK MENU ═══ -->
                <template v-if="menuType === 'track'">
                  <!-- Navigation -->
                  <!-- Multiple artists: Spotify-style flyout submenu -->
                  <button 
                    v-if="hasArtist && parsedArtists.length > 1"
                    class="menu-item has-submenu" 
                    :class="{ 'is-submenu-active': isDesktop && activeSubmenu === 'artists' }"
                    @mouseenter="handleTriggerMouseEnter('artists', $event)"
                    @mouseleave="handleTriggerMouseLeave('artists')"
                    @click="handleArtistTriggerClick"
                  >
                    <User :size="18" />
                    <span>Перейти к артисту</span>
                    <ChevronRight :size="16" class="submenu-arrow" />
                  </button>
                  <!-- Single artist: direct navigation -->
                  <button 
                    v-else-if="hasArtist && parsedArtists.length === 1" 
                    class="menu-item" 
                    @mouseenter="handleRegularItemMouseEnter"
                    @click="exec('goToArtist')"
                  >
                    <User :size="18" />
                    <span>Перейти к артисту</span>
                  </button>

                  <button v-if="hasAlbum" class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('goToAlbum')">
                    <Disc3 :size="18" />
                    <span>{{ albumButtonText }}</span>
                  </button>
                  <div v-if="hasArtist || hasAlbum" class="menu-divider" />

                  <!-- Queue (hide for current track in player) -->
                  <template v-if="menuContext !== 'player'">
                    <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('playNext')">
                      <Play :size="18" fill="currentColor" />
                      <span>Включить следующим</span>
                    </button>
                    <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('addToQueue')">
                      <ListMusic :size="18" />
                      <span>Добавить в очередь</span>
                    </button>
                  </template>

                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('addToPlaylist')">
                    <Plus :size="18" />
                    <span>Добавить в плейлист</span>
                  </button>
                  <div class="menu-divider" />

                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('edit')">
                    <Pencil :size="18" />
                    <span>Редактировать</span>
                  </button>

                  <!-- Share submenu (Spotify-style) -->
                  <button 
                    class="menu-item has-submenu" 
                    :class="{ 'is-submenu-active': isDesktop && activeSubmenu === 'share' }"
                    @mouseenter="handleTriggerMouseEnter('share', $event)"
                    @mouseleave="handleTriggerMouseLeave('share')"
                    @click="handleShareTriggerClick"
                  >
                    <Share2 :size="18" />
                    <span>Поделиться</span>
                    <ChevronRight :size="16" class="submenu-arrow" />
                  </button>

                  <!-- HD version available for current playing track -->
                  <button v-if="hasHDVersion" class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('downloadHD')">
                    <Disc3 :size="18" />
                    <span>Скачать HD версию</span>
                  </button>
                  <div class="menu-divider" />

                  <!-- Remove from playlist (in playlist context) -->
                  <button v-if="inPlaylistContext" class="menu-item danger" @mouseenter="handleRegularItemMouseEnter" @click="exec('removeFromPlaylist', playlistId)">
                    <Minus :size="18" />
                    <span>Убрать из плейлиста</span>
                  </button>

                  <!-- Dislike action -->
                  <button class="menu-item" :class="{ 'disliked-active': isDisliked }" @mouseenter="handleRegularItemMouseEnter" @click="exec('toggleDislike')">
                    <ThumbsDown :size="18" :fill="isDisliked ? 'currentColor' : 'none'" />
                    <span>{{ isDisliked ? 'Убрать дизлайк' : 'Не нравится' }}</span>
                  </button>

                  <div class="menu-divider" />

                  <!-- Owner can delete -->
                  <button v-if="isTrackOwner" class="menu-item danger" @mouseenter="handleRegularItemMouseEnter" @click="exec('delete')">
                    <Trash2 :size="18" />
                    <span>Удалить полностью</span>
                  </button>
                  <!-- In library but not owner - remove from library -->
                  <button v-else-if="isInLibrary" class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('removeFromLibrary')">
                    <Minus :size="18" />
                    <span>Убрать из библиотеки</span>
                  </button>
                  <!-- Not in library - add -->
                  <button v-else class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('addToLibrary')">
                    <Plus :size="18" />
                    <span>Добавить в библиотеку</span>
                  </button>
                </template>

                <!-- ═══ PLAYLIST MENU ═══ -->
                <template v-else-if="menuType === 'playlist'">
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('open')">
                    <FolderOpen :size="18" />
                    <span>Открыть</span>
                  </button>
                  <button v-if="isDesktop" class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('togglePin')">
                    <PinOff v-if="uiStore.isPlaylistPinned(menuData?.id)" :size="18" />
                    <Pin v-else :size="18" />
                    <span>{{ uiStore.isPlaylistPinned(menuData?.id) ? 'Открепить от сайдбара' : 'Закрепить в сайдбаре' }}</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('playAll')">
                    <Play :size="18" fill="currentColor" />
                    <span>Воспроизвести все</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('shuffle')">
                    <Shuffle :size="18" />
                    <span>Перемешать</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('addToQueue')">
                    <ListMusic :size="18" />
                    <span>Добавить в очередь</span>
                  </button>

                  <!-- Share submenu (Spotify-style) -->
                  <button 
                    class="menu-item has-submenu" 
                    :class="{ 'is-submenu-active': isDesktop && activeSubmenu === 'share' }"
                    @mouseenter="handleTriggerMouseEnter('share', $event)"
                    @mouseleave="handleTriggerMouseLeave('share')"
                    @click="handleShareTriggerClick"
                  >
                    <Share2 :size="18" />
                    <span>Поделиться</span>
                    <ChevronRight :size="16" class="submenu-arrow" />
                  </button>

                  <!-- Only for user playlists that user owns (non-system) -->
                  <template v-if="isPlaylistOwner && !menuData?.is_system && menuData?.system_type !== 'minecraft' && menuData?.system_type !== 'minecraft_main'">
                    <div class="menu-divider" />
                    <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('rename')">
                      <Pencil :size="18" />
                      <span>Переименовать</span>
                    </button>
                    <button class="menu-item danger" @mouseenter="handleRegularItemMouseEnter" @click="exec('delete')">
                      <Trash2 :size="18" />
                      <span>Удалить плейлист</span>
                    </button>
                  </template>
                  <!-- For public playlists from other users: subscribe/unsubscribe -->
                  <template v-else-if="menuData?.is_public">
                    <div class="menu-divider" />
                    <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('toggleSubscription')">
                      <Check v-if="menuData?.is_subscribed" :size="18" />
                      <Plus v-else :size="18" />
                      <span>{{ menuData?.is_subscribed ? 'Убрать из медиатеки' : 'Добавить в медиатеку' }}</span>
                    </button>
                  </template>
                </template>

                <!-- ═══ LIKED / FAVORITES MENU ═══ -->
                <template v-else-if="menuType === 'liked'">
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('open')">
                    <FolderOpen :size="18" />
                    <span>Открыть</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('playAll')">
                    <Play :size="18" fill="currentColor" />
                    <span>Воспроизвести все</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('shuffle')">
                    <Shuffle :size="18" />
                    <span>Перемешать</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('addToQueue')">
                    <ListMusic :size="18" />
                    <span>Добавить в очередь</span>
                  </button>
                </template>

                <!-- ═══ ALBUM MENU ═══ -->
                <template v-else-if="menuType === 'album'">
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('open')">
                    <FolderOpen :size="18" />
                    <span>Открыть альбом</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('playAll')">
                    <Play :size="18" fill="currentColor" />
                    <span>Воспроизвести все</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('shuffle')">
                    <Shuffle :size="18" />
                    <span>Перемешать</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('addToQueue')">
                    <ListMusic :size="18" />
                    <span>Добавить в очередь</span>
                  </button>

                  <!-- Share submenu (Spotify-style) -->
                  <button 
                    class="menu-item has-submenu" 
                    :class="{ 'is-submenu-active': isDesktop && activeSubmenu === 'share' }"
                    @mouseenter="handleTriggerMouseEnter('share', $event)"
                    @mouseleave="handleTriggerMouseLeave('share')"
                    @click="handleShareTriggerClick"
                  >
                    <Share2 :size="18" />
                    <span>Поделиться</span>
                    <ChevronRight :size="16" class="submenu-arrow" />
                  </button>
                  <div class="menu-divider" />

                  <!-- Multi-artist or single artist navigation for album -->
                  <button 
                    v-if="hasAlbumArtist && parsedAlbumArtists.length > 1"
                    class="menu-item has-submenu" 
                    :class="{ 'is-submenu-active': isDesktop && activeSubmenu === 'artists' }"
                    @mouseenter="handleTriggerMouseEnter('artists', $event)"
                    @mouseleave="handleTriggerMouseLeave('artists')"
                    @click="handleArtistTriggerClick"
                  >
                    <User :size="18" />
                    <span>Перейти к артисту</span>
                    <ChevronRight :size="16" class="submenu-arrow" />
                  </button>
                  <button v-else-if="hasAlbumArtist" class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('goToArtist')">
                    <User :size="18" />
                    <span>Перейти к артисту</span>
                  </button>
                </template>

                <!-- ═══ ARTIST MENU ═══ -->
                <template v-else-if="menuType === 'artist'">
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('open')">
                    <User :size="18" />
                    <span>Открыть артиста</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('playAll')">
                    <Play :size="18" fill="currentColor" />
                    <span>Воспроизвести все</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('shuffle')">
                    <Shuffle :size="18" />
                    <span>Перемешать</span>
                  </button>
                  <button class="menu-item" @mouseenter="handleRegularItemMouseEnter" @click="exec('addToQueue')">
                    <ListMusic :size="18" />
                    <span>Добавить в очередь</span>
                  </button>
                </template>
              </div>
            </template>
          </div>
        </Transition>

        <!-- Desktop Floating Flyout Submenu (Spotify-style) -->
        <Transition name="flyout-scale">
          <div
            v-if="isDesktop && activeSubmenu"
            ref="flyoutSubmenuEl"
            class="flyout-submenu"
            :style="{ left: submenuPosition.x + 'px', top: submenuPosition.y + 'px' }"
            @mouseenter="handleSubmenuMouseEnter"
            @mouseleave="handleSubmenuMouseLeave"
            @click.stop
            @contextmenu.stop.prevent
          >
            <!-- Artists Submenu -->
            <template v-if="activeSubmenu === 'artists'">
              <button 
                v-for="artist in currentSubmenuArtists" 
                :key="artist"
                class="submenu-item"
                @click="goToSpecificArtist(artist)"
              >
                <User :size="15" />
                <span class="submenu-text">{{ artist }}</span>
              </button>
            </template>

            <!-- Share Submenu -->
            <template v-else-if="activeSubmenu === 'share'">
              <button class="submenu-item" @click="handleShareAction('copy')">
                <Link2 :size="15" />
                <span class="submenu-text">{{ shareCopyLabel }}</span>
              </button>
              <button class="submenu-item" @click="handleShareAction('inline')">
                <AtSign :size="15" />
                <span class="submenu-text">Скопировать инлайн-запрос</span>
              </button>
              <button class="submenu-item" @click="handleShareAction('telegram')">
                <Send :size="15" />
                <span class="submenu-text">Отправить в чат Telegram</span>
              </button>
              <button class="submenu-item" @click="handleShareAction('download')">
                <CloudDownload :size="15" />
                <span class="submenu-text">Скачать в Telegram</span>
              </button>
            </template>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>

  <!-- Playlist Picker Modal -->
  <PlaylistPicker
    :show="showPlaylistPicker"
    :track="editingItem"
    @close="closePlaylistPicker"
    @createNew="openCreatePlaylist"
    @added="onPlaylistAdded"
  />

  <!-- Edit Track Modal -->
  <EditTrackModal
    :show="showEditModal"
    :track="editingItem"
    @close="closeEditModal"
    @saved="onTrackSaved"
  />

  <!-- Create Playlist Modal -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="showCreatePlaylist" class="modal-overlay" @click="closeCreatePlaylist">
        <div class="modal-content" @click.stop>
          <h3>Создать плейлист</h3>
          <input 
            v-model="newPlaylistName" 
            type="text" 
            class="modal-input"
            placeholder="Название плейлиста"
            @keyup.enter="confirmCreatePlaylist"
            ref="createPlaylistInput"
          />
          <div class="modal-actions">
            <button type="button" class="modal-btn cancel" @click="closeCreatePlaylist">Отмена</button>
            <button type="button" class="modal-btn confirm" @click="confirmCreatePlaylist">Создать</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- Rename Playlist Modal -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="showRenameModal" class="modal-overlay" @click="closeRenameModal">
        <div class="modal-content" @click.stop>
          <h3>Переименовать плейлист</h3>
          <input 
            v-model="renameValue" 
            type="text" 
            class="modal-input"
            placeholder="Название плейлиста"
            @keyup.enter="confirmRename"
            ref="renameInput"
          />
          <div class="modal-actions">
            <button type="button" class="modal-btn cancel" @click="closeRenameModal">Отмена</button>
            <button type="button" class="modal-btn confirm" @click="confirmRename">Сохранить</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, watch, nextTick, ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useContextMenu } from '@/composables/useContextMenu'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { usePlayerStore } from '@/stores/player'
import { useShare } from '@/composables/useShare'
import { getAllTrackArtists } from '@/utils/formatters'
import PlaylistPicker from '@/components/PlaylistPicker.vue'
import EditTrackModal from '@/components/EditTrackModal.vue'
import TagChips from '@/components/TagChips.vue'
import { 
  X, User, Disc3, Play, ListMusic, Plus, Minus, Pencil, Check,
  Trash2, FolderOpen, Shuffle, Music, Mic2, ChevronRight, ChevronDown,
  ChevronLeft, ThumbsDown, Heart, Share2, Pin, PinOff, Link2, Send, CloudDownload, AtSign
} from 'lucide-vue-next'

const router = useRouter()
const uiStore = useUIStore()
const { copyLink, copyInlineCommand, shareToTelegramChat, downloadToTelegram } = useShare()

// Submenu state (Spotify-style)
const activeSubmenu = ref(null) // 'artists' | 'share' | null (desktop flyout)
const mobileSubmenu = ref(null) // 'artists' | 'share' | null (mobile subview)
const submenuPosition = ref({ x: 0, y: 0 })
const currentTriggerEl = ref(null)
const flyoutSubmenuEl = ref(null)
let openSubmenuTimer = null
let closeSubmenuTimer = null

const isDisliked = computed(() => menuType.value === 'track' && !!menuData.value?.is_disliked)

const authStore = useAuthStore()
const renameInput = ref(null)
const menuSheet = ref(null)
const isDesktop = ref(false)
const adjustedPosition = ref({ x: 0, y: 0 })

const handleTagClick = (tag) => {
  if (!tag) return
  closeMenu()
  const cleanTag = tag.replace(/^#/, '')
  router.push({ path: '/search', query: { tag: cleanTag } })
}

// Detect desktop
const checkDesktop = () => {
  isDesktop.value = window.innerWidth >= 768 && !('ontouchstart' in window)
}

let menuOpenTimestamp = 0

const handleOverlayTouchStart = (e) => {
  // Passive touch feedback
}

const handleOverlayContextMenu = (e) => {
  e.preventDefault()
  closeMenu()
}

const handleOverlayClick = (e) => {
  if (e && e.target !== e.currentTarget) return
  // Ignore clicks that occur immediately after opening (e.g. synthetic click on finger release after long-press)
  if (Date.now() - menuOpenTimestamp < 350) {
    return
  }
  closeMenu()
}

// Handle keyboard events
const handleKeyDown = (e) => {
  if (e.key === 'Escape') {
    if (activeSubmenu.value) {
      activeSubmenu.value = null
      return
    }
    if (mobileSubmenu.value) {
      mobileSubmenu.value = null
      return
    }
    closeMenu()
  }
}

onMounted(() => {
  checkDesktop()
  window.addEventListener('resize', checkDesktop)
  window.addEventListener('keydown', handleKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('resize', checkDesktop)
  window.removeEventListener('keydown', handleKeyDown)
  if (openSubmenuTimer) clearTimeout(openSubmenuTimer)
  if (closeSubmenuTimer) clearTimeout(closeSubmenuTimer)
})

const {
  isOpen,
  menuType,
  menuData,
  menuContext,
  menuPosition,
  closeMenu: rawCloseMenu,
  executeAction,
  showPlaylistPicker,
  showEditModal,
  showRenameModal,
  showCreatePlaylist,
  editingItem,
  renameValue,
  newPlaylistName,
  closePlaylistPicker,
  onPlaylistAdded,
  openCreatePlaylist,
  closeCreatePlaylist,
  confirmCreatePlaylist,
  closeEditModal,
  onTrackSaved,
  closeRenameModal,
  confirmRename,
} = useContextMenu()

const closeMenu = () => {
  activeSubmenu.value = null
  mobileSubmenu.value = null
  if (openSubmenuTimer) { clearTimeout(openSubmenuTimer); openSubmenuTimer = null }
  if (closeSubmenuTimer) { clearTimeout(closeSubmenuTimer); closeSubmenuTimer = null }
  rawCloseMenu()
}

const createPlaylistInput = ref(null)

// Auto-focus rename input
watch(showRenameModal, (show) => {
  if (show) {
    nextTick(() => renameInput.value?.focus())
  }
})

// Auto-focus create playlist input
watch(showCreatePlaylist, (show) => {
  if (show) {
    nextTick(() => createPlaylistInput.value?.focus())
  }
})

// Reset submenu state and track open timestamp
watch(isOpen, (open) => {
  if (open) {
    menuOpenTimestamp = Date.now()
    overlayTouchStarted = false
    activeSubmenu.value = null
    mobileSubmenu.value = null
  } else {
    activeSubmenu.value = null
    mobileSubmenu.value = null
    if (openSubmenuTimer) { clearTimeout(openSubmenuTimer); openSubmenuTimer = null }
    if (closeSubmenuTimer) { clearTimeout(closeSubmenuTimer); closeSubmenuTimer = null }
  }
})

// Execute action shorthand
const exec = (action, extra = null) => {
  executeAction(action, extra)
}

// Go to specific artist (for multi-artist tracks or albums)
const goToSpecificArtist = (artistName) => {
  executeAction('goToArtistByName', artistName)
}

// Submenu positioning & interactions (Spotify desktop flyout)
const computeSubmenuPosition = (triggerEl, submenuType) => {
  if (!triggerEl) return
  const rect = triggerEl.getBoundingClientRect()
  const submenuWidth = 240
  const padding = 8

  // Default placement: immediately to the right of the main menu sheet
  let x = rect.right + 4
  // If overflows right screen edge, flip to the left
  if (x + submenuWidth + padding > window.innerWidth) {
    x = Math.max(padding, rect.left - submenuWidth - 4)
  }

  // Align top with trigger item
  let y = rect.top - 4
  const count = submenuType === 'artists' ? (currentSubmenuArtists.value.length || 1) : 4
  const estimatedHeight = Math.min(340, count * 40 + 16)
  if (y + estimatedHeight + padding > window.innerHeight) {
    y = Math.max(padding, window.innerHeight - estimatedHeight - padding)
  }

  submenuPosition.value = { x, y }

  nextTick(() => {
    if (flyoutSubmenuEl.value) {
      const el = flyoutSubmenuEl.value
      const actualWidth = el.offsetWidth || submenuWidth
      const actualHeight = el.offsetHeight || estimatedHeight
      let curX = submenuPosition.value.x
      let curY = submenuPosition.value.y
      if (curX + actualWidth + padding > window.innerWidth) {
        curX = Math.max(padding, rect.left - actualWidth - 4)
      }
      if (curY + actualHeight + padding > window.innerHeight) {
        curY = Math.max(padding, window.innerHeight - actualHeight - padding)
      }
      submenuPosition.value = { x: curX, y: curY }
    }
  })
}

const handleTriggerMouseEnter = (type, event) => {
  if (!isDesktop.value) return
  if (closeSubmenuTimer) {
    clearTimeout(closeSubmenuTimer)
    closeSubmenuTimer = null
  }
  if (openSubmenuTimer) {
    clearTimeout(openSubmenuTimer)
  }
  const el = event.currentTarget
  currentTriggerEl.value = el
  openSubmenuTimer = setTimeout(() => {
    computeSubmenuPosition(el, type)
    activeSubmenu.value = type
  }, 60)
}

const handleTriggerMouseLeave = (type) => {
  if (!isDesktop.value) return
  if (openSubmenuTimer) {
    clearTimeout(openSubmenuTimer)
    openSubmenuTimer = null
  }
  closeSubmenuTimer = setTimeout(() => {
    if (activeSubmenu.value === type) {
      activeSubmenu.value = null
    }
  }, 180)
}

const handleSubmenuMouseEnter = () => {
  if (closeSubmenuTimer) {
    clearTimeout(closeSubmenuTimer)
    closeSubmenuTimer = null
  }
}

const handleSubmenuMouseLeave = () => {
  if (!isDesktop.value) return
  closeSubmenuTimer = setTimeout(() => {
    activeSubmenu.value = null
  }, 180)
}

const handleRegularItemMouseEnter = () => {
  if (!isDesktop.value) return
  if (openSubmenuTimer) {
    clearTimeout(openSubmenuTimer)
    openSubmenuTimer = null
  }
  if (activeSubmenu.value) {
    activeSubmenu.value = null
  }
}

const handleShareTriggerClick = (event) => {
  if (isDesktop.value) {
    computeSubmenuPosition(event.currentTarget, 'share')
    activeSubmenu.value = activeSubmenu.value === 'share' ? null : 'share'
  } else {
    mobileSubmenu.value = 'share'
  }
}

const handleArtistTriggerClick = (event) => {
  if (isDesktop.value) {
    computeSubmenuPosition(event.currentTarget, 'artists')
    activeSubmenu.value = activeSubmenu.value === 'artists' ? null : 'artists'
  } else {
    mobileSubmenu.value = 'artists'
  }
}

// Share Payload Builder
const getSharePayload = () => {
  const data = menuData.value
  if (!data) return {}

  if (menuType.value === 'track') {
    return {
      type: 'track',
      id: data.id,
      title: data.title || data.file_name || 'Трек',
      subtitle: data.artist || 'Неизвестен',
      coverUrl: data.cover_url || ''
    }
  } else if (menuType.value === 'playlist') {
    return {
      type: 'playlist',
      id: data.id,
      title: data.name || 'Плейлист',
      subtitle: `${data.track_count || data.tracks_count || 0} треков`,
      coverUrl: data.custom_cover_url || ''
    }
  } else if (menuType.value === 'album') {
    return {
      type: 'album',
      id: data.id,
      title: data.name || 'Альбом',
      subtitle: data.album_artist || data.artist || 'Альбом',
      coverUrl: data.cover_url || ''
    }
  }
  return {
    type: menuType.value,
    id: data.id,
    title: data.name || data.title || '',
    subtitle: '',
    coverUrl: data.cover_url || ''
  }
}

const handleShareAction = async (action) => {
  const payload = getSharePayload()
  closeMenu()

  switch (action) {
    case 'copy':
      await copyLink(payload)
      break
    case 'inline':
      await copyInlineCommand(payload)
      break
    case 'telegram':
      await shareToTelegramChat(payload)
      break
    case 'download':
      await downloadToTelegram(payload)
      break
  }
}
// Calculate position for desktop mode (under cursor, within screen bounds)
watch([isOpen, menuPosition], ([open, pos]) => {
  if (open && isDesktop.value && pos.x > 0) {
    nextTick(() => {
      const menu = menuSheet.value
      if (!menu) return
      
      const menuWidth = menu.offsetWidth || 280
      const menuHeight = menu.offsetHeight || 400
      const padding = 8
      
      let x = pos.x
      let y = pos.y
      
      // Adjust if menu goes beyond right edge
      if (x + menuWidth + padding > window.innerWidth) {
        x = window.innerWidth - menuWidth - padding
      }
      
      // Adjust if menu goes beyond bottom edge
      if (y + menuHeight + padding > window.innerHeight) {
        y = window.innerHeight - menuHeight - padding
      }
      
      // Ensure minimum offset from edges
      x = Math.max(padding, x)
      y = Math.max(padding, y)
      
      adjustedPosition.value = { x, y }
    })
  }
})

// Share labels
const shareCopyLabel = computed(() => {
  switch (menuType.value) {
    case 'track': return 'Скопировать ссылку на трек'
    case 'playlist': return 'Скопировать ссылку на плейлист'
    case 'album': return 'Скопировать ссылку на альбом'
    default: return 'Скопировать ссылку'
  }
})

// Album multi-artist support
const parsedAlbumArtists = computed(() => {
  const data = menuData.value
  if (!data) return []
  const artistStr = data.album_artist || data.artist
  if (!artistStr) return []
  return getAllTrackArtists(artistStr)
})

const currentSubmenuArtists = computed(() => {
  if (menuType.value === 'album') return parsedAlbumArtists.value
  return parsedArtists.value
})

// ═══════════════════════════════════════════════════════════
// COMPUTED HELPERS
// ═══════════════════════════════════════════════════════════

const hasCover = computed(() => {
  return menuData.value?.cover_url || menuData.value?.image_url
})

const coverStyle = computed(() => {
  const url = menuData.value?.cover_url || menuData.value?.image_url
  if (url) {
    return {
      backgroundImage: `url(${url})`,
      backgroundSize: 'cover',
      backgroundPosition: 'center'
    }
  }
  if (menuType.value === 'liked') {
    return {
      background: 'linear-gradient(135deg, #450af5 0%, #8b5cf6 50%, #c084fc 100%)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      color: '#ffffff'
    }
  }
  return {}
})

const coverIcon = computed(() => {
  switch (menuType.value) {
    case 'track': return Music
    case 'playlist': return ListMusic
    case 'album': return Disc3
    case 'artist': return Mic2
    case 'liked': return Heart
    default: return Music
  }
})

const title = computed(() => {
  const data = menuData.value
  if (!data) return ''
  
  switch (menuType.value) {
    case 'track': return data.title || 'Без названия'
    case 'playlist': return data.name || 'Плейлист'
    case 'album': return data.name || data.album_name || 'Альбом'
    case 'artist': return typeof data === 'string' ? data : data.name || 'Артист'
    case 'liked': return data.name || 'Понравившиеся'
    default: return ''
  }
})

const subtitle = computed(() => {
  const data = menuData.value
  if (!data) return ''
  
  switch (menuType.value) {
    case 'track': return data.artist || 'Неизвестный исполнитель'
    case 'playlist': return `${data.track_count || 0} ${getTracksWord(data.track_count || 0)}`
    case 'album': return data.album_artist || data.artist || ''
    case 'artist': return `${data.track_count || ''} ${data.track_count ? getTracksWord(data.track_count) : ''}`
    case 'liked': return `${data.track_count || 0} ${getTracksWord(data.track_count || 0)}`
    default: return ''
  }
})

// Track-specific
const hasArtist = computed(() => {
  const data = menuData.value
  if (!data) return false
  // Check if we have artist metadata OR can extract from filename
  if (data.artist && data.artist !== 'Неизвестный исполнитель') return true
  // For tracks without metadata, check if we can extract from filename
  if (!data.artist && data.file_name) {
    const extracted = getAllTrackArtists(null, data.title, data.file_name)
    return extracted.length > 0
  }
  return false
})

// Parse artists into array (from artist field + extracted from title + filename)
const parsedArtists = computed(() => {
  const data = menuData.value
  if (!data) return []
  return getAllTrackArtists(data.artist, data.title, data.file_name)
})

const hasAlbum = computed(() => {
  const data = menuData.value
  return data?.album_id || data?.album?.id || data?.album_name
})

// Get album button text - show album name
const albumButtonText = computed(() => {
  const data = menuData.value
  const albumName = data?.album?.name || data?.album_name
  
  if (albumName) {
    return `Перейти к альбому (${albumName})`
  }
  
  return 'Перейти к альбому'
})

const isTrackOwner = computed(() => {
  const userId = authStore.user?.id
  return menuData.value?.uploader?.id === userId
})

const isInLibrary = computed(() => {
  const data = menuData.value
  if (data?.in_library !== undefined) return data.in_library
  if (data?.library_source && data.library_source !== 'global') return true
  if (['library', 'liked', 'playlist'].includes(menuContext.value)) return true
  return false
})

const inPlaylistContext = computed(() => {
  return menuContext.value?.startsWith('playlist:')
})

const playlistId = computed(() => {
  if (inPlaylistContext.value) {
    return menuContext.value.split(':')[1]
  }
  return null
})

const isPlaylistOwner = computed(() => {
  const userId = authStore.user?.id
  // Check if playlist has owner_id or user_id field
  return menuData.value?.owner_id === userId || 
         menuData.value?.user_id === userId ||
         menuData.value?.is_owner === true
})

// Track HD version (only available in player context)
const hasHDVersion = computed(() => {
  if (menuContext.value !== 'player') return false
  // Get from playerStore since HD info is only available for current track
  const playerStore = usePlayerStore()
  return !!playerStore.hdTrackInfo
})

// HD MIME types for track detection
const HD_MIME_TYPES = [
  'audio/flac', 'audio/x-flac',
  'audio/wav', 'audio/x-wav',
  'audio/aiff', 'audio/x-aiff',
  'audio/x-m4a', 'audio/mp4',
  'audio/alac', 'audio/x-alac'
]
const MAX_STREAMABLE_SIZE = 20 * 1024 * 1024

// Check if current track is HD/large file (show download HD option)
const isTrackHD = computed(() => {
  if (menuType.value !== 'track') return false
  const track = menuData.value
  if (!track) return false
  // HD format check
  if (track.mime_type && HD_MIME_TYPES.includes(track.mime_type.toLowerCase())) return true
  // Large file check
  if (track.file_size && track.file_size > MAX_STREAMABLE_SIZE) return true
  return false
})

// Album-specific
const hasAlbumArtist = computed(() => {
  return menuData.value?.album_artist || menuData.value?.artist
})

// Helpers
const getTracksWord = (count) => {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod100 >= 11 && mod100 <= 14) return 'треков'
  if (mod10 === 1) return 'трек'
  if (mod10 >= 2 && mod10 <= 4) return 'трека'
  return 'треков'
}
</script>

<style scoped>
.menu-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  z-index: var(--z-contextmenu, 1100);
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

/* Desktop overlay - transparent, just for catching clicks */
.menu-overlay.desktop {
  background: transparent;
  backdrop-filter: none;
}

.menu-sheet {
  width: 100%;
  max-width: 400px;
  max-height: 80vh;
  background: var(--c-bg-2);
  border-radius: var(--r-xl) var(--r-xl) 0 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 -8px 28px var(--sh-dark);
  border-top: var(--border-neu);
}

/* Desktop menu - floating under cursor */
.menu-sheet.desktop {
  position: fixed;
  width: 280px;
  max-width: none;
  max-height: 70vh;
  border-radius: var(--r-lg);
  background: var(--c-bg-2);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 
    10px 10px 30px rgba(0, 0, 0, 0.85),
    -3px -3px 8px var(--sh-light);
  border: var(--border-neu);
}

.menu-sheet.desktop .menu-header {
  padding: 12px;
}

.menu-sheet.desktop .menu-cover {
  width: 40px;
  height: 40px;
}

.menu-sheet.desktop .menu-item {
  padding: 10px 14px;
  font-size: 14px;
}

.menu-sheet.desktop .menu-close {
  display: none;
}

.menu-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.menu-cover {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  background: linear-gradient(135deg, #333, #222);
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.5);
  flex-shrink: 0;
}

.menu-info {
  flex: 1;
  min-width: 0;
}

.menu-title {
  font-weight: 600;
  color: white;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.menu-subtitle {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.menu-close {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.menu-tags {
  padding: 4px 16px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.menu-items {
  padding: 8px 0;
  overflow-y: auto;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: calc(100% - 16px);
  margin: 1px 8px;
  padding: 10px 14px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  color: var(--c-text-1);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
}

.menu-item:hover {
  background: var(--c-bg-3);
  color: #FFFFFF;
}

.menu-item:active {
  background: var(--c-bg-4);
  transform: scale(0.98);
}

.menu-item.danger {
  color: #ff6b6b;
}

.menu-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.08);
  margin: 6px 14px;
}

/* Submenu styles (Spotify style) */
.menu-item.has-submenu {
  justify-content: flex-start;
  position: relative;
}

.menu-item.has-submenu .submenu-arrow {
  margin-left: auto;
  opacity: 0.5;
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.menu-item.has-submenu:hover .submenu-arrow,
.menu-item.is-submenu-active .submenu-arrow {
  opacity: 1;
}

.menu-item.is-submenu-active {
  background: var(--c-bg-3);
  color: white;
}

/* Floating Submenu (Spotify Desktop Flyout) */
.flyout-submenu {
  position: fixed;
  width: 240px;
  background: var(--c-bg-2);
  border: var(--border-neu);
  border-radius: var(--r-md);
  box-shadow: 
    12px 12px 30px rgba(0, 0, 0, 0.85),
    -3px -3px 8px var(--sh-light);
  padding: 6px;
  z-index: calc(var(--z-contextmenu, 1100) + 20);
  max-height: 380px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}

.submenu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 9px 12px;
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.9);
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
  text-align: left;
  border-radius: 5px;
  transition: background 0.12s ease, color 0.12s ease;
}

.submenu-item:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
}

.submenu-item:active {
  background: rgba(255, 255, 255, 0.18);
}

.submenu-item svg {
  opacity: 0.75;
  flex-shrink: 0;
}

.submenu-item:hover svg {
  opacity: 1;
}

.submenu-text {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.submenu-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.08);
  margin: 5px 6px;
}

/* Mobile subview header & back button */
.mobile-sub-header {
  padding: 12px 16px !important;
}

.menu-back-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  color: white;
  padding: 6px 12px 6px 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s ease;
}

.menu-back-btn:active {
  background: rgba(255, 255, 255, 0.18);
}

/* Flyout scale animation */
.flyout-scale-enter-active,
.flyout-scale-leave-active {
  transition: transform 0.12s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.12s ease;
  transform-origin: top left;
}

.flyout-scale-enter-from,
.flyout-scale-leave-to {
  transform: scale(0.95);
  opacity: 0;
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  z-index: var(--z-modal, 1200);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.modal-content {
  background: #1a1a1a;
  border-radius: 12px;
  padding: 20px;
  width: 100%;
  max-width: 320px;
}

.modal-content h3 {
  margin: 0 0 16px;
  color: white;
  font-size: 18px;
}

.modal-input {
  width: 100%;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(255, 255, 255, 0.05);
  color: white;
  font-size: 15px;
  outline: none;
}

.modal-input:focus {
  border-color: var(--c-accent);
}

.modal-actions {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}

.modal-btn {
  flex: 1;
  padding: 12px;
  border-radius: 8px;
  border: none;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}

.modal-btn.cancel {
  background: rgba(255, 255, 255, 0.1);
  color: white;
}

.modal-btn.confirm {
  background: var(--c-accent);
  color: white;
}

/* Animations */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(100%);
}

/* Desktop scale animation */
.scale-enter-active,
.scale-leave-active {
  transition: transform 0.15s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.15s ease;
  transform-origin: top left;
}

.scale-enter-from,
.scale-leave-to {
  transform: scale(0.9);
  opacity: 0;
}
</style>
