<script setup>
import { ref, computed, onMounted, onUnmounted, provide } from 'vue'
import { api, fmt } from './api.js'
import OverviewCards from './components/OverviewCards.vue'
import NavChart from './components/NavChart.vue'
import BacktestPanel from './components/BacktestPanel.vue'
import PositionsTable from './components/PositionsTable.vue'
import OrdersPanel from './components/OrdersPanel.vue'
import RegimeCard from './components/RegimeCard.vue'
import ReportViewer from './components/ReportViewer.vue'
import OpsTable from './components/OpsTable.vue'
import JobLog from './components/JobLog.vue'

const account = ref('large')          // 当前选中账户，驱动全部面板与主题色
const overviewL = ref(null)
const overviewS = ref(null)
const regime = ref(null)
const positions = ref([])
const orders = ref([])
const trades = ref([])
const ops = ref([])
const freshness = ref(null)
const toast = ref('')
let toastTimer = null

function say(msg) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 2600)
}
provide('toast', say)
provide('account', account)

// ---------------------------------------------------------------- 主题色
// 规则：选中账户的持仓——上涨市值占比决定红色覆盖比例，平均涨幅决定鲜艳度；绿色同理。
const theme = computed(() => {
  const rows = positions.value
  if (!rows || !rows.length) return { red: 0, green: 0, redV: 0, greenV: 0 }
  let upMV = 0, downMV = 0, flatMV = 0, upSum = 0, downSum = 0
  for (const r of rows) {
    const mv = Number(r.weight) || 0
    if (r.pct == null) { flatMV += mv; continue }
    if (r.pct > 0) { upMV += mv; upSum += r.pct * mv }
    else if (r.pct < 0) { downMV += mv; downSum += r.pct * mv }
    else flatMV += mv
  }
  const total = upMV + downMV + flatMV || 1
  const avgUp = upMV > 0 ? upSum / upMV : 0
  const avgDown = downMV > 0 ? downSum / downMV : 0
  return {
    red: upMV / total,
    green: downMV / total,
    redV: Math.min(Math.abs(avgUp) / 0.02, 1),   // 平均涨 2% 即最鲜艳
    greenV: Math.min(Math.abs(avgDown) / 0.02, 1)
  }
})
// 切账户时数据未到之前，主题先平滑退到中性，避免"僵住后猛跳"
const themeSuppress = ref(false)
const themeView = computed(() => themeSuppress.value
  ? { red: 0, green: 0, redV: 0, greenV: 0 }
  : theme.value)
const redOpacity = computed(() => (themeView.value.red > 0.001 ? 0.16 + 0.30 * themeView.value.redV : 0))
const greenOpacity = computed(() => (themeView.value.green > 0.001 ? 0.16 + 0.30 * themeView.value.greenV : 0))

// ---------------------------------------------------------------- 数据
async function loadAccountScoped() {
  const acc = account.value
  const [ov, pos, ord, trd, op] = await Promise.all([
    api.get(`/api/overview?account=${acc}`).catch(() => null),
    api.get(`/api/positions?account=${acc}`).catch(() => ({ rows: [] })),
    api.get(`/api/orders?account=${acc}`).catch(() => ({ rows: [] })),
    api.get(`/api/trades?account=${acc}`).catch(() => ({ rows: [] })),
    api.get(`/api/ops?account=${acc}`).catch(() => ({ rows: [] }))
  ])
  if (acc !== account.value) return          // 切换竞态保护
  if (acc === 'large') overviewL.value = ov; else overviewS.value = ov
  positions.value = pos.rows || []
  orders.value = ord.rows || []
  trades.value = trd.rows || []
  ops.value = op.rows || []
}

async function loadCommon() {
  const [l, s, rg, fr] = await Promise.all([
    api.get('/api/overview?account=large').catch(() => null),
    api.get('/api/overview?account=small').catch(() => null),
    api.get('/api/regime').catch(() => null),
    api.get('/api/freshness').catch(() => null)
  ])
  overviewL.value = l
  overviewS.value = s
  regime.value = rg
  freshness.value = fr
}

async function loadAll() {
  await Promise.all([loadCommon(), loadAccountScoped()])
}

let timer = null
onMounted(() => {
  loadAll()
  timer = setInterval(loadAll, 15000)
})
onUnmounted(() => clearInterval(timer))

function switchAccount(acc) {
  if (account.value === acc) return
  account.value = acc
  themeSuppress.value = true
  loadAccountScoped().finally(() => { themeSuppress.value = false })
}

const accLabel = computed(() =>
  account.value === 'large' ? '大账户（100万）' : '小账户（2000元）')
</script>

