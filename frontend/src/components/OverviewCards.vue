<script setup>
import { fmt } from '../api.js'

const props = defineProps({ accounts: Array, overviews: Object, selected: String })
const emit = defineEmits(['select'])

const shortName = { large: '大账户', small: '小账户', div: '红利', topk: '换手' }
</script>

<template>
  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
    <div v-for="a in accounts" :key="a.key"
         class="cursor-pointer rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur transition"
         :class="selected === a.key ? 'ring-2 ring-sky-500' : 'hover:ring-1 hover:ring-slate-300'"
         @click="emit('select', a.key)">
      <div class="flex items-center justify-between">
        <span class="text-xs font-medium text-slate-500">{{ a.label }}</span>
        <span v-if="selected===a.key" class="rounded-full bg-sky-100 px-2 py-0.5 text-[11px] text-sky-700">当前</span>
      </div>
      <template v-if="overviews && overviews[a.key] && !overviews[a.key].empty">
        <div class="tabular mt-1 text-xl font-bold">
          {{ fmt.n(overviews[a.key].nav, 2) }} <span class="text-xs font-normal text-slate-400">元</span>
        </div>
        <div class="tabular mt-1 text-sm" :class="fmt.signCls(overviews[a.key].day_pnl)">
          今日 {{ overviews[a.key].day_pnl >= 0 ? '+' : '' }}{{ fmt.n(overviews[a.key].day_pnl, 2) }}
          （{{ overviews[a.key].prev_nav ? fmt.pct(overviews[a.key].nav / overviews[a.key].prev_nav - 1) : '-' }}）
        </div>
        <div class="tabular mt-0.5 text-sm" :class="fmt.signCls(overviews[a.key].cum_ret)">累计 {{ fmt.pct(overviews[a.key].cum_ret) }}</div>
        <div class="tabular mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
          <span>现金 {{ fmt.n(overviews[a.key].cash, 0) }}</span>
          <span>持仓 {{ overviews[a.key].positions }}</span>
          <span>待成交 {{ overviews[a.key].pending }}</span>
          <span v-if="overviews[a.key].date">{{ overviews[a.key].date }}</span>
        </div>
      </template>
      <div v-else class="py-6 text-sm text-slate-400">暂无数据</div>
    </div>
  </div>
</template>
