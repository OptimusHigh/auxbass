<template>
  <Teleport to="body">
    <Transition name="modal-fade">
      <div 
        v-if="showModal" 
        class="guide-modal-backdrop"
        @click.self="closeGuide"
      >
        <div class="guide-modal-container neu-card">
          <!-- Header -->
          <div class="guide-modal-header">
            <div class="guide-badge">
              <Gamepad2 v-if="guideMode === 'minecraft'" :size="14" class="guide-badge-icon" />
              <Headphones v-else :size="14" class="guide-badge-icon" />
              <span>{{ guideMode === 'minecraft' ? 'GanjaCraft Hub' : 'AuxBass Guide' }}</span>
            </div>
            
            <div class="guide-header-actions">
              <span class="guide-step-counter">{{ stepIndex + 1 }} / {{ activeSteps.length }}</span>
              <button class="guide-minimize-btn neu-btn-icon" @click="minimizeGuide" title="Свернуть гайд" aria-label="Свернуть">
                <Minus :size="16" />
              </button>
              <button class="guide-close-btn neu-btn-icon" @click="() => closeGuide(true)" title="Закрыть гайд" aria-label="Закрыть">
                <X :size="18" />
              </button>
            </div>
          </div>

          <!-- Interactive Station Stepper (Horizontal Neumorphic Tabs) -->
          <div class="guide-stepper-pills">
            <button 
              v-for="(step, idx) in activeSteps" 
              :key="step.id" 
              class="guide-pill-btn"
              :class="{ 
                active: idx === stepIndex, 
                completed: idx < stepIndex,
                'is-success': step.id === 'channel' && authStore.hasChannel 
              }"
              @click="setStep(idx)"
              :title="step.shortTitle"
            >
              <component :is="step.navIcon" :size="14" class="pill-icon" />
              <span class="pill-label">{{ step.shortTitle }}</span>
              <div class="pill-indicator"></div>
            </button>
          </div>

          <!-- Step Content Body (Animated Card) -->
          <Transition name="step-slide" mode="out-in">
            <div :key="stepIndex" class="guide-step-body">
              <!-- Visual Icon Stage -->
              <div class="guide-visual-stage">
                <div class="guide-glow-ring" :class="activeStep.themeClass"></div>
                <div class="guide-icon-wrapper neu-convex">
                  <component :is="activeStep.icon" :size="36" class="guide-main-icon" />
                </div>
              </div>

              <!-- Title & Desc -->
              <h3 class="guide-step-title">{{ activeStep.title }}</h3>
              <p class="guide-step-desc">{{ activeStep.desc }}</p>

              <!-- Live Status Bar (Contextual) -->
              <div v-if="activeStep.id === 'channel'" class="guide-status-box" :class="{ connected: authStore.hasChannel }">
                <div class="status-left">
                  <Check v-if="authStore.hasChannel" :size="16" class="status-icon success" />
                  <Radio v-else :size="16" class="status-icon warning" />
                  <span class="status-text">
                    {{ authStore.hasChannel ? `Бэкап активен: @${authStore.channelInfo?.channel_username || 'канал'}` : 'Канал не подключен (треки могут теряться)' }}
                  </span>
                </div>
                <span class="status-badge">{{ authStore.hasChannel ? 'Готово' : 'Шаг 1' }}</span>
              </div>

              <div v-else-if="activeStep.id === 'minecraft'" class="guide-status-box connected">
                <div class="status-left">
                  <Disc :size="16" class="status-icon mc" />
                  <span class="status-text">
                    Ранг: <strong>{{ userRankDisplay }}</strong> • Доступно слотов: <strong>{{ userMaxTracks }}</strong>
                  </span>
                </div>
                <span class="status-badge mc">GanjaCraft</span>
              </div>

              <!-- Action Steps List -->
              <div class="guide-bullet-box neu-concave">
                <div v-for="(item, i) in activeStep.bullets" :key="i" class="guide-bullet-item">
                  <span class="bullet-num">{{ i + 1 }}</span>
                  <span class="bullet-text">{{ item }}</span>
                </div>
              </div>

              <!-- Direct Action Button (Deep Action inside WebApp) -->
              <button 
                v-if="activeStep.action" 
                type="button" 
                class="guide-quick-action-btn"
                @click="handleQuickAction(activeStep.action)"
              >
                <component :is="activeStep.action.icon" :size="15" />
                <span>{{ activeStep.action.label }}</span>
                <ChevronRight :size="14" class="arrow" />
              </button>
            </div>
          </Transition>

          <!-- Footer Actions -->
          <div class="guide-modal-footer">
            <button 
              v-if="stepIndex > 0" 
              class="guide-back-btn neu-btn-subtle"
              @click="prevStep"
            >
              <ChevronLeft :size="18" />
              <span>Назад</span>
            </button>

            <button 
              v-if="stepIndex < activeSteps.length - 1" 
              class="guide-next-btn neu-btn-primary"
              @click="() => nextStep(activeSteps.length)"
            >
              <span>Далее</span>
              <ChevronRight :size="18" />
            </button>

            <button 
              v-else 
              class="guide-finish-btn neu-btn-primary glow"
              @click="handleFinish"
            >
              <component :is="guideMode === 'minecraft' ? Disc : Sparkles" :size="18" />
              <span>{{ guideMode === 'minecraft' ? 'К пластинкам' : 'Готово' }}</span>
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- Floating Minimized Guide Dock -->
    <Transition name="dock-pop">
      <div 
        v-if="isMinimized" 
        class="guide-floating-dock neu-card"
        @click="restoreGuide"
      >
        <div class="dock-pulse-led" :class="activeStep.themeClass"></div>
        <div class="dock-icon-wrapper neu-convex">
          <component :is="activeStep.navIcon" :size="14" class="dock-main-icon" />
        </div>
        <div class="dock-info">
          <span class="dock-badge">{{ guideMode === 'minecraft' ? 'GanjaCraft' : 'Гайд' }}</span>
          <span class="dock-step-name">{{ activeStep.shortTitle }} ({{ stepIndex + 1 }}/{{ activeSteps.length }})</span>
        </div>
        <div class="dock-controls" @click.stop>
          <button 
            class="dock-action-btn neu-btn-icon" 
            @click="restoreGuide" 
            title="Развернуть гайд"
            aria-label="Развернуть"
          >
            <Maximize2 :size="13" />
          </button>
          <button 
            v-if="stepIndex < activeSteps.length - 1"
            class="dock-action-btn neu-btn-icon" 
            @click="() => nextStep(activeSteps.length)" 
            title="Следующий шаг"
            aria-label="Далее"
          >
            <ChevronRight :size="14" />
          </button>
          <button 
            class="dock-action-btn neu-btn-icon close" 
            @click="() => closeGuide(true)" 
            title="Закрыть гайд"
            aria-label="Закрыть"
          >
            <X :size="13" />
          </button>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { 
  X, ChevronRight, ChevronLeft, Disc, Music, Sparkles, 
  Search, Radio, Headphones, Gamepad2, Send, 
  ArrowDownToLine, Zap, Check, ExternalLink, Settings, 
  Layers, HardDrive, Minus, Maximize2 
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useWelcomeGuide } from '@/composables/useWelcomeGuide'

