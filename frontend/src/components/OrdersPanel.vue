<script setup>
import { ref, computed, inject } from 'vue'
import { api, fmt } from '../api.js'

const props = defineProps({ orders: Array, trades: Array, account: String })
const emit = defineEmits(['changed'])
const say = inject('toast', () => {})

const sym = ref('')
const side = ref('buy')
const qty = ref('')
const price = ref('')
const tab = ref('pending')       // pending | history

async function cancel(id) {
  if (!confirm(`取消订单 #${id}？`)) return
  const r = await api.post('/api/orders/cancel', { account: props.account, id })
  say(r.msg)
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
                @click="tab='pending'">待成交 {{ (orders||[]).length }}</button>
        <button :class="tab==='history' ? 'bg-slate-800 text-white' : 'bg-white'"
                class="border-l border-slate-300 px-2.5 py-1" @click="tab='history'">成交记录</button>
      </div>
    </div>

    <!-- 待成交 -->
    <div v-if="tab==='pending'" class="max-h-[200px] overflow-auto">
      <table v-if="(orders||[]).length" class="tabular w-full text-sm">
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
      <div v-else class="py-6 text-center text-sm text-slate-400">无待成交订单</div>
    </div>

    <!-- 成交记录 -->
    <div v-else class="max-h-[200px] overflow-auto">
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
