/**
 * useNetworkMonitor — центральный модуль мониторинга сети и health-проверок.
 * 
 * Обязанности:
 * 1. Online/offline отслеживание (navigator.onLine + события)
 * 2. Оценка качества сети через navigator.connection + API latency
 * 3. Автоматическое оповещение при потере/восстановлении сети
 * 4. Предоставление reactive-состояния для UI (баннер, индикатор в плеере)
 * 5. Retry-координация: сигнал для player store о необходимости перезагрузки
 * 
 * Singleton-паттерн: модуль-уровень state, shared между всеми потребителями.
 */

import { ref, computed, readonly, watch } from 'vue'
import { handleHealthResponse } from './useAppUpdate'

// ============== Константы ==============
const LATENCY_CHECK_INTERVAL = 30_000  // Проверка latency каждые 30 сек (при воспроизведении)
const LATENCY_SLOW_THRESHOLD = 2000    // >2с = медленная сеть
const LATENCY_WARN_THRESHOLD = 5000    // >5с = очень медленная сеть
const RECONNECT_DEBOUNCE = 1500        // Задержка перед объявлением reconnect
const OFFLINE_GRACE_PERIOD = 3000      // Ждём 3с прежде чем показать offline (false positives)

// ============== Состояние сети (module-level singleton) ==============

/** @type {'online' | 'offline' | 'slow' | 'reconnecting'} */
const connectionState = ref('online')

/** Флаг: бэкенд недоступен (502/503 или сбой сети) */
const isBackendDown = ref(false)

/** Флаг: бот в сети (из /api/health) */
const isBotOnline = ref(true)

/** Флаг: режим техобслуживания */
const isMaintenance = ref(false)

/** Задержка последнего API ping (ms), -1 если не определена */
const latency = ref(-1)

/** Время последнего успешного API-запроса */
const lastSuccessfulRequest = ref(Date.now())

/** Когда пропала сеть (для показа длительности) */
const offlineSince = ref(null)

/** Счётчик последовательных неудач latency-проверки */
let consecutiveFailures = 0
const MAX_FAILURES_BEFORE_SLOW = 2

/** Мониторинг активен */
const isMonitoring = ref(false)

/** Флаг: сеть восстановилась (для retry в плеере) */
const networkRecovered = ref(false)

/** Информация о типе подключения */
const connectionType = ref(null) // 'wifi', '4g', '3g', '2g', 'slow-2g', etc.
const effectiveType = ref(null)
const downlink = ref(null) // Мбит/с

// ============== Таймеры ==============
let latencyInterval = null
let offlineTimeout = null
let reconnectTimeout = null

// ============== Вычисляемые свойства ==============
const isOnline = computed(() => connectionState.value !== 'offline')
const isOffline = computed(() => connectionState.value === 'offline')
const isSlow = computed(() => connectionState.value === 'slow')
const isReconnecting = computed(() => connectionState.value === 'reconnecting')
const hasIssues = computed(() => connectionState.value !== 'online' || isBackendDown.value || !isBotOnline.value || isMaintenance.value)

/** Текст для баннера */
const statusMessage = computed(() => {
  if (isMaintenance.value) return 'Технические работы'
  if (isBackendDown.value) return 'Сервер временно оффлайн'
  if (!isBotOnline.value) return 'Telegram-бот переподключается'
  switch (connectionState.value) {
    case 'offline': return 'Оффлайн-режим'
    case 'reconnecting': return 'Переподключение...'
    case 'slow': return 'Медленное соединение'
    default: return ''
  }
})

/** Текст для индикатора в плеере (краткий) */
const statusShort = computed(() => {
  if (isMaintenance.value) return 'Техобслуживание'
  if (isBackendDown.value) return 'Сервер оффлайн'
  if (!isBotOnline.value) return 'Бот оффлайн'
  switch (connectionState.value) {
    case 'offline': return 'Оффлайн'
    case 'reconnecting': return 'Подключение...'
    case 'slow': return 'Медленная сеть'
    default: return ''
  }
})

// ============== Приватные методы ==============

/**
 * Проверка latency и статуса сервера через /api/health.
 */