const router = useRouter()
const authStore = useAuthStore()

const {
  showModal,
  isMinimized,
  guideMode,
  stepIndex,
  minimizeGuide,
  restoreGuide,
  closeGuide,
  setStep,
  nextStep,
  prevStep,
} = useWelcomeGuide()

const emit = defineEmits(['finish'])

const userRankDisplay = computed(() => {
  const r = authStore.user?.mc_rank || 'player'
  const map = {
    noob: 'Новичок',
    player: 'Игрок',
    veteran: 'Ветеран',
    pro: 'Про',
    elite: 'Элита',
    legend: 'Легенда'
  }
  return map[r] || r
})

const userMaxTracks = computed(() => {
  return authStore.user?.mc_max_tracks || 5
})

// Маршрут 1: Новый слушатель (Meloman Tour)
const defaultSteps = [
  {
    id: 'channel',
    shortTitle: 'Канал',
    navIcon: Radio,
    title: 'Канал для автобэкапа',
    desc: 'Вся музыка хранится в Telegram. Приватный канал создает личное безлимитное облако для твоих треков.',
    bullets: [
      'Создай приватный канал в Telegram',
      'Добавь нашего бота администратором',
      'Отправь в канал любой трек или команду /channel'
    ],
    icon: HardDrive,
    themeClass: 'theme-accent',
    action: {
      label: 'Настроить канал в профиле',
      icon: Settings,
      type: 'route',
      target: '/settings#channel'
    }
  },
  {
    id: 'search',
    shortTitle: 'Поиск',
    navIcon: Search,
    title: 'Поиск и скачивание ссылок',
    desc: 'Универсальный загрузчик: вставляй ссылки на треки или находи музыку во встроенных каталогах.',
    bullets: [
      'Вставляй ссылки с YouTube, SoundCloud, Spotify и VK прямо в строку поиска',
      'Плеер распознает метаданные и обложку в высоком качестве',
      'Кнопка «В библиотеку» качает трек в фоновом режиме'
    ],
    icon: Search,
    themeClass: 'theme-cyan',
    action: {
      label: 'Открыть поиск музыки',
      icon: Search,
      type: 'route',
      target: '/search'
    }
  },
  {
    id: 'bot_import',
    shortTitle: 'Импорт',
    navIcon: Send,
    title: 'Импорт через Telegram-бота',
    desc: 'Быстрое пополнение медиатеки прямо из чата Telegram без входа в приложение.',
    bullets: [
      'Отправляй боту аудиофайлы (MP3, FLAC, WAV, OGG)',
      'Пересылай посты с музыкой из других каналов пачками',
      'Импортируй всю медиатеку файлом result.json из экспорта Telegram Desktop'
    ],
    icon: Send,
    themeClass: 'theme-purple',
    action: {
      label: 'Открыть диалог с ботом',
      icon: ExternalLink,
      type: 'bot'
    }
  },
  {
    id: 'playback',
    shortTitle: 'Звук',
    navIcon: Headphones,
    title: 'Hi-Fi звук и офлайн-кэш',
    desc: 'Музыка играет всегда: в фоне, при выключенном экране и без интернета.',
    bullets: [
      'Фоновый звук при сворачивании Telegram',
      'Автоматический кэш треков в память устройства для поездок в метро',
      'Эквалайзер, отображение текстов песен и смарт-очередь'
    ],
    icon: Headphones,
    themeClass: 'theme-accent',
    action: {
      label: 'Открыть медиатеку',
      icon: Layers,
      type: 'route',
      target: '/library'
    }
  },
  {
    id: 'minecraft',
    shortTitle: 'Minecraft',
    navIcon: Gamepad2,
    title: 'Пластинки на сервере GanjaCraft',
    desc: 'Связка с игровым сервером: слушай любимую музыку внутри Minecraft.',
    bullets: [
      'Плейлист «Minecraft Media» привязан к твоему нику',
      'В игре напиши команду /disc или возьми именную пластинку',
      'Твой трек услышат все игроки рядом через ванильный проигрыватель'
    ],
    icon: Disc,
    themeClass: 'theme-mc',
    action: {
      label: 'Перейти к пластинкам',
      icon: Disc,
      type: 'route_system_playlist'
    }
  }
]

