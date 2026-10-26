<script setup>
import { ref, computed, onMounted, onUnmounted, provide } from 'vue'
import { api, fmt } from './api.js'
import OverviewCards from './components/OverviewCards.vue'
import SchoolView from './components/SchoolView.vue'
import NavChart from './components/NavChart.vue'
import BacktestPanel from './components/BacktestPanel.vue'
import PositionsTable from './components/PositionsTable.vue'
import OrdersPanel from './components/OrdersPanel.vue'
import RegimeCard from './components/RegimeCard.vue'
import ReportViewer from './components/ReportViewer.vue'
import OpsTable from './components/OpsTable.vue'
import JobLog from './components/JobLog.vue'

const accounts = [
  { key: 'large', label: '大账户（100万）', strategy: '现行版 · 低换手+低波+反转' },
  { key: 'small', label: '小账户（2000元 · 执行验证）', strategy: '现行版 · 低换手+低波+反转' },
  { key: 'div', label: '红利账户（100万）', strategy: '红利版 · +股息率' },
  { key: 'topk', label: '换手账户（100万）', strategy: 'TopkDropout · 换手上界' }
]
// 按策略分组：同策略可有多个账户（下拉分组展示）
const strategyGroups = (() => {
  const seen = new Map()
  for (const a of accounts) {
    if (!seen.has(a.strategy)) seen.set(a.strategy, [])
    seen.get(a.strategy).push(a)
  }
  return [...seen.entries()].map(([name, accs]) => ({ name, accs }))
})()
const view = ref('console')   // console | school
const account = ref('large')          // 当前选中账户，驱动全部面板与主题色
const overviews = ref({})             // key -> overview
const regime = ref(null)
const positions = ref([])
const orders = ref([])
const trades = ref([])
const ops = ref([])
const alerts = ref([])
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
  const [ov, pos, ord, trd, op, al] = await Promise.all([
    api.get(`/api/overview?account=${acc}`).catch(() => null),
    api.get(`/api/positions?account=${acc}`).catch(() => ({ rows: [] })),
    api.get(`/api/orders?account=${acc}`).catch(() => ({ rows: [] })),
    api.get(`/api/trades?account=${acc}`).catch(() => ({ rows: [] })),
    api.get(`/api/ops?account=${acc}`).catch(() => ({ rows: [] })),
    api.get(`/api/position_alerts?account=${acc}`).catch(() => ({ rows: [] }))
  ])
  if (acc !== account.value) return          // 切换竞态保护
  overviews.value = { ...overviews.value, [acc]: ov }
  positions.value = pos.rows || []
  orders.value = ord.rows || []
  trades.value = trd.rows || []
  ops.value = op.rows || []
  alerts.value = al.rows || []
}

async function loadCommon() {
  const [rg, fr, ...ovs] = await Promise.all([
    api.get('/api/regime').catch(() => null),
    api.get('/api/freshness').catch(() => null),
    ...accounts.map(a => api.get(`/api/overview?account=${a.key}`).catch(() => null))
  ])
  regime.value = rg
  freshness.value = fr
  overviews.value = Object.fromEntries(accounts.map((a, i) => [a.key, ovs[i]]))
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
  accounts.find(a => a.key === account.value)?.label || '')
const currentStrategy = computed(() =>
  accounts.find(a => a.key === account.value)?.strategy || '')
const menuOpen = ref(false)   // 兼容保留
const openGroup = ref(null)   // 触屏点击兜底：一级菜单
const subOpen = ref(null)     // 触屏点击兜底：二级菜单

