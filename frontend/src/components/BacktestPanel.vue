<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import { api, fmt } from '../api.js'

const props = defineProps({ account: String })
const data = ref(null)
const elIs = ref(null)
const elOos = ref(null)
let chartIs = null
let chartOos = null
let timer = null
const onResize = () => { chartIs?.resize(); chartOos?.resize() }

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

function option(sec) {
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,.96)', borderColor: '#e2e8f0',
               textStyle: { color: '#334155', fontSize: 12 } },
    legend: { data: ['策略', '沪深300'], top: 0, textStyle: { fontSize: 11, color: '#64748b' } },
    grid: { left: 50, right: 12, top: 26, bottom: 22 },
    xAxis: { type: 'category', data: sec.dates,
             axisLabel: { fontSize: 9.5, color: '#94a3b8', interval: Math.ceil(sec.dates.length / 7) } },
    yAxis: { scale: true, axisLabel: { fontSize: 10, color: '#94a3b8' },
             splitLine: { lineStyle: { color: '#eef2f7' } } },
    series: [
      { name: '策略', type: 'line', data: sec.nav, showSymbol: false,
        lineStyle: { width: 1.6 }, itemStyle: { color: '#dc2626' },
        areaStyle: { color: 'rgba(220,38,38,0.08)' } },
      { name: '沪深300', type: 'line', data: sec.bench, showSymbol: false,
        lineStyle: { width: 1.1 }, itemStyle: { color: '#64748b' } }
    ]
  }
}

function mount(elRef, sec, key) {
  if (!sec) return
  const dom = elRef.value
  if (!dom) {                                   // 容器还没挂上：稍后再试一次
    setTimeout(() => mount(elRef, sec, key), 60)
    return
  }
  try {
    let inst = key === 'is' ? chartIs : chartOos
    if (inst && inst.getDom() !== dom) {        // 容器被重建过（切视图/重新挂载）
      inst.dispose()
      inst = null
    }
    if (!inst) {
      // 显式给宽高：首帧容器可能还没布局完，echarts 按 0×0 建图后就再也不画了
      inst = echarts.init(dom, null, {
        renderer: 'canvas',
        width: dom.clientWidth || null,
        height: 230
      })
      if (key === 'is') chartIs = inst
      else chartOos = inst
    }
    inst.setOption(option(sec), true)
    requestAnimationFrame(() => {               // 下一帧再校正一次尺寸
      try { inst.resize({ width: dom.clientWidth || undefined, height: 230 }) } catch { /* ignore */ }
    })
  } catch (e) {
    console.error('[BacktestPanel] 图表渲染失败:', key, e)
  }
}

function render() {
  const d = data.value
  if (!d || d.error) return
  mount(elIs, d.is, 'is')
  mount(elOos, d.oos, 'oos')
}

