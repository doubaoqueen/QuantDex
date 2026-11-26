<script setup>
// 运行日历（D-101，2026-11-24 用户需求）：一眼看清"哪天跑了、哪天被跳过"。
// 每个交易日四行状态：行情分区 / 四账户账本快照 / 四账户日报 / 邮件。
// 动机：11-23 的 19:00 任务未触发，快照/日报/邮件整日静默消失，
// 用户翻净值曲线才发现——缺口必须在打开控制台的第一眼可见。
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'

const emit = defineEmits(['pick'])   // D-103：点交易日 → 弹出当日账户全景

const now = new Date()
const month = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const data = ref(null)
const err = ref(false)
let timer = null

const WEEK = ['一', '二', '三', '四', '五', '六', '日']

async function load() {
  try {
    data.value = await api.get(`/api/calendar?month=${month.value}`)
    err.value = false
  } catch {
    err.value = true
  }
}

function shiftMonth(delta) {
  const [y, m] = month.value.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  month.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  load()
}

onMounted(() => {
  load()
  timer = setInterval(load, 60000)   // 60s：任务跑完后颜色自动翻绿
})
onUnmounted(() => clearInterval(timer))

const cells = computed(() => {
  if (!data.value || !data.value.days) return []
  const pad = Array.from({ length: data.value.first_weekday }, () => null)
  return [...pad, ...data.value.days]
})

const accLabel = { large: '大', small: '小', div: '红', topk: '换' }

function dayCls(d) {
  if (!d) return 'border-transparent'
  if (!d.trading) return 'border-slate-100 bg-slate-50/60 text-slate-300'
  if (d.future) return 'border-slate-200 bg-white text-slate-400'
  if (d.complete) return 'border-emerald-200 bg-emerald-50 text-emerald-800'
  return 'border-orange-300 bg-orange-50 text-orange-800 font-semibold'
}

function dotCls(d) {
  if (!d || !d.trading || d.future) return 'bg-transparent'
  if (d.complete) return 'bg-emerald-500'
  return 'bg-orange-500'
}

function tooltip(d) {
  if (!d || !d.trading) return '非交易日'
  if (d.future) return '未来交易日'
  const miss = []
  if (!d.market) miss.push('行情分区')
  for (const [k, v] of Object.entries(d.snap || {})) if (!v) miss.push(`${k} 快照`)
  for (const [k, v] of Object.entries(d.report || {})) if (!v) miss.push(`${k} 日报`)
  const tail = d.mail == null ? '邮件：未记录（11-24 前无日志）' : (d.mail ? '邮件 ✓' : '邮件 ✗')
  return miss.length ? `${d.date} 缺：${miss.join('、')}；${tail}` : `${d.date} 完整；${tail}`
}

function dayNum(d) { return d ? Number(d.date.slice(-2)) : '' }
function pick(d) { if (d && d.trading && !d.future) emit('pick', d.date) }
const clickable = (d) => d && d.trading && !d.future
</script>

<template>
  <div class="qd-card qd-card--hover">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2">
        <span class="qd-title">运行日历</span>
        <span v-if="data" class="rounded-full px-2 py-0.5 text-xs"
              :class="data.summary.gaps.length
                ? 'bg-orange-100 text-orange-800' : 'bg-emerald-100 text-emerald-800'">
          {{ data.summary.complete }}/{{ data.summary.trading }} 交易日完整
        </span>
        <span v-if="data && data.summary.gaps.length" class="text-xs text-orange-700">
          缺口：{{ data.summary.gaps.join('、') }}
        </span>
      </div>
      <div class="flex items-center gap-1">
        <button class="rounded-md border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
                @click="shiftMonth(-1)">‹</button>
        <span class="min-w-[64px] text-center text-xs font-semibold tabular text-slate-600">{{ month }}</span>
        <button class="rounded-md border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
                @click="shiftMonth(1)">›</button>
      </div>
    </div>

    <div v-if="err" class="mt-2 text-sm text-slate-400">接口异常</div>
    <div v-else-if="!data" class="mt-2 text-sm text-slate-400">加载中…</div>
    <template v-else>
      <div class="mt-3 grid grid-cols-7 gap-1 text-center text-[10px] text-slate-400">
        <span v-for="w in WEEK" :key="w">{{ w }}</span>
      </div>
      <div class="mt-1 grid grid-cols-7 gap-1">
        <div v-for="(d, i) in cells" :key="i"
             class="flex h-9 items-center justify-center rounded-md border text-xs tabular transition-colors"
             :class="[dayCls(d), clickable(d) ? 'cursor-pointer hover:ring-2 hover:ring-sky-300' : '']"
             :title="tooltip(d)"
             @click="pick(d)">
          <span class="flex items-center gap-1">
            <span v-if="d && d.trading && !d.future"
                  class="h-1.5 w-1.5 rounded-full" :class="dotCls(d)"></span>
            <span>{{ dayNum(d) }}</span>
          </span>
        </div>
      </div>
      <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-400">
        <span class="flex items-center gap-1"><span class="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>完整（行情+四账户快照+日报）</span>
        <span class="flex items-center gap-1"><span class="h-1.5 w-1.5 rounded-full bg-orange-500"></span>缺口日（悬停看缺什么）</span>
        <span>灰=非交易日</span>
        <span>点击交易日 → 当日账户全景</span>
        <span v-if="data.mail_known === false">邮件列 11-24 起记录</span>
      </div>
    </template>
  </div>
</template>
