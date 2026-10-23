<script setup>
import { fmt } from '../api.js'
defineProps({ rows: Array, account: String })
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white/90 p-4 shadow-sm backdrop-blur">
    <div class="mb-2 flex items-center gap-2">
      <span class="text-sm font-semibold">每日操作记录</span>
      <span class="text-xs text-slate-400">一天一条 · {{ account === 'large' ? '大账户' : '小账户' }}</span>
    </div>
    <div class="max-h-[180px] overflow-auto">
      <table class="w-full text-xs">
        <thead class="sticky top-0 bg-slate-50 text-slate-500">
          <tr><th class="px-2 py-1.5 text-left">日期</th><th class="text-left">动作</th><th class="text-left">摘要</th></tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in (rows||[])" :key="i" class="border-t border-slate-100">
            <td class="px-2 py-1.5 whitespace-nowrap text-slate-500">{{ r.date }}</td>
            <td class="px-1 text-slate-600">{{ r.action }}</td>
            <td class="px-2 text-slate-500">{{ r.summary }}</td>
          </tr>
          <tr v-if="!(rows||[]).length"><td colspan="3" class="py-5 text-center text-slate-400">暂无记录</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
