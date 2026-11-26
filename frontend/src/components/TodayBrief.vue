<script setup>
// 今日要点（D-114，方案 A 总览页）：当日日报的"账户"节摘要卡。
// 日报全文被踢出控制台（用户裁决：功能与其他面板重复）——摘要留总览，
// 全文由日历点选（DayDetailModal）承担，本卡的"查看完整日报"也跳那里。
import { ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({ account: String })
const emit = defineEmits(['open'])

const date = ref('')
const rows = ref([])
const loading = ref(false)
const err = ref('')

async function load() {
  loading.value = true; err.value = ''; rows.value = []; date.value = ''
  try {
    const r = await api.get(`/api/reports?account=${props.account}`)
    const d0 = (r.dates || [])[0]
    if (!d0) { err.value = '暂无日报'; loading.value = false; return }
    const t = await api.get(`/api/report?account=${props.account}&date=${d0}`)
    date.value = d0
    // 解析「## 账户」一节（到下一个 ## 为止）的键值行
    const m = /## 账户\n([\s\S]*?)(?=\n## |$)/.exec(t.text || '')
    if (m) {
      rows.value = m[1].split('\n')
        .filter(l => l.startsWith('- '))
        .map(l => l.slice(2).split(/: (.+)/, 2))
        .filter(kv => kv.length === 2)
        .map(([k, v]) => ({ k, v }))
    }
    if (!rows.value.length) err.value = '日报节选为空'
  } catch (e) { err.value = `加载失败：${e.message}` }
  loading.value = false
}

watch(() => props.account, load, { immediate: true })

const strong = (k) => /总净值|今日盈亏/.test(k)
const numCls = (v) => String(v).startsWith('+') ? 'text-red-600'
  : String(v).startsWith('-') ? 'text-emerald-600' : 'text-slate-800'
</script>

<template>
  <div class="qd-card qd-card--hover">
    <div class="flex items-center justify-between">
      <span class="qd-title">今日要点<template v-if="date"> · {{ date.slice(5) }}</template></span>
      <button v-if="date" class="qd-btn qd-btn--ghost"
              @click="emit('open', date)">查看完整日报</button>
    </div>

    <!-- 骨架屏（D-114 交互反馈包：替代"加载中…"文字） -->
    <div v-if="loading" class="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
      <div v-for="i in 8" :key="i" class="h-8 animate-pulse rounded-lg bg-slate-100"></div>
    </div>
    <div v-else-if="err" class="mt-2 text-xs text-slate-400">{{ err }}</div>
    <div v-else class="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 md:grid-cols-4">
      <div v-for="r in rows" :key="r.k" class="flex items-baseline justify-between gap-2 border-b border-dashed border-slate-100 pb-1">
        <span class="text-[11px] text-slate-500">{{ r.k }}</span>
        <span class="tabular text-xs font-semibold"
              :class="[strong(r.k) ? 'text-base ' : '', numCls(r.v)]">{{ r.v }}</span>
      </div>
    </div>
  </div>
</template>
