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
            
            <button class="guide-close-btn neu-btn-icon" @click="closeGuide" aria-label="Закрыть">
              <X :size="18" />
            </button>
          </div>

          <!-- Stepper Dots (Hi-Fi LED indicators) -->
          <div class="guide-stepper">
            <div 
              v-for="(step, idx) in activeSteps" 
              :key="idx" 
              class="guide-led-track"
              :class="{ active: idx <= stepIndex, current: idx === stepIndex }"
              @click="setStep(idx)"
            >
              <div class="guide-led-dot"></div>
            </div>
          </div>

          <!-- Step Content Body (Animated Card) -->
          <Transition name="step-slide" mode="out-in">
            <div :key="stepIndex" class="guide-step-body">
              <!-- Visual Icon Stage -->
              <div class="guide-visual-stage">
                <div class="guide-glow-ring" :class="activeStep.themeClass"></div>
                <div class="guide-icon-wrapper neu-convex">
                  <component :is="activeStep.icon" :size="38" class="guide-main-icon" />
                </div>
              </div>

              <!-- Texts -->
              <h3 class="guide-step-title">{{ activeStep.title }}</h3>
              <p class="guide-step-desc">{{ activeStep.desc }}</p>

              <!-- Highlight Tips Box -->
              <div v-if="activeStep.tip" class="guide-tip-box neu-concave">
                <Sparkles :size="16" class="tip-icon" />
                <span>{{ activeStep.tip }}</span>
              </div>
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
              <span>{{ guideMode === 'minecraft' ? 'К моим пластинкам' : 'Погнали!' }}</span>
            </button>
          </div>
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
  CloudUpload, Radio, Headphones, Gamepad2, ShieldCheck, 
  ArrowDownToLine, Zap 
} from 'lucide-vue-next'
import { useWelcomeGuide } from '@/composables/useWelcomeGuide'

const router = useRouter()
const {
  showModal,
  guideMode,
  stepIndex,
  closeGuide,
  setStep,
  nextStep,
  prevStep,
} = useWelcomeGuide()

const emit = defineEmits(['finish'])

const minecraftSteps = [
  {
    title: 'Музыкальный хаб GanjaCraft',
    desc: 'Твой игровой ранг автоматически синхронизирован с плеером. Все добавленные сюда треки превращаются в персональные пластинки прямо на сервере!',
    tip: 'Слоты для треков расширяются вместе с покупкой рангов на сервере (/ranks).',
    icon: Disc,
    themeClass: 'theme-mc',
  },
  {
    title: 'Как добавить свои треки',
    desc: 'Вставь ссылку на YouTube, SoundCloud или VK в поиск — плеер скачает песню сам. Также можно просто перетащить MP3/FLAC файл или скинуть его нашему Telegram-боту.',
    tip: 'Звук автоматически конвертируется в оптимальный формат без задержек в игре.',
    icon: CloudUpload,
    themeClass: 'theme-accent',
  },
  {
    title: 'Звучание в мире Minecraft',
    desc: 'В игре используй команду /disc или возьми свою именную пластинку. Ставь её в любой ванильный проигрыватель или бумбокс — трек услышат все игроки рядом!',
    tip: 'Обложки треков отображаются в высоком качестве на корпусах бумбоксов.',
    icon: Radio,
    themeClass: 'theme-cyan',
  },
  {
    title: 'Твой персональный стриминг',
    desc: 'AuxBass — это не только склад пластинок, но и полноценный Hi-Fi плеер. Слушай любимую музыку на телефоне в фоне без рекламы и сохраняй в оффлайн-кэш.',
    tip: 'Открывай любимые плейлисты в любое время прямо из Telegram.',
    icon: Headphones,
    themeClass: 'theme-purple',
  },
]

const defaultSteps = [
  {
    title: 'Добро пожаловать в AuxBass',
    desc: 'Твой персональный Hi-Fi аудиокомбайн в Telegram. Слушай музыку в высоком качестве без ограничений и навязчивой рекламы.',
    tip: 'Поддерживается фоновое воспроизведение при сворачивании приложения.',
    icon: Headphones,
    themeClass: 'theme-accent',
  },
  {
    title: 'Безлимитная медиатека',
    desc: 'Скачивай треки по ссылкам из YouTube, VK и SoundCloud в один клик. Пересылай аудиофайлы Telegram-боту — они сразу окажутся в библиотеке.',
    tip: 'Вся музыка хранится в облаке Telegram и не занимает место на телефоне.',
    icon: ArrowDownToLine,
    themeClass: 'theme-cyan',
  },
  {
    title: 'Hi-Fi звук и оффлайн-кэш',
    desc: 'Плеер автоматически кэширует треки во внутреннюю память. Слушай любимые альбомы даже без интернета — в метро, самолёте или за городом.',
    tip: 'Управляй эквалайзером и создавай смарт-плейлисты в пару тапов.',
    icon: Zap,
    themeClass: 'theme-purple',
  },
  {
    title: 'Интеграция с Minecraft',
    desc: 'Играешь на сервере GanjaCraft? Твои треки из плейлиста "Minecraft Media" автоматически появляются в игре в виде кастомных виниловых пластинок!',
    tip: 'На сервере напиши команду /disc для выбора своей музыки.',
    icon: Gamepad2,
    themeClass: 'theme-mc',
  },
]

const activeSteps = computed(() => {
  return guideMode.value === 'minecraft' ? minecraftSteps : defaultSteps
})

const activeStep = computed(() => {
  return activeSteps.value[stepIndex.value] || activeSteps.value[0]
})

