<script setup>
import { ref, nextTick, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'
import { api, fmt } from '../api.js'

// 全局共享「点标的 → 近一年走势」弹窗（2026-11-14，自 D-052 的持仓私有版抽出）。
// 由 App.vue 挂载一次并提供 provide('kline').open(symbol, ctx)，任何面板可调用。
// req = { symbol, account, ctx? }：ctx 为持仓行对象（有股数/盈亏/权重），其他来源不传。
const props = defineProps({ req: Object })
const emit = defineEmits(['close'])

const detail = ref(null)
const chartEl = ref(null)
const loading = ref(false)
const derr = ref('')
let chart = null

async function load() {
  const q = props.req
  if (!q || !q.symbol) return
  detail.value = null
  derr.value = ''
  loading.value = true
  try {
    detail.value = await api.get(`/api/kline?symbol=${q.symbol}&account=${q.account || 'large'}&days=250`)
    if (detail.value && detail.value.err) derr.value = detail.value.err
  } catch (e) {
    derr.value = `加载失败：${e.message}`
  } finally {
    loading.value = false
  }
  await nextTick()
  draw()
}

watch(() => props.req, (q) => { if (q) load() })

function close() { emit('close') }

function draw() {
  const d = detail.value
  if (!d || d.err || !d.dates || !chartEl.value) return
  const since = d.held_since
  const idx = since ? d.dates.indexOf(since) : -1
  chart?.dispose()
  chart = echarts.init(chartEl.value, null, { renderer: 'canvas', height: 300 })
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,.96)',
               borderColor: '#e2e8f0', textStyle: { color: '#334155', fontSize: 12 } },
    grid: { left: 58, right: 16, top: 24, bottom: 24 },
    xAxis: { type: 'category', data: d.dates,
             axisLabel: { fontSize: 9.5, color: '#94a3b8', interval: Math.ceil(d.dates.length / 8) } },
    yAxis: { type: 'value', scale: true, name: '价格', nameTextStyle: { fontSize: 10, color: '#94a3b8' },
             axisLabel: { fontSize: 10, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#eef2f7' } } },
    series: [{
      name: '收盘价', type: 'line', data: d.close, showSymbol: false,
      lineStyle: { width: 1.6 }, itemStyle: { color: '#0ea5e9' },
      areaStyle: { color: 'rgba(14,165,233,0.08)' },
      markArea: idx >= 0 ? {
        silent: true, itemStyle: { color: 'rgba(220,38,38,0.06)' },
        label: { show: true, position: 'insideTop', color: '#be123c', fontSize: 10, formatter: '持有中' },
        data: [[{ xAxis: d.dates[idx] }, { xAxis: d.dates[d.dates.length - 1] }]]
      } : undefined
    }]
  }, true)
}

function onKey(e) { if (e.key === 'Escape') close() }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => { window.removeEventListener('keydown', onKey); chart?.dispose(); chart = null })
</script>

<template>
  <!-- Teleport 到 body：卡片有 backdrop-blur 会创建新的包含块，fixed 定位在里面会跑偏（D-052 教训） -->
  <Teleport to="body">
    <div v-if="req" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
         @click.self="close">
      <div class="w-full max-w-3xl rounded-xl bg-white p-4 shadow-2xl">
        <div class="mb-2 flex flex-wrap items-baseline gap-2">
          <span class="text-base font-bold">{{ req.name || req.ctx?.name || detail?.name || req.symbol }}</span>
          <span class="text-xs text-slate-400">{{ req.symbol }}<template v-if="detail && detail.industry"> · {{ detail.industry }}</template></span>
          <span v-if="req.ctx" class="text-xs text-slate-500">
            持有 {{ fmt.n(req.ctx.shares, 0) }} 股 · 现价 {{ fmt.n(req.ctx.close) }} ·
            昨日盈亏 <b :class="fmt.signCls(req.ctx.pnl)">{{ fmt.n(req.ctx.pnl, 0) }}</b> ·
            权重 {{ (req.ctx.weight * 100).toFixed(1) }}%
          </span>
          <span v-else-if="detail && detail.last" class="text-xs text-slate-500">现价 {{ fmt.n(detail.last) }}</span>
          <span v-if="detail && detail.held_since" class="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] text-rose-700">
            持有自 {{ detail.held_since }}
          </span>
          <button class="ml-auto rounded-lg bg-slate-100 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-200"
                  @click="close">关闭</button>
        </div>
        <div v-if="loading" class="py-16 text-center text-sm text-slate-400">
          <span class="live-dot">●</span> 加载走势…
        </div>
        <div v-else-if="derr" class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{{ derr }}</div>
        <template v-else-if="detail">
          <div ref="chartEl" class="h-[300px] w-full"></div>
          <div class="mt-1 text-[11px] text-slate-400">
            近 {{ detail.dates.length }} 个交易日收盘价（前复权）<template v-if="detail.held_since"> · 浅红区间 = 当前持有期</template>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>
