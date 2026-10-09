import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ingestionApi } from '@/api/client'
import apiCache from '@/utils/apiCache'
import { useUIStore } from './ui'
import { useLibraryStore } from './library'
import { useAuthStore } from './auth'
import { usePlayerStore } from './player'
import { deleteCachedAudio, deleteCachedUrl } from './playerCache'

export const useTasksStore = defineStore('tasks', () => {
  const uiStore = useUIStore()
  const libraryStore = useLibraryStore()
  const authStore = useAuthStore()

  // Map of jobId -> job object
  const jobs = ref(new Map())
  
  // Map of jobId -> metadata: { type: 'exportify' | 'import', title: string, previewData?: any }
  const jobMetas = ref(new Map())

  // Set of minimized job IDs
  const minimizedJobIds = ref(new Set())

  // Modal visibility states
  const showExportifyModal = ref(false)
  const showImportModal = ref(false)
  const exportifyModalOptions = ref({})

  // Current active job for modals
  const currentExportifyJob = ref(null)
  const currentImportJob = ref(null)

  // Polling timers map
  const pollTimers = new Map()

  // ══════════════════════════════════════════════════════════════════════
  // Quick Download Queue (Single / Multi-Track Add from Search & Likes)
  // ══════════════════════════════════════════════════════════════════════
  const downloadQueue = ref([])
  const isQueueProcessing = ref(false)
  const currentQueueTrack = ref(null)
  const queueTotalBatch = ref(0)
  const queueCompletedBatch = ref(0)
  const queueFailedBatch = ref(0)
  const trackStatusMap = ref(new Map())
  const queueVisible = ref(false)
  let queueDismissTimer = null

  const isTrackQueued = (url) => {
    if (!url) return false
    return trackStatusMap.value.get(url) === 'queued'
  }

  const isTrackDownloading = (url) => {
    if (!url) return false
    return trackStatusMap.value.get(url) === 'downloading'
  }

  const isTrackCompleted = (url) => {
    if (!url) return false
    return trackStatusMap.value.get(url) === 'completed'
  }

  const enqueueTrack = (track, source = 'soundcloud') => {
    if (!track || !track.url) return

    const currentStatus = trackStatusMap.value.get(track.url)
    if (currentStatus === 'queued' || currentStatus === 'downloading') {
      uiStore.toast?.info('Уже в очереди', `${track.artist || ''} — ${track.title || ''}`)
      return
    }

    if (queueDismissTimer) {
      clearTimeout(queueDismissTimer)
      queueDismissTimer = null
    }

    // If queue was idle, reset batch counters
    if (!isQueueProcessing.value && downloadQueue.value.length === 0) {
      queueTotalBatch.value = 0
      queueCompletedBatch.value = 0
      queueFailedBatch.value = 0
    }

    queueTotalBatch.value++
    queueVisible.value = true

    const queueItem = {
      url: track.url,
      title: track.title || 'Трек',
      artist: track.artist || 'Неизвестный исполнитель',
      duration: track.duration,
      cover_url: track.cover_url,
      genre: track.genre,
      tags: track.tags,
      source: source || 'soundcloud',
      is_drm_preview: Boolean(track.is_drm_preview),
      rawTrack: track,
    }

    const nextMap = new Map(trackStatusMap.value)
    nextMap.set(track.url, 'queued')
    trackStatusMap.value = nextMap

    downloadQueue.value.push(queueItem)

    // UI feedback toast
    if (downloadQueue.value.length === 1 && !isQueueProcessing.value) {
      uiStore.toast?.info('Загрузка начата', `${queueItem.artist} — ${queueItem.title}`)
    } else {
      uiStore.toast?.info(
        'Добавлено в очередь',
        `${queueItem.artist} — ${queueItem.title} (${downloadQueue.value.length} в очереди)`
      )
    }

    if (!isQueueProcessing.value) {
      processNextQueueItem()
    }
  }

  const processNextQueueItem = async () => {
    if (downloadQueue.value.length === 0) {
      isQueueProcessing.value = false
      currentQueueTrack.value = null

      if (queueCompletedBatch.value > 1) {
        uiStore.toast?.success(
          'Очередь завершена',
          `Успешно добавлено ${queueCompletedBatch.value} треков в медиатеку`
        )
      }

      // Auto-hide widget after 5 seconds
      queueDismissTimer = setTimeout(() => {
        queueVisible.value = false
      }, 5000)
      return
    }

    isQueueProcessing.value = true
    const item = downloadQueue.value[0]
    currentQueueTrack.value = item

    const nextMap = new Map(trackStatusMap.value)
    nextMap.set(item.url, 'downloading')
    trackStatusMap.value = nextMap

    try {
      const res = await ingestionApi.quickImport({
        url: item.url,
        title: item.title,
        artist: item.artist,
        duration: item.duration,
        cover_url: item.cover_url,
        genre: item.genre,
        tags: item.tags,
        is_drm_preview: item.is_drm_preview,
        add_to_library: true,
        preview_only: false,
      })

      const trackObj = res.data?.track
      if (trackObj && item.rawTrack) {
        item.rawTrack.in_library = true
        item.rawTrack.already_in_tg = true
        item.rawTrack.is_chunk = false
        item.rawTrack.track_id = trackObj.id
      }

      if (trackObj) {
        // Evict any stale preview chunk cache from IndexedDB and memory
        await deleteCachedAudio(trackObj.id).catch(() => {})
        deleteCachedUrl(trackObj.id)

        try {
          const playerStore = usePlayerStore()
          playerStore.patchTrack(trackObj.id, {
            is_chunk: false,
            duration: trackObj.duration,
            file_size: trackObj.file_size,
          })
        } catch (_) {}

        libraryStore.addTrackOptimistic(trackObj)
      } else {
        apiCache.invalidateRelated('track')
      }

      queueCompletedBatch.value++
      const mapDone = new Map(trackStatusMap.value)
      mapDone.set(item.url, 'completed')
      trackStatusMap.value = mapDone

      libraryStore.fetchTracks({ refresh: true, bypassCache: true })
    } catch (err) {
      console.error('[TasksStore] Quick import queue error:', err)
      queueFailedBatch.value++
      const mapFail = new Map(trackStatusMap.value)
      mapFail.set(item.url, 'failed')
      trackStatusMap.value = mapFail

      const errorMsg = err.response?.data?.detail || err.message || 'Ошибка загрузки'
      uiStore.toast?.error('Ошибка импорта', `${item.title}: ${errorMsg}`)
    } finally {
      // Remove finished item from queue
      downloadQueue.value.shift()
      currentQueueTrack.value = null

      // Delay 300ms before next item
      setTimeout(() => {
        processNextQueueItem()
      }, 300)
    }
  }

  const cancelQueue = () => {
    downloadQueue.value = []
    currentQueueTrack.value = null
    isQueueProcessing.value = false
    queueVisible.value = false
    if (queueDismissTimer) {
      clearTimeout(queueDismissTimer)
      queueDismissTimer = null
    }
    uiStore.toast?.info('Очередь отменена', 'Оставшиеся загрузки отменены')
  }

  // ══════════════════════════════════════════════════════════════════════
  // Long-Running Ingestion Jobs (Spotify Exportify & SoundCloud Full)
  // ══════════════════════════════════════════════════════════════════════

  /**
   * Register a new job and begin background polling
   */
  const registerJob = (job, meta = {}) => {
    if (!job || !job.id) return

    // Clone to ensure reactivity
    const nextJobs = new Map(jobs.value)
    nextJobs.set(job.id, job)
    jobs.value = nextJobs

    const nextMetas = new Map(jobMetas.value)
    nextMetas.set(job.id, {
      type: meta.type || 'exportify',
      title: job.title || meta.title || 'Импорт медиатеки',
      ...meta,
    })
    jobMetas.value = nextMetas

    if (!showImportModal.value && !showExportifyModal.value) {
      const nextMin = new Set(minimizedJobIds.value)
      nextMin.add(job.id)
      minimizedJobIds.value = nextMin
    }

    if (meta.type === 'exportify') {
      currentExportifyJob.value = job
    } else if (meta.type === 'import') {
      currentImportJob.value = job
    }

    startPolling(job.id)
  }

  /**
   * Start polling job status
   */
  const startPolling = (jobId) => {
    stopPolling(jobId)

    const timer = setInterval(async () => {
      try {
        const res = await ingestionApi.getJob(jobId)
        const updated = res.data

        const nextJobs = new Map(jobs.value)
        nextJobs.set(jobId, updated)
        jobs.value = nextJobs

        // Sync with active modal ref
        const meta = jobMetas.value.get(jobId)
        if (meta?.type === 'exportify' && currentExportifyJob.value?.id === jobId) {
          currentExportifyJob.value = updated
        } else if (meta?.type === 'import' && currentImportJob.value?.id === jobId) {
          currentImportJob.value = updated
        }

        if (['completed', 'failed', 'cancelled'].includes(updated.status)) {
          stopPolling(jobId)

          if (updated.status === 'completed') {
            apiCache.invalidateRelated('track')
            libraryStore.fetchTracks({ refresh: true, bypassCache: true })
            libraryStore.fetchPlaylists(true)

            uiStore.toast?.success(
              'Импорт завершён!',
              `Успешно обработано треков: ${updated.processed_tracks}`
            )
          } else if (updated.status === 'failed') {
            uiStore.toast?.error('Ошибка импорта', updated.error_message || 'Не удалось завершить задачу')
          }

          // Auto-remove finished job from minimized widget after 6 seconds
          setTimeout(() => {
            const nextMin = new Set(minimizedJobIds.value)
            if (nextMin.has(jobId)) {
              nextMin.delete(jobId)
              minimizedJobIds.value = nextMin
            }
            const nextJobs = new Map(jobs.value)
            nextJobs.delete(jobId)
            jobs.value = nextJobs
          }, 6000)
        }
      } catch (err) {
        console.error(`[TasksStore] Polling error for job ${jobId}:`, err)
      }
    }, 1500)

    pollTimers.set(jobId, timer)
  }

  /**
   * Stop polling for a specific job
   */
  const stopPolling = (jobId) => {
    if (pollTimers.has(jobId)) {
      clearInterval(pollTimers.get(jobId))
      pollTimers.delete(jobId)
    }
  }

  /**
   * Minimize an active job into floating widget
   */
  const minimizeJob = (jobId) => {
    if (!jobId) return

    const nextMin = new Set(minimizedJobIds.value)
    nextMin.add(jobId)
    minimizedJobIds.value = nextMin

    const meta = jobMetas.value.get(jobId)
    if (meta?.type === 'exportify') {
      showExportifyModal.value = false
    } else if (meta?.type === 'import') {
      showImportModal.value = false
    }

    uiStore.toast?.info(
      'Процесс свернут',
      'Импорт продолжается в фоне. Прогресс доступен в меню профиля'
    )
  }

  /**
   * Restore a minimized job back into its full modal
   */
  const restoreJob = (jobId) => {
    if (!jobId) return

    if (jobId === 'quick-download-queue') {
      uiStore.toast?.info(
        'Очередь добавления',
        `В очереди: ${downloadQueue.value.length} треков. Загрузка продолжается в фоне.`
      )
      return
    }

    const nextMin = new Set(minimizedJobIds.value)
    nextMin.delete(jobId)
    minimizedJobIds.value = nextMin

    const job = jobs.value.get(jobId)
    const meta = jobMetas.value.get(jobId)

    if (meta?.type === 'exportify') {
      currentExportifyJob.value = job
      showExportifyModal.value = true
    } else if (meta?.type === 'import') {
      currentImportJob.value = job
      showImportModal.value = true
    }
  }

  /**
   * Cancel an ongoing job
   */
  const cancelJob = async (jobId) => {
    if (!jobId) return

    if (jobId === 'quick-download-queue') {
      cancelQueue()
      return
    }

    try {
      await ingestionApi.cancelJob(jobId)
      stopPolling(jobId)

      const job = jobs.value.get(jobId)
      if (job) {
        const nextJobs = new Map(jobs.value)
        nextJobs.set(jobId, { ...job, status: 'cancelled' })
        jobs.value = nextJobs
      }

      const nextMin = new Set(minimizedJobIds.value)
      nextMin.delete(jobId)
      minimizedJobIds.value = nextMin

      uiStore.toast?.info('Отменено', 'Импорт был отменен')
    } catch (err) {
      console.error('[TasksStore] Cancel job error:', err)
    }
  }

  /**
   * Modal opening shortcuts
   */
  const openExportifyModal = async (options = {}) => {
    const { useAuthStore } = await import('./auth')
    const authStore = useAuthStore()
    if (!authStore.requireChannel('импорта музыки')) {
      return
    }
    exportifyModalOptions.value = options || {}
    showExportifyModal.value = true
  }

  const closeExportifyModal = () => {
    // If active job is in progress, minimize it instead of killing!
    if (currentExportifyJob.value && currentExportifyJob.value.status === 'in_progress') {
      minimizeJob(currentExportifyJob.value.id)
      return
    }
    showExportifyModal.value = false
    exportifyModalOptions.value = {}
    currentExportifyJob.value = null
  }

  const openImportModal = async () => {
    const { useAuthStore } = await import('./auth')
    const authStore = useAuthStore()
    if (!authStore.requireChannel('импорта музыки')) {
      return
    }
    showImportModal.value = true
  }

  const closeImportModal = () => {
    if (currentImportJob.value && currentImportJob.value.status === 'in_progress') {
      minimizeJob(currentImportJob.value.id)
      return
    }
    showImportModal.value = false
    currentImportJob.value = null
  }

  /**
   * List of all currently minimized active tasks (modal jobs + quick queue)
   */
  /**
   * List of all currently minimized active tasks (modal jobs + quick queue)
   */
  const activeMinimizedJobs = computed(() => {
    const list = []
    const processedIds = new Set()

    // 1. Minimized modal jobs + active background jobs
    for (const [jobId, job] of jobs.value.entries()) {
      if (processedIds.has(jobId)) continue

      const isExportifyModalOpen = showExportifyModal.value && currentExportifyJob.value?.id === jobId
      const isImportModalOpen = showImportModal.value && currentImportJob.value?.id === jobId

      // If the modal is currently open in foreground, don't show as a minimized card in floating widget
      if (isExportifyModalOpen || isImportModalOpen) {
        continue
      }

      const isMinimized = minimizedJobIds.value.has(jobId)
      const isActive = job.status === 'in_progress' || job.status === 'pending'

      if (isMinimized || isActive) {
        processedIds.add(jobId)
        const meta = jobMetas.value.get(jobId) || {
          type: job.provider_name === 'spotify' ? 'exportify' : 'import',
          title: job.title || 'Импорт музыки',
        }
        list.push({ job, meta })
      }
    }

    // 2. Live quick-download queue
    if (queueVisible.value) {
      const total = Math.max(queueTotalBatch.value, queueCompletedBatch.value + queueFailedBatch.value)
      const done = queueCompletedBatch.value + queueFailedBatch.value
      const isFinished = !isQueueProcessing.value && downloadQueue.value.length === 0
      const pct = total > 0 ? Math.round((done / total) * 100) : 100
      const cur = currentQueueTrack.value

      let stepText = ''
      let currentTrackName = ''
      if (isFinished) {
        stepText = `Все треки успешно добавлены (${queueCompletedBatch.value})`
      } else if (cur) {
        currentTrackName = `${cur.artist} — ${cur.title}`
        const pendingCount = downloadQueue.value.length - 1
        stepText = pendingCount > 0
          ? `Скачивание аудио • ещё ${pendingCount} в очереди`
          : 'Скачивание и добавление в Telegram...'
      } else {
        stepText = 'Обработка очереди...'
      }

      list.push({
        job: {
          id: 'quick-download-queue',
          title: total > 1 ? `Добавление треков (${done}/${total})` : 'Добавление в медиатеку',
          status: isFinished ? 'completed' : 'in_progress',
          processed_tracks: done,
          total_tracks: total,
          progress_percent: pct,
          current_track_title: currentTrackName,
          current_step: stepText,
          download_percent: null,
        },
        meta: {
          type: cur?.source === 'spotify' ? 'exportify' : 'import',
          title: 'Очередь загрузки',
          isQueue: true,
        },
      })
    }

    return list
  })

  const hasActiveImports = computed(() => activeMinimizedJobs.value.length > 0)

  const isImportingInProgress = computed(() => {
    return activeMinimizedJobs.value.some(item => item.job?.status === 'in_progress')
  })

  const overallProgress = computed(() => {
    const active = activeMinimizedJobs.value
    if (active.length === 0) return 0
    const inProgress = active.filter(item => item.job?.status === 'in_progress')
    const list = inProgress.length > 0 ? inProgress : active
    const total = list.reduce((acc, item) => acc + (Number(item.job?.progress_percent) || 0), 0)
    return Math.min(100, Math.max(0, Math.round(total / list.length)))
  })

  /**
   * Check for any active import jobs on the server
   */
  const checkRecentJobs = async () => {
    if (!authStore.isAuthenticated) return
    try {
      const res = await ingestionApi.getRecent()
      const recentList = res.data || []
      for (const job of recentList) {
        if (job.status === 'in_progress' || job.status === 'pending') {
          if (!jobs.value.has(job.id) || !pollTimers.has(job.id)) {
            registerJob(job, {
              type: job.provider_name === 'spotify' ? 'exportify' : 'import',
              title: job.title || 'Импорт музыки',
            })
          } else {
            const nextJobs = new Map(jobs.value)
            nextJobs.set(job.id, job)
            jobs.value = nextJobs
          }
        }
      }
    } catch (e) {
      // Ignore network errors
    }
  }

  return {
    jobs,
    jobMetas,
    minimizedJobIds,
    showExportifyModal,
    exportifyModalOptions,
    showImportModal,
    currentExportifyJob,
    currentImportJob,
    activeMinimizedJobs,
    hasActiveImports,
    isImportingInProgress,
    overallProgress,
    downloadQueue,
    isQueueProcessing,
    currentQueueTrack,
    queueTotalBatch,
    queueCompletedBatch,
    registerJob,
    minimizeJob,
    restoreJob,
    cancelJob,
    openExportifyModal,
    closeExportifyModal,
    openImportModal,
    closeImportModal,
    enqueueTrack,
    cancelQueue,
    isTrackQueued,
    isTrackDownloading,
    isTrackCompleted,
    checkRecentJobs,
  }
})