// 巧思：账户行背景色 = 盈亏状态（A 股习惯：盈利红 / 亏损绿 / 未开仓灰）
function rowCls(o) {
  const c = o && o.cum_ret != null ? o.cum_ret : null
  if (c == null || c === 0) return 'bg-slate-400'
  return c > 0 ? 'bg-gradient-to-r from-rose-600 to-red-500' : 'bg-gradient-to-r from-emerald-700 to-emerald-600'
}
function isGroupActive(g) { return g.accs.some(a => a.key === account.value) }
function shortStrategy(name) { return name.split(' · ')[0] }
function cumText(o) {
  const c = o && o.cum_ret != null ? o.cum_ret : null
  if (c == null) return '…'
  return (c > 0 ? '+' : '') + (c * 100).toFixed(2) + '%'
}
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
    <!-- 量化学院视图（独立于控制台数据面板） -->
    <SchoolView v-if="view==='school'" class="fixed inset-0 z-40 overflow-auto bg-slate-900" />
    <div v-else>
    <!-- 顶栏 -->
    <header class="sticky top-0 z-20 border-b border-slate-200/70 bg-white/80 backdrop-blur">
      <div class="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-4 py-2.5">
        <h1 class="flex items-center gap-2 text-base font-bold">
          <button :class="view==='console' ? 'text-white' : 'text-slate-400'"
                  class="rounded px-1 transition" @click="view='console'">控制台</button>
          <span class="text-slate-600">/</span>
          <button :class="view==='school' ? 'text-white' : 'text-slate-400'"
                  class="rounded px-1 transition" @click="view='school'">量化学院</button>
        </h1>
        <div class="group relative">
          <button class="flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium shadow-sm hover:border-sky-400"
                  @click="openGroup = openGroup ? null : 'root'">
            <span>{{ shortStrategy(currentStrategy) }}</span>
            <span class="text-xs text-slate-500">{{ accLabel }}</span>
            <span class="text-[10px] text-slate-400 transition-transform duration-200"
                  :class="(openGroup || menuOpen) && 'rotate-180'">▼</span>
          </button>
          <!-- 一级：全部策略（悬停触发按钮或面板内均保持展开） -->
          <div v-if="openGroup === 'root'" class="fixed inset-0 z-30" @click="openGroup = null; subOpen = null"></div>
          <div class="invisible absolute left-0 z-40 mt-1.5 w-52 -translate-y-1 rounded-xl border border-slate-200 bg-white p-1.5 opacity-0 shadow-2xl transition-all duration-150
                      group-hover:visible group-hover:translate-y-0 group-hover:opacity-100"
               :class="openGroup === 'root' ? 'visible translate-y-0 opacity-100' : ''">
            <div v-for="g in strategyGroups" :key="g.name" class="group/sub relative">
              <button class="flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition"
                      :class="isGroupActive(g) ? 'bg-slate-100 font-semibold' : 'hover:bg-slate-50'"
                      @click="subOpen = subOpen === g.name ? null : g.name">
                <span class="flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 rounded-full"
                        :class="isGroupActive(g) ? 'bg-sky-500' : 'bg-slate-300'"></span>
                  {{ shortStrategy(g.name) }}
                </span>
                <span class="text-[10px] text-slate-400"
                      :class="subOpen === g.name && 'text-sky-500'">▸</span>
              </button>
              <!-- 二级：该策略的账户（悬停策略行向右弹出） -->
              <div class="invisible absolute left-full top-0 z-40 ml-1.5 w-64 -translate-x-1 rounded-xl border border-slate-200 bg-white p-2 opacity-0 shadow-2xl transition-all duration-150
                          group-hover/sub:visible group-hover/sub:translate-x-0 group-hover/sub:opacity-100"
                   :class="subOpen === g.name ? 'visible translate-x-0 opacity-100' : ''">
                <div class="px-2 pb-1 pt-1.5 text-[11px] font-medium tracking-wide text-slate-400">
                  {{ g.name }}
                </div>
                <button v-for="a in g.accs" :key="a.key"
                        @click="switchAccount(a.key); openGroup = null; subOpen = null"
                        class="mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm text-white shadow-sm transition hover:brightness-110"
                        :class="rowCls(overviews[a.key])">
                  <span class="flex items-center gap-1.5 font-medium">
                    <span class="w-4">{{ account === a.key ? '✓' : '' }}</span>{{ a.label }}
                  </span>
                  <span class="tabular text-xs font-semibold">{{ cumText(overviews[a.key]) }}</span>
                </button>
              </div>
            </div>
            <div class="px-3 pb-1 pt-1 text-[10px] text-slate-400">悬停策略查看账户 · 行色=盈亏</div>
          </div>
        </div>

        <span class="hidden text-xs text-slate-500 md:inline">{{ accLabel }}</span>
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
        <div class="lg:col-span-2">
          <OverviewCards :accounts="accounts" :overviews="overviews" :selected="account"
                         @select="switchAccount" />
        </div>
        <RegimeCard :regime="regime" />
      </div>

      <!-- 净值曲线 -->
      <NavChart :account="account" :label="accLabel" />

      <!-- 回测与样本外（原 15_dashboard 的职能） -->
      <BacktestPanel :account="account" />

      <!-- 持仓 + 订单 -->
      <div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div class="xl:col-span-2">
          <PositionsTable :rows="positions" :account="account" :alerts="alerts" :orders="orders" />
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
  </div>
</template>
