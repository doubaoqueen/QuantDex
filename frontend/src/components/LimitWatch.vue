<script setup>
// 涨跌停观察（D-063 用户需求）：全池视角——今天市场上谁在板上。
// 持仓行的涨跌停徽章只在自己持仓触板时可见；这里补市场视角，收盘口径。
import { onMounted, ref } from 'vue'
import { api, fmt } from '../api.js'

const data = ref(null)
const err = ref(false)
onMounted(async () => {
  try { data.value = await api.get('/api/limit_watch') } catch { err.value = true }
})
const show = (list) => (list || []).slice(0, 12)
const rest = (list) => (list || []).length - 12
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="flex items-center justify-between">
      <span class="text-xs font-medium text-slate-500">涨跌停观察（全池）</span>
      <span class="tabular text-xs text-slate-400" v-if="data?.date">{{ data.date }}</span>
    </div>

    <div v-if="err" class="mt-2 text-sm text-slate-400">接口异常</div>
    <div v-else-if="!data" class="mt-2 text-sm text-slate-400">加载中…</div>
    <div v-else-if="data.note && !data.up.length && !data.down.length"
         class="mt-2 text-sm text-slate-400">{{ data.note }}</div>
    <template v-else>
      <div class="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
        <!-- 涨停 -->
        <div>
          <div class="flex items-center gap-2">
            <span class="rounded-full bg-rose-600 px-2 py-0.5 text-xs font-semibold text-white">涨停</span>
            <span class="tabular text-xs text-slate-400">{{ data.up.length }} 只</span>
          </div>
          <div class="mt-2 max-h-44 space-y-1 overflow-y-auto pr-1">
            <div v-for="x in show(data.up)" :key="x.symbol"
                 class="flex items-center justify-between rounded-md bg-rose-50 px-2 py-1 text-sm">
              <span class="truncate">
                <span class="font-medium text-slate-700">{{ x.name }}</span>
                <span class="tabular ml-1 text-xs text-slate-400">{{ x.symbol }}</span>
                <span class="ml-1 text-xs text-slate-400">{{ x.industry }}</span>
              </span>
              <span class="tabular font-medium text-rose-600">{{ fmt.pct(x.pct) }}</span>
            </div>
            <div v-if="rest(data.up) > 0" class="pl-2 text-xs text-slate-400">还有 {{ rest(data.up) }} 只…</div>
            <div v-if="!data.up.length" class="text-xs text-slate-400">无</div>
          </div>
        </div>
        <!-- 跌停 -->
        <div>
          <div class="flex items-center gap-2">
            <span class="rounded-full bg-emerald-600 px-2 py-0.5 text-xs font-semibold text-white">跌停</span>
            <span class="tabular text-xs text-slate-400">{{ data.down.length }} 只</span>
          </div>
          <div class="mt-2 max-h-44 space-y-1 overflow-y-auto pr-1">
            <div v-for="x in show(data.down)" :key="x.symbol"
                 class="flex items-center justify-between rounded-md bg-emerald-50 px-2 py-1 text-sm">
              <span class="truncate">
                <span class="font-medium text-slate-700">{{ x.name }}</span>
                <span class="tabular ml-1 text-xs text-slate-400">{{ x.symbol }}</span>
                <span class="ml-1 text-xs text-slate-400">{{ x.industry }}</span>
              </span>
              <span class="tabular font-medium text-emerald-600">{{ fmt.pct(x.pct) }}</span>
            </div>
            <div v-if="rest(data.down) > 0" class="pl-2 text-xs text-slate-400">还有 {{ rest(data.down) }} 只…</div>
            <div v-if="!data.down.length" class="text-xs text-slate-400">无</div>
          </div>
        </div>
      </div>
      <div class="mt-2 text-xs text-slate-400" v-if="data.note">{{ data.note }}</div>
    </template>
  </div>
</template>
