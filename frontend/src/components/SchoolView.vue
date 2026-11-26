<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api.js'

const emit = defineEmits(['back'])
const d = ref(null)
onMounted(async () => {
  try { d.value = await api.get('/api/school') } catch { d.value = { ic_rows: [], strategies: [] } }
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => window.removeEventListener('keydown', onKey))
const onKey = (e) => { if (e.key === 'Escape') emit('back') }

const sections = [
  { id: 'world', t: '一、世界观' }, { id: 'data', t: '二、数据' },
  { id: 'factor', t: '三、因子' }, { id: 'ops', t: '四、算子' },
  { id: 'model', t: '五、模型与引擎' },
  { id: 'test', t: '六、检验' }, { id: 'portfolio', t: '七、组合构建' },
  { id: 'strategy', t: '八、策略图谱' }, { id: 'backtest', t: '九、回测' },
  { id: 'exec', t: '十、执行' }, { id: 'discipline', t: '十一、纪律' },
  { id: 'budget', t: '十二、风险预算' }, { id: 'pitfalls', t: '十三、踩坑清单' }
]
</script>

<template>
  <div class="mx-auto max-w-5xl py-2 text-slate-700">
    <h1 class="text-2xl font-bold text-slate-900">QuantDex 量化学院</h1>
    <p class="mb-6 mt-1 text-xs text-slate-500">
      页面上的每个数字都来自旁边正在运行的项目
      <span v-if="d?.daily_syms">（{{ d.daily_syms }} 只标的 / {{ d.total_rows }} 行 /
        {{ d.n_factors }} 个因子<span v-if="d?.n_expr"> / 其中 {{ d.n_expr }} 个表达式因子</span> /
        {{ d.experiments }} 份实验存档）</span>
    </p>

    <div class="mb-6 flex flex-wrap gap-2">
      <div v-for="a in d?.accounts || []" :key="a.key"
           class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2">
        <div class="text-[11px] text-slate-500">{{ a.label }}</div>
        <div class="text-sm font-bold text-slate-900">{{ a.nav == null ? '建仓中' : a.nav.toLocaleString() + ' 元' }}</div>
      </div>
    </div>

    <nav class="sticky top-14 z-10 mb-6 flex flex-wrap gap-3 rounded-xl border border-slate-200 bg-white/90 p-3 backdrop-blur">
      <a v-for="s in sections" :key="s.id" :href="'#' + s.id" class="text-xs text-slate-500 hover:text-sky-600">{{ s.t }}</a>
    </nav>

    <section id="world" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">一、世界观：一条流水线</h2>
      <div class="my-3 flex flex-wrap items-center gap-1.5">
        <span v-for="s in ['📡 数据','🧮 因子','🎯 策略','🧪 回测','🧾 执行','📊 复盘']" :key="s"
              class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm">{{ s }}</span>
        <span class="text-sky-600">↺ 循环迭代</span>
      </div>
      <p class="text-sm text-slate-600">量化不是预测水晶球，是把投资决策变成<b>可检验的流水线</b>：数据回答"世界长什么样"，因子把数据压成"分数"，策略把分数变成"买卖规则"，回测验证规则"历史上行不行"，执行把规则跑起来，复盘回答"哪里错了"。任何一环偷懒，后面全白费。本项目四条流水线（四账户）正在这条循环上真实运行。</p>
      <p class="mt-2 text-sm text-slate-500">两条必修的底层认知：<b class="text-slate-700">① 期望收益来自便宜</b>——低波/低换手这类因子赚钱的机理，是回撤时恰好在买变便宜的资产，所以"跌了要动作"的直觉注定负贡献（见「十、执行」三连败）；<b class="text-slate-700">② 诚实比聪明值钱</b>——本页所有数字都标注口径与决策编号（D-xxx），错了用更正批注改，不用记忆改。</p>
      <div class="mt-2 rounded-lg border border-blue-900 bg-blue-950/40 p-3 text-xs text-blue-200">qlib 第 1 课：整条流水线可以被"一坨配置"描述——可序列化 = 可复现 = 可改进。这正是我们做实验存档的原因。</div>
    </section>

    <section id="data" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">二、数据：量化的大米</h2>
      <table class="w-full text-sm"><thead><tr class="bg-slate-50 text-left text-xs text-slate-500"><th class="p-2">层</th><th class="p-2">内容</th><th class="p-2">我们项目</th></tr></thead>
      <tbody class="text-slate-600">
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">行情</td><td class="p-2">开高低收/量/额/换手（一切的地基）</td><td class="p-2 text-slate-500">{{ d?.daily_syms || 0 }} 只 × 2015→今，按日分区</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">参考</td><td class="p-2">日历、成分（<b>PIT 历史成分防幸存者偏差</b>）、行业</td><td class="p-2 text-slate-500">沪深300/中证500 PIT + 申万一级行业</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">公司行为</td><td class="p-2">分红除权、涨跌停、停牌</td><td class="p-2 text-slate-500">835 只分红记录 + qfq 复权</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">财务 / 估值</td><td class="p-2">利润/现金流（基本面原料）、PE/PB/市值</td><td class="p-2 text-slate-500">示例级——P1-a/b 待补</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">资金 / 宏观</td><td class="p-2">北向/两融/龙虎榜、CPI/利率</td><td class="p-2 text-slate-500">北向+两融全历史、6 表</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">分钟线</td><td class="p-2">5/15/30/60 分钟（1 分钟无免费全历史）</td><td class="p-2 text-slate-500">baostock 自 2011，存不复权口径（D-060）</td></tr>
      </tbody></table>
      <p class="mt-3 text-sm text-slate-600">数据层的两道<b>自愈护栏</b>（都来自真实事故）：<b class="text-slate-700">qfq 锚点护栏</b>——每天全量扫描自洽性，除权锚点断裂（源数据晚更新一天）的股票自动全史重抓，曾有 16 只一夜断裂被次日自动修复；<b class="text-slate-700">保鲜检查</b>——增量更新靠"最后日期 + 交易日历"判断缺口，漏掉整天会触发区间重抓，而不是永远跳过。</p>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">新人第一课：单位陷阱——北向=亿元、两融=万元、有的源量是"手"有的是"股"。不同源混用前先定量纲。东财快照量纲曾手→股污染 100 倍、279 行被自检逮住（D-059）。</div>
    </section>

    <section id="factor" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">三、因子：把数据压成分数</h2>
      <p class="text-sm text-slate-600">因子 = 每天给每只股票算的一个<b>分数</b>。它不是预测，是可检验的猜想：分数高的股票未来是否真的更好？方向不预设——IC 的符号会告诉你。</p>
      <p class="mt-2 text-sm text-slate-500"><b class="text-slate-700">IC 怎么读</b>：每天把因子值、N 日后收益各自排名，算横截面相关（rank IC）。为什么用排名不用数值——去量纲、抗极值。|IC|&gt;0.03 有戏、&gt;0.05 不错；持有期 &gt;1 天时 IC 序列自相关，普通 t 值虚高，显著性以 Newey-West t（|t|&gt;2）为准。表达式因子按家族分类（K线/动量/波动/均线/位置/量能…），榜上"家族"列即出处。</p>
      <h3 class="mb-1 mt-3 text-sm text-slate-600">实时 IC 榜（20 日前瞻；★ = FDR q≤0.1 真显著，(t&gt;2) 仅为候选）</h3>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">因子</th><th class="p-2">家族</th><th class="p-2">IC5</th><th class="p-2">IC20</th><th class="p-2">NW-t</th><th class="p-2">ICIR</th><th class="p-2">FDR q</th><th class="p-2">覆盖</th><th class="p-2">含义</th></tr></thead>
      <tbody>
        <tr v-for="r in d?.ic_rows || []" :key="r.factor" class="border-t border-slate-200">
          <td class="p-2 font-mono text-sky-300">{{ r.factor }}</td><td class="p-2 text-slate-500">{{ r.family }}</td>
          <td class="p-2 text-slate-500">{{ r.ic5 == null ? '-' : r.ic5.toFixed(4) }}</td>
          <td class="p-2">{{ r.ic20.toFixed(4) }}</td>
          <td class="p-2" :class="r.fdr ? 'font-bold text-rose-400' : 'text-slate-500'">{{ r.t20.toFixed(2) }}<span v-if="r.fdr"> ★</span><span v-else-if="r.sig" class="text-slate-500"> (t&gt;2)</span></td>
          <td class="p-2 text-slate-500">{{ r.icir20 == null ? '-' : r.icir20.toFixed(2) }}</td>
          <td class="p-2" :class="r.fdr ? 'text-rose-400' : 'text-slate-500'">{{ r.q20 == null ? '-' : r.q20.toFixed(3) }}</td>
          <td class="p-2 text-slate-500">{{ (r.coverage * 100).toFixed(0) }}%</td>
          <td class="p-2 text-slate-500">{{ r.desc }}</td>
        </tr>
      </tbody></table>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">诚实警告（D-042）：同一份数据测几十个因子，总有几个 |t|&gt;2 是运气——BH-FDR 控制错误发现率之后，我们 46 个因子里 <b>0 个</b>通过 q&lt;0.1。所以榜上的 ★ 极难出现，(t&gt;2) 只是"候选"不是"显著"；"显著名单 ≠ 准入名单"。看榜纪律：从榜上挑因子再回测 = 用同一份数据又选又考，必须过 walk-forward（见「六、检验」）。</div>
      <div class="mt-2 rounded-lg border border-blue-900 bg-blue-950/40 p-3 text-xs text-blue-200">入池闸门（D-073）：一个因子要进策略，需三关全过——① 经济先验说得通（为什么它该赚钱）；② walk-forward 样本外为正；③ 加入组合有增量证据（不是已有因子的影子）。我们曾测出 82 对相关性 &gt;0.7 的因子、其中 6 对 r=1.0——55 个因子实际约 30 个独立信号（D-062）。</div>
    </section>

    <section id="ops" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">四、算子：因子的语法</h2>
      <p class="mb-2 text-sm text-slate-600">因子不用手写 pandas 循环——用表达式（字符串公式）描述，引擎自动算。加一个因子从"改代码"变成"加一行字符串"（qlib 教给我们的最重要形态）。</p>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">算子</th><th class="p-2">含义</th><th class="p-2">例句</th></tr></thead>
      <tbody>
        <tr v-for="o in d?.ops_rows || []" :key="o.op" class="border-t border-slate-200">
          <td class="p-2 font-mono font-bold text-sky-300">{{ o.op }}</td><td class="p-2 text-slate-600">{{ o.desc }}</td>
          <td class="p-2 font-mono text-slate-500">{{ o.eg }}</td>
        </tr>
      </tbody></table>
    </section>

    <section id="model" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">五、模型与引擎：普通代码有什么不同</h2>
      <p class="mb-3 text-sm text-slate-600">这两个词听着神秘，其实都是普通代码——区别只在<b>扮演的角色</b>。下面的代码全部来自本项目，可直接打开对照。</p>

      <p class="mb-1 text-sm text-slate-700"><b>引擎 = 按固定规则运行的世界模拟器。</b><span class="text-slate-600">整个回测引擎（src/backtest/engine.py）只有 66 行，核心就三句：昨天的仓位 × 今天的收益 = 策略毛收益；每一笔换手扣佣金/滑点/印花税/过户费；净值 = 每日净收益复利累乘。它不关心"该买什么"——双均线塞进去能跑、多因子塞进去也能跑。策略才是决策层，引擎只是"如果那天你真按信号下单，账户会变成什么样"的记账机器。</span></p>
      <pre class="mb-2 overflow-auto rounded-lg border border-slate-200 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-sky-200">out["strat_ret"] = out["position"].shift(1).fillna(0) * out["ret"]
out["cost"]      = (turnover * slippage + buys * fee
                    + sells * (fee + stamp_duty) + turnover * transfer_fee)
out["nav"]       = (1 + out["strat_ret_net"]).cumprod()   # 复利累乘 = 净值曲线</pre>
      <div class="mb-3 rounded-lg border border-blue-900 bg-blue-950/40 p-3 text-xs text-blue-200">为什么规则要单独做成引擎：口径只有一份，回测和影子盘的净值才有可比性——D-034 的 12pp 差距就是"两边口径不一致"交的学费。</div>

      <p class="mb-1 text-sm text-slate-700"><b>模型 = 代码壳 + 一堆"从历史数据里学出来的数字"。</b><span class="text-slate-600">现行版的打分函数（src/strategies/multi_factor.py）其实已经是一个最简单的模型：3 个因子、权重全是 1/3——只不过这个 1/3 是拍脑袋定的，不是学出来的。</span></p>
      <pre class="mb-2 overflow-auto rounded-lg border border-slate-200 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-emerald-200">for f, direction in factors:
    r = p[f] if direction > 0 else (1.0 - p[f])   # 方向统一
    score = r if score is None else score + r      # 每个因子一票，票值相等
p["score"] = score / len(factors)                  # 等权合成——权重是"人写的"，不是"学出来的"</pre>
      <p class="mb-2 text-sm text-slate-600">模型区别于普通代码的根本点是<b>两个阶段</b>：普通代码只有"运行"；模型先"训练"（拿历史数据调出参数——学生做历年真题），再"推理"（拿今天的数据打分——上考场做新题）。LightGBM 要做的事，翻成人话就是把"每个因子一票"换成"让机器学出每个因子的权重和非线性组合"；再往后的 attention（已留占位，见 TODO 路线 4），是连"因子之间怎么交互"都让模型自己学。引擎一行不用改——这正是引擎/策略/模型分层的意义。</p>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2"></th><th class="p-2">普通脚本</th><th class="p-2">引擎</th><th class="p-2">模型</th></tr></thead>
      <tbody class="text-slate-600">
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">行为由谁决定</td><td class="p-2">程序员写死的规则</td><td class="p-2">程序员写死的<b>世界规则</b></td><td class="p-2">程序壳 + <b>数据决定参数</b></td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">换策略要改它吗</td><td class="p-2">通常要</td><td class="p-2">不用（通用）</td><td class="p-2">壳不用、参数要<b>重新训练</b></td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">出错的样子</td><td class="p-2">算错数、报错</td><td class="p-2">口径错（12pp 那种）</td><td class="p-2 font-semibold text-orange-300">悄悄过拟合——不报错，只是未来悄悄变笨</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">本项目实例</td><td class="p-2">抓取 / 调度脚本</td><td class="p-2">engine.py / 影子盘撮合</td><td class="p-2">等权打分（升级版 = LightGBM / attention）</td></tr>
      </tbody></table>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">所以检验体系层层加码（WF → FDR → 影子盘）全是冲着模型来的：普通代码错了会报错，模型错了不报错。也所以 TODO 里 attention 的门槛是"必须打赢 LightGBM 基线"——换模型不是换代码，是换一个学出来的脑子，得先证明新脑子比旧脑子好。</div>
    </section>

    <section id="test" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">六、检验：猜想必须过几关</h2>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><b class="text-sm text-slate-900">① RankIC / NW-t</b><p class="mt-1 text-xs text-slate-600">因子排名 vs 未来收益排名的日截面相关。|IC|>0.03 有戏、>0.05 不错；持有期>1 天时以 Newey-West t（|t|>2）为准——普通 t 虚高。</p></div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><b class="text-sm text-slate-900">② ICIR / 分层</b><p class="mt-1 text-xs text-slate-600">稳定性 = IC均值/标准差；按分数分 5 组看单调性。算术净值会被肥尾欺骗——以中位数口径为准。</p></div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><b class="text-sm text-slate-900">③ 多重检验（FDR）</b><p class="mt-1 text-xs text-slate-600">测得越多，纯运气的"显著"越多。BH-FDR 控制错误发现率后我们 46 个因子 0 个通过——所以还要看第④关。</p></div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><b class="text-sm text-slate-900">④ 影子盘对照</b><p class="mt-1 text-xs text-slate-600">终极检验：真实记账、T+1 开盘撮合、费用全扣。回测 vs 影子盘的偏差 = 你所有疏漏的总和。</p></div>
      </div>
      <h3 class="mb-1 mt-4 text-sm text-slate-600">walk-forward 实录（每年用之前的数据选参数，当年样本外考试）</h3>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">测试年</th><th class="p-2">选中参数</th><th class="p-2">训练 IR</th><th class="p-2">样本外 IR</th></tr></thead>
      <tbody>
        <tr v-for="f in d?.wf_folds || []" :key="f.year" class="border-t border-slate-200">
          <td class="p-2 font-mono text-slate-600">{{ f.year }}</td>
          <td class="p-2 text-slate-500">{{ f.params }}</td>
          <td class="p-2 text-slate-500">{{ f.train_ir }}</td>
          <td class="p-2 font-semibold" :class="f.oos_ir.startsWith('-') ? 'text-emerald-400' : 'text-rose-300'">{{ f.oos_ir }}</td>
        </tr>
      </tbody></table>
      <p class="mt-2 text-xs text-slate-500">{{ d?.wf_summary }}</p>
      <div class="mt-2 rounded-lg border border-blue-900 bg-blue-950/40 p-3 text-xs text-blue-200">{{ d?.wf_lesson }}</div>
      <p class="mt-2 text-xs text-slate-500">颜色约定：A 股口径红涨绿跌——样本外 IR 为正显示红色（赚钱）、为负显示绿色（亏钱）。</p>
    </section>

    <section id="portfolio" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">七、组合构建：从分数到订单</h2>
      <p class="mb-2 text-sm text-slate-600">因子分再好，也要经过<b>组合层</b>才变成钱：谁入选、给多少权重、什么时候换。这一层最常见的坑是"悄悄加动作"——每加一个规则都要回测发言。</p>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">步骤</th><th class="p-2">做法</th><th class="p-2">在哪看</th></tr></thead>
      <tbody>
        <tr v-for="p in d?.portfolio_steps || []" :key="p.step" class="border-t border-slate-200">
          <td class="p-2 font-semibold text-slate-700">{{ p.step }}</td>
          <td class="p-2 text-slate-600">{{ p.desc }}</td>
          <td class="p-2 text-slate-500">{{ p.where }}</td>
        </tr>
      </tbody></table>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">持有期错配教训：rev_5（5 日反转）的预测力 5 天就衰减，却挂在 20 日调仓的组合里——等于把强信号掺进 15 天噪音。因子衰减周期和调仓节奏必须匹配（第二篇）。</div>
    </section>

    <section id="strategy" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">八、策略图谱：主流打法 × 我们的进度</h2>
      <div class="mb-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">诚实披露：四策略并行后存在<b>择优偏差</b>——样本外最强的天然被展示，真实预期应打折扣。</div>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">策略</th><th class="p-2">思想</th><th class="p-2">因子</th><th class="p-2">数据</th><th class="p-2">进度</th></tr></thead>
      <tbody>
        <tr v-for="s in d?.strategies || []" :key="s.name" class="border-t border-slate-200">
          <td class="p-2"><b class="text-slate-900">{{ s.name }}</b><br><span class="text-[10px] text-slate-500">{{ s.who }}</span></td>
          <td class="p-2 text-slate-600">{{ s.idea }}</td><td class="p-2 font-mono text-slate-500">{{ s.factors }}</td>
          <td class="p-2 text-slate-500">{{ s.data }}</td>
          <td class="p-2"><span class="rounded-full px-2 py-0.5 text-slate-900" :style="{ background: s.style }">{{ s.statusText }}</span></td>
        </tr>
      </tbody></table>
    </section>

    <section id="backtest" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">九、回测：诚实的时光机</h2>
      <p class="mb-2 text-sm text-slate-600">回测最大的敌人不是算错，是悄悄作弊。保真铁律（每条来自真实踩坑）：</p>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">铁律</th><th class="p-2">我们踩的坑</th></tr></thead>
      <tbody class="text-slate-600">
        <tr class="border-t border-slate-200"><td class="p-2">T+1：信号次日成交，且<b>口径必须统一</b></td><td class="p-2">回测收盘价 vs 实盘开盘价差过 12pp（D-034）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">成本：佣金+最低佣金+印花+过户+滑点</td><td class="p-2">最低佣金对小资金 = 0.8% 起步；滑点 0.0005 是按成交额校准过的准数，不是拍脑袋（D-067）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">不可成交：涨跌停/停牌</td><td class="p-2">分板块 10%/20%，容差 0.1%</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">PIT：只用当时真实存在的成分</td><td class="p-2">幸存者偏差曾把最强因子从 +0.042 改写成 -0.003</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">现金约束：买入不许超可用现金</td><td class="p-2">含费用预留、最小一手——2000 元账户因此天然降杠杆（D-038）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">费用可见性：每笔代价单列</td><td class="p-2">日报单列交易代价——费用是行为补丁，看得见才守得住（D-043）</td></tr>
      </tbody></table>
    </section>

    <section id="exec" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">十、执行：从纸面到账户</h2>
      <p class="mb-2 text-sm text-slate-600">T+1 两段式口径（D-001）：今日收盘后生成挂单 → 次日开盘按开盘价撮合。中间隔着人工审批，过了还有五道闸，每道都来自真实事故：</p>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">闸</th><th class="p-2">规则</th><th class="p-2">来历</th></tr></thead>
      <tbody class="text-slate-600">
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">审批</td><td class="p-2">人工可否决挂单，但真实否决率以账本为准</td><td class="p-2 text-slate-500">"我以为全否了"实际只拦 30%（D-053，见「十一、纪律」）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">拒单</td><td class="p-2">涨跌停买不进/卖不出、停牌记拒单</td><td class="p-2 text-slate-500">撮合口径与回测同源（board_limit）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">幂等</td><td class="p-2">同 信号日+标的+方向 的挂单有唯一索引，重复提交直接被拒</td><td class="p-2 text-slate-500">防"手滑双击变双仓"（D-059）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">自检</td><td class="p-2">数字审计（净值恒等式/现金连续性）退出码非 0 即失败</td><td class="p-2 text-slate-500">东财量纲手→股污染 279 行被自检逮住（D-059）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2 font-semibold">退市解锁</td><td class="p-2">退市股的 pending 挂单自动解锁清理，不再卡队列</td><td class="p-2 text-slate-500">退市股曾永久卡住撮合（D-062）</td></tr>
      </tbody></table>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">防御性减仓三连败（回测否决了三次直觉）：弱市减仓、个股止损、组合断路器全部负贡献。防御的正确位置是"投入多少钱"（风险预算），不是"跌了怎么办"（交易规则）。</div>
    </section>

    <section id="discipline" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">十一、纪律：比策略更值钱的部分</h2>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">机制</th><th class="p-2">作用</th><th class="p-2">我们的实现</th></tr></thead>
      <tbody class="text-slate-600">
        <tr class="border-t border-slate-200"><td class="p-2">决策日志</td><td class="p-2">记录"当时为什么这么定"，防事后自欺</td><td class="p-2 text-slate-500">80+ 条（D-001~D-082）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">实验存档</td><td class="p-2">参数↔结果↔代码版本绑定，三个月后可追溯</td><td class="p-2 text-slate-500">{{ d?.experiments || 0 }} 份 JSON</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">抓而不用=浪费</td><td class="p-2">每份抓来的数据必须有 ≥1 个消费者才值得入库</td><td class="p-2 text-slate-500">5 分钟线入库前先写好三个用途（D-060）</td></tr>
        <tr class="border-t border-slate-200"><td class="p-2">更正批注</td><td class="p-2">记录错了就改，但用批注改——让后来者知道这里错过</td><td class="p-2 text-slate-500">"确认无成交"的错误复核即被批注更正</td></tr>
      </tbody></table>
      <h3 class="mb-2 mt-4 text-sm text-slate-600">事故复盘（直觉会绕回来，账本要接得住）</h3>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div v-for="p in d?.postmortem || []" :key="p.date" class="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div class="flex items-center justify-between"><b class="text-sm text-slate-900">{{ p.title }}</b><span class="font-mono text-[10px] text-slate-500">{{ p.date }}</span></div>
          <p class="mt-1 text-xs text-slate-600">{{ p.what }}</p>
          <p class="mt-1 text-xs text-sky-300">{{ p.lesson }}</p>
        </div>
      </div>
      <p class="mt-2 text-xs text-slate-500">{{ d?.postmortem_lesson }}</p>
    </section>

    <section id="budget" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">十二、风险预算：该投多少钱</h2>
      <div class="rounded-lg border border-blue-900 bg-blue-950/40 p-3 text-sm text-blue-200">可投入本金 = 可承受亏损 ÷ 策略历史最大回撤。<span class="text-xs">例：能承受亏 2 万 ÷ 27.5% ≈ 7.3 万。公式来自一次真实的疼痛：-30% 浮亏在 2500 元上拿得住，放到 10 万上就是两种人生——所以预算按<b>金额</b>设，不按"几成仓位"（D-009）。</span></div>
      <table class="mt-3 w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">策略</th><th class="p-2">历史最大回撤</th><th class="p-2">WF 样本外年化</th><th class="p-2">L=2万 最大可投入</th><th class="p-2">预期年收益带</th></tr></thead>
      <tbody>
        <tr v-for="r in d?.risk_budget || []" :key="r.strategy" class="border-t border-slate-200">
          <td class="p-2 text-slate-700">{{ r.strategy }}</td>
          <td class="p-2 text-slate-500">{{ r.mdd }}</td>
          <td class="p-2 text-slate-500">{{ r.wf_ann }}</td>
          <td class="p-2 font-semibold text-slate-900">{{ r.invest }}</td>
          <td class="p-2 text-slate-500">{{ r.band }}</td>
        </tr>
      </tbody></table>
      <p class="mt-1 text-[10px] text-slate-500">{{ d?.risk_note }}</p>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">{{ d?.risk_callout }}</div>
      <p class="mt-2 text-sm text-slate-500">顺序论：体检是研究链的<b>消费者</b>——必须等策略定义冻结、影子盘跑满四周拿到"现实 vs 回测"的偏差系数（D-068），期望带才校准成诚实区间。反过来先做体检，引用的就是一堆还在变动的数字，那是给幻觉做 UI。</p>
    </section>

    <section id="pitfalls" class="mb-6">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">十三、踩坑清单（每条都真实付过学费）</h2>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div v-for="p in d?.pitfalls || []" :key="p.title" class="rounded-xl border border-orange-900/60 bg-orange-950/30 p-3">
          <b class="text-sm text-orange-200">{{ p.title }}</b>
          <p class="mt-1 text-xs text-slate-600">{{ p.desc }}</p>
        </div>
      </div>
      <p class="mt-4 text-sm text-slate-600">——学完这一页，你需要的"必需之物"就是：干净的数据、可检验的猜想、诚实的时光机、能跑的账户、和不骗自己的纪律。</p>
    </section>

    <section id="attr" class="mb-10">
      <h2 class="mb-2 border-l-4 border-sky-500 pl-2 text-lg font-bold text-slate-900">十四、归因：把"赚了"拆成可命名的部分</h2>
      <p class="mb-2 text-sm text-slate-600">一条净值曲线无法区分<b>本事</b>与<b>运气</b>——归因就是把它拆成三层：① 跑没跑赢基准 ② 扣除市场暴露后还剩多少（α/β 回归）③ 剩余里执行成本占多少。每一层都有对应的坑。</p>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div v-for="a in d?.attr_method || []" :key="a.name" class="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <b class="text-sm text-slate-900">{{ a.name }}</b>
          <p class="mt-1 text-xs text-slate-600">{{ a.desc }}</p>
          <p class="mt-1 text-xs text-slate-500">{{ a.lesson }}</p>
        </div>
      </div>
      <h3 class="mb-1 mt-4 text-sm text-slate-600">四周实测（{{ d?.attr_window }}）</h3>
      <table class="w-full text-xs"><thead><tr class="bg-slate-50 text-left text-slate-500"><th class="p-2">账户</th><th class="p-2">区间收益</th><th class="p-2">000300 同窗</th><th class="p-2">超额</th><th class="p-2">β</th><th class="p-2">α（年化）</th><th class="p-2">t(α)</th></tr></thead>
      <tbody>
        <tr v-for="r in d?.attr_rows || []" :key="r.acc" class="border-t border-slate-200">
          <td class="p-2 font-semibold text-slate-700">{{ r.acc }}</td>
          <td class="p-2" :class="r.ret.startsWith('-') ? 'text-emerald-400' : 'text-rose-300'">{{ r.ret }}</td>
          <td class="p-2 text-slate-500">{{ r.bench }}</td>
          <td class="p-2 font-semibold" :class="r.excess.startsWith('-') ? 'text-emerald-400' : 'text-rose-300'">{{ r.excess }}</td>
          <td class="p-2 font-mono text-slate-600">{{ r.beta }}</td>
          <td class="p-2 font-mono text-slate-600">{{ r.alpha_ann }}</td>
          <td class="p-2 font-mono" :class="Math.abs(parseFloat(r.t)) >= 2 ? 'text-amber-300' : 'text-slate-500'">{{ r.t }}</td>
        </tr>
      </tbody></table>
      <div class="mt-2 rounded-lg border border-blue-900 bg-blue-950/40 p-3 text-xs text-blue-200">{{ d?.attr_note }}</div>
      <div class="mt-2 rounded-lg border border-orange-900 bg-orange-950/40 p-3 text-xs text-orange-200">裁决教训：{{ d?.attr_lesson }}</div>
      <p class="mt-2 text-xs text-slate-500">颜色约定：红涨绿跌。t(α) |t|≥2 才算可裁决——四周的功效上限只有 1.5，所以这批数字都是期望带，不是证明。</p>
    </section>
  </div>
</template>
