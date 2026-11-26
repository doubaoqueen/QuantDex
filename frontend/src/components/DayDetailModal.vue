<script setup>
// 当日账户全景弹窗（D-103）：点日历某天 → 该日四账户的净值/收益/持仓/成交/日报。
// 数据来自账本快照（按当日收盘价），缺口日如实显示"该账户当日无快照"。
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, fmt } from '../api.js'

const props = defineProps({ req: String })   // 日期字符串，null = 关闭
const emit = defineEmits(['close'])
const kline = inject('kline', { open: () => {} })

const data = ref(null)
const loading = ref(false)
const err = ref('')
const acc = ref('large')
const showReport = ref(false)

watch(() => props.req, (d) => { if (d) load() })

async function load() {
  loading.value = true; err.value = ''; data.value = null; showReport.value = false
  try {
    data.value = await api.get(`/api/day_detail?date=${props.req}`)
    if (data.value && data.value.error) err.value = data.value.error
    else acc.value = data.value.accounts?.[0]?.key || 'large'
  } catch (e) { err.value = `加载失败：${e.message}` }
  loading.value = false
}

function close() { emit('close') }
function onKey(e) { if (e.key === 'Escape') close() }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))

const cur = computed(() => (data.value?.accounts || []).find(a => a.key === acc.value))
const positions = computed(() => (cur.value?.positions || []).slice(0, 12))
const fills = computed(() => cur.value?.fills || [])
const hasAny = computed(() => (data.value?.accounts || []).some(a => a.nav != null))
const fillsCount = (a) => (a.fills || []).length
function sideCls(s) { return s === 'buy' ? 'text-red-600' : 'text-emerald-600' }
</script>

