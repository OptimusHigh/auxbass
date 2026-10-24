/**
 * Unified track search composable
 * Three-tier search: user library → friends → global network
 * Used by EditPlaylistModal and LibraryTracks for consistent search behavior
 */
import { ref, computed, onUnmounted } from 'vue'
import api, { tracksApi, socialApi } from '@/api/client'

/**
 * @param {Object} options
 * @param {number} options.perPage - Results per tier (default: 50)
 * @param {number} options.debounceDelay - Debounce delay ms (default: 300)
 * @returns {Object} Search state and methods
 */
export function useTrackSearch({ perPage = 50, debounceDelay = 300 } = {}) {
  const searchQuery = ref('')
  const libraryResults = ref([])
  const friendsResults = ref([])
  const globalResults = ref([])
  const isSearching = ref(false)
  const isFriendsLoading = ref(false)
  const isGlobalLoading = ref(false)
  let searchTimeout = null

  // Pagination state for library/friends/global
  const libraryPage = ref(1)
  const libraryTotal = ref(0)
  const isLibraryLoadingMore = ref(false)
  const friendsPage = ref(1)
  const friendsTotal = ref(0)
  const globalPage = ref(1)
  const globalTotal = ref(0)
  const isFriendsLoadingMore = ref(false)
  const isGlobalLoadingMore = ref(false)

  // Current query and dedup set (cached for loadMore)
  let _currentQuery = ''
  let _libraryIds = new Set()
  let _activeController = null
  let _searchSequence = 0

  const hasMoreLibrary = computed(() => libraryResults.value.length < libraryTotal.value)
  const hasMoreFriends = computed(() => friendsResults.value.length < friendsTotal.value)
  const hasMoreGlobal = computed(() => globalResults.value.length < globalTotal.value)

  const hasAnyResults = computed(() =>
    libraryResults.value.length > 0 ||
    friendsResults.value.length > 0 ||
    globalResults.value.length > 0
  )

  /** Flat merged list (library first, then friends, then global) — for simple consumers */
  const allResults = computed(() => [
    ...libraryResults.value,
    ...friendsResults.value,
    ...globalResults.value,
  ])

  const debouncedSearch = () => {
    if (searchTimeout) clearTimeout(searchTimeout)
    searchTimeout = setTimeout(search, debounceDelay)
  }

  const search = async () => {
    const query = searchQuery.value.trim()
    if (!query) {
      if (_activeController) {
        _activeController.abort()
        _activeController = null
      }
      _searchSequence++
      libraryResults.value = []
      friendsResults.value = []
      globalResults.value = []
      libraryTotal.value = 0
      friendsTotal.value = 0
      globalTotal.value = 0
      isSearching.value = false
      isFriendsLoading.value = false
      isGlobalLoading.value = false
      return
    }

    // Cancel any previous in-flight search to prevent race conditions
    if (_activeController) {
      _activeController.abort()
    }
    _activeController = new AbortController()
    const currentSignal = _activeController.signal
    const currentSeq = ++_searchSequence

    _currentQuery = query

    libraryPage.value = 1
    friendsPage.value = 1
    globalPage.value = 1

    isSearching.value = true
    isFriendsLoading.value = true
    isGlobalLoading.value = true

    try {
      const [libraryRes, friendsRes, globalRes] = await Promise.all([
        api.get('/library', {
          params: { search: query, per_page: perPage, page: 1 },
          signal: currentSignal
        }).catch(error => {
          if (error?.code === 'ERR_CANCELED' || error?.name === 'CanceledError') return null
          console.error('Failed to search library:', error)
          return null
        }),
        socialApi.searchFriends(query, perPage, 1, { signal: currentSignal }).catch(error => {
          if (error?.code === 'ERR_CANCELED' || error?.name === 'CanceledError') return null
          console.error('Failed to search friends:', error)
          return null
        }),
        tracksApi.getGlobal({
          search: query,
          per_page: perPage,
          page: 1
        }, { signal: currentSignal }).catch(error => {
          if (error?.code === 'ERR_CANCELED' || error?.name === 'CanceledError') return null
          console.error('Failed to search global:', error)
          return null
        })
      ])

      // If a newer search was started or this search was aborted, ignore stale response
      if (currentSeq !== _searchSequence || currentSignal.aborted || query !== searchQuery.value.trim()) {
        return
      }

      // 1) User's library
      const libItems = libraryRes?.data?.items || []
      libraryResults.value = libItems
      libraryTotal.value = libraryRes?.data?.total != null ? libraryRes.data.total : libItems.length

      _libraryIds = new Set(libItems.map(t => t.id))

      // 2) Friends' libraries
      const friendsItems = friendsRes?.data?.items || []
      friendsTotal.value = friendsRes?.data?.total || 0
      friendsResults.value = friendsItems.filter(t => !_libraryIds.has(t.id))

      // 3) Global network
      const friendsIds = new Set(friendsResults.value.map(t => t.id))
      const globalItems = globalRes?.data?.items || []
      globalTotal.value = globalRes?.data?.total || 0
      globalResults.value = globalItems.filter(
        t => !_libraryIds.has(t.id) && !friendsIds.has(t.id)
      )
    } finally {
      if (currentSeq === _searchSequence) {
        isSearching.value = false
        isFriendsLoading.value = false
        isGlobalLoading.value = false
      }
    }
  }

  /** Load more user library results */
  const loadMoreLibrary = async () => {
    if (!hasMoreLibrary.value || isLibraryLoadingMore.value || !_currentQuery) return
    isLibraryLoadingMore.value = true
    try {
      libraryPage.value++
      const libraryRes = await api.get('/library', {
        params: { search: _currentQuery, per_page: perPage, page: libraryPage.value }
      })
      const newItems = libraryRes.data.items || []
      libraryResults.value = [...libraryResults.value, ...newItems]
      libraryTotal.value = libraryRes.data.total != null ? libraryRes.data.total : libraryTotal.value
      for (const t of newItems) {
        _libraryIds.add(t.id)
      }
    } catch (error) {
      console.error('Failed to load more library tracks:', error)
      libraryPage.value--
    } finally {
      isLibraryLoadingMore.value = false
    }
  }

  /** Load more friends results */
  const loadMoreFriends = async () => {
    if (!hasMoreFriends.value || isFriendsLoadingMore.value || !_currentQuery) return
    isFriendsLoadingMore.value = true
    try {
      friendsPage.value++
      const friendsRes = await socialApi.searchFriends(_currentQuery, perPage, friendsPage.value)
      const newItems = (friendsRes.data.items || []).filter(t => !_libraryIds.has(t.id))
      friendsResults.value = [...friendsResults.value, ...newItems]
      friendsTotal.value = friendsRes.data.total || friendsTotal.value
    } catch (error) {
      console.error('Failed to load more friends:', error)
      friendsPage.value--
    } finally {
      isFriendsLoadingMore.value = false
    }
  }

  /** Load more global results */
  const loadMoreGlobal = async () => {
    if (!hasMoreGlobal.value || isGlobalLoadingMore.value || !_currentQuery) return
    isGlobalLoadingMore.value = true
    try {
      globalPage.value++
      const globalRes = await tracksApi.getGlobal({
        search: _currentQuery,
        per_page: perPage,
        page: globalPage.value
      })
      const friendsIds = new Set(friendsResults.value.map(t => t.id))
      const newItems = (globalRes.data.items || []).filter(
        t => !_libraryIds.has(t.id) && !friendsIds.has(t.id)
      )
      globalResults.value = [...globalResults.value, ...newItems]
      globalTotal.value = globalRes.data.total || globalTotal.value
    } catch (error) {
      console.error('Failed to load more global:', error)
      globalPage.value--
    } finally {
      isGlobalLoadingMore.value = false
    }
  }

  const clearSearch = () => {
    if (searchTimeout) clearTimeout(searchTimeout)
    if (_activeController) {
      _activeController.abort()
      _activeController = null
    }
    _searchSequence++
    searchQuery.value = ''
    libraryResults.value = []
    friendsResults.value = []
    globalResults.value = []
    libraryTotal.value = 0
    friendsTotal.value = 0
    globalTotal.value = 0
    libraryPage.value = 1
    friendsPage.value = 1
    globalPage.value = 1
    isSearching.value = false
    isFriendsLoading.value = false
    isGlobalLoading.value = false
    isLibraryLoadingMore.value = false
    _currentQuery = ''
    _libraryIds = new Set()
  }

  /**
   * Search only friends + global tiers (for consumers that manage their own library search).
   * Deduplicates against provided library track IDs.
   * @param {string} query - Search query
   * @param {Array} existingLibraryTracks - Library results to deduplicate against
   */
  const searchFriendsAndGlobal = async (query, existingLibraryTracks = []) => {
    if (!query) {
      friendsResults.value = []
      globalResults.value = []
      friendsTotal.value = 0
      globalTotal.value = 0
      return
    }

    _currentQuery = query
    _libraryIds = new Set(existingLibraryTracks.map(t => t.id))

    // Friends & Global in parallel
    friendsPage.value = 1
    globalPage.value = 1
    isFriendsLoading.value = true
    isGlobalLoading.value = true

    try {
      const [friendsRes, globalRes] = await Promise.all([
        socialApi.searchFriends(query, perPage, 1).catch(error => {
          console.error('Failed to search friends:', error)
          return null
        }),
        tracksApi.getGlobal({ search: query, per_page: perPage, page: 1 }).catch(error => {
          console.error('Failed to search global:', error)
          return null
        })
      ])

      const friendsItems = friendsRes?.data?.items || []
      friendsTotal.value = friendsRes?.data?.total || 0
      friendsResults.value = friendsItems.filter(t => !_libraryIds.has(t.id))

      const friendsIds = new Set(friendsResults.value.map(t => t.id))
      const globalItems = globalRes?.data?.items || []
      globalTotal.value = globalRes?.data?.total || 0
      globalResults.value = globalItems.filter(
        t => !_libraryIds.has(t.id) && !friendsIds.has(t.id)
      )
    } finally {
      isFriendsLoading.value = false
      isGlobalLoading.value = false
    }
  }

  onUnmounted(() => {
    if (searchTimeout) clearTimeout(searchTimeout)
    if (_activeController) {
      _activeController.abort()
      _activeController = null
    }
  })

  return {
    searchQuery,
    libraryResults,
    friendsResults,
    globalResults,
    isSearching,
    isFriendsLoading,
    isGlobalLoading,
    libraryPage,
    libraryTotal,
    hasMoreLibrary,
    isLibraryLoadingMore,
    hasMoreFriends,
    hasMoreGlobal,
    friendsTotal,
    globalTotal,
    hasAnyResults,
    allResults,
    debouncedSearch,
    search,
    searchFriendsAndGlobal,
    loadMoreLibrary,
    loadMoreFriends,
    loadMoreGlobal,
    clearSearch,
  }
}
