<script setup>
import { ref, watch, computed } from 'vue'
import { api } from '../api.js'

const props = defineProps({ account: String })

const data = ref(null)
const err = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  err.value = ''
  try {
    const d = await api.get(`/api/factors?account=${props.account}`)
    if (d && d.err) { err.value = d.err; data.value = null } else { data.value = d }
  } catch (e) {
    err.value = `加载失败：${e.message}`
  } finally {
    loading.value = false
  }
}

watch(() => props.account, load, { immediate: true })

const heldCount = computed(() => (data.value?.rows || []).filter(r => r.held).length)

// 排序：持仓在前（综合分降序）→ 候选（综合分降序）。
// 此前后端按全池代码序返回持仓行，用户看到的"按编号乱排"即来源于此（2026-11-08 修正）。
const sortedRows = computed(() => {
  const rows = [...(data.value?.rows || [])]
  rows.sort((a, b) => (b.held - a.held) || (b.score - a.score))
  return rows
})

// 分位条的"好坏"：dir=-1 越低越好，dir=+1 越高越好
// good = 方向调整后的全池百分位（0~1，越大越好）→ 绿 ≥2/3、黄 ≥1/3、红其余
function tone(dir, rank) {
  if (rank === null || rank === undefined) return { bar: 'bg-slate-200', text: 'text-slate-400', cell: '' }
  const good = dir < 0 ? 1 - rank : rank
  if (good >= 0.66) return { bar: 'bg-emerald-500', text: 'text-emerald-600', cell: 'bg-emerald-50/70' }
  if (good >= 0.33) return { bar: 'bg-amber-500', text: 'text-amber-600', cell: 'bg-amber-50/70' }
  return { bar: 'bg-rose-500', text: 'text-rose-600', cell: 'bg-rose-50/70' }
}

// 综合分颜色：按池排名分位（rank 越小越好）
function scoreTone(rank, pool) {
  if (!rank || !pool) return 'text-slate-600'
  const p = rank / pool
  if (p <= 0.05) return 'text-emerald-600'
  if (p <= 0.15) return 'text-amber-600'
  return 'text-rose-600'
}

// 原值单位的坑：turnover_20 已经是百分数（0.67 = 0.67%）；vol_20 是**日**标准差，
// 需 ×√250 年化才符合直觉（0.013 → 20.6%）；rev_5/dy_ttm 是比率，×100 成百分数。
function rawText(key, v) {
  if (v === null || v === undefined) return '-'
  if (key === 'turnover_20') return v.toFixed(2) + '%'
  if (key === 'vol_20') return (v * Math.sqrt(250) * 100).toFixed(1) + '%'
  return (v * 100).toFixed(2) + '%'
}
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/80 p-3 backdrop-blur">
    <div class="mb-2 flex flex-wrap items-baseline gap-2">
      <h3 class="text-sm font-semibold text-slate-800">因子</h3>
      <span class="text-[11px] text-slate-400">
        {{ data?.strategy || '' }}<template v-if="data?.date"> · {{ data.date }}</template>
        <template v-if="data?.mechanism"> · {{ data.mechanism }}</template>
        <template v-if="data?.pool"> · 池 {{ data.pool }} 只</template>
      </span>
      <span v-if="data" class="ml-auto text-[11px] text-slate-400">
        持仓 {{ heldCount }} 只 + 候选前 15 · 持仓在前、组内按综合分降序
      </span>
    </div>

    <div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span v-for="f in (data?.factors || [])" :key="f.key"
            class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600"
            :title="f.hint">
        {{ f.label }}
        <b :class="f.dir < 0 ? 'text-emerald-600' : 'text-sky-600'">{{ f.dir < 0 ? '↓' : '↑' }}</b>
      </span>
      <span class="flex items-center gap-1 text-[11px] text-slate-400">
        颜色=方向调整后 vs 全池：
        <i class="inline-block h-2 w-2 rounded-full bg-emerald-500"></i>占优
        <i class="inline-block h-2 w-2 rounded-full bg-amber-500"></i>中性
        <i class="inline-block h-2 w-2 rounded-full bg-rose-500"></i>劣势
      </span>
    </div>

    <div v-if="err" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{{ err }}</div>
    <div v-else-if="loading && !data" class="py-6 text-center text-xs text-slate-400">加载因子…</div>

    <div v-else-if="data" class="max-h-[420px] overflow-auto">
      <table class="tabular w-full text-xs">
        <thead class="sticky top-0 z-10 bg-slate-50 text-[11px] text-slate-500">
          <tr>
            <th class="px-2 py-1.5 text-left">标的</th>
            <th>状态</th>
            <th v-for="f in data.factors" :key="f.key" class="w-[22%]">{{ f.label }}</th>
            <th>综合分</th>
            <th>池排名</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, idx) in sortedRows" :key="r.symbol"
              class="border-t border-slate-100"
              :class="[r.held ? 'bg-sky-50/40' : '',
                       idx > 0 && sortedRows[idx - 1].held !== r.held ? 'border-t-2 border-t-slate-300' : '']">
            <td class="px-2 py-1.5">
              <span class="font-medium">{{ r.name || r.symbol }}</span>
              <span class="ml-1 text-[10px] text-slate-400">{{ r.symbol }}</span>
            </td>
            <td class="text-center">
              <span v-if="r.held" class="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] text-sky-700">持仓</span>
              <span v-else class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">候选</span>
            </td>
            <td v-for="f in data.factors" :key="f.key"
                class="px-1.5" :class="tone(f.dir, r['r_' + f.key]).cell">
              <div class="flex items-center gap-1">
                <div class="h-2 flex-1 overflow-hidden rounded-full bg-slate-200/80">
                  <div class="h-full rounded-full" :class="tone(f.dir, r['r_' + f.key]).bar"
                       :style="{ width: ((r['r_' + f.key] ?? 0) * 100).toFixed(0) + '%' }"></div>
                </div>
                <span class="w-8 shrink-0 text-right font-medium"
                      :class="tone(f.dir, r['r_' + f.key]).text">
                  {{ r['r_' + f.key] === null ? '-' : (r['r_' + f.key] * 100).toFixed(0) }}
                </span>
              </div>
              <div class="text-right text-[10px] text-slate-400">
                {{ rawText(f.key, r[f.key]) }}
              </div>
            </td>
            <td class="text-right font-semibold" :class="scoreTone(r.rank, data.pool)">
              {{ r.score.toFixed(3) }}
            </td>
            <td class="text-right text-slate-500">{{ r.rank }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="data?.note" class="mt-2 text-[11px] leading-relaxed text-slate-400">{{ data.note }}</p>
  </div>
</template>
