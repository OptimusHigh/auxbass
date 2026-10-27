/**
 * Universal Context Menu Manager
 * Единый обработчик контекстного меню для ВСЕХ типов элементов
 * 
 * Типы: 'track', 'playlist', 'album', 'artist', 'user'
 * 
 * Использование:
 *   const { openMenu } = useContextMenu()
 *   @contextmenu.prevent="openMenu('track', track)"
 *   @contextmenu.prevent="openMenu('playlist', playlist)"
 */
import { ref, inject, markRaw } from 'vue'
import { useRouter } from 'vue-router'
import { usePlayerStore } from '@/stores/player'
import { useLibraryStore } from '@/stores/library'
import { useAuthStore } from '@/stores/auth'
import { useUIStore } from '@/stores/ui'
import { useModals } from '@/composables/useModals'
import { playerApi, playlistsApi, albumsApi, tracksApi } from '@/api/client'
import { getAllTrackArtists } from '@/utils/formatters'
import { useShare } from '@/composables/useShare'
import { suppressNextClick, suppressNextContextMenu } from '@/utils/touch'

// Singleton state - shared across all components
const isOpen = ref(false)
const menuType = ref(null)        // 'track' | 'playlist' | 'album' | 'artist' | 'user'
const menuData = ref(null)        // The actual item data
const menuContext = ref(null)     // Additional context (e.g., 'player', 'sidebar', 'library')
const menuPosition = ref({ x: 0, y: 0 })  // Mouse position for desktop context menu

// Modal states
const showPlaylistPicker = ref(false)
const showEditModal = ref(false)
const showEditPlaylistModal = ref(false)
const showCreatePlaylist = ref(false)
const editingItem = ref(null)
const editingPlaylist = ref(null)
const newPlaylistName = ref('')

/**
 * Universal Context Menu composable
 */
