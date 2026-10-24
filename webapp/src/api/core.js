import axios from 'axios'
import apiCache from '../utils/apiCache'
import { useNetworkMonitor } from '../composables/useNetworkMonitor'

// Lazy reference to network monitor (initialized on first use)
let _networkMonitor = null
const getNetworkMonitor = () => {
  if (!_networkMonitor) _networkMonitor = useNetworkMonitor()
  return _networkMonitor
}

// ============== Retry Configuration ==============
const RETRY_CONFIG = {
  maxRetries: 1,           // Одна повторная попытка
  retryDelay: 1000,        // Задержка перед retry (мс)
  retryableStatuses: [502, 503, 504, 0], // 0 = network error
  retryableMethods: ['get', 'head', 'options'], // Только идемпотентные
}

// Use relative path for production, env variable for development
const API_URL = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_URL,
  timeout: 30000,
})

// Add auth headers to all requests
api.interceptors.request.use((config) => {
  const tg = window.Telegram?.WebApp
  
  // Prefer Telegram WebApp auth if available
  if (tg?.initData) {
    config.headers['X-Telegram-Init-Data'] = tg.initData
  } else {
    // Fall back to JWT token for browser auth
    const token = authStorage.getToken()
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
  }
  
  // Check cache for GET requests (unless explicitly bypassed)
  if (config.method === 'get') {
    if (config.bypassCache) {
      config.headers = config.headers || {}
      config.headers['Cache-Control'] = 'no-cache'
      config.params = { ...config.params, _t: Date.now() }
    } else {
      const cacheKey = apiCache.generateKey(config.url, config.params)
      const cachedResponse = apiCache.get(cacheKey)
      
      if (cachedResponse) {
        // Return cached response (cancel actual request)
        config.adapter = () => Promise.resolve({
          data: cachedResponse,
          status: 200,
          statusText: 'OK (cached)',
          headers: {},
          config,
          request: {}
        })
      }
    }
  }
  
  return config
})

// Handle errors (including 401 for expired tokens)
api.interceptors.response.use(
  (response) => {
    // Cache successful GET responses (including fresh responses from bypassCache)
    if (response.config.method === 'get' && response.status === 200) {
      const cacheKey = apiCache.generateKey(response.config.url, response.config.params)
      apiCache.set(cacheKey, response.data)
    }
    
    // Notify network monitor of successful request
    try { getNetworkMonitor().recordSuccessfulRequest() } catch {}
    
    return response
  },
  async (error) => {
    // Ignore canceled requests (e.g. from AbortController on rapid search typing)
    if (axios.isCancel(error) || error.code === 'ERR_CANCELED') {
      return Promise.reject(error)
    }

    const config = error.config
    
    // Notify network monitor of failed request
    try { getNetworkMonitor().recordFailedRequest(error) } catch {}
    
    // === Retry logic for transient errors ===
    if (config && !config._retried) {
      const status = error.response?.status || 0
      const isRetryable = RETRY_CONFIG.retryableStatuses.includes(status)
      const isIdempotent = RETRY_CONFIG.retryableMethods.includes(config.method)
      const isNetworkError = !error.response && error.code !== 'ERR_CANCELED'
      
      if ((isRetryable && isIdempotent) || (isNetworkError && isIdempotent)) {
        config._retried = true
        console.warn(`[API Retry] Retrying ${config.method?.toUpperCase()} ${config.url} after ${status || 'network'} error`)
        
        await new Promise(resolve => setTimeout(resolve, RETRY_CONFIG.retryDelay))
        return api(config)
      }
    }
    
    // If 401 and we're using JWT, clear the token
    if (error.response?.status === 401 && !window.Telegram?.WebApp?.initData) {
      const hadToken = !!authStorage.getToken()
      authStorage.clear()
      // Only dispatch logout event if we actually had a session/token
      if (hadToken) {
        window.dispatchEvent(new CustomEvent('auth:logout'))
      }
    }
    console.error('API Error:', error.response?.data || error.message)
    return Promise.reject(error)
  }
)

export default api

// ============== Auth Token Storage ==============
const AUTH_TOKEN_KEY = 'tg_player_auth_token'
const AUTH_USER_KEY = 'tg_player_auth_user'

export const authStorage = {
  getToken: () => localStorage.getItem(AUTH_TOKEN_KEY),
  setToken: (token) => localStorage.setItem(AUTH_TOKEN_KEY, token),
  removeToken: () => localStorage.removeItem(AUTH_TOKEN_KEY),
  
  getUser: () => {
    try {
      const user = localStorage.getItem(AUTH_USER_KEY)
      return user ? JSON.parse(user) : null
    } catch {
      return null
    }
  },
  setUser: (user) => localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user)),
  removeUser: () => localStorage.removeItem(AUTH_USER_KEY),
  
  clear: () => {
    localStorage.removeItem(AUTH_TOKEN_KEY)
    localStorage.removeItem(AUTH_USER_KEY)
  },
  
  isAuthenticated: () => {
    // Check if we have Telegram WebApp auth OR JWT token
    const tg = window.Telegram?.WebApp
    return !!(tg?.initData) || !!localStorage.getItem(AUTH_TOKEN_KEY)
  },
  
  isTelegramWebApp: () => {
    const tg = window.Telegram?.WebApp
    return !!(tg?.initData)
  }
}

// ============== Cache Helpers ==============

// Helper to create cacheable API method
export const cacheable = (method) => method

// Helper to create non-cacheable API method (mutations)
export const nonCacheable = (method, invalidateType) => {
  return (...args) => {
    return method(...args).then(response => {
      // Invalidate related caches after mutation
      if (invalidateType) {
        apiCache.invalidateRelated(invalidateType, args[0])
      }
      return response
    })
  }
}
