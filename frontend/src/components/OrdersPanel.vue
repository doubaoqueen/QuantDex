<script setup>
import { ref, computed, inject, watch } from 'vue'
import { api, fmt } from '../api.js'

const props = defineProps({ orders: Array, trades: Array, account: String })
const emit = defineEmits(['changed'])
const say = inject('toast', () => {})

const sym = ref('')
const side = ref('buy')
const qty = ref('')
const price = ref('')
const tab = ref('pending')       // pending | history | fees
const feeData = ref({ summary: null, recent: [] })
const diff = ref({ rows: [], nav: null })

async function loadDiff() {
  diff.value = await api.get(`/api/rebalance_diff?account=${props.account}`)
    .catch(() => ({ rows: [], nav: null }))
}
async function loadFees() {
  feeData.value = await api.get(`/api/fees?account=${props.account}`)
    .catch(() => ({ summary: null, recent: [] }))
}
watch(() => [props.account, props.orders], () => { loadDiff(); loadFees() }, { immediate: true })

async function cancel(id) {
  if (!confirm(`取消订单 #${id}？（该笔调仓将被否决，次日不会执行）`)) return
  const r = await api.post('/api/orders/cancel', { account: props.account, id })
  say(r.msg)
  emit('changed')
}

async function cancelAll() {
  const n = (props.orders || []).length
  if (!n) return
  if (!confirm(`全部取消 ${n} 笔待成交订单？（整次调仓将被否决）`)) return
  for (const o of props.orders) {
    await api.post('/api/orders/cancel', { account: props.account, id: o.id }).catch(() => {})
  }
  say(`已全部取消 ${n} 笔`)
  emit('changed')
}

async function submit() {
  const q = Number(qty.value)
  if (!/^\d{6}$/.test(sym.value.trim())) return say('代码须为 6 位数字')
  if (!Number.isInteger(q) || q <= 0 || q % 100 !== 0) return say('数量必须是 100 的整数倍')
  const r = await api.post('/api/orders/add', {
    account: props.account, symbol: sym.value.trim(), side: side.value,
    qty: q, price: price.value ? Number(price.value) : null })
  say(r.msg)
  if (r.ok) { sym.value = ''; qty.value = ''; price.value = '' }
  emit('changed')
}

const statusBadge = {
  filled: 'bg-emerald-100 text-emerald-700',
  pending: 'bg-amber-100 text-amber-700',
  cancelled: 'bg-slate-200 text-slate-500',
  rejected_limit_up: 'bg-rose-100 text-rose-700',
  rejected_limit_down: 'bg-rose-100 text-rose-700',
  rejected_suspended: 'bg-slate-300 text-slate-600'
}
const statusText = {
  filled: '已成交', pending: '待成交', cancelled: '已取消',
  rejected_limit_up: '涨停拒单', rejected_limit_down: '跌停拒单', rejected_suspended: '停牌拒单'
}
const shownTrades = computed(() => (props.trades || []).slice(0, 30))
</script>