onMounted(() => {
  load()
  timer = setInterval(load, 60000)
  window.addEventListener('resize', onResize)
})
watch(() => props.account, load)
onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('resize', onResize)
  chartIs?.dispose()
  chartOos?.dispose()
})
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <span class="text-sm font-semibold">回测 · 样本内 / 样本外</span>
      <span v-if="data && data.strategy" class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">{{ data.strategy }}</span>
      <span class="text-[11px] text-slate-400">两段分开画，不拼成一条线 · 各自从 1.0 起算 · 保真口径（扣费/涨跌停/停牌）</span>
      <span class="ml-auto text-[11px] text-amber-700">历史模拟，非实盘收益</span>
    </div>

    <div v-if="data && !data.error" class="space-y-3">
      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <!-- 样本内 -->
        <div v-if="data.is">
          <div class="mb-1 flex items-baseline gap-2">
            <span class="text-xs font-semibold text-slate-700">样本内</span>
            <span class="text-[11px] text-slate-400">{{ data.is.stats.start }} ~ {{ data.is.stats.end }}</span>
            <span class="text-[11px] text-slate-400">· 参数固定 Top50 / 20 日</span>
          </div>
          <div ref="elIs" class="h-[230px] w-full"></div>
          <div class="mt-1 grid grid-cols-4 gap-1.5 text-center text-[11px]">
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">总收益</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.is.stats.total)">{{ fmt.pct(data.is.stats.total) }}</div>
            </div>
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">年化</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.is.stats.ann)">{{ fmt.pct(data.is.stats.ann) }}</div>
            </div>
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">超额</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.is.stats.excess)">{{ fmt.pct(data.is.stats.excess) }}</div>
            </div>
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">最大回撤</div>
              <div class="tabular font-semibold text-emerald-700">{{ fmt.pct(data.is.stats.mdd) }}</div>
            </div>
          </div>
        </div>
        <div v-else class="rounded-lg bg-amber-50 px-3 py-8 text-center text-xs text-amber-700">
          样本内数据缺失（检查 scripts/05 的产物 portfolio_results/nav_*.parquet）
        </div>

        <!-- 样本外 -->
        <div v-if="data.oos">
          <div class="mb-1 flex items-baseline gap-2">
            <span class="text-xs font-semibold text-sky-700">样本外（walk-forward）</span>
            <span class="text-[11px] text-slate-400">{{ data.oos.stats.start }} ~ {{ data.oos.stats.end }}</span>
            <span class="text-[11px] text-slate-400">· 每年只用之前数据重新选参</span>
          </div>
          <div ref="elOos" class="h-[230px] w-full"></div>
          <div class="mt-1 grid grid-cols-4 gap-1.5 text-center text-[11px]">
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">总收益</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.oos.stats.total)">{{ fmt.pct(data.oos.stats.total) }}</div>
            </div>
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">年化</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.oos.stats.ann)">{{ fmt.pct(data.oos.stats.ann) }}</div>
            </div>
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">超额</div>
              <div class="tabular font-semibold" :class="fmt.signCls(data.oos.stats.excess)">{{ fmt.pct(data.oos.stats.excess) }}</div>
            </div>
            <div class="rounded bg-slate-50 py-1">
              <div class="text-slate-500">最大回撤</div>
              <div class="tabular font-semibold text-emerald-700">{{ fmt.pct(data.oos.stats.mdd) }}</div>
            </div>
          </div>
        </div>
        <div v-else class="rounded-lg bg-amber-50 px-3 py-8 text-center text-xs text-amber-700">
          样本外数据缺失（检查 scripts/06 的产物 walkforward_results/nav_oos_*.parquet）
        </div>
      </div>

      <!-- 分年度 + 折明细 -->
      <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <div class="mb-1 text-xs font-medium text-slate-500">分年度收益（标出属于哪一段）</div>
          <table class="tabular w-full text-sm">
            <thead class="bg-slate-50 text-xs text-slate-500">
              <tr><th class="px-2 py-1.5 text-left">年份</th><th>段</th><th>策略</th><th>沪深300</th><th>超额</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in data.yearly" :key="r.year" class="border-t border-slate-100">
                <td class="px-2 py-1">{{ r.year }}</td>
                <td class="text-center text-[11px]" :class="r.seg === '样本外' ? 'text-sky-700' : 'text-slate-400'">{{ r.seg }}</td>
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
                <td class="text-right" :class="fmt.signCls(f.train_ir)">{{ fmt.n(f.train_ir) }}</td>
                <td class="text-right font-medium">{{ fmt.n(f.oos_ir) }}</td>
                <td class="text-right" :class="fmt.signCls(f.oos_ann)">{{ fmt.pct(f.oos_ann / 100) }}</td>
                <td class="text-right text-emerald-700">{{ fmt.pct(f.oos_mdd / 100) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="mt-1 text-[11px] leading-snug text-slate-400">
            已知局限：walk-forward 只重选 TopN/调仓日，<b>因子组合本身是看过全样本后定的</b>；
            D-042 的 FDR 多重检验里 46 个因子 q&lt;0.1 一个都没通过。样本外 2022~2026 是一段单一震荡/熊市行情。
          </div>
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
