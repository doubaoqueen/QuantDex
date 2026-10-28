<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import { api, fmt } from '../api.js'

const props = defineProps({ account: String })
const data = ref(null)
const elMain = ref(null)
let chart = null
let timer = null
const onResize = () => chart?.resize()

async function load() {
  try {
    data.value = await api.get(`/api/backtest?account=${props.account}`)
  } catch (e) {
    data.value = { error: String(e) }
  }
  await nextTick()
  if (data.value && data.value.error) {
    setTimeout(load, 5000)      // 接口出错（多为瞬时故障）：稍后自动重试
    return
  }
  render()
}

function option(c) {
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,.96)', borderColor: '#e2e8f0',
               textStyle: { color: '#334155', fontSize: 12 } },
    legend: { data: ['固定 Top50/20', 'walk-forward（每年重选 N）', '沪深300'],
              top: 0, textStyle: { fontSize: 11, color: '#64748b' } },
    grid: { left: 52, right: 14, top: 28, bottom: 22 },
    xAxis: { type: 'category', data: c.dates,
             axisLabel: { fontSize: 9.5, color: '#94a3b8', interval: Math.ceil(c.dates.length / 8) } },
    yAxis: { scale: true, axisLabel: { fontSize: 10, color: '#94a3b8' },
             splitLine: { lineStyle: { color: '#eef2f7' } } },
    series: [
      { name: '固定 Top50/20', type: 'line', data: c.fixed, showSymbol: false,
        lineStyle: { width: 1.5 }, itemStyle: { color: '#dc2626' } },
      { name: 'walk-forward（每年重选 N）', type: 'line', data: c.walk, showSymbol: false,
        lineStyle: { width: 1.8 }, itemStyle: { color: '#2563eb' } },
      { name: '沪深300', type: 'line', data: c.bench, showSymbol: false,
        lineStyle: { width: 1.1 }, itemStyle: { color: '#94a3b8' } }
    ]
  }
}

function mount() {
  const c = data.value?.cmp
  if (!c) return
  const dom = elMain.value
  if (!dom) { setTimeout(mount, 60); return }          // 容器还没挂上
  try {
    if (chart && chart.getDom() !== dom) { chart.dispose(); chart = null }
    if (!chart) chart = echarts.init(dom, null, { renderer: 'canvas', height: 300 })
    chart.setOption(option(c), true)
    requestAnimationFrame(() => {
      try { chart.resize({ height: 300 }) } catch { /* ignore */ }
    })
  } catch (e) {
    console.error('[BacktestPanel] 图表渲染失败:', e)
  }
}

function render() {
  if (!data.value || data.value.error) return
  mount()
}

const nText = (n) => (Array.isArray(n) ? `${n[0]} 只 / ${n[1]} 日调仓` : '—')

