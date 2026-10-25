<template>
  <div v-if="show" class="mc-quota-panel">
    <!-- Header: Disc icon, Title, Guide button, Rank Badge -->
    <div class="quota-header">
      <div class="quota-title-row">
        <span class="quota-icon-well">
          <Disc :size="14" class="quota-icon-disc" />
        </span>
        <span class="quota-label">Слоты пластинок</span>
        <button 
          type="button" 
          class="quota-guide-btn" 
          title="Как это работает?" 
          @click="$emit('openGuide')"
        >
          <HelpCircle :size="12" />
          <span>Гайд</span>
        </button>
      </div>

      <div style="display: flex; align-items: center; gap: 6px;">
        <span v-if="maxDurationDisplay" class="quota-duration-chip" title="Максимальная длительность трека для вашего ранга">
          ⏱ до {{ maxDurationDisplay }}
        </span>
        <div class="quota-rank-chip" :title="`Ранг: ${rankDisplay}`">
          <span class="rank-led" :style="{ background: rankColor, boxShadow: `0 0 6px ${rankColor}` }"></span>
          <span class="rank-name">{{ rankDisplay }}</span>
        </div>
      </div>
    </div>

    <!-- Gauge Info Row -->
    <div class="quota-info-row">
      <div class="quota-counter" :class="countClass">
        <span class="count-current">{{ current }}</span>
        <span class="count-sep">/</span>
        <span class="count-max">{{ max }}</span>
      </div>
      <span class="quota-status-text" :class="countClass">
        {{ statusText }}
      </span>
    </div>

    <!-- Neumorphic Inset LED Track -->
    <div class="quota-led-track">
      <div
        class="quota-led-fill"
        :style="{ width: fillPercent + '%', background: barGradient }"
        :class="{ 'fill-full-pulse': isFull }"
      >
        <div class="quota-led-glow" :style="{ background: barGlowColor }"></div>
      </div>
    </div>

    <!-- Hint on limit reach -->
    <div v-if="isFull" class="quota-limit-alert">
      Лимит исчерпан. Повысьте ранг на сервере через <code>/ranks</code>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Disc, HelpCircle } from 'lucide-vue-next'

const props = defineProps({
  current:            { type: Number, default: 0 },
  max:                { type: Number, default: 5 },
  rank:               { type: String, default: 'noob' },
  rankDisplay:        { type: String, default: 'Новичок' },
  maxDurationDisplay: { type: String, default: '' },
  show:               { type: Boolean, default: true },
})

defineEmits(['openGuide'])

const fillPercent = computed(() => Math.min(100, (props.current / Math.max(1, props.max)) * 100))
const isFull = computed(() => props.current >= props.max)
const remaining = computed(() => Math.max(0, props.max - props.current))

const countClass = computed(() => ({
  'is-full': isFull.value,
  'is-warning': fillPercent.value >= 80 && !isFull.value,
}))

const statusText = computed(() => {
  if (isFull.value) return 'Заполнено'
  if (remaining.value <= 3) return `Осталось: ${remaining.value}`
  return `${Math.round(fillPercent.value)}%`
})

const barGradient = computed(() => {
  const p = fillPercent.value
  if (p >= 100) return 'linear-gradient(90deg, #d32f2f 0%, #f44336 100%)'
  if (p >= 80)  return 'linear-gradient(90deg, #d97706 0%, #f59e0b 100%)'
  return 'linear-gradient(90deg, #169c46 0%, #1db954 60%, #22c55e 100%)'
})

const barGlowColor = computed(() => {
  const p = fillPercent.value
  if (p >= 100) return 'rgba(244, 67, 54, 0.5)'
  if (p >= 80)  return 'rgba(245, 158, 11, 0.45)'
  return 'rgba(29, 185, 84, 0.45)'
})

const rankColorMap = {
  noob:    '#9ca3af',
  player:  '#1db954',
  veteran: '#38bdf8',
  pro:     '#fbbf24',
  elite:   '#f97316',
  legend:  '#c084fc',
}
const rankColor = computed(() => rankColorMap[props.rank] || '#9ca3af')
</script>

