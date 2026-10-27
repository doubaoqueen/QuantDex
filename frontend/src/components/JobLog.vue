<script setup>
import { ref, inject, onUnmounted } from 'vue'
import { api } from '../api.js'

const emit = defineEmits(['done'])
const say = inject('toast', () => {})

const running = ref(false)
const name = ref('')
const log = ref('')
const err = ref('')

// 后端任务名 -> 人话（自动链会跑 daily/report，不再是只有 update/pipeline）
const LABELS = {
  update: '更新行情',
  spot: '检查行情',
  daily: '更新行情 + 流水线 + 日报邮件',
  report: '补流水线 + 日报邮件',
  pipeline: '流水线（不发信）'
}
const label = (n) => LABELS[n] || n || ''

let timer = null

async function poll() {
  try {
    const s = await api.get('/api/run/status')
    running.value = s.running
    name.value = s.name || ''
    log.value = s.log || ''
    err.value = ''
  } catch (e) {
    // 401/403/500 都别静默：之前接口一失败，页面看着像"点了没反应"
    err.value = `读取任务状态失败：${e.message}`
    running.value = false
    return
  }
  if (running.value) {
    timer = setTimeout(poll, 3000)
  } else {
    clearTimeout(timer)
    if (name.value) {
      say(`任务完成：${label(name.value)}`)
      emit('done')
    }
  }
}

async function run(kind) {
  err.value = ''
  try {
    const r = kind === 'update'
      ? await api.post('/api/update')
      : await api.post('/api/run', { job: 'pipeline' })
    say(r.msg || '已提交')
    if (r && r.ok === false) return      // 后端明确拒绝（已有任务在跑），不必去轮询
    clearTimeout(timer)
    poll()
  } catch (e) {
    err.value = `操作失败：${e.message}`
    say(`操作失败：${e.message}`)
  }
}

poll()
onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex items-center gap-2">
      <span class="text-sm font-semibold">后台任务</span>
      <span v-if="running" class="live-dot rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-700">
        运行中：{{ label(name) }}
      </span>
      <span v-else class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">空闲</span>
    </div>
    <div class="mb-2 flex gap-2">
      <button class="rounded-lg bg-sky-600 px-3 py-1.5 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
              :disabled="running" @click="run('update')">更新行情</button>
      <button class="rounded-lg bg-slate-700 px-3 py-1.5 text-xs text-white hover:bg-slate-800 disabled:opacity-40"
              :disabled="running" @click="run('pipeline')">跑流水线</button>
    </div>
    <div v-if="err"
         class="mb-2 rounded-lg bg-red-50 px-2.5 py-1.5 text-[11.5px] leading-snug text-red-700">
      {{ err }}
    </div>
    <pre class="max-h-[160px] overflow-auto rounded-lg bg-slate-900 p-2.5 text-[11.5px] leading-snug text-slate-300">{{ log || '（暂无日志）' }}</pre>
  </div>
</template>