const checkLatency = async () => {
  const start = Date.now()
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), LATENCY_WARN_THRESHOLD + 1000)
    
    const resp = await fetch('/api/health', {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
    })
    
    clearTimeout(timeout)
    
    const elapsed = Date.now() - start
    latency.value = elapsed
    lastSuccessfulRequest.value = Date.now()
    consecutiveFailures = 0

    if (resp.ok) {
      isBackendDown.value = false
      try {
        const data = await resp.json()
        isBotOnline.value = data.bot_online !== false
        isMaintenance.value = data.maintenance === true || data.status === 'maintenance'
        handleHealthResponse(data)
      } catch (_) {
        isBotOnline.value = true
        isMaintenance.value = false
      }
    } else {
      if (resp.status === 502 || resp.status === 503 || resp.status === 504) {
        isBackendDown.value = true
      }
    }
    
    // Обновить состояние на основе latency
    if (connectionState.value === 'offline' || connectionState.value === 'reconnecting') {
      // Восстановление сети!
      handleNetworkRecovery()
    } else if (elapsed > LATENCY_SLOW_THRESHOLD) {
      if (connectionState.value !== 'slow') {
        console.warn(`[NetworkMonitor] Slow network detected: ${elapsed}ms latency`)
      }
      connectionState.value = 'slow'
    } else {
      connectionState.value = 'online'
    }
    
    return elapsed
  } catch (e) {
    latency.value = -1
    consecutiveFailures++
    
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      connectionState.value = 'offline'
      offlineSince.value = offlineSince.value || Date.now()
      isBackendDown.value = false
    } else {
      if (consecutiveFailures >= MAX_FAILURES_BEFORE_SLOW) {
        isBackendDown.value = true
        connectionState.value = 'slow'
        console.warn(`[NetworkMonitor] API unreachable (${consecutiveFailures} failures), marking as slow/down`)
      }
    }
    
    return -1
  }
}

/** Обработка восстановления сети */
const handleNetworkRecovery = () => {
  console.log('[NetworkMonitor] Network recovered!')
  connectionState.value = 'online'
  offlineSince.value = null
  consecutiveFailures = 0
  
  // Сигнал для плеера — можно retry
  networkRecovered.value = true
  // Автосброс через тик
  setTimeout(() => { networkRecovered.value = false }, 100)
}

/** Обработчик события online */
const handleOnline = () => {
  console.log('[NetworkMonitor] Browser online event')
  clearTimeout(offlineTimeout)
  clearTimeout(reconnectTimeout)
  
  // Переходим в reconnecting — нужно убедиться что API доступен
  connectionState.value = 'reconnecting'
  
  // Проверяем реальное подключение
  reconnectTimeout = setTimeout(async () => {
    const result = await checkLatency()
    if (result > 0) {
      handleNetworkRecovery()
    }
  }, RECONNECT_DEBOUNCE)
}

/** Обработчик события offline */
const handleOffline = () => {
  console.warn('[NetworkMonitor] Browser offline event')
  clearTimeout(offlineTimeout)
  clearTimeout(reconnectTimeout)
  
  // Даём grace period чтобы исключить false positive
  offlineTimeout = setTimeout(() => {
    if (!navigator.onLine) {
      connectionState.value = 'offline'
      offlineSince.value = Date.now()
      console.warn('[NetworkMonitor] Confirmed offline')
    }
  }, OFFLINE_GRACE_PERIOD)
}

/** Обновить информацию о типе подключения */
const updateConnectionInfo = () => {
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection
  if (conn) {
    connectionType.value = conn.type || null
    effectiveType.value = conn.effectiveType || null
    downlink.value = conn.downlink || null
    
    // Предупредить о slow-2g/2g
    if (conn.effectiveType === 'slow-2g' || conn.effectiveType === '2g') {
      if (connectionState.value === 'online') {
        connectionState.value = 'slow'
        console.warn(`[NetworkMonitor] Slow effective type: ${conn.effectiveType}`)
      }
    }
  }
}

// ============== Публичный API ==============

/**
 * Запустить мониторинг сети. Вызывать один раз из App.vue onMounted.
 */