// Маршрут 2: Игрок из Minecraft (Minecraft Player Tour)
const minecraftSteps = [
  {
    id: 'minecraft',
    shortTitle: 'Пластинки',
    navIcon: Disc,
    title: 'Твои пластинки в Minecraft',
    desc: 'Плейлист «Minecraft Media» автоматически компилируется в ресурспак сервера GanjaCraft.',
    bullets: [
      'Слоты пластинок зависят от твоего ранга на сервере (/ranks)',
      'Команда /disc в игре открывает коллекцию пластинок',
      'Звук слышен всем игрокам рядом с проигрывателем или бумбоксом'
    ],
    icon: Disc,
    themeClass: 'theme-mc',
    action: {
      label: 'Открыть плейлист пластинок',
      icon: Disc,
      type: 'route_system_playlist'
    }
  },
  {
    id: 'search',
    shortTitle: 'Поиск',
    navIcon: Search,
    title: 'Добавление треков по ссылкам',
    desc: 'Скачивай треки из любых сервисов в один клик прямо в Web App.',
    bullets: [
      'Вставь ссылку на YouTube, SoundCloud или VK в строку поиска',
      'Плеер сам скачает аудио и адаптирует формат под Minecraft',
      'Нажми «В библиотеку» и добавь трек в «Minecraft Media»'
    ],
    icon: Search,
    themeClass: 'theme-cyan',
    action: {
      label: 'Открыть поиск',
      icon: Search,
      type: 'route',
      target: '/search'
    }
  },
  {
    id: 'bot_import',
    shortTitle: 'Бот-фасттрек',
    navIcon: Send,
    title: 'Быстрая отправка файлов боту',
    desc: 'Если у тебя есть готовый аудиофайл — просто скинь его в диалог.',
    bullets: [
      'Скинь MP3 или FLAC боту сервера GanjaCraft или боту плеера',
      'Бот автоматически привяжет трек к твоему слоту пластинки',
      'Через 10 секунд пластинка появится на сервере'
    ],
    icon: Send,
    themeClass: 'theme-purple',
    action: {
      label: 'Открыть диалог с ботом',
      icon: ExternalLink,
      type: 'bot'
    }
  },
  {
    id: 'channel',
    shortTitle: 'Бэкап',
    navIcon: Radio,
    title: 'Канал для сохранности музыки',
    desc: 'Telegram удаляет временные файлы. Личный канал гарантирует, что пластинки не исчезнут.',
    bullets: [
      'Создай приватный канал и добавь туда нашего бота',
      'Бот будет сохранять твои пластинки в надежное облако',
      'Никаких потерь звука даже после обновлений'
    ],
    icon: HardDrive,
    themeClass: 'theme-accent',
    action: {
      label: 'Настроить канал бэкапа',
      icon: Settings,
      type: 'route',
      target: '/settings#channel'
    }
  },
  {
    id: 'playback',
    shortTitle: 'Плеер',
    navIcon: Headphones,
    title: 'Бонус: Полноценный Hi-Fi плеер',
    desc: 'AuxBass — это персональный стриминг без рекламы для телефона и ПК.',
    bullets: [
      'Слушай музыку в фоне при заблокированном экране телефона',
      'Сохраняй любимые альбомы в офлайн-кэш для поездок',
      'Создавай тематические плейлисты помимо игровых пластинок'
    ],
    icon: Headphones,
    themeClass: 'theme-accent',
    action: {
      label: 'Исследовать плеер',
      icon: Layers,
      type: 'route',
      target: '/library'
    }
  }
]

