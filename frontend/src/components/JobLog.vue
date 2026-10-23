<script setup>
import { ref, inject, onUnmounted } from 'vue'
import { api } from '../api.js'

const emit = defineEmits(['done'])
const say = inject('toast', () => {})

const running = ref(false)
const name = ref('')
const log = ref('')

let timer = null

async function poll() {
  const s = await api.get('/api/run/status').catch(() => null)
  if (!s) return
  running.value = s.running
  name.value = s.name || ''
  log.value = s.log || ''
  if (s.running) {
    timer = setTimeout(poll, 3000)
  } else {
    clearTimeout(timer)
    if (name.value) { say(`任务完成：${name.value}`); emit('done') }
  }
}

async function run(kind) {
  const r = kind === 'update'
    ? await api.post('/api/update')
    : await api.post('/api/run', { job: 'pipeline' })
  say(r.msg)
  clearTimeout(timer)
  poll()
}

poll()
onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex items-center gap-2">
      <span class="text-sm font-semibold">后台任务</span>
      <span v-if="running" class="live-dot rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-700">
        运行中：{{ name }}
      </span>
      <span v-else class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">空闲</span>
    </div>
    <div class="mb-2 flex gap-2">
      <button class="rounded-lg bg-sky-600 px-3 py-1.5 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
              :disabled="running" @click="run('update')">更新行情</button>
      <button class="rounded-lg bg-slate-700 px-3 py-1.5 text-xs text-white hover:bg-slate-800 disabled:opacity-40"
              :disabled="running" @click="run('pipeline')">跑流水线</button>
    </div>
    <pre class="max-h-[160px] overflow-auto rounded-lg bg-slate-900 p-2.5 text-[11.5px] leading-snug text-slate-300">{{ log || '（暂无日志）' }}</pre>
  </div>
</template>