const startMonitoring = () => {
  if (isMonitoring.value) return
  isMonitoring.value = true
  
  console.log('[NetworkMonitor] Starting network monitoring')
  
  // Начальное состояние
  if (!navigator.onLine) {
    connectionState.value = 'offline'
    offlineSince.value = Date.now()
  }
  
  // События браузера
  window.addEventListener('online', handleOnline)
  window.addEventListener('offline', handleOffline)
  
  // Network Information API
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection
  if (conn) {
    updateConnectionInfo()
    conn.addEventListener('change', updateConnectionInfo)
  }
  
  // Периодическая проверка latency
  startLatencyChecks()
}

/**
 * Остановить мониторинг (cleanup).
 */
const stopMonitoring = () => {
  isMonitoring.value = false
  
  window.removeEventListener('online', handleOnline)
  window.removeEventListener('offline', handleOffline)
  
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection
  if (conn) {
    conn.removeEventListener('change', updateConnectionInfo)
  }
  
  stopLatencyChecks()
  clearTimeout(offlineTimeout)
  clearTimeout(reconnectTimeout)
}

/**
 * Запустить/перезапустить периодические проверки latency.
 * Вызывается при начале воспроизведения.
 */
const startLatencyChecks = () => {
  stopLatencyChecks()
  // Первая проверка сразу
  checkLatency()
  // Далее — по интервалу
  latencyInterval = setInterval(checkLatency, LATENCY_CHECK_INTERVAL)
}

const stopLatencyChecks = () => {
  if (latencyInterval) {
    clearInterval(latencyInterval)
    latencyInterval = null
  }
}

/**
 * Записать успешный API-запрос (вызывать из API interceptor).
 * Помогает быстрее обнаружить восстановление и сбросить slow-статус.
 */
const recordSuccessfulRequest = () => {
  lastSuccessfulRequest.value = Date.now()
  consecutiveFailures = 0
  isBackendDown.value = false
  
  if (connectionState.value !== 'online') {
    connectionState.value = 'online'
    offlineSince.value = null
  }
}

/**
 * Записать неудачный API-запрос.
 */
const recordFailedRequest = (error) => {
  // If browser itself is offline, this is client offline, NOT a backend crash!
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    connectionState.value = 'offline'
    offlineSince.value = offlineSince.value || Date.now()
    return
  }

  const status = error?.response?.status
  // Ignore track-specific unstreamable/HD 503 errors from player stream
  const reqUrl = error?.config?.url || ''
  if (status === 503 && reqUrl.includes('/api/player/stream/')) {
    return
  }
  if (status === 502 || status === 503 || status === 504) {
    consecutiveFailures++
    if (consecutiveFailures >= 2) {
      isBackendDown.value = true
    }
    return
  }

  // Network error (no response) while navigator.onLine is true
  if (!error?.response && error?.code !== 'ECONNABORTED' && error?.code !== 'ERR_CANCELED') {
    consecutiveFailures++
    if (consecutiveFailures >= MAX_FAILURES_BEFORE_SLOW) {
      isBackendDown.value = true
      connectionState.value = 'slow'
    }
  }
}

/**
 * Принудительная проверка — вызывать при подозрении на проблему.
 */
const forceCheck = () => {
  return checkLatency()
}

// ============== Composable ==============

export function useNetworkMonitor() {
  return {
    // Reactive state (readonly для внешних потребителей)
    connectionState: readonly(connectionState),
    isBackendDown: readonly(isBackendDown),
    isBotOnline: readonly(isBotOnline),
    isMaintenance: readonly(isMaintenance),
    latency: readonly(latency),
    offlineSince: readonly(offlineSince),
    connectionType: readonly(connectionType),
    effectiveType: readonly(effectiveType),
    downlink: readonly(downlink),
    networkRecovered: readonly(networkRecovered),
    
    // Computed
    isOnline,
    isOffline,
    isSlow,
    isReconnecting,
    hasIssues,
    statusMessage,
    statusShort,
    
    // Methods
    startMonitoring,
    stopMonitoring,
    startLatencyChecks,
    stopLatencyChecks,
    recordSuccessfulRequest,
    recordFailedRequest,
    forceCheck,
  }
}