const activeSteps = computed(() => {
  return guideMode.value === 'minecraft' ? minecraftSteps : defaultSteps
})

const activeStep = computed(() => {
  return activeSteps.value[stepIndex.value] || activeSteps.value[0]
})

const navigateToSystemPlaylist = async () => {
  try {
    const { playlistsApi } = await import('@/api')
    let sys = null
    try {
      const resp = await playlistsApi.getManageAll({ bypassCache: true })
      const items = resp.data?.items || resp.data || []
      sys = items.find(pl => pl.is_system || pl.name === '🎮 Minecraft Media' || pl.name?.toLowerCase().includes('minecraft'))
    } catch {
      // fallback
    }
    if (!sys) {
      try {
        const resp2 = await playlistsApi.getAll({ include_subscribed: false }, { bypassCache: true })
        const items2 = resp2.data?.items || resp2.data || []
        sys = items2.find(pl => pl.is_system || pl.name === '🎮 Minecraft Media' || pl.name?.toLowerCase().includes('minecraft'))
      } catch {
        // fallback
      }
    }
    if (sys && sys.id) {
      router.push(`/playlist/${sys.id}`)
      return
    }
  } catch (e) {
    console.warn('Could not locate system playlist:', e)
  }
  router.push('/library?tab=playlists')
}