<template>
  <Teleport to="body">
    <div v-if="req" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
         @click.self="close">
      <div class="flex max-h-[88vh] w-full max-w-4xl flex-col rounded-2xl bg-white p-5 shadow-2xl">
        <div class="mb-2 flex flex-wrap items-baseline gap-2">
          <span class="text-base font-bold">{{ req }} 账户全景</span>
          <span v-if="data" class="flex items-center gap-1.5 text-[11px]">
            <span class="rounded-full px-2 py-0.5" :class="data.trading ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-500'">
              {{ data.trading ? '交易日' : '非交易日' }}</span>
            <span v-if="data.trading && !hasAny" class="rounded-full bg-orange-100 px-2 py-0.5 text-orange-700">缺口日（流水缺失）</span>
            <span v-if="data.mail != null" class="rounded-full px-2 py-0.5"
                  :class="data.mail ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'">
              邮件{{ data.mail ? '已发' : '未记录' }}</span>
          </span>
          <button class="ml-auto rounded-lg bg-slate-100 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-200"
                  @click="close">关闭</button>
        </div>

        <div v-if="loading" class="py-12 text-center text-sm text-slate-400">加载当日数据…</div>
        <div v-else-if="err" class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{{ err }}</div>
        <template v-else-if="data">
          <!-- 账户卡：点选切换 -->
          <div class="grid grid-cols-2 gap-2 md:grid-cols-4">
            <button v-for="a in data.accounts" :key="a.key"
                    class="rounded-lg border p-2.5 text-left transition"
                    :class="a.key === acc ? 'border-sky-400 bg-sky-50' : 'border-slate-200 hover:border-slate-300'"
                    @click="acc = a.key; showReport = false">
              <div class="text-xs text-slate-500">{{ a.label }}</div>
              <div v-if="a.nav != null" class="mt-0.5 text-sm font-bold tabular">
                {{ fmt.n(a.nav, 2) }}
                <span class="text-[11px] font-semibold" :class="fmt.signCls(a.day_ret)">
                  {{ a.day_ret != null ? fmt.pct(a.day_ret) : '-' }}
                </span>
              </div>
              <div v-else class="mt-0.5 text-sm font-semibold text-orange-600">当日无快照</div>
              <div class="text-[11px] text-slate-400">
                {{ a.n_pos != null ? `${a.n_pos} 持仓` : '—' }}
                {{ fillsCount(a) ? ` · ${fillsCount(a)} 笔成交` : '' }}
              </div>
            </button>
          </div>

          <div class="mt-3 min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            <template v-if="cur">
              <div v-if="cur.nav == null" class="rounded-lg bg-orange-50 px-3 py-2 text-sm text-orange-700">
                该账户 {{ req }} 没有账本快照——若为交易日，这是缺口日（D-102 自动补流水会在下次日更时追平）。
              </div>
              <template v-else>
                <div class="flex flex-wrap gap-x-5 gap-y-1 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
                  <span>现金 <b class="tabular">{{ fmt.n(cur.cash, 2) }}</b></span>
                  <span>市值 <b class="tabular">{{ fmt.n(cur.mv, 2) }}</b></span>
                  <span>累计 <b class="tabular" :class="fmt.signCls(cur.cum_ret)">{{ fmt.pct(cur.cum_ret) }}</b></span>
                  <span>持仓 {{ cur.n_pos }} 只</span>
                </div>

                <!-- 当日成交 -->
                <div v-if="fills.length">
                  <div class="mb-1 text-xs font-semibold text-slate-500">当日成交（{{ fills.length }} 笔）</div>
                  <div class="max-h-32 overflow-y-auto rounded-lg border border-slate-200">
                    <table class="w-full text-xs tabular">
                      <thead class="sticky top-0 bg-slate-50 text-slate-400">
                        <tr><th class="px-2 py-1 text-left">代码</th><th class="px-2 py-1">方向</th>
                            <th class="px-2 py-1 text-right">数量</th><th class="px-2 py-1 text-right">价格</th>
                            <th class="px-2 py-1 text-left">状态</th></tr>
                      </thead>
                      <tbody>
                        <tr v-for="(f, i) in fills" :key="i" class="border-t border-slate-100">
                          <td class="cursor-pointer px-2 py-1 text-left text-sky-700 hover:underline"
                              @click="kline.open(f.symbol)">{{ f.symbol }}</td>
                          <td class="px-2 py-1 text-center" :class="sideCls(f.side)">
                            {{ f.side === 'buy' ? '买' : '卖' }}</td>
                          <td class="px-2 py-1 text-right">{{ fmt.n(f.qty, 0) }}</td>
                          <td class="px-2 py-1 text-right">{{ fmt.n(f.price, 2) }}</td>
                          <td class="px-2 py-1 text-left text-slate-500">{{ f.status }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <!-- 持仓 -->
                <div v-if="positions.length">
                  <div class="mb-1 text-xs font-semibold text-slate-500">
                    持仓（按权重前 {{ positions.length }}{{ cur.n_pos > 12 ? ` / ${cur.n_pos}` : '' }}，点代码看走势）
                  </div>
                  <div class="max-h-52 overflow-y-auto rounded-lg border border-slate-200">
                    <table class="w-full text-xs tabular">
                      <thead class="sticky top-0 bg-slate-50 text-slate-400">
                        <tr><th class="px-2 py-1 text-left">代码</th><th class="px-2 py-1 text-right">股数</th>
                            <th class="px-2 py-1 text-right">收盘</th><th class="px-2 py-1 text-right">当日涨跌</th>
                            <th class="px-2 py-1 text-right">权重</th></tr>
                      </thead>
                      <tbody>
                        <tr v-for="p in positions" :key="p.symbol" class="border-t border-slate-100">
                          <td class="cursor-pointer px-2 py-1 text-left text-sky-700 hover:underline"
                              @click="kline.open(p.symbol)">{{ p.symbol }}</td>
                          <td class="px-2 py-1 text-right">{{ fmt.n(p.shares, 0) }}</td>
                          <td class="px-2 py-1 text-right">{{ fmt.n(p.close, 2) }}</td>
                          <td class="px-2 py-1 text-right" :class="fmt.signCls(p.pct)">{{ fmt.pct(p.pct) }}</td>
                          <td class="px-2 py-1 text-right text-slate-500">
                            {{ p.weight != null ? (p.weight * 100).toFixed(1) + '%' : '-' }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <!-- 日报 -->
                <div v-if="cur.report">
                  <button class="rounded-lg border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                          @click="showReport = !showReport">
                    {{ showReport ? '收起日报' : '查看当日日报' }}
                  </button>
                  <pre v-if="showReport"
                       class="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900 p-3 text-[11px] leading-snug text-slate-300">{{ cur.report }}</pre>
                </div>
              </template>
            </template>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>
