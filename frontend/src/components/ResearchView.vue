<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api.js'

const emit = defineEmits(['back'])
const items = ref([])
const sel = ref(null)          // 当前选中产物的全文 JSON
const selName = ref('')
const chartEl = ref(null)
let chart = null
let chartDone = false

onMounted(async () => {
  try { items.value = (await api.get('/api/research')).items || [] } catch { items.value = [] }
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => { window.removeEventListener('keydown', onKey); if (chart) chart.dispose() })
const onKey = (e) => { if (e.key === 'Escape') emit('back') }

const verdictClass = (v) => {
  const s = (v || '').toLowerCase()
  if (!s) return 'bg-slate-700 text-slate-300'
  if (s.includes('provisional') || s.includes('待验')) return 'bg-amber-900/60 text-amber-200'
  if (s.includes('reject') || s.includes('closed') || s.includes('否决') || s.includes('全灭') || s.includes('关闭'))
    return 'bg-emerald-900/60 text-emerald-200'
  if (s.includes('pass') || s.includes('open') || s.includes('通过') || s.includes('触发'))
    return 'bg-rose-900/60 text-rose-200'
  return 'bg-slate-700 text-slate-300'
}

async function openItem(it) {
  selName.value = `${it.src} · ${it.name}`
  try { sel.value = await api.get(`/api/research_detail?name=${encodeURIComponent(it.file)}`) }
  catch { sel.value = { error: '加载失败' } }
  chartDone = false
  drawChart()
}

function drawChart() {
  if (chartDone || !chartEl.value || !sel.value) return
  const metrics = flatMetrics(sel.value)
  const keys = Object.keys(metrics)
  if (keys.length < 2) return
  chart = echarts.init(chartEl.value)
  chart.setOption({
    grid: { left: 150, right: 30, top: 20, bottom: 60 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: keys, axisLabel: { color: '#94a3b8', fontSize: 10, width: 140, overflow: 'truncate' } },
    series: [{ type: 'bar', data: keys.map(k => metrics[k]),
               label: { show: true, position: 'right', color: '#e2e8f0', fontSize: 10,
                        formatter: (p) => p.value.toFixed(4) },
               itemStyle: { color: (p) => (p.value >= 0 ? '#f87171' : '#34d399') } }],
    tooltip: {},
    backgroundColor: 'transparent'
  })
  chartDone = true
}

// 递归收集 JSON 里所有"数值叶子"（键路径 → 值），供指标条形图
function flatMetrics(obj, pre = '', depth = 0, acc = {}) {
  if (depth > 4) return acc
  for (const [k, v] of Object.entries(obj)) {
    const key = pre ? `${pre}·${k}` : k
    if (typeof v === 'number' && Number.isFinite(v)) acc[key] = v
    else if (v && typeof v === 'object' && !Array.isArray(v)) flatMetrics(v, key, depth + 1, acc)
  }
  return acc
}

const isTable = (v) => Array.isArray(v) && v.length > 0 && typeof v[0] === 'object'
const scalarEntries = (obj) => Object.entries(obj || {}).filter(([, v]) => typeof v !== 'object')
</script>

<template>
  <div class="fixed inset-0 z-40 overflow-auto bg-slate-900 text-slate-200">
    <div class="sticky top-0 z-20 -mx-0 mb-4 flex h-14 items-center justify-between border-b border-slate-800 bg-slate-900/95 px-6 backdrop-blur">
      <div class="text-xs text-slate-500">QuantDex 研究室 · 每一份检验的存档与裁决（自动发现，零页面成本）</div>
      <button @click="emit('back')"
              class="rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs text-slate-200 transition hover:border-sky-400 hover:text-sky-300">
        ← 返回控制台（Esc）
      </button>
    </div>

    <div class="mx-auto max-w-6xl px-6 pb-16">
      <h1 class="text-xl font-bold text-white">研究室 · 实验档案库</h1>
      <p class="mb-4 mt-1 text-xs text-slate-400">
        {{ items.length }} 份存档自动发现（实验存档 + 研究结果）——新研究脚本只要落盘 JSON 就出现在这里，无需任何前端改动。
      </p>

      <div class="grid grid-cols-1 gap-4 md:grid-cols-[320px_1fr]">
        <!-- 左：档案列表 -->
        <div class="space-y-1.5">
          <button v-for="it in items" :key="it.id" @click="openItem(it)"
                  class="w-full rounded-lg border p-2.5 text-left transition"
                  :class="selName.includes(it.name) ? 'border-sky-500 bg-slate-800' : 'border-slate-800 bg-slate-800/40 hover:border-slate-600'">
            <div class="flex items-center justify-between gap-2">
              <span class="truncate text-xs font-semibold text-slate-200">{{ it.name }}</span>
              <span v-if="it.verdict" class="rounded-full px-2 py-0.5 text-[10px]"
                    :class="verdictClass(it.verdict)">{{ it.verdict }}</span>
            </div>
            <div class="mt-1 text-[10px] text-slate-500">{{ it.src }} · {{ it.date }}</div>
            <div v-if="it.note" class="mt-1 truncate text-[10px] text-slate-400">{{ it.note }}</div>
          </button>
        </div>

        <!-- 右：详情渲染 -->
        <div v-if="sel" class="space-y-4">
          <div class="rounded-xl border border-slate-700 bg-slate-800/60 p-3">
            <div class="mb-1 text-xs font-semibold text-sky-300">{{ selName }}</div>
            <div v-if="sel.verdict" class="mb-2">
              <span class="rounded-full px-3 py-1 text-xs font-bold"
                    :class="verdictClass(sel.verdict)">裁决：{{ sel.verdict }}</span>
            </div>
            <div ref="chartEl" class="h-64 w-full"></div>
          </div>
          <div class="rounded-xl border border-slate-700 bg-slate-800/60 p-3">
            <div class="mb-2 text-xs font-semibold text-slate-400">原始 JSON（树形）</div>
            <pre class="max-h-[500px] overflow-auto whitespace-pre-wrap break-all text-[11px] leading-5 text-slate-300">{{ JSON.stringify(sel, null, 2) }}</pre>
          </div>
        </div>
        <div v-else class="text-sm text-slate-500">← 左侧选择一份存档查看详情</div>
      </div>
    </div>
  </div>
</template>
