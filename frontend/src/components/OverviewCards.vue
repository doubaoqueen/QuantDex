<script setup>
import { fmt } from '../api.js'

const props = defineProps({ large: Object, small: Object, selected: String })
const emit = defineEmits(['select'])

function cardCls(acc) {
  return props.selected === acc
    ? 'ring-2 ring-sky-500'
    : 'hover:ring-1 hover:ring-slate-300'
}
</script>

<template>
  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
    <div v-for="a in [{key:'large', label:'大账户（100万）', o:large}, {key:'small', label:'小账户（2000元）', o:small}]"
         :key="a.key"
         class="cursor-pointer rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur transition"
         :class="cardCls(a.key)"
         @click="emit('select', a.key)">
      <div class="flex items-center justify-between">
        <span class="text-xs font-medium text-slate-500">{{ a.label }}</span>
        <span v-if="selected===a.key" class="rounded-full bg-sky-100 px-2 py-0.5 text-[11px] text-sky-700">当前</span>
      </div>
      <template v-if="a.o && !a.o.empty">
        <div class="tabular mt-1 text-2xl font-bold">{{ fmt.n(a.o.nav, 2) }} <span class="text-sm font-normal text-slate-400">元</span></div>
        <div class="tabular mt-1 text-sm" :class="fmt.signCls(a.o.day_pnl)">
          今日 {{ a.o.day_pnl >= 0 ? '+' : '' }}{{ fmt.n(a.o.day_pnl, 2) }}
          （{{ a.o.prev_nav ? fmt.pct(a.o.nav / a.o.prev_nav - 1) : '-' }}）
        </div>
        <div class="tabular mt-0.5 text-sm" :class="fmt.signCls(a.o.cum_ret)">累计 {{ fmt.pct(a.o.cum_ret) }}</div>
        <div class="tabular mt-2 flex gap-3 text-xs text-slate-500">
          <span>现金 {{ fmt.n(a.o.cash, 0) }}</span>
          <span>持仓 {{ a.o.positions }}</span>
          <span>待成交 {{ a.o.pending }}</span>
          <span v-if="a.o.date">{{ a.o.date }}</span>
        </div>
      </template>
      <div v-else class="py-6 text-sm text-slate-400">暂无数据</div>
    </div>
  </div>
</template>
