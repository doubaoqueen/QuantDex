<script setup>
// 策略动向（D-072，用户构想）：把 20 日调仓之间的黑箱打开——
// 每天展示策略的影子：调仓节奏 / 行业迁移 / 影子调仓重合度。
// 纪律边界：与流水线完全同分；只展示不构成建议；无任何操作入口（防 D-057 式诱惑）。
import { ref, watch, computed } from 'vue'
import { api, fmt } from '../api.js'

const props = defineProps({ account: String })
const data = ref(null)
const err = ref('')

async function load() {
  try {
    const d = await api.get(`/api/strategy_outlook?account=${props.account}`)
    data.value = d && d.err ? null : d
    if (d && d.err) err.value = d.err
  } catch (e) { err.value = e.message }
}
watch(() => props.account, load, { immediate: true })

const industries = computed(() => {
  if (!data.value) return []
  const h = data.value.industry_held || {}
  const s = data.value.industry_shadow || {}
  const keys = [...new Set([...Object.keys(h), ...Object.keys(s)])]
  const maxH = Math.max(1, ...Object.values(h))
  return keys.map(k => ({ name: k, held: h[k] || 0, shadow: s[k] || 0, maxH }))
    .sort((a, b) => (b.held - a.held) || (b.shadow - a.shadow))
})
const showInd = computed(() => industries.value.slice(0, 9))
const restCount = computed(() => Math.max(0, industries.value.length - 9))
const pace = computed(() => {
  const d = data.value
  if (!d || d.held_days === null || d.held_days === undefined) return null
  return Math.min(100, (d.held_days / d.rebalance) * 100)
})
const driftLabel = computed(() => {
  const p = data.value?.overlap_pct
  if (p === null || p === undefined) return '-'
  if (p >= 0.92) return '漂移很小——下轮大概率小修'
  if (p >= 0.84) return '中度漂移——下轮有集中换手'
  return '大幅漂移——下轮将大换血'
})
const showRows = (list) => (list || []).slice(0, 20)
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-1 flex flex-wrap items-baseline gap-2">
      <h3 class="text-sm font-semibold text-slate-800">策略动向（影子调仓）</h3>
      <span class="tabular text-[11px] text-slate-400" v-if="data?.date">截至 {{ data.date }} · 池 {{ data.pool }} 只</span>
      <span class="ml-auto text-[11px] text-slate-400">仅展示 · 实际执行按 20 日纪律 · 不构成建议</span>
    </div>

    <div v-if="err" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{{ err }}</div>
    <div v-else-if="!data" class="py-5 text-center text-xs text-slate-400">加载中…</div>
    <template v-else>
      <!-- ① 调仓节奏 -->
      <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span class="text-slate-500">调仓节奏</span>
        <div class="h-2 w-44 overflow-hidden rounded-full bg-slate-100">
          <div class="h-full rounded-full bg-sky-500" :style="{ width: (pace ?? 0) + '%' }"></div>
        </div>
        <span class="tabular font-medium text-slate-700">
          {{ data.held_days === null ? '尚无成交' : `已持有 ${data.held_days} / ${data.rebalance} 个交易日` }}
        </span>
        <span class="text-slate-400" v-if="data.last_fill">最近成交 {{ data.last_fill }}</span>
        <span class="text-slate-400" v-if="data.next_sched">下一排程日 {{ data.next_sched }}</span>
      </div>

      <!-- ② 行业迁移对比 -->
      <div class="mt-4">
        <div class="flex items-center gap-3 text-[11px] text-slate-400">
          <span class="font-medium text-slate-600">行业分布（只数）</span>
          <span class="flex items-center gap-1"><i class="inline-block h-2 w-3 rounded bg-sky-400"></i>当前持仓</span>
          <span class="flex items-center gap-1"><i class="inline-block h-2 w-3 rounded bg-violet-400"></i>影子 Top50</span>
        </div>
        <div class="mt-2 space-y-1.5">
          <div v-for="r in showInd" :key="r.name" class="grid grid-cols-[76px_1fr] items-center gap-2">
            <span class="truncate text-xs text-slate-600">{{ r.name }}</span>
            <div class="space-y-0.5">
              <div class="h-2 rounded bg-sky-100">
                <div class="h-full rounded bg-sky-400"
                     :style="{ width: (r.held / r.maxH * 100).toFixed(0) + '%' }"></div>
              </div>
              <div class="h-2 rounded bg-violet-100">
                <div class="h-full rounded bg-violet-400"
                     :style="{ width: (r.shadow / r.maxH * 100).toFixed(0) + '%' }"></div>
              </div>
            </div>
          </div>
          <div v-if="restCount > 0" class="text-[11px] text-slate-400">还有 {{ restCount }} 个行业…</div>
        </div>
      </div>

      <!-- ③ 影子调仓摘要 -->
      <div class="mt-4 rounded-lg border border-slate-100 bg-slate-50/60 p-3">
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span class="text-xs font-medium text-slate-600">影子调仓（若今日执行）</span>
          <span class="tabular text-xs text-slate-700">
            重合度 <b class="text-base" :class="data.overlap_pct >= 0.92 ? 'text-emerald-600' : 'text-amber-600'">
              {{ (data.overlap_pct * 100).toFixed(0) }}%</b>
          </span>
          <span class="text-[11px] text-slate-500">{{ driftLabel }}</span>
          <span class="tabular ml-auto text-xs text-slate-500">
            留任 {{ data.counts.keep }} / 新增 <b class="text-emerald-600">{{ data.counts.new }}</b>
            / 出局 <b class="text-rose-600">{{ data.counts.exit }}</b>
          </span>
        </div>
        <details class="mt-2">
          <summary class="cursor-pointer text-[11px] text-slate-400 hover:text-slate-600">展开新增/出局明细</summary>
          <div class="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <div class="text-[11px] font-medium text-emerald-700">将新增（{{ data.rows_new.length }}）</div>
              <div class="mt-1 max-h-32 overflow-y-auto text-xs">
                <div v-for="r in showRows(data.rows_new)" :key="r.symbol"
                     class="flex justify-between rounded bg-emerald-50 px-2 py-0.5">
                  <span>{{ r.name }} <span class="text-slate-400">{{ r.symbol }}</span></span>
                  <span class="text-slate-400">{{ r.industry }}</span>
                </div>
                <div v-if="!data.rows_new.length" class="text-slate-400">无</div>
              </div>
            </div>
            <div>
              <div class="text-[11px] font-medium text-rose-700">将出局（{{ data.rows_exit.length }}）</div>
              <div class="mt-1 max-h-32 overflow-y-auto text-xs">
                <div v-for="r in showRows(data.rows_exit)" :key="r.symbol"
                     class="flex justify-between rounded bg-rose-50 px-2 py-0.5">
                  <span>{{ r.name }} <span class="text-slate-400">{{ r.symbol }}</span></span>
                  <span class="text-slate-400">{{ r.industry }}</span>
                </div>
                <div v-if="!data.rows_exit.length" class="text-slate-400">无</div>
              </div>
            </div>
          </div>
        </details>
        <p class="mt-2 text-[11px] leading-relaxed text-slate-400">{{ data.note }}</p>
      </div>
    </template>
  </div>
</template>
