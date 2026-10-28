<script setup>
import { ref, computed } from 'vue'
import { fmt } from '../api.js'

const props = defineProps({ rows: Array, account: String, alerts: Array, orders: Array })

// 尚未建仓但已有待成交买单（如新账户周末挂单、待下个开盘撮合）→ 展示"即将持仓"
const pendingBuys = computed(() =>
  (props.orders || []).filter(o => o.side === 'buy' && o.status === 'pending'))
const showUpcoming = computed(() =>
  !(props.rows || []).length && pendingBuys.value.length > 0)
const buyTotal = computed(() =>
  pendingBuys.value.reduce((s, o) => s + Number(o.qty) * Number(o.price), 0))
const fillDate = computed(() =>
  pendingBuys.value.length ? pendingBuys.value[0].fill_date : '')

const sortKey = ref('weight')
const sortAsc = ref(false)
const filter = ref('')

const sorted = computed(() => {
  let r = [...(props.rows || [])]
  if (filter.value.trim()) {
    const q = filter.value.trim().toLowerCase()
    r = r.filter(x => String(x.symbol).includes(q) ||
      String(x.name || '').toLowerCase().includes(q))
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

// 涨停/跌停只做提示：撮合侧已经在开盘价上判过涨跌停并拒单（涨停买不进/跌停卖不出）
const limitRows = computed(() => (props.rows || []).filter(x => x.limit))

const cols = [
  { key: 'symbol', label: '标的', num: false },
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
      <span class="text-xs text-slate-400">{{ ({large:'大账户',small:'小账户',div:'红利账户',topk:'换手账户'})[account] }} · {{ (rows||[]).length }} 只</span>
      <span class="ml-auto text-xs">
        <span class="text-red-600">涨 {{ upDown.up }}</span>
        <span class="mx-1 text-slate-300">/</span>
        <span class="text-emerald-600">跌 {{ upDown.down }}</span>
        <span class="mx-1 text-slate-300">/</span>
        <span class="text-slate-500">平 {{ upDown.flat }}</span>
      </span>
      <input v-model="filter" placeholder="筛选名称/代码…"
             class="w-32 rounded-lg border border-slate-300 px-2 py-1 text-xs focus:border-sky-500 focus:outline-none" />
    </div>

    <!-- 当日涨跌停：封板意味着当日买卖都会受限（撮合已按涨跌停拒单） -->
    <div v-if="limitRows.length" class="mb-2 flex flex-wrap items-center gap-1.5">
      <span class="text-xs font-medium text-slate-500">今日封板</span>
      <span v-for="r in limitRows" :key="r.symbol"
            class="tabular rounded-full px-2 py-0.5 text-[11px]"
            :class="r.limit === '涨停' ? 'bg-rose-600 text-white' : 'bg-emerald-600 text-white'"
            :title="`${r.symbol} ${r.name || ''} ${r.limit}`">
        {{ r.name || r.symbol }} {{ r.limit }}
      </span>
    </div>

    <!-- 持仓预警：距持有期高点回撤 ≥10%（仅提醒；处置类规则已被回测否定 D-026） -->
    <div v-if="alerts && alerts.length" class="mb-2 flex flex-wrap items-center gap-1.5">
      <span class="text-xs font-medium text-rose-700">持仓预警</span>
      <span v-for="a in alerts" :key="a.symbol" class="tabular rounded-full px-2 py-0.5 text-xs"
            :class="a.level==='重度' ? 'bg-rose-600 text-white' : 'bg-amber-100 text-amber-800'"
            :title="`持有自 ${a.held_since} · 高点 ${fmt.n(a.peak)} → 现价 ${fmt.n(a.close)}`">
        {{ a.symbol }} 距持有期高点 {{ (a.dd_peak*100).toFixed(1) }}%
      </span>
    </div>

    <!-- 即将持仓：账户尚未建仓、有待成交买单（新账户 T+1 建仓期） -->
    <div v-if="showUpcoming">
      <div class="mb-2 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">
        该账户尚未建仓：<b>{{ pendingBuys.length }}</b> 笔买入挂单将于
        <b>{{ fillDate }}</b> 开盘按开盘价撮合（T+1 口径）。以下为目标组合预览（参考权重=金额占比，成交后转为实际持仓）：
      </div>
      <div class="max-h-[380px] overflow-auto rounded-lg border border-slate-100">
        <table class="tabular w-full text-sm">
          <thead class="sticky top-0 bg-slate-50 text-xs text-slate-500">
            <tr><th class="px-3 py-2 text-left">标的</th><th>数量</th><th>参考价</th><th>参考金额</th><th>参考权重</th></tr>
          </thead>
          <tbody>
            <tr v-for="o in [...pendingBuys].sort((a,b)=>b.qty*b.price-a.qty*a.price)" :key="o.id"
                class="border-t border-slate-100">
              <td class="px-3 py-1.5">
                <span class="font-medium">{{ o.name || o.symbol }}</span>
                <span class="ml-1 text-[11px] text-slate-400">{{ o.symbol }}</span>
              </td>
              <td class="text-right text-slate-500">{{ fmt.n(o.qty, 0) }}</td>
              <td class="text-right">{{ fmt.n(o.price) }}</td>
              <td class="text-right">{{ fmt.n(o.qty * o.price, 0) }}</td>
              <td class="text-right">
                <div class="flex items-center justify-end gap-1.5">
                  <div class="h-1.5 w-14 overflow-hidden rounded-full bg-slate-200">
                    <div class="h-full rounded-full bg-rose-400"
                         :style="{ width: ((o.qty*o.price/buyTotal*100)) + '%' }"></div>
                  </div>
                  <span class="w-12 text-slate-500">{{ (o.qty*o.price/buyTotal*100).toFixed(1) }}%</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 已建仓：正常持仓表 -->
    <div v-else class="max-h-[420px] overflow-auto rounded-lg border border-slate-100">
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
            <td class="px-3 py-1.5">
              <div class="font-medium">{{ r.name || r.symbol }}</div>
              <div class="text-[11px] text-slate-400">
                {{ r.symbol }}<span v-if="r.industry"> · {{ r.industry }}</span>
              </div>
            </td>
            <td class="px-3 py-1.5 text-right text-slate-500">{{ fmt.n(r.shares, 0) }}</td>
            <td class="px-3 py-1.5 text-right">{{ fmt.n(r.close) }}</td>
            <td class="px-3 py-1.5 text-right font-semibold" :class="fmt.signCls(r.pct)" :style="{ background: heat(r.pct) }">
              {{ fmt.pct(r.pct) }}
              <span v-if="r.limit" class="ml-1 rounded px-1 text-[10px] text-white"
                    :class="r.limit === '涨停' ? 'bg-rose-600' : 'bg-emerald-600'">{{ r.limit }}</span>
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
