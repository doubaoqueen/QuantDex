<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { api, fmt } from '../api.js'

const data = ref(null)
const view = ref('full')            // full | oos
const el = ref(null)
let chart = null
let timer = null
const onResize = () => chart?.resize()

async function load() {
  const r = await api.get('/api/backtest').catch(e => ({ error: String(e) }))
  data.value = r
  await nextTick()               // 等 v-if 打开、图表容器挂载后再渲染
  if (r && r.error) {
    // 接口出错（多为旧版本页面/瞬时故障）：稍后自动重试
    setTimeout(load, 5000)
  } else {
    render()
  }
}

function render() {
  const d = data.value
  if (!d || d.error) return
  // 懒初始化：数据到位后 v-if 才渲染图表容器，此时才 init（避免对 null dom 初始化中断数据加载）
  if (!chart) {
    if (!el.value) return
    chart = echarts.init(el.value)
  }
  const sec = d[view.value]
  const s = sec.stats
  const opt = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,.96)', borderColor: '#e2e8f0', textStyle: { color: '#334155', fontSize: 12 } },
    legend: { data: ['策略（回测）', '沪深300'], top: 0, textStyle: { fontSize: 12, color: '#64748b' } },
    grid: { left: 56, right: 16, top: 30, bottom: 26 },
    xAxis: { type: 'category', data: sec.dates, axisLabel: { fontSize: 10, color: '#94a3b8' } },
    yAxis: { scale: true, axisLabel: { fontSize: 10, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#eef2f7' } } },
    series: [
      { name: '策略（回测）', type: 'line', data: sec.nav, showSymbol: false,
        lineStyle: { width: 1.6 }, itemStyle: { color: '#dc2626' },
        areaStyle: { color: 'rgba(220,38,38,0.10)' },
        markArea: view.value === 'full' && d.oos_start ? {
          silent: true, itemStyle: { color: 'rgba(59,130,246,0.07)' },
          label: { show: true, position: 'insideTop', color: '#3b82f6', fontSize: 11,
                   formatter: '样本外（walk-forward）' },
          data: [[{ xAxis: d.oos_start }, { xAxis: sec.dates[sec.dates.length - 1] }]]
        } : undefined },
      { name: '沪深300', type: 'line', data: sec.bench, showSymbol: false,
        lineStyle: { width: 1.2 }, itemStyle: { color: '#64748b' } }
    ]
  }
  chart.setOption(opt, true)
}