const handleFinish = async () => {
  const isMc = guideMode.value === 'minecraft'
  closeGuide()
  emit('finish', guideMode.value)
  if (isMc) {
    try {
      const { playlistsApi } = await import('@/api')
      const resp = await playlistsApi.getAll()
      const items = resp.data?.items || resp.data || []
      const sys = items.find(pl => pl.is_system)
      if (sys) {
        router.push(`/playlist/${sys.id}`)
      }
    } catch (e) {
      // fallback
    }
  }
}
</script>

<style scoped>
.guide-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.78);
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
  max-width: 440px;
  padding: 24px 20px 20px;
  box-shadow: 10px 10px 30px var(--sh-dark, rgba(0,0,0,0.8)),
              -5px -5px 15px var(--sh-light, rgba(255,255,255,0.02));
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.guide-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
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

.guide-close-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--c-bg-2, #1A1A1A);
  color: var(--c-text-2, #B0B0B0);
  border: none;
  cursor: pointer;
  box-shadow: 3px 3px 6px var(--sh-dark, rgba(0,0,0,0.5)),
              -2px -2px 4px var(--sh-light, rgba(255,255,255,0.03));
  transition: all 0.15s ease;
}

.guide-close-btn:active {
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0,0,0,0.6)),
              inset -1px -1px 2px var(--sh-inset-light, rgba(255,255,255,0.04));
  transform: scale(0.95);
}

/* Stepper LED Indicators */
.guide-stepper {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 24px;
}

.guide-led-track {
  width: 28px;
  height: 6px;
  border-radius: 999px;
  background: var(--c-bg-0, #0D0D0D);
  box-shadow: inset 1px 1px 3px rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  cursor: pointer;
  transition: all 0.25s ease;
  padding: 1px;
}

.guide-led-dot {
  width: 100%;
  height: 100%;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  transition: all 0.25s ease;
}

.guide-led-track.active .guide-led-dot {
  background: rgba(29, 185, 84, 0.5);
}

.guide-led-track.current {
  width: 38px;
}

.guide-led-track.current .guide-led-dot {
  background: var(--c-accent, #1db954);
  box-shadow: 0 0 10px var(--c-accent-glow, rgba(29, 185, 84, 0.6));
}

/* Step Body */
.guide-step-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  min-height: 250px;
}

.guide-visual-stage {
  position: relative;
  width: 96px;
  height: 96px;
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.guide-glow-ring {
  position: absolute;
  inset: -10px;
  border-radius: 50%;
  filter: blur(16px);
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
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: var(--c-bg-2, #1A1A1A);
  border: 1px solid rgba(255, 255, 255, 0.05);
  box-shadow: 6px 6px 14px var(--sh-dark, rgba(0,0,0,0.6)),
              -4px -4px 10px var(--sh-light, rgba(255,255,255,0.03));
  display: flex;
  align-items: center;
  justify-content: center;
}

.guide-main-icon {
  color: var(--c-accent, #1db954);
}

.guide-step-title {
  font-size: 19px;
  font-weight: 700;
  color: var(--c-text-1, #FFFFFF);
  margin-bottom: 10px;
  letter-spacing: -0.2px;
}

.guide-step-desc {
  font-size: 14px;
  line-height: 1.5;
  color: var(--c-text-2, #B0B0B0);
  margin-bottom: 16px;
  padding: 0 8px;
}

.guide-tip-box {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--r-md, 12px);
  background: var(--c-bg-0, #0D0D0D);
  border: 1px solid rgba(255, 255, 255, 0.03);
  box-shadow: inset 2px 2px 4px var(--sh-inset-dark, rgba(0,0,0,0.5)),
              inset -1px -1px 2px var(--sh-inset-light, rgba(255,255,255,0.02));
  font-size: 12px;
  line-height: 1.4;
  color: #A0A0A0;
  text-align: left;
  width: 100%;
}

.tip-icon {
  color: var(--c-accent, #1db954);
  flex-shrink: 0;
  margin-top: 1px;
}

/* Footer Actions */
.guide-modal-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 20px;
}

.guide-back-btn {
  height: 44px;
  padding: 0 16px;
  border-radius: var(--r-md, 12px);
  background: var(--c-bg-2, #1A1A1A);
  color: var(--c-text-2, #B0B0B0);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: 4px 4px 8px var(--sh-dark, rgba(0,0,0,0.5)),
              -2px -2px 6px var(--sh-light, rgba(255,255,255,0.02));
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
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
  height: 44px;
  padding: 0 20px;
  border-radius: var(--r-md, 12px);
  background: linear-gradient(135deg, #1db954 0%, #158a3e 100%);
  color: #FFFFFF;
  border: none;
  font-size: 14px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
  box-shadow: 4px 4px 10px rgba(0, 0, 0, 0.5),
              0 0 14px var(--c-accent-glow, rgba(29, 185, 84, 0.35));
  transition: all 0.15s ease;
}

.guide-next-btn:active, .guide-finish-btn:active {
  transform: scale(0.97);
  box-shadow: inset 2px 2px 5px rgba(0, 0, 0, 0.4);
}

.guide-finish-btn.glow {
  background: linear-gradient(135deg, #1db954 0%, #00bcd4 100%);
  box-shadow: 0 0 18px rgba(0, 188, 212, 0.4);
}

/* Transitions */
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.25s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}

.step-slide-enter-active,
.step-slide-leave-active {
  transition: all 0.2s ease;
}
.step-slide-enter-from {
  opacity: 0;
  transform: translateX(16px);
}
.step-slide-leave-to {
  opacity: 0;
  transform: translateX(-16px);
}
</style>