export function useContextMenu() {
  const router = useRouter()
  const playerStore = usePlayerStore()
  const libraryStore = useLibraryStore()
  const uiStore = useUIStore()
  const { closeFullPlayer } = useModals()
  const { openShare } = useShare()
  const telegram = inject('telegram', null)

  // ═══════════════════════════════════════════════════════════
  // MENU CONTROL
  // ═══════════════════════════════════════════════════════════

  const haptic = (type = 'light') => {
    telegram?.HapticFeedback?.impactOccurred?.(type)
  }

  /**
   * Open context menu for any element type
   * @param {string} type - 'track' | 'playlist' | 'album' | 'artist' | 'user'
   * @param {Object} data - The item data
   * @param {string} context - Optional context ('player', 'sidebar', 'library', etc.)
   * @param {Event} event - Mouse or touch event
   */
  const openMenu = (type, data, context = null, event = null) => {
    // If context was passed as an Event object (e.g. openMenu('track', track, $event))
    if (context instanceof Event || (context && typeof context === 'object' && ('clientX' in context || 'touches' in context))) {
      event = context
      context = null
    }

    // Defensive unwrap: if data is a wrapped event payload from VirtualTrackList or VirtualGrid
    if (data && typeof data === 'object') {
      if (data.track && !data.id) {
        if (!event && data.event) event = data.event
        if (!context && data.context) context = data.context
        data = data.track
      } else if (data.item && !data.id && !data.name && !data.title) {
        if (!event && data.event) event = data.event
        data = data.item
      }
    }

    haptic('light')
    menuType.value = type
    menuData.value = data
    menuContext.value = context
    
    // Capture mouse or touch position
    const clientX = event?.clientX ?? event?.touches?.[0]?.clientX ?? event?.changedTouches?.[0]?.clientX
    const clientY = event?.clientY ?? event?.touches?.[0]?.clientY ?? event?.changedTouches?.[0]?.clientY
    if (clientX !== undefined && clientY !== undefined) {
      menuPosition.value = { x: clientX, y: clientY }
    } else {
      menuPosition.value = { x: 0, y: 0 }
    }
    
    // Suppress trailing synthetic click and native contextmenu if opened by a touch interaction
    const isTouchInteraction = Boolean(
      event?.touches?.length || 
      event?.changedTouches?.length || 
      (event?.pointerType && event.pointerType !== 'mouse') ||
      (event?.type === 'contextmenu' && event.button === 0) ||
      (!event && ('ontouchstart' in window))
    )

    if (isTouchInteraction) {
      suppressNextClick()
      suppressNextContextMenu()
    }

    isOpen.value = true
  }

  const closeMenu = () => {
    isOpen.value = false
    // Delay clearing data for exit animation
    setTimeout(() => {
      menuType.value = null
      menuData.value = null
      menuContext.value = null
    }, 200)
  }

  // ═══════════════════════════════════════════════════════════
  // TRACK ACTIONS
  // ═══════════════════════════════════════════════════════════

  const trackActions = {
    goToArtist: (track) => {
      closeMenu()
      closeFullPlayer() // Close full player if open
      // Get all artists including from filename for tracks without metadata
      const artists = getAllTrackArtists(track?.artist, track?.title, track?.file_name)
      if (artists.length > 0) {
        const artistName = artists[0]
        router.push(`/artist/${encodeURIComponent(artistName)}`)
      }
    },

    // Go to specific artist by name (for multi-artist selection)
    goToArtistByName: (track, artistName) => {
      closeMenu()
      closeFullPlayer()
      if (artistName) {
        router.push(`/artist/${encodeURIComponent(artistName)}`)
      }
    },

    goToAlbum: async (track) => {
      closeMenu()
      closeFullPlayer() // Close full player if open
      const albumId = track?.album_id || track?.album?.id
      if (albumId) {
        router.push(`/album/${albumId}`)
        return
      }

      const albumName = track?.album?.name || track?.album_name || (typeof track?.album === 'string' ? track.album : null)
      if (track?.id || albumName) {
        try {
          const res = await albumsApi.resolve({
            track_id: track?.id,
            album_name: albumName,
            artist: track?.artist
          })
          if (res?.data?.album_id) {
            router.push(`/album/${res.data.album_id}`)
          } else {
            uiStore.toast.info('Альбом', 'Альбом не найден')
          }
        } catch (err) {
          console.error('Failed to resolve album:', err)
          uiStore.toast.error('Ошибка', 'Не удалось открыть альбом')
        }
      }
    },

    playNext: (track) => {
      if (track) {
        playerStore.playNext(track)
        uiStore.toast.success('Добавлено', 'Трек будет воспроизведён следующим')
      }
      closeMenu()
    },

    addToQueue: (track) => {
      if (track) {
        playerStore.addToQueue(track)
        uiStore.toast.success('Добавлено', 'Трек добавлен в очередь')
      }
      closeMenu()
    },

    addToPlaylist: (track) => {
      closeMenu()
      const authStore = useAuthStore()
      if (!authStore.requireChannel('добавления в плейлист')) {
        return
      }
      editingItem.value = track
      showPlaylistPicker.value = true
    },

    edit: (track) => {
      closeMenu()
      editingItem.value = track
      showEditModal.value = true
    },

    download: (track) => {
      closeMenu()
      if (!track?.id) return
      openShare({
        type: 'track',
        id: track.id,
        title: track.title || track.file_name || 'Трек',
        subtitle: track.artist || 'Неизвестен',
        coverUrl: track.cover_url || '',
      })
    },

    downloadHD: async () => {
      // HD version is available only for current playing track
      const hdInfo = playerStore.hdTrackInfo
      if (hdInfo?.id) {
        try {
          await playerApi.download(hdInfo.id)
          uiStore.toast.success('HD отправлено', 'HD версия отправлена в Telegram')
        } catch (error) {
          console.error('Failed to download HD track:', error)
          uiStore.toast.error('Ошибка', 'Не удалось отправить HD версию')
        }
      }
      closeMenu()
    },

    // Download the track itself as HD (for HD tracks in context menu)
    downloadTrackHD: async (track) => {
      if (track?.id) {
        try {
          await playerApi.download(track.id)
          uiStore.toast.success('HD отправлено', 'HD файл отправлен в Telegram')
        } catch (error) {
          console.error('Failed to download HD track:', error)
          uiStore.toast.error('Ошибка', 'Не удалось отправить HD файл')
        }
      }
      closeMenu()
    },

    delete: async (track) => {
      if (!track?.id) return closeMenu()
      
      if (confirm('Удалить трек полностью?')) {
        try {
          await libraryStore.deleteTrack(track.id)
          uiStore.toast.success('Удалено', 'Трек удалён')
        } catch (error) {
          console.error('Failed to delete track:', error)
          uiStore.toast.error('Ошибка', 'Не удалось удалить трек')
        }
      }
      closeMenu()
    },

    removeFromLibrary: async (track) => {
      if (!track?.id) return closeMenu()
      
      try {
        await libraryStore.removeFromLibrary(track.id)
        uiStore.toast.success('Удалено', 'Трек убран из библиотеки')
      } catch (error) {
        console.error('Failed to remove from library:', error)
        uiStore.toast.error('Ошибка', 'Не удалось убрать трек')
      }
      closeMenu()
    },

    addToLibrary: async (track) => {
      if (!track?.id) return closeMenu()
      const authStore = useAuthStore()
      if (!authStore.requireChannel('добавления в медиатеку')) {
        closeMenu()
        return
      }
      
      try {
        const success = await libraryStore.addToLibrary(track.id)
        if (success) {
          uiStore.toast.success('Добавлено', 'Трек добавлен в библиотеку')
        }
      } catch (error) {
        console.error('Failed to add to library:', error)
        uiStore.toast.error('Ошибка', 'Не удалось добавить трек')
      }
      closeMenu()
    },

    removeFromPlaylist: async (track, playlistId) => {
      if (!track?.id || !playlistId) return closeMenu()
      
      try {
        await libraryStore.removeTrackFromPlaylist(playlistId, track.id)
        uiStore.toast.success('Удалено', 'Трек убран из плейлиста')
      } catch (error) {
        console.error('Failed to remove from playlist:', error)
        uiStore.toast.error('Ошибка', 'Не удалось убрать трек')
      }
      closeMenu()
    },

    toggleDislike: async (track) => {
      if (!track?.id) return closeMenu()
      try {
        const isDisliked = await libraryStore.toggleDislike(track.id, track.is_disliked)
        if (isDisliked) {
          uiStore.toast.success('Не нравится', 'Трек исключён из автовоспроизведения')
        } else {
          uiStore.toast.success('Дизлайк снят', 'Трек возвращён в очередь')
        }
      } catch (error) {
        console.error('Failed to toggle dislike:', error)
        uiStore.toast.error('Ошибка', 'Не удалось изменить статус трека')
      }
      closeMenu()
    },

    share: (track) => {
      closeMenu()
      if (!track?.id) return
      openShare({
        type: 'track',
        id: track.id,
        title: track.title || track.file_name || 'Трек',
        subtitle: track.artist || 'Неизвестен',
        coverUrl: track.cover_url || '',
      })
    },
  }

  // ═══════════════════════════════════════════════════════════
  // PLAYLIST ACTIONS
  // ═══════════════════════════════════════════════════════════

  const playlistActions = {
    open: (playlist) => {
      closeMenu()
      if (!playlist?.id) return
      router.push(`/playlist/${playlist.id}`)
    },

    playAll: async (playlist) => {
      if (!playlist?.id) return closeMenu()
      
      try {
        const tracks = await loadPlaylistTracks(playlist)
        if (tracks?.length) {
          playerStore.recordPlaylistPlayed(playlist.id)
          playerStore.play(tracks[0], tracks, { type: 'playlist', id: playlist.id, name: playlist.name })
          uiStore.toast.success('Воспроизведение', `Играет: ${playlist.name}`)
        } else {
          uiStore.toast.info('Пусто', 'В плейлисте нет треков')
        }
      } catch (error) {
        console.error('Failed to play playlist:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести')
      }
      closeMenu()
    },

    shuffle: async (playlist) => {
      if (!playlist?.id) return closeMenu()
      
      try {
        playerStore.recordPlaylistPlayed(playlist.id)
        await playerStore.playShuffleAll('playlist', playlist.id)
        uiStore.toast.success('Перемешивание', `Играет: ${playlist.name}`)
      } catch (error) {
        console.error('Failed to shuffle playlist:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести')
      }
      closeMenu()
    },

    togglePin: (playlist) => {
      closeMenu()
      if (!playlist?.id) return
      const isPinned = uiStore.togglePinPlaylist(playlist.id)
      if (isPinned) {
        uiStore.toast.success('Закреплено', `Плейлист «${playlist.name}» закреплен в сайдбаре`)
      } else {
        uiStore.toast.info('Откреплено', `Плейлист «${playlist.name}» откреплен от сайдбара`)
      }
    },

    addToQueue: async (playlist) => {
      if (!playlist?.id) return closeMenu()
      
      try {
        const tracks = await loadPlaylistTracks(playlist)
        if (tracks?.length) {
          tracks.forEach(track => playerStore.addToQueue(track))
          uiStore.toast.success('Добавлено', `${tracks.length} треков в очереди`)
        }
      } catch (error) {
        console.error('Failed to add to queue:', error)
        uiStore.toast.error('Ошибка', 'Не удалось добавить в очередь')
      }
      closeMenu()
    },

    edit: async (playlist) => {
      closeMenu()
      if (!playlist?.id) return
      try {
        if (!playlist.tracks || playlist.tracks.length === 0) {
          const res = await playlistsApi.getOne(playlist.id)
          editingPlaylist.value = res.data
        } else {
          editingPlaylist.value = { ...playlist }
        }
      } catch (err) {
        console.error('Failed to load playlist for edit:', err)
        editingPlaylist.value = { ...playlist, tracks: [] }
      }
      showEditPlaylistModal.value = true
    },

    delete: async (playlist) => {
      if (!playlist?.id) return closeMenu()
      
      if (confirm(`Удалить плейлист "${playlist.name}"?`)) {
        try {
          await libraryStore.deletePlaylist(playlist.id)
          uiStore.toast.success('Удалено', `Плейлист "${playlist.name}" удалён`)
        } catch (error) {
          console.error('Failed to delete playlist:', error)
          uiStore.toast.error('Ошибка', 'Не удалось удалить плейлист')
        }
      }
      closeMenu()
    },

    share: (playlist) => {
      closeMenu()
      if (!playlist?.id) return
      const trackCount = playlist.tracks_count ?? playlist.tracks?.length ?? 0
      openShare({
        type: 'playlist',
        id: playlist.id,
        title: playlist.name,
        subtitle: trackCount ? `${trackCount} треков` : 'Плейлист',
        coverUrl: playlist.custom_cover_url || '',
      })
    },

    toggleSubscription: async (playlist) => {
      if (!playlist?.id) return closeMenu()
      closeMenu()
      try {
        if (playlist.is_subscribed) {
          await libraryStore.unsubscribePlaylist(playlist.id)
          playlist.is_subscribed = false
          uiStore.toast.success('Удалено', 'Плейлист убран из медиатеки')
        } else {
          await libraryStore.subscribePlaylist(playlist.id)
          playlist.is_subscribed = true
          uiStore.toast.success('Добавлено', 'Плейлист добавлен в медиатеку')
        }
      } catch (error) {
        const msg = error.response?.data?.detail || 'Не удалось обновить подписку'
        uiStore.toast.error('Ошибка', msg)
      }
    },
  }

  // ═══════════════════════════════════════════════════════════
  // ALBUM ACTIONS
  // ═══════════════════════════════════════════════════════════

  const albumActions = {
    open: (album) => {
      closeMenu()
      if (album?.id) {
        router.push(`/album/${album.id}`)
      }
    },

    playAll: async (album) => {
      if (!album?.id) return closeMenu()
      
      try {
        const response = await albumsApi.getOne(album.id)
        const tracks = response?.data?.tracks || []
        if (tracks.length) {
          playerStore.play(tracks[0], tracks)
          uiStore.toast.success('Воспроизведение', `Играет: ${album.name}`)
        } else {
          uiStore.toast.info('Пусто', 'В альбоме нет треков')
        }
      } catch (error) {
        console.error('Failed to play album:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести альбом')
      }
      closeMenu()
    },

    shuffle: async (album) => {
      if (!album?.id) return closeMenu()
      
      try {
        await playerStore.playShuffleAll('album', album.id)
        uiStore.toast.success('Перемешивание', `Играет: ${album.name}`)
      } catch (error) {
        console.error('Failed to shuffle album:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести альбом')
      }
      closeMenu()
    },

    addToQueue: async (album) => {
      if (!album?.id) return closeMenu()
      
      try {
        const response = await albumsApi.getOne(album.id)
        const tracks = response?.data?.tracks || []
        if (tracks.length) {
          tracks.forEach(track => playerStore.addToQueue(track))
          uiStore.toast.success('Добавлено', `${tracks.length} треков в очереди`)
        } else {
          uiStore.toast.info('Пусто', 'В альбоме нет треков')
        }
      } catch (error) {
        console.error('Failed to add album to queue:', error)
        uiStore.toast.error('Ошибка', 'Не удалось добавить в очередь')
      }
      closeMenu()
    },

    goToArtist: (album) => {
      closeMenu()
      const artist = album?.album_artist || album?.artist
      if (artist) {
        router.push(`/artist/${encodeURIComponent(artist)}`)
      }
    },

    share: (album) => {
      closeMenu()
      if (!album?.id) return
      openShare({
        type: 'album',
        id: album.id,
        title: album.name,
        subtitle: album.artist || 'Альбом',
        coverUrl: album.cover_url || '',
      })
    },
  }

  // ═══════════════════════════════════════════════════════════
  // ARTIST ACTIONS
  // ═══════════════════════════════════════════════════════════

  const artistActions = {
    open: (artist) => {
      closeMenu()
      const name = typeof artist === 'string' ? artist : artist?.name
      if (name) {
        router.push(`/artist/${encodeURIComponent(name)}`)
      }
    },

    playAll: async (artist) => {
      const name = typeof artist === 'string' ? artist : artist?.name
      if (!name) return closeMenu()
      
      try {
        const response = await tracksApi.getArtistDetail(name)
        const tracks = response?.tracks || []
        if (tracks.length) {
          playerStore.play(tracks[0], tracks)
          uiStore.toast.success('Воспроизведение', `Играет: ${name}`)
        }
      } catch (error) {
        console.error('Failed to play artist:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести')
      }
      closeMenu()
    },

    shuffle: async (artist) => {
      const name = typeof artist === 'string' ? artist : artist?.name
      if (!name) return closeMenu()
      
      try {
        await playerStore.playShuffleAll('artist', null, name)
        uiStore.toast.success('Перемешивание', `Играет: ${name}`)
      } catch (error) {
        console.error('Failed to shuffle artist:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести')
      }
      closeMenu()
    },

    addToQueue: async (artist) => {
      const name = typeof artist === 'string' ? artist : artist?.name
      if (!name) return closeMenu()
      
      try {
        const response = await tracksApi.getArtistDetail(name)
        const tracks = response?.tracks || []
        if (tracks.length) {
          tracks.forEach(track => playerStore.addToQueue(track))
          uiStore.toast.success('Добавлено', `${tracks.length} треков в очереди`)
        }
      } catch (error) {
        console.error('Failed to add artist to queue:', error)
        uiStore.toast.error('Ошибка', 'Не удалось добавить в очередь')
      }
      closeMenu()
    },
  }

  // ═══════════════════════════════════════════════════════════
  // LIKED / FAVORITES ACTIONS
  // ═══════════════════════════════════════════════════════════

  const likedActions = {
    open: () => {
      closeMenu()
      router.push('/liked')
    },

    playAll: async () => {
      closeMenu()
      try {
        let tracks = libraryStore.likedTracks
        if (!tracks?.length) {
          tracks = await libraryStore.fetchLikedTracks()
        }
        if (tracks?.length) {
          playerStore.playTrack(tracks[0], tracks, 0)
          uiStore.toast.success('Воспроизведение', 'Включены любимые треки')
        } else {
          uiStore.toast.info('Любимое', 'У вас пока нет сохранённых треков')
        }
      } catch (error) {
        console.error('Failed to play liked tracks:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести')
      }
    },

    shuffle: async () => {
      closeMenu()
      try {
        await playerStore.playShuffleAll('library')
        uiStore.toast.success('Перемешивание', 'Любимые треки перемешаны')
      } catch (error) {
        console.error('Failed to shuffle liked tracks:', error)
        uiStore.toast.error('Ошибка', 'Не удалось воспроизвести')
      }
    },

    addToQueue: async () => {
      closeMenu()
      try {
        let tracks = libraryStore.likedTracks
        if (!tracks?.length) {
          tracks = await libraryStore.fetchLikedTracks()
        }
        if (tracks?.length) {
          tracks.forEach(track => playerStore.addToQueue(track))
          uiStore.toast.success('Добавлено', `${tracks.length} треков в очереди`)
        } else {
          uiStore.toast.info('Пусто', 'В любимых пока нет треков')
        }
      } catch (error) {
        console.error('Failed to add liked tracks to queue:', error)
        uiStore.toast.error('Ошибка', 'Не удалось добавить в очередь')
      }
    },
  }

  // ═══════════════════════════════════════════════════════════
  // MODAL HANDLERS
  // ═══════════════════════════════════════════════════════════

  const closePlaylistPicker = () => {
    showPlaylistPicker.value = false
    editingItem.value = null
  }

  const onPlaylistAdded = (playlist) => {
    closePlaylistPicker()
    uiStore.toast.success('Добавлено', `Трек добавлен в "${playlist.name}"`)
  }

  const openCreatePlaylist = () => {
    const authStore = useAuthStore()
    if (!authStore.requireChannel('создания плейлиста')) {
      showPlaylistPicker.value = false
      return
    }
    showPlaylistPicker.value = false
    newPlaylistName.value = ''
    showCreatePlaylist.value = true
  }

  const closeCreatePlaylist = () => {
    showCreatePlaylist.value = false
    newPlaylistName.value = ''
  }

  const confirmCreatePlaylist = async () => {
    const name = newPlaylistName.value.trim()
    if (!name) return
    
    try {
      const playlist = await libraryStore.createPlaylist(name)
      if (playlist && editingItem.value?.id) {
        // Add the track to the newly created playlist
        await libraryStore.addTrackToPlaylist(playlist.id, editingItem.value.id)
        uiStore.toast.success('Создано', `Плейлист "${name}" создан и трек добавлен`)
      } else if (playlist) {
        uiStore.toast.success('Создано', `Плейлист "${name}" создан`)
      }
      closeCreatePlaylist()
      editingItem.value = null
    } catch (error) {
      console.error('Failed to create playlist:', error)
      uiStore.toast.error('Ошибка', 'Не удалось создать плейлист')
    }
  }

  const closeEditModal = () => {
    showEditModal.value = false
    editingItem.value = null
  }

  const onTrackSaved = () => {
    closeEditModal()
    uiStore.toast.success('Сохранено', 'Трек обновлён')
  }

  const closeEditPlaylistModal = () => {
    showEditPlaylistModal.value = false
    editingPlaylist.value = null
  }

  const handleSavePlaylist = async ({ name, isPublic, covers, tracks: savedTracks }) => {
    if (editingPlaylist.value) {
      if (!editingPlaylist.value.is_system) {
        editingPlaylist.value.name = name
      }
      editingPlaylist.value.is_public = isPublic
      if (savedTracks) {
        editingPlaylist.value.tracks = savedTracks
        editingPlaylist.value.track_count = savedTracks.length
      }
      if (covers?.length) {
        editingPlaylist.value.covers = covers
      }
    }
    showEditPlaylistModal.value = false
    uiStore.toast.success('Сохранено', 'Плейлист обновлён')
    if (editingPlaylist.value?.id) {
      await libraryStore.notifyPlaylistChange(editingPlaylist.value.id)
    }
    editingPlaylist.value = null
  }

  const handleDeletePlaylist = async () => {
    const pl = editingPlaylist.value
    if (!pl?.id) return
    try {
      await libraryStore.deletePlaylist(pl.id)
      uiStore.toast.success('Удалено', `Плейлист "${pl.name}" удалён`)
      showEditPlaylistModal.value = false
      editingPlaylist.value = null
    } catch (error) {
      console.error('Failed to delete playlist:', error)
      uiStore.toast.error('Ошибка', 'Не удалось удалить плейлист')
    }
  }

  const handleTracksUpdate = (tracks) => {
    if (editingPlaylist.value) {
      editingPlaylist.value.tracks = tracks
      editingPlaylist.value.track_count = tracks.length
    }
  }

  // ═══════════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════════

  const loadPlaylistTracks = async (playlist) => {
    if (!playlist?.id) return []
    
    try {
      const response = await playlistsApi.getOne(playlist.id)
      return response?.data?.tracks || []
    } catch (error) {
      console.error('Failed to load playlist tracks:', error)
      return []
    }
  }

  // ═══════════════════════════════════════════════════════════
  // UNIFIED ACTION DISPATCHER
  // ═══════════════════════════════════════════════════════════

  /**
   * Execute action based on current menu type
   * @param {string} action - Action name
   * @param {any} extra - Extra data for the action
   */
  const executeAction = (action, extra = null) => {
    const data = menuData.value
    const type = menuType.value

    switch (type) {
      case 'track':
        if (trackActions[action]) {
          trackActions[action](data, extra)
        }
        break
      case 'playlist':
        if (playlistActions[action]) {
          playlistActions[action](data, extra)
        }
        break
      case 'album':
        if (albumActions[action]) {
          albumActions[action](data, extra)
        }
        break
      case 'artist':
        if (artistActions[action]) {
          artistActions[action](data, extra)
        }
        break
      case 'liked':
        if (likedActions[action]) {
          likedActions[action](data, extra)
        }
        break
      default:
        console.warn(`Unknown menu type: ${type}`)
        closeMenu()
    }
  }

  return {
    // Menu state (readonly outside)
    isOpen,
    menuType,
    menuData,
    menuContext,
    menuPosition,

    // Menu control
    openMenu,
    closeMenu,
    executeAction,

    // Direct action access (for ContextMenu component)
    trackActions,
    playlistActions,
    albumActions,
    artistActions,
    likedActions,

    // Modal states
    showPlaylistPicker,
    showEditModal,
    showEditPlaylistModal,
    showCreatePlaylist,
    editingItem,
    editingPlaylist,
    newPlaylistName,

    // Modal handlers
    closePlaylistPicker,
    onPlaylistAdded,
    openCreatePlaylist,
    closeCreatePlaylist,
    confirmCreatePlaylist,
    closeEditModal,
    onTrackSaved,
    closeEditPlaylistModal,
    handleSavePlaylist,
    handleDeletePlaylist,
    handleTracksUpdate,
  }
}