<template>
  <div class="flex h-full flex-col rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex items-center gap-2">
      <span class="text-sm font-semibold">订单</span>
      <div class="ml-auto flex overflow-hidden rounded-lg border border-slate-300 text-xs">
        <button :class="tab==='pending' ? 'bg-slate-800 text-white' : 'bg-white'" class="px-2.5 py-1"
                @click="tab='pending'">调仓审批 {{ (orders||[]).length }}</button>
        <button :class="tab==='history' ? 'bg-slate-800 text-white' : 'bg-white'"
                class="border-l border-slate-300 px-2.5 py-1" @click="tab='history'">成交记录</button>
        <button :class="tab==='fees' ? 'bg-slate-800 text-white' : 'bg-white'"
                class="border-l border-slate-300 px-2.5 py-1" @click="tab='fees'">费用</button>
      </div>
    </div>

    <!-- 待成交：调仓审批视图（当前权重 → 调整后权重） -->
    <div v-if="tab==='pending'">
      <div v-if="diff.rows && diff.rows.length" class="max-h-[240px] overflow-auto">
        <div class="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
          <span>审批：不点取消 = 默认次日开盘执行</span>
          <button class="ml-auto rounded bg-rose-100 px-2 py-0.5 text-rose-700 hover:bg-rose-200"
                  @click="cancelAll">全部否决（{{ diff.rows.length }} 笔）</button>
        </div>
        <table class="tabular w-full text-xs">
          <thead class="sticky top-0 bg-slate-50 text-slate-500">
            <tr>
              <th class="px-2 py-1.5 text-left">代码</th><th>方向</th><th>数量</th>
              <th>当前权重</th><th>调整后</th><th>变动</th><th>成交日</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in diff.rows" :key="r.id" class="border-t border-slate-100">
              <td class="px-2 py-1.5 font-medium">{{ r.symbol }}</td>
              <td class="text-center" :class="r.side==='buy' ? 'text-red-600' : 'text-emerald-600'">
                {{ r.side==='buy' ? '买入' : '卖出' }}</td>
              <td class="text-right">{{ fmt.n(r.qty, 0) }}</td>
              <td class="text-right text-slate-500">{{ fmt.pct(r.cur_weight, 1) }}</td>
              <td class="text-right font-medium">{{ fmt.pct(r.tgt_weight, 1) }}</td>
              <td class="text-right" :class="fmt.signCls(r.delta_weight)">
                {{ (r.delta_weight>=0?'+':'') + (r.delta_weight*100).toFixed(1) + 'pp' }}</td>
              <td class="text-center text-slate-400">{{ r.fill_date }}</td>
              <td class="pl-1 text-right">
                <button class="rounded bg-rose-600 px-1.5 py-0.5 text-[11px] text-white hover:bg-rose-700"
                        @click="cancel(r.id)">否决</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="(orders||[]).length" class="max-h-[200px] overflow-auto">
        <table class="tabular w-full text-sm">
          <thead class="bg-slate-50 text-xs text-slate-500">
            <tr><th class="px-2 py-1.5 text-left">代码</th><th>方向</th><th>数量</th><th>参考价</th><th>成交日</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="o in orders" :key="o.id" class="border-t border-slate-100">
              <td class="px-2 py-1.5 font-medium">{{ o.symbol }}</td>
              <td class="text-center" :class="o.side==='buy' ? 'text-red-600' : 'text-emerald-600'">{{ o.side==='buy' ? '买入' : '卖出' }}</td>
              <td class="tabular text-right">{{ fmt.n(o.qty, 0) }}</td>
              <td class="tabular text-right">{{ fmt.n(o.price) }}</td>
              <td class="text-center text-slate-500">{{ o.fill_date }}</td>
              <td class="pl-1 text-right"><button class="rounded bg-rose-600 px-2 py-0.5 text-xs text-white hover:bg-rose-700" @click="cancel(o.id)">取消</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="py-6 text-center text-sm text-slate-400">无待成交订单</div>
    </div>

    <!-- 成交记录 -->
    <div v-else-if="tab==='history'" class="max-h-[200px] overflow-auto">
      <table v-if="shownTrades.length" class="tabular w-full text-sm">
        <thead class="bg-slate-50 text-xs text-slate-500">
          <tr><th class="px-2 py-1.5 text-left">日期</th><th class="text-left">代码</th><th>方向</th><th>数量</th><th>价</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in shownTrades" :key="i" class="border-t border-slate-100">
            <td class="px-2 py-1.5 text-slate-500">{{ t.fill_date }}</td>
            <td class="font-medium">{{ t.symbol }}</td>
            <td class="text-center" :class="t.side==='buy' ? 'text-red-600' : 'text-emerald-600'">{{ t.side==='buy' ? '买' : '卖' }}</td>
            <td class="tabular text-right">{{ fmt.n(t.qty, 0) }}</td>
            <td class="tabular text-right">{{ fmt.n(t.price) }}</td>
            <td class="text-center">
              <span class="rounded-full px-2 py-0.5 text-[11px]" :class="statusBadge[t.status] || 'bg-slate-200'">
                {{ statusText[t.status] || t.status }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="py-6 text-center text-sm text-slate-400">暂无成交记录</div>
    </div>

    <!-- 费用统计 -->
    <div v-else-if="tab==='fees'" class="max-h-[200px] overflow-auto">
      <template v-if="feeData.summary">
        <div class="mb-2 grid grid-cols-2 gap-1.5 text-xs">
          <div class="rounded-lg bg-slate-50 p-2"><div class="text-slate-500">已成交 {{ feeData.summary.n_filled }} 笔 · 成交额</div>
            <div class="tabular font-semibold">{{ fmt.n(feeData.summary.turnover, 0) }} 元</div></div>
          <div class="rounded-lg bg-slate-50 p-2"><div class="text-slate-500">费用合计（占成交额）</div>
            <div class="tabular font-semibold text-rose-600">{{ fmt.n(feeData.summary.total, 2) }} 元
              <span class="text-[10px] font-normal">({{ (feeData.summary.total/feeData.summary.turnover*100).toFixed(3) }}%)</span></div></div>
          <div class="rounded-lg bg-slate-50 p-2"><div class="text-slate-500">佣金(含过户/最低5元)</div>
            <div class="tabular">{{ fmt.n(feeData.summary.commission, 2) }} 元</div></div>
          <div class="rounded-lg bg-slate-50 p-2"><div class="text-slate-500">印花税（卖出 0.05%）</div>
            <div class="tabular">{{ fmt.n(feeData.summary.stamp, 2) }} 元</div></div>
          <div class="rounded-lg bg-slate-50 p-2 col-span-2"><div class="text-slate-500">滑点（估算，成交额×0.05%）</div>
            <div class="tabular">{{ fmt.n(feeData.summary.slip_est, 2) }} 元</div></div>
        </div>
        <table class="tabular w-full text-xs">
          <thead class="bg-slate-50 text-slate-500">
            <tr><th class="px-2 py-1 text-left">日期</th><th class="text-left">代码</th><th>方向</th><th>金额</th><th>费用</th></tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in feeData.recent" :key="i" class="border-t border-slate-100">
              <td class="px-2 py-1 text-slate-500">{{ t.fill_date }}</td>
              <td class="font-medium">{{ t.symbol }}</td>
              <td class="text-center" :class="t.side==='buy' ? 'text-red-600' : 'text-emerald-600'">{{ t.side==='buy' ? '买' : '卖' }}</td>
              <td class="text-right">{{ fmt.n(t.amount, 0) }}</td>
              <td class="text-right">{{ fmt.n(t.fee + t.stamp_duty, 2) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="mt-1 text-[11px] text-slate-400">统计区间 {{ feeData.summary.first_date }} ~ {{ feeData.summary.last_date }}；回测口径的历史成本见决策日志 D-002/D-004</div>
      </template>
      <div v-else class="py-6 text-center text-sm text-slate-400">暂无已成交订单（无费用产生）</div>
    </div>

    <!-- 人工挂单 -->
    <div class="mt-auto space-y-2 border-t border-slate-100 pt-3">
      <div class="text-xs font-medium text-slate-500">人工挂单（T+1 开盘撮合 · {{ account==='large' ? '大账户' : '小账户' }}）</div>
      <div class="flex flex-wrap items-center gap-2 text-sm">
        <input v-model="sym" placeholder="代码 如 600519" maxlength="6" size="9"
               class="rounded-lg border border-slate-300 px-2 py-1.5 focus:border-sky-500 focus:outline-none" />
        <select v-model="side" class="rounded-lg border border-slate-300 px-2 py-1.5">
          <option value="buy">买入</option><option value="sell">卖出</option>
        </select>
        <input v-model="qty" placeholder="数量(100倍)" size="9"
               class="rounded-lg border border-slate-300 px-2 py-1.5 focus:border-sky-500 focus:outline-none" />
        <input v-model="price" placeholder="价(留空=现价)" size="9"
               class="rounded-lg border border-slate-300 px-2 py-1.5 focus:border-sky-500 focus:outline-none" />
        <button class="rounded-lg bg-sky-600 px-3 py-1.5 text-white hover:bg-sky-700" @click="submit">挂单</button>
      </div>
    </div>
  </div>
</template>
