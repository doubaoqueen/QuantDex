<script setup>
import { ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({ account: String })
const dates = ref([])
const date = ref('')
const text = ref('')

async function loadDates() {
  const r = await api.get(`/api/reports?account=${props.account}`).catch(() => ({ dates: [] }))
  dates.value = r.dates || []
  date.value = dates.value[0] || ''
  loadText()
}
async function loadText() {
  if (!date.value) { text.value = '暂无日报'; return }
  const r = await api.get(`/api/report?account=${props.account}&date=${date.value}`).catch(() => ({ text: '读取失败' }))
  text.value = r.text
}
watch(() => props.account, loadDates, { immediate: true })
</script>

<template>
  <div class="h-full rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex items-center gap-2">
      <span class="text-sm font-semibold">日报</span>
      <span class="text-xs text-slate-400">{{ account === 'large' ? '大账户' : '小账户' }}</span>
      <select v-model="date" @change="loadText"
              class="ml-auto rounded-lg border border-slate-300 px-2 py-1 text-sm focus:border-sky-500 focus:outline-none">
        <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
      </select>
      <a v-if="date" :href="`/api/report?account=${account}&date=${date}`" target="_blank"
         class="text-xs text-sky-600 hover:underline">原始</a>
    </div>
    <pre class="max-h-[380px] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900 p-3 text-[12.5px] leading-relaxed text-slate-200">{{ text }}</pre>
  </div>
</template>
