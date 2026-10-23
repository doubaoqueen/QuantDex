<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api.js'

const props = defineProps({ account: String, label: String })
const el = ref(null)
let chart = null
let timer = null
const onResize = () => chart?.resize()

async function render() {
  try {
    const d = await api.get(`/api/nav_history?account=${props.account}`)
    const rows = d.rows || []
    if (!rows.length || !el.value) return
    const dates = rows.map(r => r.date)
    const base = rows[0].total_nav
    const bbase = rows.find(r => r.benchmark_nav != null)?.benchmark_nav
    const nav = rows.map(r => +(r.total_nav / base).toFixed(4))
    const bench = rows.map(r => (r.benchmark_nav != null && bbase) ? +(r.benchmark_nav / bbase).toFixed(4) : null)
    // 回撤序列
    let peak = -Infinity
    const dd = nav.map(v => { peak = Math.max(peak, v); return +((v / peak - 1) * 100).toFixed(2) })

    const option = {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,.96)', borderColor: '#e2e8f0', textStyle: { color: '#334155', fontSize: 12 } },
      legend: { data: ['策略净值', '沪深300（归一）', '回撤'], top: 0, textStyle: { fontSize: 12, color: '#64748b' } },
      grid: [
        { left: 56, right: 16, top: 30, height: '52%' },
        { left: 56, right: 16, top: '76%', height: '16%' }
      ],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
        { type: 'category', data: dates, gridIndex: 1, axisLabel: { fontSize: 10, color: '#94a3b8' } }
      ],
      yAxis: [
        { gridIndex: 0, scale: true, axisLabel: { fontSize: 10, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#eef2f7' } } },
        { gridIndex: 1, max: 0, axisLabel: { fontSize: 10, color: '#94a3b8', formatter: v => v + '%' }, splitLine: { lineStyle: { color: '#eef2f7' } } }
      ],
      series: [
        { name: '策略净值', type: 'line', data: nav, showSymbol: false, lineStyle: { width: 1.6 },
          itemStyle: { color: '#dc2626' }, areaStyle: { color: 'rgba(220,38,38,0.12)' } },
        { name: '沪深300（归一）', type: 'line', data: bench, showSymbol: false, lineStyle: { width: 1.2 }, itemStyle: { color: '#64748b' } },
        { name: '回撤', type: 'line', data: dd, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false,
          lineStyle: { width: 1 }, itemStyle: { color: '#0f766e' }, areaStyle: { color: 'rgba(15,118,110,0.15)' } }
      ]
    }
    chart.setOption(option, true)
  } catch { /* 静默：接口不可用 */ }
}

onMounted(async () => {
  await nextTick()
  chart = echarts.init(el.value)
  render()
  timer = setInterval(render, 30000)
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('resize', onResize)
  chart?.dispose()
})
watch(() => props.account, render)
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-1 flex items-center justify-between">
      <span class="text-sm font-semibold">净值曲线 <span class="ml-1 text-xs font-normal text-slate-400">{{ label }} vs 沪深300</span></span>
    </div>
    <div ref="el" class="h-[340px] w-full"></div>
  </div>
</template>
