import { ref } from 'vue'

const showModal = ref(false)
const guideMode = ref('default') // 'minecraft' | 'default'
const stepIndex = ref(0)

const STORAGE_KEY_DEFAULT = 'tg_guide_default_v1'
const STORAGE_KEY_MINECRAFT = 'tg_guide_minecraft_v1'

export function useWelcomeGuide() {
  const hasSeenGuide = (mode = 'default') => {
    try {
      const key = mode === 'minecraft' ? STORAGE_KEY_MINECRAFT : STORAGE_KEY_DEFAULT
      return localStorage.getItem(key) === 'true'
    } catch {
      return false
    }
  }

  const openGuide = (mode = 'default') => {
    guideMode.value = mode
    stepIndex.value = 0
    showModal.value = true
  }

  const closeGuide = () => {
    showModal.value = false
    try {
      const key = guideMode.value === 'minecraft' ? STORAGE_KEY_MINECRAFT : STORAGE_KEY_DEFAULT
      localStorage.setItem(key, 'true')
    } catch {
      // ignore
    }
  }

  const setStep = (idx) => {
    stepIndex.value = idx
  }

  const nextStep = (maxSteps = 4) => {
    if (stepIndex.value < maxSteps - 1) {
      stepIndex.value++
    } else {
      closeGuide()
    }
  }

  const prevStep = () => {
    if (stepIndex.value > 0) {
      stepIndex.value--
    }
  }

  return {
    showModal,
    guideMode,
    stepIndex,
    hasSeenGuide,
    openGuide,
    closeGuide,
    setStep,
    nextStep,
    prevStep,
  }
}
