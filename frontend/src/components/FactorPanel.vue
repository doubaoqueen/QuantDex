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

// 分位条的"好坏"：dir=-1 越低越好，dir=+1 越高越好
function tone(dir, rank) {
  if (rank === null || rank === undefined) return 'bg-slate-200'
  const good = dir < 0 ? 1 - rank : rank           // 0~1，越大越好
  if (good >= 0.66) return 'bg-emerald-400'
  if (good >= 0.33) return 'bg-amber-400'
  return 'bg-rose-400'
}
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/80 p-3 backdrop-blur">
    <div class="mb-2 flex flex-wrap items-baseline gap-2">
      <h3 class="text-sm font-semibold text-slate-800">因子</h3>
      <span class="text-[11px] text-slate-400">
        {{ data?.strategy || '' }}<template v-if="data?.date"> · {{ data.date }}</template>
        <template v-if="data?.pool"> · 池 {{ data.pool }} 只</template>
      </span>
      <span v-if="data" class="ml-auto text-[11px] text-slate-400">
        持仓 {{ heldCount }} 只 + 候选前 15
      </span>
    </div>

    <div class="mb-2 flex flex-wrap gap-1.5">
      <span v-for="f in (data?.factors || [])" :key="f.key"
            class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600"
            :title="f.hint">
        {{ f.label }}
        <b :class="f.dir < 0 ? 'text-emerald-600' : 'text-sky-600'">{{ f.dir < 0 ? '↓' : '↑' }}</b>
      </span>
      <span class="text-[11px] text-slate-400">↓越低越好 · ↑越高越好 · 条宽=截面分位</span>
    </div>

    <div v-if="err" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{{ err }}</div>
    <div v-else-if="loading && !data" class="py-6 text-center text-xs text-slate-400">加载因子…</div>

    <div v-else-if="data" class="max-h-[360px] overflow-auto">
      <table class="tabular w-full text-xs">
        <thead class="sticky top-0 bg-slate-50 text-[11px] text-slate-500">
          <tr>
            <th class="px-2 py-1.5 text-left">标的</th>
            <th>状态</th>
            <th v-for="f in data.factors" :key="f.key" class="w-[22%]">{{ f.label }}</th>
            <th>综合分</th>
            <th>池排名</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in data.rows" :key="r.symbol" class="border-t border-slate-100"
              :class="r.held ? 'bg-sky-50/40' : ''">
            <td class="px-2 py-1.5">
              <span class="font-medium">{{ r.name || r.symbol }}</span>
              <span class="ml-1 text-[10px] text-slate-400">{{ r.symbol }}</span>
            </td>
            <td class="text-center">
              <span v-if="r.held" class="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] text-sky-700">持仓</span>
              <span v-else class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">候选</span>
            </td>
            <td v-for="f in data.factors" :key="f.key" class="px-1">
              <div class="flex items-center gap-1">
                <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                  <div class="h-full rounded-full" :class="tone(f.dir, r['r_' + f.key])"
                       :style="{ width: ((r['r_' + f.key] ?? 0) * 100).toFixed(0) + '%' }"></div>
                </div>
                <span class="w-8 shrink-0 text-right text-[10px] text-slate-500">
                  {{ r['r_' + f.key] === null ? '-' : (r['r_' + f.key] * 100).toFixed(0) }}
                </span>
              </div>
              <div class="text-right text-[10px] text-slate-400">
                {{ f.key === 'dy_ttm' ? (r[f.key] * 100).toFixed(1) + '%' : (r[f.key] * 100).toFixed(2) + '%' }}
              </div>
            </td>
            <td class="text-right font-medium">{{ r.score.toFixed(3) }}</td>
            <td class="text-right text-slate-500">{{ r.rank }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="data?.note" class="mt-2 text-[11px] leading-relaxed text-slate-400">{{ data.note }}</p>
  </div>
</template>
