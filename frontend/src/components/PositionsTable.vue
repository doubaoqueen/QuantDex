<script setup>
import { ref, computed } from 'vue'
import { fmt } from '../api.js'

const props = defineProps({ rows: Array, account: String })

const sortKey = ref('weight')
const sortAsc = ref(false)
const filter = ref('')

const sorted = computed(() => {
  let r = [...(props.rows || [])]
  if (filter.value.trim()) {
    const q = filter.value.trim()
    r = r.filter(x => String(x.symbol).includes(q))
  }
  const k = sortKey.value
  r.sort((a, b) => {
    const va = a[k], vb = b[k]
    const na = va == null ? -Infinity : Number(va)
    const nb = vb == null ? -Infinity : Number(vb)
    return (sortAsc.value ? 1 : -1) * (na - nb)
  })
  return r
})

const upDown = computed(() => {
  const r = props.rows || []
  const up = r.filter(x => x.pct > 0).length
  const down = r.filter(x => x.pct < 0).length
  return { up, down, flat: r.length - up - down }
})

const cols = [
  { key: 'symbol', label: '代码', num: false },
  { key: 'shares', label: '股数', num: true },
  { key: 'close', label: '现价', num: true },
  { key: 'pct', label: '涨跌幅', num: true },
  { key: 'pnl', label: '当日盈亏', num: true },
  { key: 'weight', label: '权重', num: true }
]

function setSort(k) {
  if (sortKey.value === k) sortAsc.value = !sortAsc.value
  else { sortKey.value = k; sortAsc.value = false }
}

// 涨跌幅单元格背景强度：|pct| 3% 封顶
function heat(pct) {
  if (pct == null) return 'transparent'
  const a = Math.min(Math.abs(pct) / 0.03, 1) * 0.16
  return pct > 0 ? `rgba(224,49,49,${a})` : `rgba(5,150,105,${a})`
}
function weightW(w) { return ((Number(w) || 0) * 100).toFixed(1) + '%' }
</script>

<template>
  <div class="h-full rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <span class="text-sm font-semibold">持仓</span>
      <span class="text-xs text-slate-400">{{ account === 'large' ? '大账户' : '小账户' }} · {{ (rows||[]).length }} 只</span>
      <span class="ml-auto text-xs">
        <span class="text-red-600">涨 {{ upDown.up }}</span>
        <span class="mx-1 text-slate-300">/</span>
        <span class="text-emerald-600">跌 {{ upDown.down }}</span>
        <span class="mx-1 text-slate-300">/</span>
        <span class="text-slate-500">平 {{ upDown.flat }}</span>
      </span>
      <input v-model="filter" placeholder="筛选代码…"
             class="w-28 rounded-lg border border-slate-300 px-2 py-1 text-xs focus:border-sky-500 focus:outline-none" />
    </div>
    <div class="max-h-[420px] overflow-auto rounded-lg border border-slate-100">
      <table class="tabular w-full text-sm">
        <thead class="sticky top-0 bg-slate-50 text-xs text-slate-500">
          <tr>
            <th v-for="c in cols" :key="c.key" class="cursor-pointer select-none px-3 py-2 text-right first:text-left hover:text-sky-600"
                :class="{ 'text-sky-700': sortKey === c.key }"
                @click="setSort(c.key)">
              {{ c.label }}<span v-if="sortKey === c.key">{{ sortAsc ? ' ↑' : ' ↓' }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in sorted" :key="r.symbol" class="border-t border-slate-100 hover:bg-sky-50/50">
            <td class="px-3 py-1.5 font-medium">{{ r.symbol }}</td>
            <td class="px-3 py-1.5 text-right text-slate-500">{{ fmt.n(r.shares, 0) }}</td>
            <td class="px-3 py-1.5 text-right">{{ fmt.n(r.close) }}</td>
            <td class="px-3 py-1.5 text-right font-semibold" :class="fmt.signCls(r.pct)" :style="{ background: heat(r.pct) }">
              {{ fmt.pct(r.pct) }}
            </td>
            <td class="px-3 py-1.5 text-right" :class="fmt.signCls(r.pnl)">{{ r.pnl >= 0 ? '+' : '' }}{{ fmt.n(r.pnl, 0) }}</td>
            <td class="px-3 py-1.5 text-right">
              <div class="flex items-center justify-end gap-1.5">
                <div class="h-1.5 w-14 overflow-hidden rounded-full bg-slate-200">
                  <div class="h-full rounded-full bg-sky-500" :style="{ width: weightW(r.weight) }"></div>
                </div>
                <span class="w-12 text-slate-500">{{ weightW(r.weight) }}</span>
              </div>
            </td>
          </tr>
          <tr v-if="!sorted.length">
            <td colspan="6" class="px-3 py-8 text-center text-sm text-slate-400">无持仓</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
