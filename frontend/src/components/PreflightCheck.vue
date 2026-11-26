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

    <div class="mt-2 rounded-lg border border-orange-200 bg-orange-50/60 p-3 text-xs text-orange-900">
      1.5× 黑天鹅档说明：分母放大 1.5 倍——即使未来回撤达到历史的 1.5 倍也不刺穿红线。
      标准档与保守档的差价，是为"没见过所有危机"支付的保险费。alpha 未裁决（D-010），
      期望带是<b>档案口径的参考带</b>，不是收益承诺。
    </div>
    <p class="mt-2 text-[10px] text-slate-400">
      三行均为 11-22 同源新档（MDD = 05 号全样本 canonical，WF = 06 号样本外，产物指纹核对）。
      corrected 的 WF 暂采保守旧值 10.59%——新跑 12.44% 差异待定位（D-108）。
      上表为<b>等比例口径</b>（假设无摩擦等权 50 只分散）；小预算下该假设不成立，以「组合可行性核查」为准（D-104）。
    </p>
  </div>
</template>
