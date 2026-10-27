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

// Cache of successfully loaded cover URLs in current session
export const loadedUrls = new Set()

if (typeof window !== 'undefined' && window.sessionStorage) {
  try {
    const saved = JSON.parse(sessionStorage.getItem('aux_loaded_covers') || '[]')
    if (Array.isArray(saved)) {
      for (const u of saved) loadedUrls.add(u)
    }
  } catch (_) {}
}

let saveTimer = null
function persistLoadedUrl(url) {
  loadedUrls.add(url)
  if (typeof window === 'undefined' || !window.sessionStorage) return
  if (!saveTimer) {
    saveTimer = setTimeout(() => {
      saveTimer = null
      try {
        const arr = Array.from(loadedUrls).slice(-500)
        sessionStorage.setItem('aux_loaded_covers', JSON.stringify(arr))
      } catch (_) {}
    }, 1000)
  }
}

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
  if (loadedUrls.has(item.url)) {
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
    const img = item.activeImg
    // Nullify callbacks before clearing src to prevent ghost events in WebViews
    img.onload = null
    img.onerror = null
    try {
      img.src = ''
    } catch (_) {}
    item.activeImg = null
    // Guaranteed synchronous decrement and queue processing
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
    if (loadedUrls.has(item.url)) {
      item.onSuccess(item.url)
      continue
    }

    activeCount++
    const img = new Image()
    item.activeImg = img

    let timer = setTimeout(() => {
      if (item.activeImg === img) {
        item.activeImg = null
        activeCount = Math.max(0, activeCount - 1)
        item.onError()
        processQueue()
      }
    }, 8000)

    img.onload = () => {
      clearTimeout(timer)
      if (item.activeImg === img) {
        item.activeImg = null
        activeCount = Math.max(0, activeCount - 1)
        persistLoadedUrl(item.url)
        item.onSuccess(item.url)
        processQueue()
      }
    }

    img.onerror = () => {
      clearTimeout(timer)
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
    return Boolean(url && loadedUrls.has(url))
  },

  register(element, url, onSuccess, onError) {
    if (!element || !url) return

    // Fast-path: already loaded in this session, bypass observer and queue
    if (loadedUrls.has(url)) {
      onSuccess(url)
      return
    }

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
  const initialUrl = unref(urlSource)
  const isPreCached = coverLoader.isCached(initialUrl)

  const isLoaded = ref(isPreCached)
  const isError = ref(false)
  const resolvedSrc = ref(isPreCached ? initialUrl : null)

  let registeredEl = null
  let registeredUrl = null

  const cleanup = () => {
    if (registeredEl) {
      coverLoader.unregister(registeredEl)
      registeredEl = null
      registeredUrl = null
    }
  }

  const update = () => {
    const el = elRef?.value
    const url = unref(urlSource)

    if (!el || !url) {
      cleanup()
      isLoaded.value = false
      isError.value = false
      resolvedSrc.value = null
      return
    }

    if (coverLoader.isCached(url)) {
      cleanup()
      isLoaded.value = true
      isError.value = false
      resolvedSrc.value = url
      return
    }

    // Avoid unregister/re-register churn if element and url haven't changed
    if (registeredEl === el && registeredUrl === url) {
      return
    }

    cleanup()

    registeredEl = el
    registeredUrl = url
    isLoaded.value = false
    isError.value = false
    resolvedSrc.value = null

    coverLoader.register(
      el,
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

  watch(() => unref(urlSource), () => {
    update()
  })

  watch(() => elRef?.value, (newEl) => {
    if (newEl) {
      update()
    } else {
      cleanup()
    }
  })

  onMounted(() => {
    if (elRef?.value) {
      update()
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