onMounted(() => {
  load()
  timer = setInterval(load, 60000)
  window.addEventListener('resize', onResize)
})
watch(() => props.account, load)
onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('resize', onResize)
  chart?.dispose()
})
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <span class="text-sm font-semibold">回测 · 全过程同轴对比</span>
      <span v-if="data && data.strategy" class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">{{ data.strategy }}</span>
      <span v-if="data && data.cmp" class="text-[11px] text-slate-400">
        {{ data.cmp.start }} ~ {{ data.cmp.end }} · 两条策略线从同一天 1.0 起算 · 保真口径（扣费/涨跌停/停牌）
      </span>
      <span class="ml-auto text-[11px] text-amber-700">历史模拟，非实盘收益</span>
    </div>

    <div v-if="data && !data.error && data.cmp" class="space-y-3">
      <div ref="elMain" class="h-[300px] w-full"></div>

      <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div class="rounded-lg border border-rose-100 bg-rose-50/40 p-2.5">
          <div class="mb-1 text-xs font-semibold text-rose-700">固定 Top50 / 20 日（参数是看全样本定的，相当于"作弊"）</div>
          <div class="grid grid-cols-4 gap-1.5 text-center text-[11px]">
            <div><div class="text-slate-500">总收益</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.cmp.fixed_stats.total)">{{ fmt.pct(data.cmp.fixed_stats.total) }}</div></div>
            <div><div class="text-slate-500">年化</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.cmp.fixed_stats.ann)">{{ fmt.pct(data.cmp.fixed_stats.ann) }}</div></div>
            <div><div class="text-slate-500">超额</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.cmp.fixed_stats.excess)">{{ fmt.pct(data.cmp.fixed_stats.excess) }}</div></div>
            <div><div class="text-slate-500">最大回撤</div>
              <div class="tabular font-semibold text-emerald-700">{{ fmt.pct(data.cmp.fixed_stats.mdd) }}</div></div>
          </div>
        </div>
        <div class="rounded-lg border border-sky-100 bg-sky-50/40 p-2.5">
          <div class="mb-1 text-xs font-semibold text-sky-700">walk-forward（每年只用之前的数据重选 N，可实盘复现）</div>
          <div class="grid grid-cols-4 gap-1.5 text-center text-[11px]">
            <div><div class="text-slate-500">总收益</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.cmp.walk_stats.total)">{{ fmt.pct(data.cmp.walk_stats.total) }}</div></div>
            <div><div class="text-slate-500">年化</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.cmp.walk_stats.ann)">{{ fmt.pct(data.cmp.walk_stats.ann) }}</div></div>
            <div><div class="text-slate-500">超额</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.cmp.walk_stats.excess)">{{ fmt.pct(data.cmp.walk_stats.excess) }}</div></div>
            <div><div class="text-slate-500">最大回撤</div>
              <div class="tabular font-semibold text-emerald-700">{{ fmt.pct(data.cmp.walk_stats.mdd) }}</div></div>
          </div>
        </div>
      </div>

      <!-- 逐年：当年用的 N + 两条线当年表现 -->
      <div>
        <div class="mb-1 text-xs font-medium text-slate-500">
          逐年对照（样本外那几年，<b>每年年初</b>只用之前的数据重选"选几只 / 几天调仓"）
        </div>
        <table class="tabular w-full text-sm">
          <thead class="bg-slate-50 text-xs text-slate-500">
            <tr>
              <th class="px-2 py-1.5 text-left">年份</th>
              <th class="text-left">当年采用 N</th>
              <th>固定 50/20</th>
              <th>walk-forward</th>
              <th>差（WF−固定）</th>
              <th>沪深300</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in (data.yearly_cmp || [])" :key="r.year" class="border-t border-slate-100">
              <td class="px-2 py-1">{{ r.year }}</td>
              <td class="text-left text-[11px]"
                  :class="r.n_used ? 'text-sky-700' : 'text-slate-400'">
                {{ r.n_used ? nText(r.n_used) : '训练段（2018）' }}
              </td>
              <td class="text-right" :class="fmt.signCls(r.fixed)">{{ fmt.pct(r.fixed) }}</td>
              <td class="text-right font-medium" :class="fmt.signCls(r.walk)">{{ fmt.pct(r.walk) }}</td>
              <td class="text-right" :class="fmt.signCls(r.walk != null && r.fixed != null ? r.walk - r.fixed : null)">
                {{ fmt.pct(r.walk != null && r.fixed != null ? r.walk - r.fixed : null) }}
              </td>
              <td class="text-right" :class="fmt.signCls(r.bench)">{{ fmt.pct(r.bench) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="mt-1 text-[11px] leading-snug text-slate-400">
          已知局限：walk-forward 只重选"选几只 / 几天调仓"，<b>因子组合本身是看过全样本后定的</b>；
          D-042 的 FDR 多重检验里 46 个因子 q&lt;0.1 一个都没通过。2019 那一折的训练段只有 2018 一年，参数选择噪音大。
        </div>
      </div>
    </div>
    <div v-else-if="data && data.error" class="py-10 text-center text-sm text-slate-400">
      回测数据加载失败（{{ data.error }}）<span class="live-dot">· 自动重试中</span>
    </div>
    <div v-else class="py-10 text-center text-sm text-slate-400">
      暂无同轴对比数据——先运行 <code class="rounded bg-slate-100 px-1">scripts/05</code> 与
      <code class="rounded bg-slate-100 px-1">scripts/06</code>
    </div>
  </div>
</template>