<style scoped>
.mc-quota-panel {
  margin: 10px 0 6px;
  padding: 12px 14px;
  border-radius: var(--r-lg, 16px);
  background: linear-gradient(145deg, var(--c-bg-2, #1a1a1a) 0%, #151515 100%);
  box-shadow: 
    4px 4px 12px var(--sh-dark, rgba(0, 0, 0, 0.72)),
    -2px -2px 6px var(--sh-light, rgba(255, 255, 255, 0.04));
  border: 1px solid rgba(255, 255, 255, 0.035);
  max-width: 380px;
}

.quota-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.quota-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.quota-icon-well {
  width: 24px;
  height: 24px;
  border-radius: var(--r-sm, 8px);
  background: var(--c-bg-1, #121212);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 
    inset 1px 1px 3px rgba(0, 0, 0, 0.6),
    inset -1px -1px 2px rgba(255, 255, 255, 0.03);
  color: var(--c-accent, #1db954);
}

.quota-icon-disc {
  flex-shrink: 0;
}

.quota-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-1, #ffffff);
  letter-spacing: -0.01em;
}

.quota-guide-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--c-bg-2, #1a1a1a);
  border: 1px solid rgba(255, 255, 255, 0.06);
  color: var(--c-text-2, #b0b0b0);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  box-shadow: 2px 2px 5px var(--sh-dark, rgba(0, 0, 0, 0.5)), -1px -1px 3px var(--sh-light, rgba(255, 255, 255, 0.03));
}

.quota-guide-btn:hover {
  color: var(--c-text-1, #ffffff);
  border-color: rgba(29, 185, 84, 0.35);
  box-shadow: 2px 2px 8px rgba(0, 0, 0, 0.7), 0 0 8px rgba(29, 185, 84, 0.2);
}

.quota-guide-btn:active {
  transform: scale(0.96);
  box-shadow: inset 1px 1px 3px rgba(0, 0, 0, 0.7);
}

.quota-duration-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--c-bg-1, #121212);
  border: 1px solid rgba(29, 185, 84, 0.25);
  box-shadow: inset 1px 1px 3px rgba(0, 0, 0, 0.6);
  font-size: 11px;
  font-weight: 600;
  color: var(--c-accent, #1db954);
}

.quota-rank-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--c-bg-1, #121212);
  border: 1px solid rgba(255, 255, 255, 0.04);
  box-shadow: inset 1px 1px 3px rgba(0, 0, 0, 0.6);
}

.rank-led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.rank-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--c-text-2, #b0b0b0);
  text-transform: capitalize;
}

.quota-info-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 6px;
}

.quota-counter {
  display: flex;
  align-items: baseline;
  gap: 3px;
  font-variant-numeric: tabular-nums;
}

.count-current {
  font-size: 16px;
  font-weight: 700;
  color: var(--c-text-1, #ffffff);
  line-height: 1;
  transition: color 0.2s ease;
}

.quota-counter.is-full .count-current {
  color: var(--c-error, #f44336);
}

.quota-counter.is-warning .count-current {
  color: #ff9800;
}

.count-sep {
  font-size: 12px;
  color: var(--c-text-3, #808080);
  font-weight: 500;
}

.count-max {
  font-size: 12px;
  color: var(--c-text-2, #b0b0b0);
  font-weight: 600;
}

.quota-status-text {
  font-size: 11px;
  font-weight: 500;
  color: var(--c-text-3, #808080);
}

.quota-status-text.is-full {
  color: var(--c-error, #f44336);
  font-weight: 600;
}

.quota-status-text.is-warning {
  color: #ff9800;
}

/* Inset LED Well Track */
.quota-led-track {
  height: 8px;
  border-radius: 999px;
  background: var(--c-bg-0, #0d0d0d);
  box-shadow: 
    inset 2px 2px 4px rgba(0, 0, 0, 0.8),
    inset -1px -1px 2px rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(0, 0, 0, 0.5);
  overflow: hidden;
  position: relative;
}

.quota-led-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1), background 0.3s ease;
  position: relative;
  box-shadow: 0 0 10px rgba(29, 185, 84, 0.35);
}

.quota-led-glow {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 14px;
  filter: blur(3px);
  opacity: 0.8;
}

.quota-led-fill.fill-full-pulse {
  animation: quota-pulse-red 1.6s ease-in-out infinite;
}

@keyframes quota-pulse-red {
  0%, 100% { box-shadow: 0 0 6px rgba(244, 67, 54, 0.4); }
  50%       { box-shadow: 0 0 14px rgba(244, 67, 54, 0.85); }
}

.quota-limit-alert {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.035);
  font-size: 11px;
  color: var(--c-text-2, #b0b0b0);
  line-height: 1.35;
}

.quota-limit-alert code {
  color: var(--c-accent, #1db954);
  background: rgba(29, 185, 84, 0.1);
  padding: 1px 4px;
  border-radius: 4px;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
}
</style>
