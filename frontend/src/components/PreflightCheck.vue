<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'

const emit = defineEmits([])
const L = ref(20000)
const data = ref(null)
const loading = ref(false)

async function run() {
  loading.value = true
  try {
    data.value = await api.get(`/api/preflight?L=${L.value}`)
  } catch { data.value = null }
  loading.value = false
}

// 防抖：输入停顿 400ms 后请求（不 watch data——自反馈会无限循环）
let timer = null
function onInput(e) {
  const v = Number(e.target.value)
  if (!Number.isFinite(v) || v <= 0) return
  L.value = v
  clearTimeout(timer)
  timer = setTimeout(() => { run() }, 400)
}

// D-104 组合核查判定样式
const comboVerdict = () => data.value?.combo?.verdict || 'thin'
const comboLabel = computed(() => ({ ok: '可行', thin: '分散不足', fail: '不可行' }[comboVerdict()] || '-'))
const comboBorder = computed(() => ({
  ok: 'border-emerald-200 bg-emerald-50/60 text-emerald-900',
  thin: 'border-orange-200 bg-orange-50/60 text-orange-900',
  fail: 'border-red-300 bg-red-50/70 text-red-800',
}[comboVerdict()]))
const comboText = computed(() => ({
  ok: 'text-emerald-700', thin: 'text-orange-700', fail: 'text-red-700',
}[comboVerdict()]))
const comboBadge = computed(() => ({
  ok: 'bg-emerald-100 text-emerald-700',
  thin: 'bg-orange-100 text-orange-700',
  fail: 'bg-red-100 text-red-700',
}[comboVerdict()]))

run()
</script>

