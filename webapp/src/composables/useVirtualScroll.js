import { ref, shallowRef, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'

/**
 * Universal Windowed Virtual Scroll Composable
 * 
 * Implements high-performance DOM windowing with sparse paged data fetching:
 * - Accurate total virtual height known from `total` (scrollbar immediately reflects full size)
 * - Windowed DOM mounting: only mounts visible items + overscan buffer (~20-30 items)
 * - Automatically unmounts items scrolled out of view to keep memory & DOM size constant
 * - Sparse data loading: placeholders/skeletons rendered for un-fetched chunks
 * - Reliable relative-top scroll tracking inside nested scroll containers or window
 * 
 * @param {Object} options - Configuration options
 * @param {Function} options.fetchFn - Async function ({ offset, limit, ...params }) => Promise<{ items, total }>
 * @param {number} [options.pageSize=50] - Number of items per fetched page
 * @param {number|Function} [options.itemHeight=64] - Item/row height in px
 * @param {number} [options.gap=2] - Gap between items/rows in px
 * @param {number} [options.overscan=10] - Number of items/rows to pre-render outside viewport
 * @param {number|Function|Ref} [options.columns=1] - Number of columns (1 for lists, dynamic for grids)
 * @param {boolean} [options.immediate=true] - Whether to load first page immediately on mount
 * @param {Ref<HTMLElement>} [options.scrollContainer=null] - Custom scroll container ref
 */
export function useVirtualScroll(options = {}) {
  const {
    fetchFn,
    pageSize = options.limit || 50,
    itemHeight = 64,
    gap = 2,
    overscan = 10,
    columns = 1,
    immediate = true,
    scrollContainer = null,
    maxCachedPages = 12
  } = options

  // State
  const total = ref(0)
  const loading = ref(false)
  const loadingMore = ref(false)
  const error = ref(null)
  const initialized = ref(false)
  const loadTriggerRef = ref(null)

  // Map of loaded items: index -> item
  const itemsMap = shallowRef(new Map())
  const loadedPages = new Set()
  // Map of in-flight pages: pageOffset -> AbortController
  const pendingPages = new Map()

  // Scroll tracking state
  const containerRef = ref(null)
  const scrollTop = ref(0)
  const viewportHeight = ref(800)
  const containerWidth = ref(0)

  // Extra parameters for filtering/searching/sorting
  const extraParams = ref({})

  let detectedScrollContainer = null
  let ticking = false
  let resizeObserver = null
  let dwellTimer = null
  let lastScrollTop = 0
  let lastScrollTime = 0

  // Helpers
  const findScrollContainer = (el) => {
    if (scrollContainer?.value) return scrollContainer.value
    if (scrollContainer && typeof scrollContainer === 'object' && 'nodeType' in scrollContainer) return scrollContainer
    let parent = el?.parentElement
    while (parent && parent !== document.body && parent !== document.documentElement) {
      if (parent.classList?.contains('main-content')) {
        return parent
      }
      const style = window.getComputedStyle(parent)
      if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && parent.clientHeight > 0 && parent.scrollHeight > parent.clientHeight) {
        return parent
      }
      parent = parent.parentElement
    }
    const mainContent = document.querySelector('.main-content')
    if (mainContent) return mainContent
    return window
  }

  // Column count (reactive or prop/option)
  const colCount = computed(() => {
    if (typeof columns === 'function') return Math.max(1, columns(containerWidth.value))
    if (typeof columns === 'object' && 'value' in columns) return Math.max(1, columns.value)
    return Math.max(1, Number(columns) || 1)
  })

  // Height calculations
  const effectiveItemHeight = computed(() => {
    const h = typeof itemHeight === 'function' ? itemHeight(containerWidth.value) : itemHeight
    return Number(h) || 64
  })

  const effectiveRowHeight = computed(() => effectiveItemHeight.value + gap)

  const totalRows = computed(() => Math.ceil(total.value / colCount.value))

  const totalHeight = computed(() => {
    if (total.value <= 0) return 0
    return Math.max(0, totalRows.value * effectiveRowHeight.value - gap)
  })

  // Windowed indices calculation
  const startRow = computed(() => {
    return Math.max(0, Math.floor(scrollTop.value / effectiveRowHeight.value) - overscan)
  })

  const endRow = computed(() => {
    const minVisibleRows = Math.ceil(viewportHeight.value / effectiveRowHeight.value)
    const calculatedEnd = Math.ceil((scrollTop.value + viewportHeight.value) / effectiveRowHeight.value) + overscan
    // Guarantee window is always at least minVisibleRows + overscan tall
    return Math.min(totalRows.value, Math.max(startRow.value + minVisibleRows + overscan, calculatedEnd))
  })

  const startIndex = computed(() => startRow.value * colCount.value)
  const endIndex = computed(() => Math.min(total.value, endRow.value * colCount.value))

  const topOffset = computed(() => startRow.value * effectiveRowHeight.value)

  // Visible items slice
  const visibleItems = computed(() => {
    const list = []
    const start = startIndex.value
    const end = endIndex.value
    const map = itemsMap.value

    for (let i = start; i < end; i++) {
      const hasItem = map.has(i)
      list.push({
        index: i,
        data: hasItem ? map.get(i) : null,
        isPlaceholder: !hasItem
      })
    }
    return list
  })

  const hasMore = computed(() => itemsMap.value.size < total.value)
  const isEmpty = computed(() => initialized.value && !loading.value && total.value === 0)

  // Maximum concurrent page requests
  const MAX_CONCURRENT_PAGES = 2

  // Evict pages that are furthest from current visible window to free memory
  const evictDistantPages = () => {
    if (loadedPages.size <= maxCachedPages) return

    const start = startIndex.value
    const end = endIndex.value
    // Safe buffer zone around visible range: visible ± (overscan * colCount + pageSize * 2)
    const buffer = Math.max(pageSize * 2, overscan * colCount.value + pageSize)
    const minSafeIndex = Math.max(0, start - buffer)
    const maxSafeIndex = end + buffer

    // Find candidates for eviction (pages completely outside safe range)
    const candidates = []
    const center = (start + end) / 2

    for (const pageOffset of loadedPages) {
      const pageEnd = pageOffset + pageSize
      // Check if page falls completely outside safe range
      if (pageEnd <= minSafeIndex || pageOffset >= maxSafeIndex) {
        const pageCenter = pageOffset + pageSize / 2
        const distance = Math.abs(pageCenter - center)
        candidates.push({ pageOffset, distance })
      }
    }

    // Sort by distance descending (furthest first)
    candidates.sort((a, b) => b.distance - a.distance)

    // How many pages to evict
    const countToEvict = loadedPages.size - maxCachedPages
    if (countToEvict <= 0 || candidates.length === 0) return

    const pagesToEvict = candidates.slice(0, countToEvict).map(c => c.pageOffset)
    if (pagesToEvict.length === 0) return

    const updated = new Map(itemsMap.value)
    for (const offset of pagesToEvict) {
      for (let i = 0; i < pageSize; i++) {
        updated.delete(offset + i)
      }
      loadedPages.delete(offset)
    }
    itemsMap.value = updated
  }

  // Cancel all pending page requests
  const abortAllPending = () => {
    for (const controller of pendingPages.values()) {
      try {
        controller.abort()
      } catch (e) {
        // Ignore abort errors
      }
    }
    pendingPages.clear()
    if (dwellTimer) {
      clearTimeout(dwellTimer)
      dwellTimer = null
    }
  }

  // Fetch a specific page/chunk by offset with AbortSignal
  const fetchPage = async (pageOffset) => {
    if (loadedPages.has(pageOffset) || pendingPages.has(pageOffset)) return
    if (!fetchFn) return

    const controller = new AbortController()
    pendingPages.set(pageOffset, controller)

    if (pageOffset === 0 && !initialized.value) {
      loading.value = true
    } else {
      loadingMore.value = true
    }

    try {
      const result = await fetchFn({
        offset: pageOffset,
        limit: pageSize,
        signal: controller.signal,
        ...extraParams.value
      })

      if (result) {
        total.value = result.total ?? (pageOffset + (result.items?.length || 0))
        const newItems = result.items || []

        // Update itemsMap
        const updated = new Map(itemsMap.value)
        for (let i = 0; i < newItems.length; i++) {
          updated.set(pageOffset + i, newItems[i])
        }
        itemsMap.value = updated
        loadedPages.add(pageOffset)

        // Evict distant pages if cache exceeded
        evictDistantPages()
      }
    } catch (err) {
      const isCanceled = err?.name === 'AbortError' || err?.code === 'ERR_CANCELED' || (err?.message && String(err.message).toLowerCase().includes('canceled'))
      if (!isCanceled) {
        error.value = err
        console.error(`[useVirtualScroll] Error fetching offset ${pageOffset}:`, err)
      }
    } finally {
      pendingPages.delete(pageOffset)
      loading.value = false
      loadingMore.value = pendingPages.size > 0
      initialized.value = true
      // Check if more visible pages need to be loaded
      checkNeededPages()
    }
  }

  // Check which pages are needed for the current visible window and abort out-of-viewport ones
  const checkNeededPages = () => {
    if (total.value <= 0) return

    const start = startIndex.value
    const end = endIndex.value
    const buffer = Math.max(pageSize * 2, overscan * colCount.value + pageSize)
    const minSafeIndex = Math.max(0, start - buffer)
    const maxSafeIndex = end + buffer

    // 1. Cancel in-flight page requests that have scrolled far outside viewport
    for (const [offset, controller] of pendingPages.entries()) {
      const pageEnd = offset + pageSize
      if (pageEnd <= minSafeIndex || offset >= maxSafeIndex) {
        try {
          controller.abort()
        } catch (e) {}
        pendingPages.delete(offset)
      }
    }

    if (pendingPages.size >= MAX_CONCURRENT_PAGES) return

    // 2. Identify missing pages in the currently visible window
    const neededOffsets = new Set()
    for (let i = start; i < end; i++) {
      if (!itemsMap.value.has(i)) {
        const pageOffset = Math.floor(i / pageSize) * pageSize
        if (!loadedPages.has(pageOffset) && !pendingPages.has(pageOffset)) {
          neededOffsets.add(pageOffset)
        }
      }
    }

    if (neededOffsets.size === 0) return

    // Prioritize chunks closest to current visible center
    const center = (start + end) / 2
    const sortedOffsets = Array.from(neededOffsets).sort((a, b) => {
      const centerA = a + pageSize / 2
      const centerB = b + pageSize / 2
      return Math.abs(centerA - center) - Math.abs(centerB - center)
    })

    for (const pageOffset of sortedOffsets) {
      if (pendingPages.size >= MAX_CONCURRENT_PAGES) break
      fetchPage(pageOffset)
    }
  }

  // Debounced scheduling of checkNeededPages during active scrolling
  const scheduleCheckNeededPages = (delay = 80) => {
    if (dwellTimer) {
      clearTimeout(dwellTimer)
    }
    dwellTimer = setTimeout(() => {
      dwellTimer = null
      updateScrollMetrics()
      checkNeededPages()
    }, delay)
  }

  // Detect UI scale applied via zoom or --ui-scale
  const getUIScale = () => {
    if (typeof document === 'undefined') return 1.0
    const zoomStyle = document.documentElement.style.zoom
    if (zoomStyle) {
      const parsed = parseFloat(zoomStyle)
      if (!isNaN(parsed) && parsed > 0) return parsed
    }
    const cssVar = getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')
    if (cssVar) {
      const parsed = parseFloat(cssVar)
      if (!isNaN(parsed) && parsed > 0) return parsed
    }
    const computedZoom = getComputedStyle(document.documentElement).zoom
    if (computedZoom) {
      const parsed = parseFloat(computedZoom)
      if (!isNaN(parsed) && parsed > 0) return parsed
    }
    return 1.0
  }

  // Recompute and update current scroll coordinates and viewport metrics
  const updateScrollMetrics = () => {
    if (!containerRef.value) return false

    const containerRect = containerRef.value.getBoundingClientRect()
    containerWidth.value = containerRef.value.clientWidth

    // If container is hidden (e.g. inside a non-active tab), skip calculation
    if (containerRect.width === 0 && containerRect.height === 0) {
      return false
    }

    const sc = detectedScrollContainer || findScrollContainer(containerRef.value)
    detectedScrollContainer = sc

    const scale = getUIScale()

    if (sc === window) {
      viewportHeight.value = window.innerHeight / scale
      const listTop = containerRect.top / scale
      scrollTop.value = Math.max(0, -listTop)
    } else if (sc) {
      viewportHeight.value = sc.clientHeight || (window.innerHeight / scale) || 800
      const scRect = sc.getBoundingClientRect()
      const relativeTop = (containerRect.top - scRect.top) / scale
      scrollTop.value = Math.max(0, -relativeTop)
    }
    return true
  }

  // Update scroll metrics and schedule needed pages check
  const updateScroll = () => {
    if (!updateScrollMetrics()) {
      ticking = false
      return
    }

    // Dwell time: calculate velocity to distinguish high-speed flick from slow browsing
    const now = performance.now()
    const deltaY = Math.abs(scrollTop.value - lastScrollTop)
    const dt = Math.max(1, now - lastScrollTime)
    const velocity = deltaY / dt // px per ms
    lastScrollTop = scrollTop.value
    lastScrollTime = now

    // If velocity is high (> 1.0 px/ms), fast flick is in progress: dwell 100ms
    // If velocity is low / settled: check quickly with 30ms dwell
    const delay = velocity > 1.0 ? 100 : 30
    scheduleCheckNeededPages(delay)
    ticking = false
  }

  // Immediate sync when scroll motion completely ceases (fires on modern browser scrollend)
  const handleScrollEnd = () => {
    if (dwellTimer) {
      clearTimeout(dwellTimer)
      dwellTimer = null
    }
    updateScrollMetrics()
    checkNeededPages()
  }

  const handleScroll = () => {
    if (!ticking) {
      window.requestAnimationFrame(updateScroll)
      ticking = true
    }
  }

  // Reset data and reload from offset 0
  const reset = async (params = null) => {
    if (params !== null) {
      extraParams.value = typeof params === 'object' && params !== null 
        ? { ...extraParams.value, ...params }
        : params
    }
    abortAllPending()
    loadedPages.clear()
    itemsMap.value = new Map()
    total.value = 0
    scrollTop.value = 0
    await fetchPage(0)
    await nextTick()
    updateScroll()
  }

  const refresh = async () => {
    abortAllPending()
    loadedPages.clear()
    itemsMap.value = new Map()
    await fetchPage(0)
    await nextTick()
    updateScroll()
  }

  const setParams = async (params) => {
    extraParams.value = { ...extraParams.value, ...params }
    return reset()
  }

  const clear = () => {
    abortAllPending()
    loadedPages.clear()
    itemsMap.value = new Map()
    total.value = 0
    initialized.value = false
  }

  // Array of currently cached items in index order (for player queue, etc.)
  const getLoadedItems = () => {
    return Array.from(itemsMap.value.entries())
      .sort((a, b) => a[0] - b[0])
      .map(entry => entry[1])
  }

  // Patch item by id or predicate
  const patchItem = (predicateOrId, data) => {
    const isFn = typeof predicateOrId === 'function'
    const updated = new Map(itemsMap.value)
    let modified = false

    for (const [index, item] of updated.entries()) {
      if (item && (isFn ? predicateOrId(item) : item.id === predicateOrId)) {
        updated.set(index, { ...item, ...data })
        modified = true
      }
    }
    if (modified) {
      itemsMap.value = updated
    }
  }

  // Remove item by id or predicate
  const removeItem = (predicateOrId) => {
    const isFn = typeof predicateOrId === 'function'
    const current = itemsMap.value
    let targetIndex = -1

    for (const [index, item] of current.entries()) {
      if (item && (isFn ? predicateOrId(item) : item.id === predicateOrId)) {
        targetIndex = index
        break
      }
    }

    if (targetIndex !== -1) {
      const updated = new Map()
      for (const [index, item] of current.entries()) {
        if (index < targetIndex) {
          updated.set(index, item)
        } else if (index > targetIndex) {
          updated.set(index - 1, item)
        }
      }
      itemsMap.value = updated
      total.value = Math.max(0, total.value - 1)
      loadedPages.clear() // Invalidate page boundary cache after shift
    }
  }

  // Watch startIndex / endIndex to fetch missing chunks and evict distant pages
  watch([startIndex, endIndex, total], () => {
    scheduleCheckNeededPages(50)
    evictDistantPages()
  })

  onMounted(async () => {
    await nextTick()
    detectedScrollContainer = findScrollContainer(containerRef.value)
    
    if (detectedScrollContainer && detectedScrollContainer !== window) {
      detectedScrollContainer.addEventListener('scroll', handleScroll, { passive: true })
      detectedScrollContainer.addEventListener('scrollend', handleScrollEnd, { passive: true })
    }
    window.addEventListener('scroll', handleScroll, { passive: true })
    window.addEventListener('scrollend', handleScrollEnd, { passive: true })
    window.addEventListener('resize', handleScroll, { passive: true })

    if (containerRef.value) {
      containerWidth.value = containerRef.value.clientWidth
      resizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          containerWidth.value = entry.contentRect.width
          updateScroll()
        }
      })
      resizeObserver.observe(containerRef.value)
    }

    if (immediate) {
      await fetchPage(0)
      await nextTick()
      updateScroll()
    }
  })

  onUnmounted(() => {
    if (dwellTimer) {
      clearTimeout(dwellTimer)
      dwellTimer = null
    }
    if (detectedScrollContainer && detectedScrollContainer !== window) {
      detectedScrollContainer.removeEventListener('scroll', handleScroll)
      detectedScrollContainer.removeEventListener('scrollend', handleScrollEnd)
    }
    window.removeEventListener('scroll', handleScroll)
    window.removeEventListener('scrollend', handleScrollEnd)
    window.removeEventListener('resize', handleScroll)
    if (resizeObserver) {
      resizeObserver.disconnect()
      resizeObserver = null
    }
    clear()
  })

  return {
    // State
    containerRef,
    loadTriggerRef,
    total,
    loading,
    loadingMore,
    loadingSkeletonCount: computed(() => (loadingMore.value ? 4 : 0)),
    error,
    initialized,
    hasMore,
    isEmpty,
    itemsMap,
    items: computed(() => getLoadedItems()),
    
    // Windowing metrics
    scrollTop,
    viewportHeight,
    containerWidth,
    totalHeight,
    topOffset,
    startIndex,
    endIndex,
    visibleItems,
    
    // Methods
    fetchPage,
    reset,
    refresh,
    setParams,
    clear,
    updateScroll,
    getLoadedItems,
    patchItem,
    removeItem
  }
}

export default useVirtualScroll