const handleQuickAction = async (action) => {
  if (!action) return

  // Minimize the guide into floating dock so user doesn't lose context
  minimizeGuide()

  if (action.type === 'route') {
    router.push(action.target)
  } else if (action.type === 'route_system_playlist') {
    await navigateToSystemPlaylist()
  } else if (action.type === 'bot') {
    try {
      if (window.Telegram?.WebApp?.openTelegramLink) {
        window.Telegram.WebApp.openTelegramLink('https://t.me/GanjaCrafterBot')
      } else {
        window.open('https://t.me/GanjaCrafterBot', '_blank')
      }
    } catch {
      window.open('https://t.me/GanjaCrafterBot', '_blank')
    }
  }
}

const handleFinish = async () => {
  const isMc = guideMode.value === 'minecraft'
  closeGuide(true)
  emit('finish', guideMode.value)
  if (isMc) {
    await navigateToSystemPlaylist()
  }
}
</script>

<style scoped>
.guide-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.82);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  z-index: 10020;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.guide-modal-container {
  background: var(--c-bg-1, #121212);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: var(--r-xl, 24px);
  width: 100%;
  max-width: 450px;
  padding: 20px 18px 18px;
  box-shadow: 12px 12px 36px var(--sh-dark, rgba(0,0,0,0.85)),
              -6px -6px 18px var(--sh-light, rgba(255,255,255,0.02));
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.guide-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.guide-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--c-bg-2, #1A1A1A);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0,0,0,0.5)),
              inset -1px -1px 2px var(--sh-inset-light, rgba(255,255,255,0.02));
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--c-text-2, #B0B0B0);
}

.guide-badge-icon {
  color: var(--c-accent, #1db954);
}

.guide-header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.guide-step-counter {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-text-2, #808080);
  font-variant-numeric: tabular-nums;
}

.guide-close-btn {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--c-bg-2, #1A1A1A);
  color: var(--c-text-2, #B0B0B0);
  border: none;
  cursor: pointer;
  box-shadow: 2px 2px 5px var(--sh-dark, rgba(0,0,0,0.5)),
              -1px -1px 3px var(--sh-light, rgba(255,255,255,0.03));
  transition: all 0.15s ease;
}

.guide-close-btn:active {
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0,0,0,0.6));
  transform: scale(0.95);
}

/* Stepper Pills Navigation */
.guide-stepper-pills {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
  margin-bottom: 18px;
  background: var(--c-bg-0, #0D0D0D);
  padding: 4px;
  border-radius: var(--r-md, 12px);
  box-shadow: inset 2px 2px 5px rgba(0, 0, 0, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.03);
}

.guide-pill-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 6px 2px;
  border-radius: 8px;
  background: transparent;
  border: none;
  color: var(--c-text-2, #757575);
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
}

.guide-pill-btn .pill-label {
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}

.guide-pill-btn.active {
  background: var(--c-bg-2, #1A1A1A);
  color: var(--c-accent, #1db954);
  box-shadow: 2px 2px 6px rgba(0, 0, 0, 0.6),
              -1px -1px 3px rgba(255, 255, 255, 0.04);
}

.guide-pill-btn.completed {
  color: var(--c-text-1, #E0E0E0);
}

.guide-pill-btn.is-success .pill-icon {
  color: var(--c-accent, #1db954);
}

/* Step Body */
.guide-step-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  min-height: 290px;
}

.guide-visual-stage {
  position: relative;
  width: 76px;
  height: 76px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.guide-glow-ring {
  position: absolute;
  inset: -8px;
  border-radius: 50%;
  filter: blur(14px);
  opacity: 0.35;
  transition: all 0.3s ease;
}

.guide-glow-ring.theme-mc {
  background: radial-gradient(circle, #1db954 0%, #107c41 70%);
}
.guide-glow-ring.theme-accent {
  background: radial-gradient(circle, #1db954 0%, #00bcd4 70%);
}
.guide-glow-ring.theme-cyan {
  background: radial-gradient(circle, #00bcd4 0%, #0288d1 70%);
}
.guide-glow-ring.theme-purple {
  background: radial-gradient(circle, #a855f7 0%, #6366f1 70%);
}

.guide-icon-wrapper {
  position: relative;
  width: 66px;
  height: 66px;
  border-radius: 50%;
  background: var(--c-bg-2, #1A1A1A);
  border: 1px solid rgba(255, 255, 255, 0.05);
  box-shadow: 5px 5px 12px var(--sh-dark, rgba(0,0,0,0.6)),
              -3px -3px 8px var(--sh-light, rgba(255,255,255,0.03));
  display: flex;
  align-items: center;
  justify-content: center;
}

.guide-main-icon {
  color: var(--c-accent, #1db954);
}

.guide-step-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-text-1, #FFFFFF);
  margin-bottom: 6px;
  letter-spacing: -0.2px;
}

.guide-step-desc {
  font-size: 13px;
  line-height: 1.45;
  color: var(--c-text-2, #A0A0A0);
  margin-bottom: 12px;
  padding: 0 4px;
}

/* Status Box */
.guide-status-box {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 12px;
  border-radius: var(--r-md, 10px);
  background: rgba(255, 152, 0, 0.1);
  border: 1px solid rgba(255, 152, 0, 0.25);
  margin-bottom: 10px;
  font-size: 12px;
}

.guide-status-box.connected {
  background: rgba(29, 185, 84, 0.12);
  border-color: rgba(29, 185, 84, 0.3);
}

.status-left {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--c-text-1, #E0E0E0);
  text-align: left;
}

.status-icon.success { color: var(--c-accent, #1db954); }
.status-icon.warning { color: #ff9800; }
.status-icon.mc { color: #00bcd4; }

.status-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: #B0B0B0;
}

.status-badge.mc {
  background: rgba(0, 188, 212, 0.18);
  color: #00bcd4;
}

/* Bullets Box */
.guide-bullet-box {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--r-md, 12px);
  background: var(--c-bg-0, #0D0D0D);
  border: 1px solid rgba(255, 255, 255, 0.03);
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0,0,0,0.5)),
              inset -1px -1px 2px var(--sh-inset-light, rgba(255,255,255,0.02));
  margin-bottom: 12px;
  text-align: left;
}

.guide-bullet-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  line-height: 1.4;
  color: #C0C0C0;
}

.bullet-num {
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: var(--c-bg-2, #1A1A1A);
  color: var(--c-accent, #1db954);
  font-size: 10px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
}

/* Quick Action Button */
.guide-quick-action-btn {
  width: 100%;
  height: 38px;
  padding: 0 14px;
  border-radius: var(--r-md, 10px);
  background: var(--c-bg-2, #1A1A1A);
  color: var(--c-accent, #1db954);
  border: 1px solid rgba(29, 185, 84, 0.25);
  box-shadow: 3px 3px 6px var(--sh-dark, rgba(0,0,0,0.4)),
              -1px -1px 3px var(--sh-light, rgba(255,255,255,0.02));
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.guide-quick-action-btn:active {
  box-shadow: inset 2px 2px 4px rgba(0, 0, 0, 0.5);
  transform: scale(0.98);
}

.guide-quick-action-btn .arrow {
  color: var(--c-text-2, #808080);
}

/* Footer Actions */
.guide-modal-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}

.guide-back-btn {
  height: 42px;
  padding: 0 14px;
  border-radius: var(--r-md, 12px);
  background: var(--c-bg-2, #1A1A1A);
  color: var(--c-text-2, #B0B0B0);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: 3px 3px 7px var(--sh-dark, rgba(0,0,0,0.5)),
              -2px -2px 5px var(--sh-light, rgba(255,255,255,0.02));
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.guide-back-btn:active {
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0,0,0,0.6));
  transform: scale(0.97);
}

.guide-next-btn, .guide-finish-btn {
  flex: 1;
  height: 42px;
  padding: 0 18px;
  border-radius: var(--r-md, 12px);
  background: linear-gradient(135deg, #1db954 0%, #158a3e 100%);
  color: #FFFFFF;
  border: none;
  font-size: 13px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  cursor: pointer;
  box-shadow: 4px 4px 10px rgba(0, 0, 0, 0.5),
              0 0 12px var(--c-accent-glow, rgba(29, 185, 84, 0.35));
  transition: all 0.15s ease;
}

.guide-next-btn:active, .guide-finish-btn:active {
  transform: scale(0.97);
  box-shadow: inset 2px 2px 5px rgba(0, 0, 0, 0.4);
}

.guide-finish-btn.glow {
  background: linear-gradient(135deg, #1db954 0%, #00bcd4 100%);
  box-shadow: 0 0 16px rgba(0, 188, 212, 0.4);
}

/* Floating Minimized Guide Dock */
.guide-floating-dock {
  position: fixed;
  bottom: calc(var(--player-height, 64px) + env(safe-area-inset-bottom, 0px) + 14px);
  right: 14px;
  z-index: var(--z-modal, 1200);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--c-bg-2, #1A1A1A);
  border-radius: var(--r-full, 9999px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.75),
              0 0 16px var(--c-accent-glow, rgba(29, 185, 84, 0.25));
  cursor: pointer;
  user-select: none;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1),
              box-shadow 0.2s ease,
              border-color 0.2s ease;
}

.guide-floating-dock:hover {
  transform: translateY(-2px);
  border-color: rgba(29, 185, 84, 0.3);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.85),
              0 0 22px var(--c-accent-glow, rgba(29, 185, 84, 0.35));
}

.guide-floating-dock:active {
  transform: scale(0.97);
}

.dock-pulse-led {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #1db954;
  box-shadow: 0 0 8px #1db954;
  animation: led-pulse 2s infinite ease-in-out;
  flex-shrink: 0;
}

.dock-pulse-led.theme-cyan {
  background: #00bcd4;
  box-shadow: 0 0 8px #00bcd4;
}

.dock-pulse-led.theme-purple {
  background: #b388ff;
  box-shadow: 0 0 8px #b388ff;
}

.dock-pulse-led.theme-mc {
  background: #1db954;
  box-shadow: 0 0 10px rgba(29, 185, 84, 0.8);
}

.dock-icon-wrapper {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--c-bg-3, #222222);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #FFFFFF;
  box-shadow: inset 1px 1px 2px rgba(255,255,255,0.06), 2px 2px 5px rgba(0,0,0,0.5);
  flex-shrink: 0;
}

.dock-info {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.dock-badge {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--c-accent, #1db954);
}

.dock-step-name {
  font-size: 12px;
  font-weight: 600;
  color: #FFFFFF;
  white-space: nowrap;
}

.dock-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: 4px;
}

.dock-action-btn {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--c-bg-3, #222222);
  border: 1px solid rgba(255, 255, 255, 0.05);
  color: var(--c-text-2, #B0B0B0);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
}

.dock-action-btn:hover {
  color: #FFFFFF;
  background: var(--c-bg-4, #2A2A2A);
  transform: scale(1.05);
}

.dock-action-btn:active {
  transform: scale(0.92);
  box-shadow: inset 1px 1px 3px rgba(0,0,0,0.6);
}

.dock-action-btn.close:hover {
  color: #ff5252;
}

/* Minimize button in header */
.guide-minimize-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--c-bg-2, #1A1A1A);
  border: 1px solid rgba(255, 255, 255, 0.04);
  color: var(--c-text-2, #B0B0B0);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
  box-shadow: 2px 2px 5px var(--sh-dark, rgba(0,0,0,0.5)), -1px -1px 3px var(--sh-light, rgba(255,255,255,0.03));
}

.guide-minimize-btn:hover {
  color: #FFFFFF;
  background: var(--c-bg-3, #222222);
}

.guide-minimize-btn:active {
  transform: scale(0.94);
  box-shadow: inset 2px 2px 4px rgba(0, 0, 0, 0.5);
}

/* Transitions */
.dock-pop-enter-active,
.dock-pop-leave-active {
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.dock-pop-enter-from,
.dock-pop-leave-to {
  opacity: 0;
  transform: translateY(18px) scale(0.92);
}

.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.22s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}

.step-slide-enter-active,
.step-slide-leave-active {
  transition: all 0.18s ease;
}
.step-slide-enter-from {
  opacity: 0;
  transform: translateX(14px);
}
.step-slide-leave-to {
  opacity: 0;
  transform: translateX(-14px);
}
</style>
