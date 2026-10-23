<script setup>
import { computed } from 'vue'
import { fmt } from '../api.js'

const props = defineProps({ regime: Object })
const r = computed(() => props.regime || {})

const level = computed(() => {
  if (!r.value.close) return { text: '无数据', color: 'bg-slate-400' }
  if (r.value.above60 && r.value.above200) return { text: '偏强', color: 'bg-emerald-500' }
  if (r.value.above200) return { text: '震荡', color: 'bg-amber-500' }
  return { text: '偏弱', color: 'bg-rose-500' }
})

const rows = computed(() => [
  { k: '沪深300 收盘', v: fmt.n(r.value.close) },
  { k: 'MA20 / MA60 / MA200', v: `${fmt.n(r.value.ma20,0)} / ${fmt.n(r.value.ma60,0)} / ${fmt.n(r.value.ma200,0)}` },
  { k: '近 20 日 / 60 日', v: `${fmt.pct(r.value.ret20)} / ${fmt.pct(r.value.ret60)}` },
  { k: '距一年高点', v: fmt.pct(r.value.dd) },
  { k: '站上 MA20/60/200', v: [r.value.above20, r.value.above60, r.value.above200].map(x => x ? '✓' : '✗').join(' / ') }
])
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="flex items-center justify-between">
      <span class="text-xs font-medium text-slate-500">市场状态</span>
      <span class="rounded-full px-2.5 py-0.5 text-xs font-semibold text-white" :class="level.color">
        {{ level.text }}
      </span>
    </div>
    <div class="tabular mt-1 text-xs text-slate-400" v-if="r.date">截至 {{ r.date }}</div>
    <table class="tabular mt-2 w-full text-sm">
      <tbody>
        <tr v-for="row in rows" :key="row.k" class="border-b border-slate-100 last:border-0">
          <td class="py-1.5 text-slate-500">{{ row.k }}</td>
          <td class="py-1.5 text-right font-medium">{{ row.v }}</td>
        </tr>
      </tbody>
    </table>
    <!-- 宽度进度条：站上 MA60 的股票比例 -->
    <div class="mt-3">
      <div class="flex justify-between text-xs text-slate-500">
        <span>市场宽度（站上 MA60 比例）</span>
        <span class="tabular font-medium">{{ r.breadth == null ? '-' : fmt.pct(r.breadth, 1) }}</span>
      </div>
      <div class="mt-1 h-2.5 w-full overflow-hidden rounded-full bg-slate-200">
        <div class="h-full rounded-full transition-all duration-1000"
             :class="(r.breadth ?? 0) >= 0.5 ? 'bg-rose-500' : ((r.breadth ?? 0) >= 0.3 ? 'bg-amber-500' : 'bg-emerald-500')"
             :style="{ width: ((r.breadth ?? 0) * 100) + '%' }"></div>
      </div>
    </div>
  </div>
</template>
