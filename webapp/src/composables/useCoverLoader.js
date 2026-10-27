import { ref, watch, unref, onMounted, onBeforeUnmount } from 'vue'

/**
 * Global Viewport-First LIFO Cover Loader
 * 
 * Features:
 * - Dwell Time: 120ms before requesting images (eliminates fast scroll burst requests)
 * - LIFO Priority: latest viewed elements load first when scrolling stops
 * - Concurrency Limit: max 4 parallel image requests (keeps HTTP sockets open for API & audio)
 * - Automatic Abort: clears img.src when element leaves viewport or unmounts, freeing browser & server sockets
 * - In-Memory Cache: instant display without dwell for already loaded covers
 */

const MAX_CONCURRENT_IMAGES = 4
const DWELL_TIME_MS = 120

// Cache of successfully loaded cover URLs
const loadedCoversCache = new Set()

// Active loaders state
let activeCount = 0
const lifoStack = [] // Array of items waiting to be loaded (LIFO)
const registry = new Map() // DOM Element -> item

let sharedObserver = null

function getSharedObserver() {
  if (typeof window === 'undefined' || !('IntersectionObserver' in window)) {
    return null
  }
  if (!sharedObserver) {
    sharedObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const item = registry.get(entry.target)
        if (!item) continue

        if (entry.isIntersecting) {
          item.isIntersecting = true
          startDwell(item)
        } else {
          item.isIntersecting = false
          cancelDwell(item)
          removeFromStack(item)
          abortLoading(item)
        }
      }
    }, {
      rootMargin: '100px 0px'
    })
  }
  return sharedObserver
}

function startDwell(item) {
  if (!item.url) return

  // Instant display if already cached
  if (loadedCoversCache.has(item.url)) {
    item.onSuccess(item.url)
    return
  }

  cancelDwell(item)
  item.dwellTimer = setTimeout(() => {
    item.dwellTimer = null
    if (item.isIntersecting) {
      pushToStack(item)
      processQueue()
    }
  }, DWELL_TIME_MS)
}

function cancelDwell(item) {
  if (item.dwellTimer) {
    clearTimeout(item.dwellTimer)
    item.dwellTimer = null
  }
}

function pushToStack(item) {
  removeFromStack(item)
  // Push to the end of the stack (LIFO: last added is first popped)
  lifoStack.push(item)
}

function removeFromStack(item) {
  const index = lifoStack.indexOf(item)
  if (index !== -1) {
    lifoStack.splice(index, 1)
  }
}

function abortLoading(item) {
  if (item.activeImg) {
    item.activeImg.onload = null
    item.activeImg.onerror = null
    // Resetting img.src to empty string immediately cancels browser HTTP request
    item.activeImg.src = ''
    item.activeImg = null
    activeCount = Math.max(0, activeCount - 1)
    processQueue()
  }
}

function processQueue() {
  while (activeCount < MAX_CONCURRENT_IMAGES && lifoStack.length > 0) {
    const item = lifoStack.pop()
    if (!item) break

    // If item left viewport or element was removed from DOM, skip
    if (!item.isIntersecting || !document.contains(item.element)) {
      continue
    }

    // Already cached while waiting?
    if (loadedCoversCache.has(item.url)) {
      item.onSuccess(item.url)
      continue
    }

    activeCount++
    const img = new Image()
    item.activeImg = img

    img.onload = () => {
      if (item.activeImg === img) {
        item.activeImg = null
        activeCount = Math.max(0, activeCount - 1)
        loadedCoversCache.add(item.url)
        item.onSuccess(item.url)
        processQueue()
      }
    }

    img.onerror = () => {
      if (item.activeImg === img) {
        item.activeImg = null
        activeCount = Math.max(0, activeCount - 1)
        item.onError()
        processQueue()
      }
    }

    img.src = item.url
  }
}

export const coverLoader = {
  isCached(url) {
    return Boolean(url && loadedCoversCache.has(url))
  },

  register(element, url, onSuccess, onError) {
    if (!element || !url) return

    // If already in registry with same URL, do nothing
    const existing = registry.get(element)
    if (existing && existing.url === url) return

    if (existing) {
      coverLoader.unregister(element)
    }

    const item = {
      element,
      url,
      isIntersecting: false,
      dwellTimer: null,
      activeImg: null,
      onSuccess,
      onError
    }

    registry.set(element, item)

    const observer = getSharedObserver()
    if (observer) {
      observer.observe(element)
    } else {
      // Fallback if IntersectionObserver is not available
      startDwell(item)
    }
  },

  unregister(element) {
    if (!element) return
    const item = registry.get(element)
    if (!item) return

    const observer = getSharedObserver()
    if (observer) {
      observer.unobserve(element)
    }

    cancelDwell(item)
    removeFromStack(item)
    abortLoading(item)
    registry.delete(element)
  }
}

/**
 * Vue Composable to lazily load cover art for a component
 * 
 * @param {Ref<HTMLElement>} elRef - Reference to the cover wrapper element
 * @param {Ref<string>|Function|string} urlSource - Cover image URL
 */
export function useLazyCover(elRef, urlSource) {
  const isLoaded = ref(false)
  const isError = ref(false)
  const resolvedSrc = ref(null)

  const cleanup = () => {
    if (elRef?.value) {
      coverLoader.unregister(elRef.value)
    }
  }

  const update = (rawUrl) => {
    cleanup()

    const url = unref(rawUrl)
    if (!url) {
      isLoaded.value = false
      isError.value = false
      resolvedSrc.value = null
      return
    }

    if (coverLoader.isCached(url)) {
      isLoaded.value = true
      isError.value = false
      resolvedSrc.value = url
      return
    }

    isLoaded.value = false
    isError.value = false
    resolvedSrc.value = null

    if (elRef?.value) {
      coverLoader.register(
        elRef.value,
        url,
        (loadedUrl) => {
          resolvedSrc.value = loadedUrl
          isLoaded.value = true
          isError.value = false
        },
        () => {
          isError.value = true
          isLoaded.value = false
          resolvedSrc.value = null
        }
      )
    }
  }

  watch(() => unref(urlSource), (newUrl) => {
    update(newUrl)
  })

  watch(() => elRef?.value, (newEl) => {
    if (newEl) {
      update(urlSource)
    } else {
      cleanup()
    }
  })

  onMounted(() => {
    if (elRef?.value) {
      update(urlSource)
    }
  })

  onBeforeUnmount(() => {
    cleanup()
  })

  return {
    isLoaded,
    isError,
    resolvedSrc
  }
}

export default useLazyCover
