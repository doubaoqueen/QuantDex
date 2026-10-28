<script setup>
import { ref, computed, watch } from 'vue'
import { api, fmt } from '../api.js'

const props = defineProps({ account: String })
const rows = ref([])
const meta = ref({ strategy: '', date: '', cash: null })
const loading = ref(false)
const err = ref('')

async function load() {
  loading.value = true
  err.value = ''
  try {
    const d = await api.get(`/api/candidates?account=${props.account}`)
    rows.value = d.rows || []
    meta.value = { strategy: d.strategy, date: d.date, cash: d.cash }
  } catch (e) {
    err.value = `候选池加载失败：${e.message}`
    rows.value = []
  } finally {
    loading.value = false
  }
}

// 切账户才重算；候选池在后端有 15 分钟缓存，不会每次刷新都跑全池打分
watch(() => props.account, load)
load()

const limitCls = (l) => l === '涨停' ? 'bg-rose-600 text-white' : 'bg-emerald-600 text-white'
const statusCls = (s) => s === '持仓中' ? 'bg-sky-100 text-sky-700'
  : (s === '待成交' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600')
const affordable = (r) => meta.value.cash == null || r.lot_cost <= meta.value.cash
const industries = computed(() => {
  const m = {}
  for (const r of rows.value) {
    const k = r.industry || '未分类'
    m[k] = (m[k] || 0) + 1
  }
  return Object.entries(m).sort((a, b) => b[1] - a[1])
})
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <span class="text-sm font-semibold">备选标的</span>
      <span class="text-xs text-slate-400">
        {{ meta.strategy || '全池海选' }}
        <template v-if="meta.date"> · 截至 {{ meta.date }}</template>
        <template v-if="meta.cash != null"> · 可用现金 {{ fmt.n(meta.cash, 0) }} 元</template>
      </span>
      <div class="ml-auto flex items-center gap-2">
        <span v-if="industries.length" class="hidden text-[11px] text-slate-400 md:inline">
          行业：{{ industries.map(([k, v]) => `${k}${v}`).join(' / ') }}
        </span>
        <button class="rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:border-sky-400"
                :disabled="loading" @click="load">重算</button>
      </div>
    </div>

    <div v-if="err" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{{ err }}</div>

    <div v-else-if="loading" class="py-8 text-center text-sm text-slate-400">
      <span class="live-dot">●</span> 全池打分中（首次约 10~15 秒，之后 15 分钟内走缓存）…
    </div>

    <div v-else-if="rows.length" class="max-h-[320px] overflow-auto rounded-lg border border-slate-100">
      <table class="tabular w-full text-sm">
        <thead class="sticky top-0 bg-slate-50 text-xs text-slate-500">
          <tr>
            <th class="px-3 py-2 text-left">#</th>
            <th class="px-3 py-2 text-left">标的</th>
            <th class="px-3 py-2 text-left">行业</th>
            <th class="px-3 py-2 text-right">分数</th>
            <th class="px-3 py-2 text-right">现价</th>
            <th class="px-3 py-2 text-right">今日</th>
            <th class="px-3 py-2 text-right">一手成本</th>
            <th class="px-3 py-2 text-center">状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.symbol" class="border-t border-slate-100 hover:bg-sky-50/50">
            <td class="px-3 py-1.5 text-slate-400">{{ r.rank }}</td>
            <td class="px-3 py-1.5">
              <span class="font-medium">{{ r.name || r.symbol }}</span>
              <span class="ml-1.5 text-xs text-slate-400">{{ r.symbol }}</span>
              <span v-if="r.limit" class="ml-1.5 rounded-full px-1.5 py-0.5 text-[10px]"
                    :class="limitCls(r.limit)">{{ r.limit }}</span>
            </td>
            <td class="px-3 py-1.5 text-xs text-slate-500">{{ r.industry || '-' }}</td>
            <td class="px-3 py-1.5 text-right">{{ r.score.toFixed(3) }}</td>
            <td class="px-3 py-1.5 text-right">{{ fmt.n(r.close) }}</td>
            <td class="px-3 py-1.5 text-right" :class="fmt.signCls(r.pct)">{{ fmt.pct(r.pct) }}</td>
            <td class="px-3 py-1.5 text-right" :class="affordable(r) ? '' : 'text-rose-500'">
              {{ fmt.n(r.lot_cost, 0) }}
            </td>
            <td class="px-3 py-1.5 text-center">
              <span class="rounded-full px-2 py-0.5 text-[11px]" :class="statusCls(r.status)">{{ r.status }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="py-6 text-center text-sm text-slate-400">暂无候选数据</div>

    <div class="mt-1.5 text-[11px] text-slate-400">
      口径：与流水线同分的截面 rank 合成（低换手 + 低波 + 反转；红利版含股息率），只展示前 15 名。
      <b>仅供人工挂单参考</b>——策略本身按 20 日调仓自动执行，不在候选池里挑票。
    </div>
  </div>
</template>