onMounted(() => {
  load()                          // 数据先行；图表在数据到位、容器挂载后懒初始化
  timer = setInterval(load, 60000)
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('resize', onResize)
  chart?.dispose()
})
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <span class="text-sm font-semibold">回测 · 样本外</span>
      <div class="flex overflow-hidden rounded-lg border border-slate-300 text-xs">
        <button :class="view==='full' ? 'bg-slate-800 text-white' : 'bg-white'" class="px-2.5 py-1"
                @click="view='full'; render()">全周期</button>
        <button :class="view==='oos' ? 'bg-slate-800 text-white' : 'bg-white'"
                class="border-l border-slate-300 px-2.5 py-1" @click="view='oos'; render()">样本外</button>
      </div>
      <span v-if="data && !data.error" class="text-xs text-slate-400">
        {{ data[view].stats.start }} ~ {{ data[view].stats.end }} · 保真口径（扣费/涨跌停/停牌）
      </span>
      <span class="ml-auto text-[11px] text-amber-700">历史模拟，非实盘收益</span>
    </div>

    <div v-if="data && !data.error">
      <!-- 指标卡 -->
      <div class="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <div class="rounded-lg bg-slate-50 p-2">
          <div class="text-[11px] text-slate-500">总收益</div>
          <div class="tabular text-lg font-bold" :class="fmt.signCls(data[view].stats.total)">{{ fmt.pct(data[view].stats.total) }}</div>
        </div>
        <div class="rounded-lg bg-slate-50 p-2">
          <div class="text-[11px] text-slate-500">年化</div>
          <div class="tabular text-lg font-bold" :class="fmt.signCls(data[view].stats.ann)">{{ fmt.pct(data[view].stats.ann) }}</div>
        </div>
        <div class="rounded-lg bg-slate-50 p-2">
          <div class="text-[11px] text-slate-500">最大回撤</div>
          <div class="tabular text-lg font-bold text-emerald-700">{{ fmt.pct(data[view].stats.mdd) }}</div>
        </div>
        <div class="rounded-lg bg-slate-50 p-2">
          <div class="text-[11px] text-slate-500">超额 vs 沪深300</div>
          <div class="tabular text-lg font-bold" :class="fmt.signCls(data[view].stats.excess)">{{ fmt.pct(data[view].stats.excess) }}</div>
        </div>
        <div class="rounded-lg bg-slate-50 p-2">
          <div class="text-[11px] text-slate-500">夏普</div>
          <div class="tabular text-lg font-bold">{{ fmt.n(data[view].stats.sharpe) }}</div>
        </div>
        <div class="rounded-lg bg-slate-50 p-2">
          <div class="text-[11px] text-slate-500">10 万本金变成</div>
          <div class="tabular text-lg font-bold">{{ fmt.n(100000 * (1 + data[view].stats.total), 0) }}</div>
        </div>
      </div>

      <div ref="el" class="h-[300px] w-full"></div>

      <!-- 分年度 + 折明细 -->
      <div class="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <div class="mb-1 text-xs font-medium text-slate-500">分年度收益（全周期口径）</div>
          <table class="tabular w-full text-sm">
            <thead class="bg-slate-50 text-xs text-slate-500">
              <tr><th class="px-2 py-1.5 text-left">年份</th><th>策略</th><th>沪深300</th><th>超额</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in data.yearly" :key="r.year" class="border-t border-slate-100">
                <td class="px-2 py-1">{{ r.year }}</td>
                <td class="text-right font-medium" :class="fmt.signCls(r.strategy)">{{ fmt.pct(r.strategy) }}</td>
                <td class="text-right" :class="fmt.signCls(r.bench)">{{ fmt.pct(r.bench) }}</td>
                <td class="text-right" :class="fmt.signCls(r.strategy != null && r.bench != null ? r.strategy - r.bench : null)">{{ fmt.pct(r.strategy != null && r.bench != null ? r.strategy - r.bench : null) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="data.folds">
          <div class="mb-1 text-xs font-medium text-slate-500">walk-forward 折明细（每年只用之前数据选参）</div>
          <table class="tabular w-full text-sm">
            <thead class="bg-slate-50 text-xs text-slate-500">
              <tr><th class="px-2 py-1.5 text-left">测试年</th><th>参数 TopN/调仓日</th><th>训练 IR</th><th>样本外 IR</th><th>样本外年化</th><th>样本外 MDD</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in data.folds" :key="f.test_year" class="border-t border-slate-100">
                <td class="px-2 py-1">{{ f.test_year }}</td>
                <td class="text-center">{{ Array.isArray(f.combo) ? f.combo.join('/') : f.combo }}</td>
                <td class="text-right">{{ fmt.n(f.train_ir) }}</td>
                <td class="text-right font-medium">{{ fmt.n(f.oos_ir) }}</td>
                <td class="text-right" :class="fmt.signCls(f.oos_ann)">{{ fmt.pct(f.oos_ann / 100) }}</td>
                <td class="text-right text-emerald-700">{{ fmt.pct(f.oos_mdd / 100) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="mt-1 text-[11px] text-slate-400">数据源：portfolio_results / walkforward_results（05/06 脚本产物，重跑后自动更新）</div>
        </div>
      </div>
    </div>
    <div v-else class="py-10 text-center text-sm text-slate-400">
      <template v-if="data && data.error">
        回测数据加载失败（{{ data.error }}）<span class="live-dot">· 自动重试中</span>
      </template>
      <template v-else>
        暂无回测数据——先运行 <code class="rounded bg-slate-100 px-1">scripts/05</code> 与
        <code class="rounded bg-slate-100 px-1">scripts/06</code> 生成结果
      </template>
    </div>
  </div>
</template>
