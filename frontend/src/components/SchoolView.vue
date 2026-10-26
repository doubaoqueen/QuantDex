<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const d = ref(null)
onMounted(async () => {
  try { d.value = await api.get('/api/school') } catch { d.value = { ic_rows: [], strategies: [] } }
})
const sections = [
  { id: 'world', t: '一、世界观' }, { id: 'data', t: '二、数据' },
  { id: 'factor', t: '三、因子' }, { id: 'ops', t: '四、算子' },
  { id: 'test', t: '五、检验' }, { id: 'strategy', t: '六、策略图谱' },
  { id: 'backtest', t: '七、回测' }, { id: 'exec', t: '八、执行' },
  { id: 'discipline', t: '九、纪律' }
]
</script>

<template>
  <div class="mx-auto max-w-5xl px-6 py-8 text-slate-200">
    <h1 class="text-2xl font-bold text-white">QuantDex 量化学院</h1>
    <p class="mb-6 mt-1 text-xs text-slate-400">
      面向新人的量化入门——页面上的每个数字都来自旁边正在运行的项目
      <span v-if="d?.daily_syms">（{{ d.daily_syms }} 只标的 / {{ d.total_rows }} 行 / {{ d.n_factors }} 个因子 / {{ d.experiments }} 份实验存档）</span>
    </p>

    <div class="mb-6 flex flex-wrap gap-2">
      <div v-for="a in d?.accounts || []" :key="a.key"
           class="rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-2">
        <div class="text-[11px] text-slate-400">{{ a.label }}</div>
        <div class="text-sm font-bold text-white">{{ a.nav == null ? '建仓中' : a.nav.toLocaleString() + ' 元' }}</div>
      </div>
    </div>

    <nav class="sticky top-14 z-10 mb-6 flex flex-wrap gap-3 rounded-xl border border-slate-700 bg-slate-900/90 p-3 backdrop-blur">
      <a v-for="s in sections" :key="s.id" :href="'#' + s.id" class="text-xs text-slate-400 hover:text-sky-400">{{ s.t }}</a>
    </nav>

    <section id="world" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">一、世界观：一条流水线</h2>
      <div class="my-3 flex flex-wrap items-center gap-1.5">
        <span v-for="s in ['📡 数据','🧮 因子','🎯 策略','🧪 回测','🧾 执行','📊 复盘']" :key="s"
              class="rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm">{{ s }}</span>
        <span class="text-sky-400">↺ 循环迭代</span>
      </div>
      <p class="text-sm text-slate-300">量化不是预测水晶球，是把投资决策变成<b>可检验的流水线</b>：数据回答"世界长什么样"，因子把数据压成"分数"，策略把分数变成"买卖规则"，回测验证规则"历史上行不行"，执行把规则跑起来，复盘回答"哪里错了"。任何一环偷懒，后面全白费。本项目四条流水线（四账户）正在这条循环上真实运行。</p>
      <div class="mt-2 rounded-lg border border-blue-900 bg-blue-950/40 p-3 text-xs text-blue-200">qlib 第 1 课：整条流水线可以被"一坨配置"描述——可序列化 = 可复现 = 可改进。这正是我们做实验存档的原因。</div>
    </section>

    <section id="data" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">二、数据：量化的大米</h2>
      <table class="w-full text-sm"><thead><tr class="bg-slate-800 text-left text-xs text-slate-400"><th class="p-2">层</th><th class="p-2">内容</th><th class="p-2">我们项目</th></tr></thead>
      <tbody class="text-slate-300">
        <tr class="border-t border-slate-700"><td class="p-2 font-semibold">行情</td><td class="p-2">开高低收/量/额/换手（一切的地基）</td><td class="p-2 text-slate-400">{{ d?.daily_syms || 0 }} 只 × 2015→今，按日分区</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2 font-semibold">参考</td><td class="p-2">日历、成分（<b>PIT 历史成分防幸存者偏差</b>）、行业</td><td class="p-2 text-slate-400">沪深300/中证500 PIT + 申万一级行业</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2 font-semibold">公司行为</td><td class="p-2">分红除权、涨跌停、停牌</td><td class="p-2 text-slate-400">835 只分红记录 + qfq 复权</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2 font-semibold">财务 / 估值</td><td class="p-2">利润/现金流（基本面原料）、PE/PB/市值</td><td class="p-2 text-slate-400">示例级——P1-a/b 待补</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2 font-semibold">资金 / 宏观</td><td class="p-2">北向/两融/龙虎榜、CPI/利率</td><td class="p-2 text-slate-400">北向+两融全历史、6 表</td></tr>
      </tbody></table>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">新人第一课：单位陷阱——北向=亿元、两融=万元、有的源量是"手"有的是"股"。不同源混用前先定量纲。</div>
    </section>

    <section id="factor" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">三、因子：把数据压成分数</h2>
      <p class="text-sm text-slate-300">因子 = 每天给每只股票算的一个<b>分数</b>。它不是预测，是可检验的猜想：分数高的股票未来是否真的更好？方向不预设——IC 的符号会告诉你。</p>
      <h3 class="mb-1 mt-3 text-sm text-slate-300">实时 IC 榜（20 日前瞻，★ = |NW-t|>2）</h3>
      <table class="w-full text-xs"><thead><tr class="bg-slate-800 text-left text-slate-400"><th class="p-2">因子</th><th class="p-2">家族</th><th class="p-2">IC</th><th class="p-2">NW-t</th><th class="p-2">覆盖</th><th class="p-2">含义</th></tr></thead>
      <tbody>
        <tr v-for="r in d?.ic_rows || []" :key="r.factor" class="border-t border-slate-700">
          <td class="p-2 font-mono text-sky-300">{{ r.factor }}</td><td class="p-2 text-slate-400">{{ r.family }}</td>
          <td class="p-2">{{ r.ic20.toFixed(4) }}</td>
          <td class="p-2" :class="r.sig ? 'font-bold text-rose-400' : 'text-slate-400'">{{ r.t20.toFixed(2) }}{{ r.sig ? ' ★' : '' }}</td>
          <td class="p-2 text-slate-400">{{ (r.coverage * 100).toFixed(0) }}%</td>
          <td class="p-2 text-slate-500">{{ r.desc }}</td>
        </tr>
      </tbody></table>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">看榜纪律：这份榜是全样本筛的——从榜上挑因子再做回测 = 用同一份数据又选又考。挑进策略前必须过 walk-forward。</div>
    </section>

    <section id="ops" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">四、算子：因子的语法</h2>
      <p class="mb-2 text-sm text-slate-300">因子不用手写 pandas 循环——用表达式（字符串公式）描述，引擎自动算。加一个因子从"改代码"变成"加一行字符串"（qlib 教给我们的最重要形态）。</p>
      <table class="w-full text-xs"><thead><tr class="bg-slate-800 text-left text-slate-400"><th class="p-2">算子</th><th class="p-2">含义</th><th class="p-2">例句</th></tr></thead>
      <tbody>
        <tr v-for="o in d?.ops_rows || []" :key="o.op" class="border-t border-slate-700">
          <td class="p-2 font-mono font-bold text-sky-300">{{ o.op }}</td><td class="p-2 text-slate-300">{{ o.desc }}</td>
          <td class="p-2 font-mono text-slate-400">{{ o.eg }}</td>
        </tr>
      </tbody></table>
    </section>

    <section id="test" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">五、检验：猜想必须过三关</h2>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div class="rounded-xl border border-slate-700 bg-slate-800/60 p-3"><b class="text-sm text-white">① RankIC / NW-t</b><p class="mt-1 text-xs text-slate-300">因子排名 vs 未来收益排名的日截面相关。|IC|>0.03 有戏、>0.05 不错；持有期>1 天时以 Newey-West t（|t|>2）为准——普通 t 虚高。</p></div>
        <div class="rounded-xl border border-slate-700 bg-slate-800/60 p-3"><b class="text-sm text-white">② ICIR / 分层</b><p class="mt-1 text-xs text-slate-300">稳定性 = IC均值/标准差；按分数分 5 组看单调性。算术净值会被肥尾欺骗——以中位数口径为准。</p></div>
        <div class="rounded-xl border border-slate-700 bg-slate-800/60 p-3"><b class="text-sm text-white">③ walk-forward</b><p class="mt-1 text-xs text-slate-300">每年只用之前的数据选参数，下一年考试——参数不许偷看未来。它回答"这不是运气吗"。</p></div>
        <div class="rounded-xl border border-slate-700 bg-slate-800/60 p-3"><b class="text-sm text-white">④ 影子盘对照</b><p class="mt-1 text-xs text-slate-300">终极检验：真实记账、T+1 开盘撮合、费用全扣。回测 vs 影子盘的偏差 = 你所有疏漏的总和。</p></div>
      </div>
    </section>

    <section id="strategy" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">六、策略图谱：主流打法 × 我们的进度</h2>
      <div class="mb-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">诚实披露：四策略并行后存在<b>择优偏差</b>——样本外最强的天然被展示，真实预期应打折扣。</div>
      <table class="w-full text-xs"><thead><tr class="bg-slate-800 text-left text-slate-400"><th class="p-2">策略</th><th class="p-2">思想</th><th class="p-2">因子</th><th class="p-2">数据</th><th class="p-2">进度</th></tr></thead>
      <tbody>
        <tr v-for="s in d?.strategies || []" :key="s.name" class="border-t border-slate-700">
          <td class="p-2"><b class="text-white">{{ s.name }}</b><br><span class="text-[10px] text-slate-500">{{ s.who }}</span></td>
          <td class="p-2 text-slate-300">{{ s.idea }}</td><td class="p-2 font-mono text-slate-400">{{ s.factors }}</td>
          <td class="p-2 text-slate-400">{{ s.data }}</td>
          <td class="p-2"><span class="rounded-full px-2 py-0.5 text-white" :style="{ background: s.style }">{{ s.statusText }}</span></td>
        </tr>
      </tbody></table>
    </section>

    <section id="backtest" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">七、回测：诚实的时光机</h2>
      <p class="mb-2 text-sm text-slate-300">回测最大的敌人不是算错，是悄悄作弊。四条保真铁律（每条来自真实踩坑）：</p>
      <table class="w-full text-xs"><thead><tr class="bg-slate-800 text-left text-slate-400"><th class="p-2">铁律</th><th class="p-2">我们踩的坑</th></tr></thead>
      <tbody class="text-slate-300">
        <tr class="border-t border-slate-700"><td class="p-2">T+1：信号次日成交，且<b>口径必须统一</b></td><td class="p-2">回测收盘价 vs 实盘开盘价差过 12pp（D-034）</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2">成本：佣金+最低佣金+印花+过户+滑点</td><td class="p-2">最低佣金对小资金 = 0.8% 起步</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2">不可成交：涨跌停/停牌</td><td class="p-2">分板块 10%/20%，容差 0.1%</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2">PIT：只用当时真实存在的成分</td><td class="p-2">幸存者偏差曾把最强因子从 +0.042 改写成 -0.003</td></tr>
      </tbody></table>
    </section>

    <section id="exec" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">八、执行：从纸面到账户</h2>
      <p class="text-sm text-slate-300">每日收盘后流水线生成挂单 → 人工审批（可否决）→ 次日开盘按开盘价撮合 → 涨跌停/停牌自动拒单 → 每晚一封合并日报。三个账户用不同策略并行，谁好谁坏让净值说话。</p>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">防御性减仓三连败（回测否决了三次直觉）：弱市减仓、个股止损、组合断路器全部负贡献。防御的正确位置是"投入多少钱"（风险预算），不是"跌了怎么办"（交易规则）。</div>
    </section>

    <section id="discipline" class="mb-6">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-white">九、纪律：比策略更值钱的部分</h2>
      <table class="w-full text-xs"><thead><tr class="bg-slate-800 text-left text-slate-400"><th class="p-2">机制</th><th class="p-2">作用</th><th class="p-2">我们的实现</th></tr></thead>
      <tbody class="text-slate-300">
        <tr class="border-t border-slate-700"><td class="p-2">决策日志</td><td class="p-2">记录"当时为什么这么定"，防事后自欺</td><td class="p-2 text-slate-400">36+ 条（D-001~）</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2">实验存档</td><td class="p-2">参数↔结果↔代码版本绑定，三个月后可追溯</td><td class="p-2 text-slate-400">{{ d?.experiments || 0 }} 份 JSON</td></tr>
        <tr class="border-t border-slate-700"><td class="p-2">风险预算</td><td class="p-2">最大可承受亏损 ÷ 策略历史回撤 = 最大投入</td><td class="p-2 text-slate-400">实盘前最后一道门</td></tr>
      </tbody></table>
      <p class="mt-4 text-sm text-slate-300">——学完这一页，你需要的"必需之物"就是：干净的数据、可检验的猜想、诚实的时光机、能跑的账户、和不骗自己的纪律。</p>
    </section>
  </div>
</template>
