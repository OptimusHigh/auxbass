<template>
  <div v-if="show" class="mc-quota-bar">
    <div class="mc-quota-header">
      <span class="mc-quota-icon">🎵</span>
      <span class="mc-quota-label">Слоты пластинок</span>
      <span class="mc-quota-count" :class="countClass">{{ current }} / {{ max }}</span>
      <span class="mc-quota-rank">
        <span class="rank-dot" :style="{ background: rankColor }"></span>
        {{ rankDisplay }}
      </span>
    </div>
    <div class="mc-quota-track">
      <div
        class="mc-quota-fill"
        :style="{ width: fillPercent + '%', background: fillColor }"
        :class="{ 'fill-pulse': isFull }"
      ></div>
    </div>
    <div v-if="isFull" class="mc-quota-hint">
      Лимит исчерпан. Повысьте ранг на сервере GanjaCraft через&nbsp;<code>/ranks</code>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  current:     { type: Number, default: 0 },
  max:         { type: Number, default: 5 },
  rank:        { type: String, default: 'noob' },
  rankDisplay: { type: String, default: 'Новичок' },
  show:        { type: Boolean, default: true },
})

const fillPercent = computed(() => Math.min(100, (props.current / Math.max(1, props.max)) * 100))
const isFull = computed(() => props.current >= props.max)

const fillColor = computed(() => {
  const p = fillPercent.value
  if (p >= 100) return 'var(--c-error, #f44336)'
  if (p >= 80)  return '#ff9800'
  return 'var(--c-accent, #1db954)'
})

const countClass = computed(() => ({
  'count-full':    isFull.value,
  'count-warning': fillPercent.value >= 80 && !isFull.value,
}))

const rankColorMap = {
  noob:    '#9ca3af',
  player:  '#1db954',
  veteran: '#fbbf24',
  pro:     '#f97316',
  elite:   '#ef4444',
  legend:  '#a855f7',
}
const rankColor = computed(() => rankColorMap[props.rank] || '#9ca3af')
</script>

<style scoped>
.mc-quota-bar {
  margin: 10px 0 4px;
  padding: 10px 14px 8px;
  border-radius: var(--r-md, 12px);
  background: var(--c-bg-2, #1a1a1a);
  box-shadow: 3px 3px 8px var(--sh-dark, rgba(0,0,0,.45)),
              -2px -2px 5px var(--sh-light, rgba(255,255,255,.04));
  border: 1px solid rgba(255,255,255,.03);
}

.mc-quota-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: 13px;
}

.mc-quota-icon { font-size: 15px; }

.mc-quota-label {
  flex: 1;
  color: var(--c-text-2, #b0b0b0);
  font-weight: 500;
}

.mc-quota-count {
  font-weight: 700;
  color: var(--c-text-1, #fff);
  font-variant-numeric: tabular-nums;
  transition: color .2s;
}
.mc-quota-count.count-full    { color: #f44336; }
.mc-quota-count.count-warning { color: #ff9800; }

.mc-quota-rank {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--c-text-2, #b0b0b0);
}
.rank-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 0 5px currentColor;
}

/* Inset track */
.mc-quota-track {
  height: 6px;
  border-radius: 99px;
  background: var(--c-bg-0, #0d0d0d);
  box-shadow: inset 2px 2px 4px rgba(0,0,0,.6),
              inset -1px -1px 2px rgba(255,255,255,.03);
  overflow: hidden;
}

.mc-quota-fill {
  height: 100%;
  border-radius: 99px;
  transition: width .35s ease, background .3s ease;
  box-shadow: 0 0 8px rgba(29,185,84,.35);
}
.mc-quota-fill.fill-pulse {
  animation: pulse-red 1.4s ease-in-out infinite;
}
@keyframes pulse-red {
  0%, 100% { box-shadow: 0 0 6px rgba(244,67,54,.4); }
  50%       { box-shadow: 0 0 14px rgba(244,67,54,.8); }
}

.mc-quota-hint {
  margin-top: 7px;
  font-size: 11px;
  color: var(--c-text-2, #b0b0b0);
  line-height: 1.4;
}
.mc-quota-hint code {
  color: var(--c-accent, #1db954);
  font-size: 11px;
}
</style>
