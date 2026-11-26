<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api.js'

const emit = defineEmits(['back'])
const items = ref([])
const sel = ref(null)          // 当前选中产物的全文 JSON
const selName = ref('')
const chartEl = ref(null)
const detailTop = ref(null)    // D-107：窄屏选中后平滑滚动锚点
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
  if (!s) return 'bg-slate-100 text-slate-600'
  if (s.includes('provisional') || s.includes('待验')) return 'bg-amber-100 text-amber-800'
  if (s.includes('reject') || s.includes('closed') || s.includes('否决') || s.includes('全灭') || s.includes('关闭'))
    return 'bg-emerald-100 text-emerald-800'
  if (s.includes('pass') || s.includes('open') || s.includes('通过') || s.includes('触发'))
    return 'bg-rose-100 text-rose-800'
  return 'bg-slate-100 text-slate-600'
}

async function openItem(it) {
  selName.value = `${it.src} · ${it.name}`
  try { sel.value = await api.get(`/api/research_detail?name=${encodeURIComponent(it.file)}`) }
  catch { sel.value = { error: '加载失败' } }
  chartDone = false
  await nextTick()          // 等 v-if="sel" 的 DOM 先挂载，chartEl 才存在（D-102 修复：空图 bug）
  drawChart()
  // D-107 体感修复：窄屏（上下堆叠布局）选中档案后，详情在列表下方很远——
  // 平滑滚到详情顶部，让"点选 → 看到数据"即时成立。桌面端左栏 sticky 自滚动，无需此步。
  if (window.matchMedia('(max-width: 767px)').matches && detailTop.value) {
    detailTop.value.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
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
    yAxis: { type: 'category', data: keys, axisLabel: { color: '#64748b', fontSize: 10, width: 140, overflow: 'truncate' } },
    series: [{ type: 'bar', data: keys.map(k => metrics[k]),
               label: { show: true, position: 'right', color: '#334155', fontSize: 10,
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
  <div class="mx-auto max-w-6xl pb-16 text-slate-700">
    <h1 class="text-xl font-bold text-slate-900">研究室 · 实验档案库</h1>
    <p class="mb-4 mt-1 text-xs text-slate-500">
      {{ items.length }} 份存档自动发现（实验存档 + 研究结果）——新研究脚本只要落盘 JSON 就出现在这里，无需任何前端改动。
    </p>

    <div class="grid grid-cols-1 gap-5 md:grid-cols-[320px_1fr]">
      <!-- 左：档案列表。桌面端 sticky + 自身滚动（长列表不再把详情顶出视口），
           窄屏堆叠时选中后由 openItem 平滑滚到详情 -->
      <div class="space-y-1.5 md:sticky md:top-[76px] md:max-h-[calc(100vh-96px)] md:self-start md:overflow-y-auto md:pr-1">
        <button v-for="it in items" :key="it.id" @click="openItem(it)"
                class="w-full rounded-xl border p-2.5 text-left transition-all duration-150"
                :class="selName.includes(it.name)
                  ? 'border-sky-500 bg-sky-50/70 shadow-sm'
                  : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-sky-300 hover:shadow-sm'">
          <div class="flex items-center justify-between gap-2">
            <span class="truncate text-xs font-semibold text-slate-700">{{ it.name }}</span>
            <span v-if="it.verdict" class="qd-chip" :class="verdictClass(it.verdict)">{{ it.verdict }}</span>
          </div>
          <div class="mt-1 text-[10px] text-slate-500">{{ it.src }} · {{ it.date }}</div>
          <div v-if="it.note" class="mt-1 truncate text-[10px] text-slate-500">{{ it.note }}</div>
        </button>
      </div>

      <!-- 右：详情渲染 -->
      <div v-if="sel" ref="detailTop" class="space-y-4">
        <div class="qd-card p-4">
          <div class="qd-title mb-2">{{ selName }}</div>
          <div v-if="sel.verdict" class="mb-3">
            <span class="qd-chip px-3 py-1 text-xs" :class="verdictClass(sel.verdict)">裁决：{{ sel.verdict }}</span>
          </div>
          <div ref="chartEl" class="h-64 w-full"></div>
        </div>
        <div class="qd-card p-4">
          <div class="qd-title mb-2">原始 JSON（树形）</div>
          <pre class="max-h-[500px] overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-50 p-3 text-[11px] leading-5 text-slate-600">{{ JSON.stringify(sel, null, 2) }}</pre>
        </div>
      </div>
      <div v-else class="qd-card p-6 text-sm text-slate-400">← 左侧选择一份存档查看详情</div>
    </div>
  </div>
</template>