<template>
  <div class="qd-card qd-card--hover">
    <div class="flex items-center justify-between">
      <span class="qd-title">开户前体检（D-076 v2 / 构想一）</span>
      <span class="rounded-full bg-slate-200 px-2.5 py-0.5 text-[10px] font-semibold text-slate-600">约束满足 · 非收益承诺</span>
    </div>

    <div class="mt-3 flex items-baseline gap-2">
      <label class="text-xs text-slate-500">可承受亏损 L（元）</label>
      <input type="number" :value="L" min="1000" step="1000"
             class="w-28 rounded-lg border border-slate-300 px-2 py-1 text-sm font-semibold"
             @input="onInput" />
    </div>

    <p v-if="data" class="mt-2 text-xs text-slate-500">
      约束满足式（D-009）：可投入 = L ÷ |历史 MDD|；1.5× 压力档 = 为"没见过所有危机"支付的保险费。
    </p>

    <div v-if="data" class="mt-3 overflow-x-auto">
      <table class="w-full text-xs">
        <thead>
          <tr class="bg-slate-100 text-left text-slate-500">
            <th class="p-1.5">策略</th><th class="p-1.5">历史 MDD</th>
            <th class="p-1.5">WF 样本外年化</th><th class="p-1.5">最大可投入</th>
            <th class="p-1.5">1.5× 保守</th><th class="p-1.5">年期望带（元）</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in data.rows" :key="r.key" class="border-t border-slate-100">
            <td class="p-1.5 font-semibold text-slate-700">{{ r.name }}<span v-if="r.wf_note.includes('旧档')" class="ml-1 text-[10px] text-amber-600">*</span></td>
            <td class="p-1.5 font-mono text-slate-600">-{{ (r.mdd * 100).toFixed(2) }}%</td>
            <td class="p-1.5 font-mono text-slate-600">{{ (r.wf_ann * 100).toFixed(2) }}%<span v-if="r.wf_note.includes('旧档')" class="text-amber-600">*</span></td>
            <td class="p-1.5 font-mono font-bold text-slate-800">{{ r.invest.toLocaleString() }}</td>
            <td class="p-1.5 font-mono text-slate-500">{{ r.invest_15.toLocaleString() }}</td>
            <td class="p-1.5 font-mono text-slate-600">{{ r.band_lo.toLocaleString() }} ~ {{ r.band_hi.toLocaleString() }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="data" class="mt-3 space-y-2 rounded-lg border border-blue-200 bg-blue-50/60 p-3 text-xs text-blue-900">
      <div class="font-semibold">D-098 四周归因校准（2026-10-19 ~ 11-20）</div>
      <div>影子盘实测带（相对基准）：{{ (data.calibration.live_band_vs_bench[0] * 100).toFixed(2) }}% ~ {{ (data.calibration.live_band_vs_bench[1] * 100).toFixed(2) }}%</div>
      <div>执行偏差：{{ data.calibration.exec_deviation }} | ST 敞口实测：{{ data.calibration.st_exposure }} 笔</div>
      <div class="font-semibold">D-010 状态：{{ data.calibration.d010 }}</div>
    </div>

    <!-- D-104：组合可行性核查——等比例公式看不见的整手/最低佣金/集中度 -->
    <div v-if="data && data.combo && !data.combo.err" class="mt-3 rounded-lg border p-3 text-xs"
         :class="comboBorder">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <span class="font-semibold" :class="comboText">组合可行性核查（整手·最低佣金·集中度）</span>
        <span class="rounded-full px-2 py-0.5 text-[10px] font-semibold" :class="comboBadge">
          {{ comboLabel }}
        </span>
      </div>
      <div class="mt-1.5 text-[11px] text-slate-500">
        按主策略推荐投入 <b class="tabular">{{ data.combo.budget.toLocaleString() }}</b> 元、当前候选池（分数序），
        实际能组 <b>{{ data.combo.k }}</b> / {{ data.combo.pool }} 只
        <template v-if="data.combo_15 && !data.combo_15.err">
          ；保守档 {{ data.combo_15.budget.toLocaleString() }} 元 → {{ data.combo_15.k }} 只
        </template>
      </div>
      <div class="mt-1.5 text-[11px]" :class="comboText">{{ data.combo.verdict_text }}</div>

      <div class="mt-2 grid grid-cols-3 gap-2 text-center">
        <div class="rounded-md bg-slate-50 p-1.5">
          <div class="text-[10px] text-slate-400">最大权重（设计 2%）</div>
          <div class="tabular font-bold" :class="comboText">{{ (data.combo.max_weight * 100).toFixed(1) }}%</div>
        </div>
        <div class="rounded-md bg-slate-50 p-1.5">
          <div class="text-[10px] text-slate-400">单票跌停冲击（理想 {{ data.combo.ideal_single_limit.toLocaleString() }} 元）</div>
          <div class="tabular font-bold" :class="comboText">
            {{ data.combo.single_limit_loss.toLocaleString() }} 元
            <span class="text-[10px] font-normal text-slate-400" v-if="data.combo.single_vs_L != null">
              （L 的 {{ (data.combo.single_vs_L * 100).toFixed(1) }}%）
            </span>
          </div>
        </div>
        <div class="rounded-md bg-slate-50 p-1.5">
          <div class="text-[10px] text-slate-400">单轮全成本（建+清）</div>
          <div class="tabular font-bold" :class="comboText">{{ (data.combo.roundtrip_pct * 100).toFixed(2) }}%</div>
        </div>
      </div>

      <div class="mt-2 max-h-40 overflow-y-auto rounded-md border border-slate-200">
        <table class="w-full text-[11px] tabular">
          <thead class="sticky top-0 bg-slate-50 text-slate-400">
            <tr><th class="px-2 py-1 text-left">#</th><th class="px-2 py-1 text-left">代码</th>
                <th class="px-2 py-1 text-left">名称</th><th class="px-2 py-1 text-right">收盘</th>
                <th class="px-2 py-1 text-right">手数</th><th class="px-2 py-1 text-right">金额</th>
                <th class="px-2 py-1 text-right">跌停</th></tr>
          </thead>
          <tbody>
            <tr v-for="q in data.combo.positions" :key="q.symbol" class="border-t border-slate-100">
              <td class="px-2 py-1 text-slate-400">{{ q.rank }}</td>
              <td class="px-2 py-1">{{ q.symbol }}</td>
              <td class="px-2 py-1 text-slate-600">{{ q.name }}</td>
              <td class="px-2 py-1 text-right">{{ q.close.toFixed(2) }}</td>
              <td class="px-2 py-1 text-right">{{ q.lots }}</td>
              <td class="px-2 py-1 text-right">{{ q.amount.toLocaleString() }}</td>
              <td class="px-2 py-1 text-right text-slate-400">-{{ (q.limit * 100).toFixed(0) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="mt-1.5 flex flex-wrap gap-x-4 text-[10px] text-slate-400">
        <span>建仓用 {{ data.combo.cost.toLocaleString() }} 元 · 剩余现金 {{ data.combo.cash_left.toLocaleString() }} 元</span>
        <span v-if="data.combo.b_min">舒适起步预算 ≈ {{ data.combo.b_min.toLocaleString() }} 元（前 10 只等权一手+成本余量）</span>
      </div>
    </div>
    <div v-else-if="data && data.combo && data.combo.err"
         class="mt-3 rounded-lg border border-orange-200 bg-orange-50/60 p-2 text-[11px] text-orange-800">
      组合核查不可用：{{ data.combo.err }}
    </div>

    <!-- D-119：预算回放——这个预算自己会经历的净值路径，而不是"50 只组合"的借来数字 -->
    <div v-if="data && data.replay" class="mt-3 rounded-lg border border-violet-200 bg-violet-50/60 p-3 text-xs text-violet-950">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <span class="font-semibold">预算回放（逐期整手 · 按真实 20 日调仓序列）</span>
        <span class="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
          {{ data.replay.nearest_budget.toLocaleString() }} 元档
        </span>
      </div>
      <div class="mt-1.5 grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
        <div class="rounded-md bg-white/70 p-1.5">
          <div class="text-[10px] text-slate-400">实际持仓（目标 50）</div>
          <div class="tabular font-bold">{{ data.replay.avg_positions.toFixed(0) }} 只</div>
        </div>
        <div class="rounded-md bg-white/70 p-1.5">
          <div class="text-[10px] text-slate-400">平均仓位</div>
          <div class="tabular font-bold">{{ (data.replay.avg_invested * 100).toFixed(0) }}%</div>
        </div>
        <div class="rounded-md bg-white/70 p-1.5">
          <div class="text-[10px] text-slate-400">回放年化</div>
          <div class="tabular font-bold" :class="data.replay.ann < 0 ? 'text-red-600' : 'text-emerald-600'">
            {{ (data.replay.ann * 100).toFixed(1) }}%
          </div>
        </div>
        <div class="rounded-md bg-white/70 p-1.5">
          <div class="text-[10px] text-slate-400">成本 / 投入资金</div>
          <div class="tabular font-bold text-orange-700">{{ (data.replay.cost_on_invested * 100).toFixed(1) }}%</div>
        </div>
      </div>
      <div class="mt-2 rounded-md bg-white/70 p-2 text-[11px] leading-relaxed">
        <b>满仓折算 MDD {{ (data.replay.mdd_norm * 100).toFixed(1) }}%</b>
        vs 策略 {{ (data.replay.ref_mdd * 100).toFixed(1) }}%
        （放大 {{ (data.replay.mdd_norm / data.replay.ref_mdd).toFixed(2) }}×）——
        实际回撤 {{ (data.replay.mdd * 100).toFixed(1) }}% 之所以更浅，是因为只投出去
        {{ (data.replay.avg_invested * 100).toFixed(0) }}%，不是组合更安全。
        <template v-if="data.replay.worst_year">
          <br />最差年份：{{ data.replay.worst_year[0] }} 跑输策略
          <b>{{ (data.replay.worst_year[1] * -100).toFixed(1) }}pp</b>（仓位在牛市被"一手门槛"自动压低）。
        </template>
      </div>
      <div class="mt-2 max-h-40 overflow-y-auto rounded-md border border-violet-200/70">
        <table class="w-full text-[11px] tabular">
          <thead class="sticky top-0 bg-violet-100/80 text-violet-700">
            <tr><th class="px-2 py-1 text-left">分段</th><th class="px-2 py-1 text-right">仓位</th>
                <th class="px-2 py-1 text-right">回放年化</th><th class="px-2 py-1 text-right">策略年化</th>
                <th class="px-2 py-1 text-right">差额</th></tr>
          </thead>
          <tbody>
            <tr v-for="(v, k) in data.replay.by_year" :key="k" class="border-t border-violet-100">
              <td class="px-2 py-1">{{ k }}</td>
              <td class="px-2 py-1 text-right">{{ (v.invested * 100).toFixed(0) }}%</td>
              <td class="px-2 py-1 text-right">{{ (v.ann * 100).toFixed(1) }}%</td>
              <td class="px-2 py-1 text-right text-slate-500">{{ (v.ref_ann * 100).toFixed(1) }}%</td>
              <td class="px-2 py-1 text-right font-semibold"
                  :class="(v.ann - v.ref_ann) < 0 ? 'text-red-600' : 'text-emerald-600'">
                {{ ((v.ann - v.ref_ann) * 100).toFixed(1) }}pp
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="mt-1.5 text-[10px] text-violet-700/80">
        回放口径：真实 20 日调仓权重 + T+1 开盘成交 + 100 股整手 + 最低佣金 5 元 + 法定费率（D-118）。
        产物由 62 号脚本生成，随 nightly 刷新。
      </p>
    </div>
    <div v-else-if="data" class="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-500">
      预算回放不可用（未找到 62 号产物）——运行 <code>python scripts/62_mini_budget_replay.py</code>。
    </div>

    <div class="mt-2 rounded-lg border border-orange-200 bg-orange-50/60 p-3 text-xs text-orange-900">
      1.5× 黑天鹅档说明：分母放大 1.5 倍——即使未来回撤达到历史的 1.5 倍也不刺穿红线。
      标准档与保守档的差价，是为"没见过所有危机"支付的保险费。alpha 未裁决（D-010），
      期望带是<b>档案口径的参考带</b>，不是收益承诺。
    </div>
    <p class="mt-2 text-[10px] text-slate-400">
      三行均为 11-27 口径修正新档（D-118：按日 ST 剔除 / 法定费率 / 全收益基准；
      MDD = 05 号全样本 Top50/20，WF = 06 号样本外）。
      上表为<b>等比例口径</b>（假设无摩擦等权 50 只分散）；小预算下该假设不成立——
      以「组合可行性核查」（截面能否买到）与「预算回放」（这个预算自己的历史路径）为准。
    </p>
  </div>
</template>
