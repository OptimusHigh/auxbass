import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { tracksApi, playlistsApi, playerApi, artistsApi } from '../api/client'
import apiCache from '../utils/apiCache'
import { getAllCachedTracks } from '../utils/audioCacheDb'
import { setCachedUrl, deleteCachedAudio, deleteCachedUrl } from './playerCache'

export const useLibraryStore = defineStore('library', () => {
  // LocalStorage keys for instant hydration (SWR) - scoped by user ID to prevent cross-account leakage
  const CACHE_KEY_PLAYLISTS = 'tg_player_cached_playlists'
  const CACHE_KEY_LIKED = 'tg_player_cached_liked'
  const CACHE_KEY_HISTORY = 'tg_player_cached_history'

  const getCacheKey = (baseKey) => {
    try {
      const raw = localStorage.getItem('tg_player_user')
      if (raw) {
        const u = JSON.parse(raw)
        if (u?.id) return `${baseKey}_${u.id}`
      }
    } catch (_) {}
    return baseKey
  }

  const loadFromStorage = (key, defaultVal) => {
    try {
      const realKey = getCacheKey(key)
      const raw = localStorage.getItem(realKey)
      return raw ? JSON.parse(raw) : defaultVal
    } catch {
      return defaultVal
    }
  }

  const saveToStorage = (key, val) => {
    try {
      const realKey = getCacheKey(key)
      localStorage.setItem(realKey, JSON.stringify(val))
    } catch (_) {}
  }

  const cleanCoverUrl = (url) => {
    if (!url || typeof url !== 'string') return null
    return url.replace(/[?&]_cb=\d+/, '').replace(/\?$/, '')
  }

  const sanitizePlaylists = (list) => {
    if (!Array.isArray(list)) return []
    return list.map((p) => {
      const covers = (p.covers || (p.cover_url ? [p.cover_url] : [])).map(cleanCoverUrl).filter(Boolean)
      const cover_url = cleanCoverUrl(p.cover_url) || covers[0] || null
      return {
        ...p,
        cover_url,
        covers
      }
    })
  }

  // State - My Library with instant local cache hydration
  const tracks = ref([])
  const playlists = ref(sanitizePlaylists(loadFromStorage(CACHE_KEY_PLAYLISTS, [])))
  const artists = ref([])
  const artistsTotal = ref(0)
  const globalArtists = ref([])  // All artists from global library
  const artistScope = ref('library')  // 'library' or 'global'
  const artistImages = ref({})  // In-memory cache for artist images
  const genres = ref([])
  const history = ref(loadFromStorage(CACHE_KEY_HISTORY, []))
  const likedTracks = ref(loadFromStorage(CACHE_KEY_LIKED, []))  // Liked tracks
  const loading = ref(false)
  const refreshing = ref(false)
  const total = ref(0)
  const page = ref(1)
  const hasMore = ref(true)
  const currentSearchParams = ref({})  // Store current search/filter params for loadMore
  
  // State - Global Library
  const globalTracks = ref([])
  const globalLoading = ref(false)
  const globalTotal = ref(0)
  const globalPage = ref(1)
  const globalHasMore = ref(true)
  const globalSearchParams = ref({})  // Store current search/filter params for global loadMore
  const recentUploads = ref([])
  const popularTracks = ref([])
  const globalStats = ref(null)
  const topUsers = ref([])
  const selectedUser = ref(null)
  const selectedUserTracks = ref([])

  // State - Real-time Library Sync
  const lastSyncTimestamp = ref(null)
  const lastKnownTrackId = ref(null)
  const lastKnownTotal = ref(null)
  const isSyncing = ref(false)
  let syncTimer = null
  let visibilityListenerAttached = false

  // Check library sync state (polls lightweight /sync-state endpoint)
  const checkSyncState = async (force = false) => {
    if (isSyncing.value) return
    isSyncing.value = true
    try {
      const params = {}
      if (lastSyncTimestamp.value && !force) {
        params.since = lastSyncTimestamp.value
      }

      const res = await tracksApi.getSyncState(params)
      const data = res.data
      if (!data) return

      const prevTrackId = lastKnownTrackId.value
      const prevTotal = lastKnownTotal.value

      lastSyncTimestamp.value = data.last_updated_at || new Date().toISOString()
      lastKnownTrackId.value = data.last_track_id
      lastKnownTotal.value = data.total_tracks

      // Update total tracks count in store if changed
      if (data.total_tracks !== undefined && total.value !== data.total_tracks) {
        total.value = data.total_tracks
      }

      // If stats are returned, update artists and total
      if (data.stats) {
        if (data.stats.artist_count !== undefined) {
          artistsTotal.value = data.stats.artist_count
        }
      }

      // Check if new tracks were added to user's library (e.g. via bot)
      const isInitialRun = prevTrackId === null
      const hasNewTracks = !isInitialRun && (
        (data.last_track_id && prevTrackId && data.last_track_id > prevTrackId) ||
        (data.total_tracks && prevTotal && data.total_tracks > prevTotal)
      )

      if (hasNewTracks) {
        console.log(`[LibrarySync] New tracks detected! (prev ID: ${prevTrackId} -> new ID: ${data.last_track_id})`)
        apiCache.invalidatePattern('/tracks')
        apiCache.invalidatePattern('/library')
        apiCache.invalidatePattern('/artists')
        apiCache.invalidatePattern('/albums')

        // Dispatch track:added:library event to trigger VirtualTrackList reset
        window.dispatchEvent(new CustomEvent('track:added:library', {
          detail: { trackId: data.last_track_id }
        }))

        // Refresh playlists and overview
        fetchPlaylists(true)

        // Show subtle notification toast
        try {
          const { useUIStore } = await import('./ui')
          const uiStore = useUIStore()
          uiStore.toast?.success('Медиатека обновлена', 'Добавлены новые треки')
        } catch (_) {}
      }

      // Check for updated/enriched tracks (title cleanups, cover avatar assignments, album updates)
      if (data.updated_tracks && data.updated_tracks.length > 0) {
        console.log(`[LibrarySync] Received ${data.updated_tracks.length} enriched/updated tracks`)
        for (const updatedTrack of data.updated_tracks) {
          await notifyTrackChange(updatedTrack.id, updatedTrack)
        }
      }
    } catch (err) {
      console.debug('[LibrarySync] Sync check error:', err?.message || err)
    } finally {
      isSyncing.value = false
    }
  }

  // Start periodic background polling for sync
  const startSyncPolling = () => {
    if (syncTimer) return
    checkSyncState()

    const getInterval = () => (typeof document !== 'undefined' && document.hidden ? 45000 : 12000)

    const scheduleNext = () => {
      const delay = getInterval()
      syncTimer = setTimeout(async () => {
        await checkSyncState()
        scheduleNext()
      }, delay)
    }

    scheduleNext()

    if (!visibilityListenerAttached && typeof document !== 'undefined') {
      const onVisibilityChange = () => {
        if (!document.hidden) {
          checkSyncState()
        }
      }
      document.addEventListener('visibilitychange', onVisibilityChange)
      visibilityListenerAttached = true
    }
  }

  const stopSyncPolling = () => {
    if (syncTimer) {
      clearTimeout(syncTimer)
      syncTimer = null
    }
  }

  // Lightweight init: only fetch primary user collections, non-blocking
  const init = async () => {
    startSyncPolling()
    return Promise.allSettled([
      fetchPlaylists(),
      fetchLikedTracks(),
      fetchHistory(20),
    ])
  }

  // Refresh active data (pull-to-refresh & force refresh)
  const refresh = async () => {
    refreshing.value = true
    try {
      apiCache.invalidatePattern('/tracks')
      apiCache.invalidatePattern('/library')
      apiCache.invalidatePattern('/artists')
      apiCache.invalidatePattern('/albums')
      await checkSyncState(true)
      await Promise.allSettled([
        fetchTracks({ refresh: true, bypassCache: true }),
        fetchPlaylists(true),
        fetchLikedTracks(true),
        fetchHistory(20),
        fetchRecentUploads(15),
      ])
      window.dispatchEvent(new CustomEvent('track:added:library'))
    } finally {
      refreshing.value = false
    }
  }

  // Fetch tracks
  const fetchTracks = async (params = {}) => {
    loading.value = true
    try {
      // Save search params for loadMore (exclude page)
      const { page: pageParam, ...searchParams } = params
      if (!pageParam || pageParam === 1) {
        currentSearchParams.value = searchParams
      }
      
      const isRefresh = Boolean(params.refresh || params.bypassCache)
      if (isRefresh) {
        apiCache.invalidatePattern('/tracks')
      }

      const response = await tracksApi.getAll({
        page: params.page || 1,
        per_page: 50,
        ...params,
      }, {
        bypassCache: isRefresh
      })
      
      const data = response.data
      
      if (params.page && params.page > 1) {
        tracks.value = [...tracks.value, ...data.items]
      } else {
        tracks.value = data.items
        
        // Aggressive prefetch: Get both file paths AND stream URLs for first 5 tracks
        // This ensures the first track can start almost instantly
        if (data.items.length > 0) {
          const firstTrackIds = data.items.slice(0, 5).map(t => t.id)
          
          // 1. Prefetch file paths on server (parallel Telegram API calls)
          playerApi.prefetch(firstTrackIds).catch(() => {})
          
          // 2. Pre-generate stream URL tokens for first 3 tracks
          // This saves ~200-400ms on first play
          playerApi.getBatchUrls(firstTrackIds.slice(0, 3))
            .then(response => {
              const urlData = response.data.urls || []
              for (const item of urlData) {
                if (item.url && !item.error) {
                  setCachedUrl(item.track_id, item.url, item.expires_at)
                }
              }
              console.log(`[Prefetch] Pre-generated ${urlData.filter(u => u.url).length} stream URLs`)
            })
            .catch(() => {})  // Fire and forget
        }
      }
      
      total.value = data.total
      page.value = data.page || params.page || 1
      hasMore.value = tracks.value.length < data.total
    } catch (error) {
      console.error('Failed to fetch tracks:', error)
      // If offline and tracks are empty, fallback to cached tracks from IndexedDB
      if (tracks.value.length === 0) {
        try {
          const cached = await getAllCachedTracks()
          if (cached.length > 0) {
            tracks.value = cached
            total.value = cached.length
            hasMore.value = false
            console.log(`[Library] Loaded ${cached.length} cached tracks in offline mode`)
          }
        } catch (_) {}
      }
    } finally {
      loading.value = false
    }
  }

  // Load more tracks (preserves current search/filter params)
  const loadMore = async () => {
    if (!hasMore.value || loading.value) return
    await fetchTracks({ ...currentSearchParams.value, page: page.value + 1 })
  }

  // Fetch playlists
  const fetchPlaylists = async (force = false) => {
    try {
      const response = await playlistsApi.getAll({}, { bypassCache: Boolean(force) })
      const raw = response.data?.items || response.data || []

      playlists.value = sanitizePlaylists(raw)
      saveToStorage(CACHE_KEY_PLAYLISTS, playlists.value.slice(0, 25))
    } catch (error) {
      console.error('Failed to fetch playlists:', error)
    }
  }

  // Fetch single playlist with tracks
  const fetchPlaylist = async (id, force = false) => {
    try {
      const response = await playlistsApi.getOne(id, {}, { bypassCache: Boolean(force) })
      return response.data
    } catch (error) {
      console.error('Failed to fetch playlist:', error)
      return null
    }
  }

  // Fetch artists (with scope: 'library' or 'global', paginated)
  const fetchArtists = async (params = {}) => {
    try {
      const { limit = 20, offset = 0, scope = artistScope.value, bypassCache = false, ...rest } = params
      artistScope.value = scope
      
      const requestParams = { offset, limit, ...rest }
      const options = bypassCache ? { bypassCache: true } : {}
      
      const response = scope === 'global'
        ? await artistsApi.getGlobal(requestParams, options)
        : await artistsApi.getAll(requestParams, options)
      
      const pageItems = response.data?.items || []
      const totalCount = response.data?.total ?? pageItems.length
      
      if (scope === 'global') {
        globalArtists.value = pageItems
      } else {
        artists.value = pageItems
      }
      artistsTotal.value = totalCount
      return response.data
    } catch (error) {
      console.error('Failed to fetch artists:', error)
      return { items: [], total: 0 }
    }
  }
  
  // Get current artists based on scope
  const currentArtists = computed(() => {
    return artistScope.value === 'global' ? globalArtists.value : artists.value
  })

  // Current artist detail for ArtistCard view
  const currentArtist = ref(null)
  const artistLoading = ref(false)

  // Fetch artist detail with tracks, albums and playlists
  const fetchArtistDetail = async (artistName, scope = 'library') => {
    artistLoading.value = true
    try {
      const response = await tracksApi.getArtistDetail(artistName, scope)
      currentArtist.value = response.data
      
      // Sync artist image to cache to avoid desync between main page and artist card
      if (response.data.image_url) {
        artistImages.value[artistName] = response.data.image_url
      }
      
      return response.data
    } catch (error) {
      console.error('Failed to fetch artist detail:', error)
      currentArtist.value = null
      return null
    } finally {
      artistLoading.value = false
    }
  }

  // Clear current artist
  const clearCurrentArtist = () => {
    currentArtist.value = null
  }

  // Set current artist (for navigation history restore)
  const setCurrentArtist = (artist) => {
    currentArtist.value = artist
  }

  // Get artist image (from in-memory cache or null)
  const getArtistImage = (artistName) => {
    return artistImages.value[artistName] || null
  }
  
  // Clear artist images cache
  const clearArtistImagesCache = () => {
    artistImages.value = {}
  }

  // Fetch genres
  const fetchGenres = async () => {
    try {
      const response = await tracksApi.getGenres()
      genres.value = response.data
    } catch (error) {
      console.error('Failed to fetch genres:', error)
    }
  }

  // Create playlist
  const createPlaylist = async (name, description = '', isPublic = false) => {
    const { useAuthStore } = await import('./auth')
    const authStore = useAuthStore()
    if (!authStore.requireChannel('создания плейлиста')) {
      return null
    }

    try {
      const response = await playlistsApi.create({ name, description, is_public: isPublic })
      await notifyPlaylistChange(response.data.id)
      return response.data
    } catch (error) {
      console.error('Failed to create playlist:', error)
      if (error.response?.status === 403) {
        authStore.promptChannelSetup('создания плейлиста')
      }
      return null
    }
  }

  // Update playlist
  const updatePlaylist = async (id, data) => {
    try {
      const response = await playlistsApi.update(id, data)
      await notifyPlaylistChange(id)
      return response.data
    } catch (error) {
      console.error('Failed to update playlist:', error)
      return null
    }
  }

  // Delete playlist
  const deletePlaylist = async (id) => {
    try {
      await playlistsApi.delete(id)
      await notifyPlaylistChange(id)
    } catch (error) {
      console.error('Failed to delete playlist:', error)
    }
  }

  // ============== Unified Change Notifications ==============
  
  /**
   * Notify the entire app that playlist data has changed.
   * Call this after ANY playlist mutation (create, delete, update, add/remove track, reorder, subscribe).
   * 
   * 1. Invalidates API cache for playlists
   * 2. Refetches playlists in store (sidebar, PlaylistPicker auto-update via reactive ref)
   * 3. Dispatches event for VirtualGrid and other non-Pinia listeners
   */
  const notifyPlaylistChange = async (playlistId = null) => {
    apiCache.invalidateRelated('playlist', playlistId)
    apiCache.invalidatePattern('/playlists')
    apiCache.invalidatePattern('/minecraft/quota')
    if (playlistId) {
      apiCache.delete(`/playlists/${playlistId}`)
      apiCache.invalidatePattern(`/playlists/${playlistId}`)
    }
    await fetchPlaylists(true)
    window.dispatchEvent(new CustomEvent('playlist:changed', {
      detail: { playlistId }
    }))
  }

  // Subscribe to public playlist
  const subscribePlaylist = async (id) => {
    try {
      const res = await playlistsApi.subscribe(id)
      await notifyPlaylistChange(id)
      return res.data
    } catch (error) {
      console.error('Failed to subscribe to playlist:', error)
      throw error
    }
  }

  // Unsubscribe from public playlist
  const unsubscribePlaylist = async (id) => {
    try {
      const res = await playlistsApi.unsubscribe(id)
      await notifyPlaylistChange(id)
      return res.data
    } catch (error) {
      console.error('Failed to unsubscribe from playlist:', error)
      throw error
    }
  }

  /**
   * Notify the entire app that track data has changed.
   * Call this after ANY track mutation (edit title/artist/album/genre).
   *
   * 1. Invalidates API cache for tracks
   * 2. Patches track in-place in all library lists (tracks, globalTracks, likedTracks, etc.)
   * 3. Patches playerStore.currentTrack and queue entries if they match
   * 4. Dispatches event for VirtualTrackList and other non-Pinia listeners
   */
  const notifyTrackChange = async (trackId, updatedData) => {
    // 1. Invalidate API cache
    apiCache.invalidateRelated('track', trackId)

    // 2. Patch track in all local lists
    const patchInList = (list) => {
      const idx = list.findIndex(t => t.id === trackId)
      if (idx !== -1) {
        Object.assign(list[idx], updatedData)
      }
    }
    patchInList(tracks.value)
    patchInList(globalTracks.value)
    patchInList(recentUploads.value)
    patchInList(popularTracks.value)
    patchInList(likedTracks.value)
    patchInList(selectedUserTracks.value)
    patchInList(history.value)

    // Patch in currentArtist tracks and album tracks
    if (currentArtist.value) {
      if (currentArtist.value.tracks) {
        patchInList(currentArtist.value.tracks)
      }
      if (currentArtist.value.albums) {
        for (const album of currentArtist.value.albums) {
          if (album.tracks) {
            patchInList(album.tracks)
          }
        }
      }
    }

    // 3. Patch in player store (currentTrack + queue + mediaSession) — dynamic import to avoid circular deps
    const { usePlayerStore } = await import('./player')
    const playerStore = usePlayerStore()
    playerStore.patchTrack(trackId, updatedData)

    // 4. Dispatch event for VirtualTrackList and other listeners
    window.dispatchEvent(new CustomEvent('track:changed', {
      detail: { trackId, data: updatedData }
    }))
  }

  /**
   * Notify the entire app that a track has been removed.
   * Removes track from all store arrays, player queue, and dispatches window event.
   */
  const notifyTrackRemoved = async (trackId) => {
    // 1. Evict persistent audio blob and cached URL
    deleteCachedAudio(trackId).catch(() => {})
    deleteCachedUrl(trackId)

    // 2. Invalidate API cache (including search and listings)
    apiCache.invalidateRelated('trackRemoved', trackId)
    apiCache.invalidatePattern('/tracks')
    apiCache.invalidatePattern('/library')
    apiCache.invalidatePattern('/search')
    apiCache.invalidatePattern('/artists')
    apiCache.invalidatePattern('/albums')

    // 3. Remove from all local lists
    const removeFromList = (list) => {
      const idx = list.findIndex(t => t.id === trackId)
      if (idx !== -1) list.splice(idx, 1)
    }
    removeFromList(tracks.value)
    removeFromList(globalTracks.value)
    removeFromList(recentUploads.value)
    removeFromList(popularTracks.value)
    removeFromList(likedTracks.value)
    removeFromList(selectedUserTracks.value)
    removeFromList(history.value)

    // Remove from currentArtist tracks and album tracks
    if (currentArtist.value) {
      if (currentArtist.value.tracks) {
        removeFromList(currentArtist.value.tracks)
      }
      if (currentArtist.value.albums) {
        for (const album of currentArtist.value.albums) {
          if (album.tracks) {
            removeFromList(album.tracks)
          }
        }
      }
    }

    // 3. Remove from player queue and handle currently playing track safely
    const { usePlayerStore } = await import('./player')
    const playerStore = usePlayerStore()
    if (typeof playerStore.handleTrackRemoved === 'function') {
      await playerStore.handleTrackRemoved(trackId)
    } else if (typeof playerStore.removeTrackFromQueue === 'function') {
      playerStore.removeTrackFromQueue(trackId)
    }

    // 4. Dispatch event for View components and other listeners
    window.dispatchEvent(new CustomEvent('track:removed', {
      detail: { trackId }
    }))
    window.dispatchEvent(new CustomEvent('track:removed:library', {
      detail: { trackId }
    }))
  }

  // Add track to playlist
  const addTrackToPlaylist = async (playlistId, trackId) => {
    const { useAuthStore } = await import('./auth')
    const authStore = useAuthStore()
    if (!authStore.requireChannel('добавления трека в плейлист')) {
      return false
    }

    try {
      await playlistsApi.addTrack(playlistId, trackId)
      await notifyPlaylistChange(playlistId)
      return true
    } catch (error) {
      console.error('Failed to add track to playlist:', error)
      if (error.response?.status === 403) {
        authStore.promptChannelSetup('добавления трека в плейлист')
      }
      return false
    }
  }

  // Remove track from playlist
  const removeTrackFromPlaylist = async (playlistId, trackId) => {
    try {
      await playlistsApi.removeTrack(playlistId, trackId)
      await notifyPlaylistChange(playlistId)
      return true
    } catch (error) {
      console.error('Failed to remove track from playlist:', error)
      return false
    }
  }

  // Update track
  const updateTrack = async (id, data) => {
    try {
      const response = await tracksApi.update(id, data)
      // Notify entire app about the track change (non-blocking — modal must close even if this fails)
      try {
        await notifyTrackChange(id, response.data)
      } catch (e) {
        console.error('notifyTrackChange failed:', e)
      }
      // Refresh artists list if artist was changed
      if (data.artist !== undefined) {
        fetchArtists(artistScope.value)
      }
      return response.data
    } catch (error) {
      console.error('Failed to update track:', error)
      throw error
    }
  }

  // Set track cover from URL (auto-match)
  const setTrackCover = async (id, coverUrl) => {
    try {
      const response = await tracksApi.setCover(id, coverUrl)
      try {
        await notifyTrackChange(id, response.data)
      } catch (e) {
        console.error('notifyTrackChange failed:', e)
      }
      return response.data
    } catch (error) {
      console.error('Failed to set track cover:', error)
      throw error
    }
  }

  // Upload custom track cover file
  const uploadTrackCover = async (id, file) => {
    try {
      const response = await tracksApi.uploadCover(id, file)
      try {
        await notifyTrackChange(id, response.data)
      } catch (e) {
        console.error('notifyTrackChange failed:', e)
      }
      return response.data
    } catch (error) {
      console.error('Failed to upload track cover:', error)
      throw error
    }
  }

  // Delete track cover
  const deleteTrackCover = async (id) => {
    try {
      const response = await tracksApi.deleteCover(id)
      try {
        await notifyTrackChange(id, response.data)
      } catch (e) {
        console.error('notifyTrackChange failed:', e)
      }
      return response.data
    } catch (error) {
      console.error('Failed to delete track cover:', error)
      throw error
    }
  }

  // Delete track
  const deleteTrack = async (id) => {
    try {
      await tracksApi.delete(id)
      await notifyTrackRemoved(id)
      total.value = Math.max(0, (total.value || 0) - 1)
      // Refresh artists list as this track's artist may no longer have tracks
      fetchArtists(artistScope.value)
    } catch (error) {
      console.error('Failed to delete track:', error)
    }
  }

  // Fetch listening history
  const fetchHistory = async (limit = 30) => {
    try {
      const response = await tracksApi.getHistory(limit)
      history.value = response.data?.items || (Array.isArray(response.data) ? response.data : [])
      saveToStorage(CACHE_KEY_HISTORY, history.value.slice(0, 25))
      return history.value
    } catch (error) {
      console.error('Failed to fetch history:', error)
      history.value = []
      return []
    }
  }

  // Fetch tracks by genre (for shuffle play)
  const fetchTracksByGenre = async (genre) => {
    try {
      const response = await tracksApi.getAll({ genre, per_page: 100 })
      return response.data.items || []
    } catch (error) {
      console.error('Failed to fetch tracks by genre:', error)
      return []
    }
  }

  // Fetch liked tracks
  const fetchLikedTracks = async (force = false) => {
    try {
      const response = await tracksApi.getLiked({ bypassCache: Boolean(force) })
      // API returns { items: [...], total: N }
      likedTracks.value = response.data?.items || (Array.isArray(response.data) ? response.data : [])
      saveToStorage(CACHE_KEY_LIKED, likedTracks.value.slice(0, 50))
      return likedTracks.value
    } catch (error) {
      console.error('Failed to fetch liked tracks:', error)
      likedTracks.value = []
      return []
    }
  }

  // Helper to find track across all local store collections
  const findTrackInStore = (trackId) => {
    if (!trackId) return null
    const searchIn = (list) => (Array.isArray(list) ? list.find(t => t?.id === trackId) : null)
    return (
      searchIn(tracks.value) ||
      searchIn(likedTracks.value) ||
      searchIn(globalTracks.value) ||
      searchIn(recentUploads.value) ||
      searchIn(popularTracks.value) ||
      searchIn(selectedUserTracks.value) ||
      searchIn(history.value) ||
      null
    )
  }

  // Check if track is liked
  const isTrackLiked = (trackId) => {
    if (!trackId) return false
    // 1. Check likedTracks array (source of truth)
    if (Array.isArray(likedTracks.value) && likedTracks.value.some(t => t?.id === trackId)) return true
    
    // 2. Check local store collections
    const track = findTrackInStore(trackId)
    if (track && typeof track.is_liked === 'boolean') {
      return track.is_liked
    }
    
    return false
  }

  // Toggle like on track
  const toggleLike = async (trackId, currentLikedState = null) => {
    if (!trackId) return false

    const { useAuthStore } = await import('./auth')
    const authStore = useAuthStore()
    if (!authStore.requireChannel('сохранения в любимые треки')) {
      return currentLikedState !== null ? currentLikedState : false
    }

    // Determine current liked status:
    // 1. Explicitly passed parameter
    // 2. isTrackLiked() check across all known collections
    let isLiked = currentLikedState
    if (isLiked === null || isLiked === undefined) {
      isLiked = isTrackLiked(trackId)
    }
    
    try {
      if (isLiked) {
        await tracksApi.unlike(trackId)
        await notifyTrackChange(trackId, { is_liked: false, liked_at: null })
        likedTracks.value = likedTracks.value.filter(t => t.id !== trackId)
        saveToStorage(CACHE_KEY_LIKED, likedTracks.value.slice(0, 50))
        return false
      } else {
        await tracksApi.like(trackId)
        const nowIso = new Date().toISOString()
        await notifyTrackChange(trackId, { is_liked: true, liked_at: nowIso, is_disliked: false, disliked_at: null })
        
        // Add to likedTracks locally or refetch
        if (!likedTracks.value.some(t => t.id === trackId)) {
          const track = findTrackInStore(trackId)
          if (track) {
            likedTracks.value.unshift({ ...track, is_liked: true, liked_at: nowIso, is_disliked: false, disliked_at: null })
            saveToStorage(CACHE_KEY_LIKED, likedTracks.value.slice(0, 50))
          } else {
            await fetchLikedTracks()
          }
        }
        return true
      }
    } catch (error) {
      console.error('Failed to toggle like:', error)
      // Handle 403 - show channel banner
      if (error.response?.status === 403) {
        authStore.promptChannelSetup('сохранения в любимые треки')
      }
      return isLiked
    }
  }

  // Toggle dislike on track
  const toggleDislike = async (trackId, currentDislikedState = null) => {
    if (!trackId) return false

    let isDisliked = currentDislikedState
    if (isDisliked === null || isDisliked === undefined) {
      const track = findTrackInStore(trackId)
      isDisliked = track?.is_disliked || false
    }

    try {
      if (isDisliked) {
        await tracksApi.undislike(trackId)
        await notifyTrackChange(trackId, { is_disliked: false, disliked_at: null })
        return false
      } else {
        await tracksApi.dislike(trackId)
        const nowIso = new Date().toISOString()
        await notifyTrackChange(trackId, { is_disliked: true, disliked_at: nowIso, is_liked: false, liked_at: null })
        likedTracks.value = likedTracks.value.filter(t => t.id !== trackId)
        return true
      }
    } catch (error) {
      console.error('Failed to toggle dislike:', error)
      return isDisliked
    }
  }

  // Get unavailable tracks count
  const unavailableCount = () => {
    return tracks.value.filter(t => t.is_unavailable).length
  }

  // Delete all unavailable tracks
  const deleteUnavailableTracks = async () => {
    try {
      const unavailable = tracks.value.filter(t => t.is_unavailable)
      for (const t of unavailable) {
        deleteCachedAudio(t.id).catch(() => {})
        deleteCachedUrl(t.id)
      }
      const result = await tracksApi.deleteAllUnavailable()
      // Remove from local state
      tracks.value = tracks.value.filter(t => !t.is_unavailable)
      likedTracks.value = likedTracks.value.filter(t => !t.is_unavailable)
      return result.data.count || 0
    } catch (error) {
      console.error('Failed to delete unavailable tracks:', error)
      return 0
    }
  }

  // ============== GLOBAL LIBRARY ==============
  
  // Fetch global tracks
  const fetchGlobalTracks = async (params = {}) => {
    globalLoading.value = true
    try {
      // Save search params for loadMoreGlobal (exclude page)
      const { page: pageParam, ...searchParams } = params
      if (!pageParam || pageParam === 1) {
        globalSearchParams.value = searchParams
      }
      
      const response = await tracksApi.getGlobal({
        page: params.page || 1,
        per_page: 50,
        ...params,
      })
      
      const data = response.data
      
      if (params.page && params.page > 1) {
        globalTracks.value = [...globalTracks.value, ...data.items]
      } else {
        globalTracks.value = data.items
      }
      
      globalTotal.value = data.total
      globalPage.value = data.page
      globalHasMore.value = globalTracks.value.length < data.total
    } catch (error) {
      console.error('Failed to fetch global tracks:', error)
    } finally {
      globalLoading.value = false
    }
  }
  
  // Load more global tracks (preserves current search/filter params)
  const loadMoreGlobal = async () => {
    if (!globalHasMore.value || globalLoading.value) return
    await fetchGlobalTracks({ ...globalSearchParams.value, page: globalPage.value + 1 })
  }
  
  // Fetch recent uploads from all users
  const fetchRecentUploads = async (limit = 20) => {
    try {
      const response = await tracksApi.getRecentUploads(limit)
      recentUploads.value = response.data?.items || (Array.isArray(response.data) ? response.data : [])
      return recentUploads.value
    } catch (error) {
      console.error('Failed to fetch recent uploads:', error)
      recentUploads.value = []
      return []
    }
  }
  
  // Fetch popular tracks globally
  const fetchPopularTracks = async (limit = 20) => {
    try {
      const response = await tracksApi.getPopular(limit)
      popularTracks.value = response.data?.items || (Array.isArray(response.data) ? response.data : [])
      return popularTracks.value
    } catch (error) {
      console.error('Failed to fetch popular tracks:', error)
      popularTracks.value = []
      return []
    }
  }
  
  // Fetch global stats
  const fetchGlobalStats = async () => {
    try {
      const response = await tracksApi.getGlobalStats()
      globalStats.value = response.data
      return response.data
    } catch (error) {
      console.error('Failed to fetch global stats:', error)
      return null
    }
  }
  
  // Add track to my library from global
  const addToLibrary = async (trackId) => {
    const { useAuthStore } = await import('./auth')
    const authStore = useAuthStore()
    if (!authStore.requireChannel('добавления в медиатеку')) {
      return false
    }

    try {
      await tracksApi.addToLibrary(trackId)
      apiCache.invalidateRelated('track', trackId)
      apiCache.invalidatePattern('/tracks')
      apiCache.invalidatePattern('/library')
      
      await notifyTrackChange(trackId, { in_library: true })
      // Also add to tracks list from a global source if found
      const searchSource = (list) => (Array.isArray(list) ? list.find(t => t?.id === trackId) : null)
      const source = searchSource(globalTracks.value) ||
                     searchSource(recentUploads.value) ||
                     searchSource(popularTracks.value) ||
                     searchSource(selectedUserTracks.value)
      if (source && Array.isArray(tracks.value) && !tracks.value.find(t => t?.id === trackId)) {
        tracks.value.unshift({ ...source, in_library: true })
        total.value = (total.value || 0) + 1
      } else {
        total.value = (total.value || 0) + 1
      }
      
      window.dispatchEvent(new CustomEvent('track:added:library', {
        detail: { trackId }
      }))
      
      // Refresh artists list for new artist
      fetchArtists(artistScope.value)
      return true
    } catch (error) {
      console.error('Failed to add to library:', error)
      // Handle 403 - show channel banner
      if (error.response?.status === 403) {
        authStore.promptChannelSetup('добавления в медиатеку')
      }
      return false
    }
  }
  
  // Optimistically add track to library list after download/import
  const addTrackOptimistic = (trackObj) => {
    if (!trackObj || !trackObj.id) return
    deleteCachedAudio(trackObj.id).catch(() => {})
    deleteCachedUrl(trackObj.id)
    apiCache.invalidateRelated('track', trackObj.id)
    apiCache.invalidatePattern('/tracks')
    apiCache.invalidatePattern('/library')
    
    if (Array.isArray(tracks.value)) {
      const idx = tracks.value.findIndex(t => t?.id === trackObj.id)
      if (idx !== -1) {
        tracks.value[idx] = { ...tracks.value[idx], ...trackObj, in_library: true, is_chunk: false }
      } else {
        tracks.value.unshift({ ...trackObj, in_library: true, is_chunk: false })
        total.value = (total.value || 0) + 1
      }
    }
    window.dispatchEvent(new CustomEvent('track:added:library', {
      detail: { trackId: trackObj.id }
    }))
    fetchArtists(artistScope.value)
  }

  // Remove track from my library
  const removeFromLibrary = async (trackId) => {
    try {
      await tracksApi.removeFromLibrary(trackId)
      deleteCachedAudio(trackId).catch(() => {})
      deleteCachedUrl(trackId)
      apiCache.invalidateRelated('track', trackId)
      apiCache.invalidatePattern('/tracks')
      apiCache.invalidatePattern('/library')
      apiCache.invalidatePattern('/search')
      apiCache.invalidatePattern('/artists')
      apiCache.invalidatePattern('/albums')
      
      // Remove from library lists
      tracks.value = Array.isArray(tracks.value) ? tracks.value.filter(t => t?.id !== trackId) : []
      likedTracks.value = Array.isArray(likedTracks.value) ? likedTracks.value.filter(t => t?.id !== trackId) : []
      total.value = Math.max(0, (total.value || 0) - 1)
      
      // Notify all lists about in_library change
      await notifyTrackChange(trackId, { in_library: false })
      
      // Dispatch library-specific removal event
      window.dispatchEvent(new CustomEvent('track:removed:library', {
        detail: { trackId }
      }))
      
      // Refresh artists list as this track's artist may no longer have tracks in library
      fetchArtists(artistScope.value)
      return true
    } catch (error) {
      console.error('Failed to remove from library:', error)
      // Handle 403 - show channel banner
      if (error.response?.status === 403) {
        const { useAuthStore } = await import('./auth')
        const authStore = useAuthStore()
        authStore.promptChannelSetup()
      }
      return false
    }
  }
  
  // Check if track is in my library
  const isInLibrary = (trackId) => {
    if (!trackId) return false
    return Array.isArray(tracks.value) && tracks.value.some(t => t?.id === trackId)
  }
  
  // Fetch top users
  const fetchTopUsers = async () => {
    try {
      const response = await tracksApi.getTopUsers(20)
      topUsers.value = response.data?.items || (Array.isArray(response.data) ? response.data : [])
      return topUsers.value
    } catch (error) {
      console.error('Failed to fetch top users:', error)
      topUsers.value = []
      return []
    }
  }
  
  // Fetch tracks by specific user
  const fetchUserTracks = async (userId) => {
    try {
      const user = Array.isArray(topUsers.value) ? topUsers.value.find(u => u?.id === userId) : null
      selectedUser.value = user || { id: userId }
      const response = await tracksApi.getUserTracks(userId, 50)
      selectedUserTracks.value = response.data?.items || (Array.isArray(response.data) ? response.data : [])
      return selectedUserTracks.value
    } catch (error) {
      console.error('Failed to fetch user tracks:', error)
      selectedUserTracks.value = []
      return []
    }
  }
  
  // Clear selected user
  const clearSelectedUser = () => {
    selectedUser.value = null
    selectedUserTracks.value = []
  }

  // Search tracks by query
  const search = async (query, scope = 'library') => {
    if (scope === 'global') {
      await fetchGlobalTracks({ search: query })
    } else {
      await fetchTracks({ search: query })
    }
  }

  // Clear search results and reload unfiltered
  const clearSearch = async () => {
    currentSearchParams.value = {}
    globalSearchParams.value = {}
    await fetchTracks()
  }

  return {
    // My library
    tracks,
    playlists,
    artists,
    artistsTotal,
    globalArtists,
    artistScope,
    artistImages,
    genres,
    history,
    likedTracks,
    loading,
    refreshing,
    total,
    hasMore,
    
    // Global library
    globalTracks,
    globalLoading,
    globalTotal,
    globalHasMore,
    recentUploads,
    popularTracks,
    globalStats,
    topUsers,
    selectedUser,
    selectedUserTracks,
    
    // Artist detail
    currentArtist,
    artistLoading,
    
    // Methods
    init,
    refresh,
    fetchTracks,
    fetchTracksByGenre,
    loadMore,
    fetchPlaylists,
    fetchPlaylist,
    fetchArtists,
    fetchArtistDetail,
    clearCurrentArtist,
    setCurrentArtist,
    fetchGenres,
    fetchHistory,
    fetchLikedTracks,
    getArtistImage,
    clearArtistImagesCache,
    createPlaylist,
    updatePlaylist,
    deletePlaylist,
    subscribePlaylist,
    unsubscribePlaylist,
    addTrackToPlaylist,
    removeTrackFromPlaylist,
    notifyPlaylistChange,
    notifyTrackChange,
    notifyTrackRemoved,
    updateTrack,
    setTrackCover,
    uploadTrackCover,
    deleteTrackCover,
    deleteTrack,
    toggleLike,
    toggleDislike,
    isTrackLiked,
    unavailableCount,
    deleteUnavailableTracks,
    
    // Global library methods
    fetchGlobalTracks,
    loadMoreGlobal,
    fetchRecentUploads,
    fetchPopularTracks,
    fetchGlobalStats,
    fetchTopUsers,
    fetchUserTracks,
    clearSelectedUser,
    addToLibrary,
    addTrackOptimistic,
    removeFromLibrary,
    isInLibrary,
    
    // Search
    search,
    clearSearch,

    // Real-time library sync
    checkSyncState,
    startSyncPolling,
    stopSyncPolling,
    isSyncing,
    
    // Cache management
    clearApiCache: () => apiCache.clear(),
    invalidateCachePattern: (pattern) => apiCache.invalidatePattern(pattern),
  }
})
