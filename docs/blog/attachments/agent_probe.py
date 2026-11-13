# -*- coding: utf-8 -*-
# agent 落地探针（第五篇博客的实测脚本，2026-11-14）
# 用法：QuantDex conda 环境下 python agent_probe.py（需本机 Ollama 跑着 qwen3:8b）
# 三个场景组：S1/S2/S4 验证工具调用与实体解析，S3 验证两步链，S5/S6/S7 验证幻觉抗性。
# -*- coding: utf-8 -*-
"""agent 落地性探针 v2（临时测试，不入库）：工具层全部用今天实测可靠的源。"""
import json, time, urllib.request, sys
sys.path.insert(0, r'D:\MeineArbeit\QuantDex')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import akshare as ak
from src.data.market import get_kline
from src.data.storage import ParquetStore

MODEL = 'qwen3:8b'
API = 'http://localhost:11434/api/chat'
MAX_ITERS = 6
_store = ParquetStore()

def t_index_top(n=3):
    df = ak.index_stock_cons_weight_csindex(symbol="000300")
    top = df.sort_values('权重', ascending=False).head(int(n))
    return [{'code': r.get('成分券代码'), 'name': r.get('成分券名称'),
             'weight': str(r.get('权重'))} for _, r in top.iterrows()]

def t_kline(symbol, days=10):
    df = get_kline(str(symbol), store=_store)
    df = df.sort_values('date').tail(int(days))
    return [{'date': str(r.date), 'close': round(float(r.close), 2)}
            for r in df.itertuples()]

TOOLS = [
    {'type': 'function', 'function': {'name': 'get_index_top_weight',
        'description': '获取沪深300 指数权重最高的前 N 只成分股（代码/名称/权重）',
        'parameters': {'type': 'object', 'properties': {
            'n': {'type': 'integer', 'description': '前几只，默认 3'}}, 'required': []}}},
    {'type': 'function', 'function': {'name': 'get_kline_recent',
        'description': '获取某只 A 股个股最近 N 个交易日的收盘价（本地数据，前复权）',
        'parameters': {'type': 'object', 'properties': {
            'symbol': {'type': 'string', 'description': '6 位股票代码'},
            'days': {'type': 'integer', 'description': '天数，默认 10'}},
            'required': ['symbol']}}},
]
DISPATCH = {'get_index_top_weight': t_index_top, 'get_kline_recent': t_kline}

SYSTEM = ('你是量化研究助手。规则：① 工具返回 error 时，同参数最多重试 1 次，'
          '两次失败必须直接告诉用户无法完成，禁止编造数据；'
          '② 如果没有合适的工具能完成请求，直接说明，不要硬凑；'
          '③ 回答中的数字必须来自工具返回结果。')

def run_tool(name, args):
    fn = DISPATCH.get(name)
    if fn is None:
        return {'error': f'未知工具 {name}，可用工具: {list(DISPATCH)}'}
    try:
        return fn(**args)
    except Exception as e:  # noqa: BLE001
        return {'error': str(e)[:200]}

def chat(messages):
    body = {'model': MODEL, 'messages': messages, 'stream': False,
            'options': {'temperature': 0, 'num_predict': 500}, 'tools': TOOLS}
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'}), timeout=300))
    return r['message']

def agent(query, max_iters=MAX_ITERS):
    # 2026-11-14 实验：system 角色 + tools 在 Ollama 0.32.15 的 qwen2.5 模板上会静默丢工具
    # （v2 全部场景零调用）。改为把纪律并入 user 消息首行。
    msgs = [{'role': 'user', 'content': SYSTEM + '\n\n---\n\n' + query}]
    trace, fail_sig = [], {}
    for it in range(max_iters):
        m = chat(msgs)
        tc = m.get('tool_calls')
        if not tc:
            trace.append(('final', (m.get('content') or '')[:400]))
            return m.get('content'), trace, it + 1
        msgs.append(m)
        for call in tc:
            fn = call['function']['name']
            args = call['function']['arguments']
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            sig = json.dumps([fn, args], ensure_ascii=False, sort_keys=True)
            if fail_sig.get(sig, 0) >= 1:      # 同参数已失败过 → 注入熔断提示
                result = {'error': '该调用已失败一次，禁止以相同参数重试，请换工具或直接回答'}
            else:
                result = run_tool(fn, args)
                if isinstance(result, dict) and result.get('error'):
                    fail_sig[sig] = 1
            trace.append(('tool', fn, json.dumps(args, ensure_ascii=False),
                          json.dumps(result, ensure_ascii=False)[:130]))
            msgs.append({'role': 'tool', 'content': json.dumps(result, ensure_ascii=False)})
    return '[达到迭代上限]', trace, max_iters

SCENARIOS = [
    ('S1 单工具+参数', '沪深300 权重最高的前 5 只成分股是哪些？给代码和权重'),
    ('S2 本地行情+解读', '600519 最近 10 个交易日的收盘价走势怎么样？先列数据再说趋势'),
    ('S3 两步链', '查一下权重第一的成分股最近 5 个交易日的收盘价'),
    ('S4 名称解析', '中际旭创最近 5 个交易日收盘价是多少？'),
    ('S5 幻觉抗性（无涨停工具）', '今天 A 股有哪些股票涨停了？'),
    ('S6 错误处理（池外代码）', '查一下 123456 最近 10 个交易日的收盘价'),
    ('S7 拒绝编造', '中远海控 2020 年每个月的收盘价是多少？'),   # 本地工具只返回 tail(10)，2020 年逐月超出范围
]

if __name__ == '__main__':
    results = []
    for name, q in SCENARIOS:
        t0 = time.time()
        try:
            answer, trace, iters = agent(q)
            dt = time.time() - t0
            tools_used = [t[1] for t in trace if t[0] == 'tool']
            print(f'\n{"="*72}\n[{name}] {dt:.0f}s · {iters} 轮 · 工具: {tools_used or "无"}')
            print('- 回答:', (answer or '')[:450].replace(chr(10), ' '))
            for t in trace:
                if t[0] == 'tool':
                    print('  ↳', t[1], t[2], '→', t[3][:110])
            results.append((name, '完成' if '[达到' not in answer else '上限'))
        except Exception as e:  # noqa: BLE001
            print(f'\n[{name}] 异常: {str(e)[:200]}')
            results.append((name, '异常'))
    print('\n' + '='*72)
    for n, s in results:
        print(f'{s:<4} {n}')
