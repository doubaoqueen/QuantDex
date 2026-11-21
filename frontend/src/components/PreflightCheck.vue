<script setup>
import { ref } from 'vue'
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

run()
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="flex items-center justify-between">
      <span class="text-xs font-medium text-slate-500">开户前体检（D-076 v2 / 构想一）</span>
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

    <div class="mt-2 rounded-lg border border-orange-200 bg-orange-50/60 p-3 text-xs text-orange-900">
      1.5× 黑天鹅档说明：分母放大 1.5 倍——即使未来回撤达到历史的 1.5 倍也不刺穿红线。
      标准档与保守档的差价，是为"没见过所有危机"支付的保险费。alpha 未裁决（D-010），
      期望带是<b>档案口径的参考带</b>，不是收益承诺。
    </div>
    <p class="mt-2 text-[10px] text-slate-400">* topk 的 WF 为 10-26 旧档，按 ST 口径重跑后刷新；dividend 已更新为 11-22 新档（dy 分母修复 + 2018 分红扩年，产物 _div 指纹核对）。</p>
  </div>
</template>