<template>
  <!-- 主题背景：软边渐变（末端渐隐到 0）+ scaleY 占比 + 透明度=鲜艳度，全程 GPU 合成 -->
  <div class="theme-layer" :style="{
    transform: `scaleY(${themeView.red.toFixed(4)})`,
    background: 'linear-gradient(180deg, rgba(224,49,49,0.9) 0%, rgba(224,49,49,0.32) 55%, rgba(224,49,49,0) 100%)',
    opacity: redOpacity
  }"></div>
  <div class="theme-layer theme-layer--bottom" :style="{
    transform: `scaleY(${themeView.green.toFixed(4)})`,
    background: 'linear-gradient(0deg, rgba(5,150,105,0.9) 0%, rgba(5,150,105,0.32) 55%, rgba(5,150,105,0) 100%)',
    opacity: greenOpacity
  }"></div>

  <div class="min-h-screen text-slate-800">
    <!-- 顶栏 -->
    <header class="sticky top-0 z-20 border-b border-slate-200/70 bg-white/80 backdrop-blur">
      <div class="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-4 py-2.5">
        <h1 class="flex items-center gap-2 text-base font-bold">
          <span class="live-dot inline-block h-2.5 w-2.5 rounded-full bg-sky-500"></span>
          QuantDex 控制台
        </h1>
        <div class="flex overflow-hidden rounded-lg border border-slate-300 text-sm">
          <button :class="account==='large' ? 'bg-slate-800 text-white' : 'bg-white'"
                  class="px-3 py-1.5" @click="switchAccount('large')">大账户</button>
          <button :class="account==='small' ? 'bg-slate-800 text-white' : 'bg-white'"
                  class="border-l border-slate-300 px-3 py-1.5" @click="switchAccount('small')">小账户</button>
        </div>
        <span class="text-xs text-slate-500">{{ accLabel }}</span>
        <div class="ml-auto flex items-center gap-2">
          <span v-if="freshness && !freshness.error"
                class="rounded-full px-2.5 py-1 text-xs"
                :class="freshness.stale ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'">
            数据：{{ freshness.last_closed }} · {{ freshness.up_to_date }}/{{ freshness.sample }} 已更新
          </span>
          <button class="rounded-lg bg-sky-600 px-3 py-1.5 text-sm text-white hover:bg-sky-700"
                  @click="loadAll(); say('已刷新')">刷新</button>
        </div>
      </div>
      <!-- 市场温度条：红/绿宽度 = 持仓涨/跌市值占比 -->
      <div class="flex h-1.5 w-full" :title="`红 ${fmt.pct(themeView.red,1)} / 绿 ${fmt.pct(themeView.green,1)}（持仓市值占比）`">
        <div class="temp-seg" :style="{ width: (themeView.red*100)+'%', backgroundColor: `rgb(224,49,49,${0.55+0.45*themeView.redV})` }"></div>
        <div class="flex-1 bg-slate-200"></div>
        <div class="temp-seg" :style="{ width: (themeView.green*100)+'%', backgroundColor: `rgb(5,150,105,${0.55+0.45*themeView.greenV})` }"></div>
      </div>
    </header>

    <main class="mx-auto max-w-[1400px] space-y-4 px-4 py-4">
      <!-- 账户卡 + 市场状态 -->
      <div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <OverviewCards :large="overviewL" :small="overviewS" :selected="account"
                       @select="switchAccount" />
        <RegimeCard :regime="regime" />
      </div>

      <!-- 净值曲线 -->
      <NavChart :account="account" :label="accLabel" />

      <!-- 回测与样本外（原 15_dashboard 的职能） -->
      <BacktestPanel />

      <!-- 持仓 + 订单 -->
      <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div class="xl:col-span-2">
          <PositionsTable :rows="positions" :account="account" />
        </div>
        <OrdersPanel :orders="orders" :trades="trades" :account="account"
                     @changed="loadAccountScoped" />
      </div>

      <!-- 日报 + 操作记录 + 任务 -->
      <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div class="xl:col-span-2">
          <ReportViewer :account="account" />
        </div>
        <div class="space-y-4">
          <OpsTable :rows="ops" :account="account" />
          <JobLog @done="loadAll" />
        </div>
      </div>

      <footer class="pb-6 pt-2 text-center text-xs text-slate-400">
        QuantDex 本地控制台 · Vue3 + Tailwind · 数据每 15 秒自动刷新 · T+1 开盘撮合口径
      </footer>
    </main>

    <div v-if="toast"
         class="fixed bottom-5 right-5 z-50 rounded-xl bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg">
      {{ toast }}
    </div>
  </div>
</template>
