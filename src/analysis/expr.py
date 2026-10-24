# -*- coding: utf-8 -*-
"""表达式因子引擎（对标 qlib 表达式思想的轻量自研实现，零依赖）。

因子 = 字符串公式，如：
    "$close/Ref($close, 5) - 1"                       5 日前至今收益
    "Mean($turnover, 20)"                             20 日均换手
    "Corr($close/Ref($close,1)-1, $turnover, 20)"     量价相关

语法（与 qlib 同形，子集）：
    $field        基础字段（open/high/low/close/volume/amount/turnover）
    Ref(x, n)     n>0 引用 n 天前（过去），n<0 引用未来（**只许出现在标签**）
    Mean/Sum/Std/Max/Min(x, n)   滚动窗口
    Delta(x, n)   x - Ref(x, n)
    Corr(x, y, n) 滚动相关
    Greater/Less(a, b)  逐元素取大/取小（b 可为常数）
    Abs(x), Log(x)
    + - * / 和括号，数值字面量

设计约定：
  - 所有运算基于单标的按日 Series（位置对齐即可，无需日期索引）；
  - 结果中的 ±inf 一律替换为 NaN（除零保护），NaN 参与截面 rank 时自动剔除；
  - 表达式解析结果按字符串缓存（同一表达式只解析一次）。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

_TOKEN = re.compile(
    r"\s*(\$[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*"
    r"|\d+\.?\d*(?:[eE][+-]?\d+)?|[-+*/(),])")


# ---------------------------------------------------------------- 算子库
def _ref(x, n):
    """n>0 = n 天前的值；n<0 = 未来值（只许出现在标签/检验，绝不进特征）。"""
    return x.shift(int(n))


def _rolling(x, n, how):
    n = int(n)
    if n <= 0:
        raise ValueError(f"窗口必须为正整数，收到 {n}")
    return getattr(x.rolling(n), how)()


def _corr(x, y, n):
    return x.rolling(int(n)).corr(y)


def _greater(a, b):
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.Series(np.maximum(a.to_numpy(), b.to_numpy()), index=a.index)
    return a.where(a > b, b) if isinstance(a, pd.Series) else (
        b.where(b > a, a) if isinstance(b, pd.Series) else max(a, b))


def _less(a, b):
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.Series(np.minimum(a.to_numpy(), b.to_numpy()), index=a.index)
    return a.where(a < b, b) if isinstance(a, pd.Series) else (
        b.where(b < a, a) if isinstance(b, pd.Series) else min(a, b))


_OPS = {
    "+": lambda a, b: a + b, "-": lambda a, b: a - b,
    "*": lambda a, b: a * b, "/": lambda a, b: a / b,
}
_FUNCS = {
    "Ref": _ref,
    "Mean": lambda x, n: _rolling(x, n, "mean"),
    "Sum": lambda x, n: _rolling(x, n, "sum"),
    "Std": lambda x, n: _rolling(x, n, "std"),
    "Max": lambda x, n: _rolling(x, n, "max"),
    "Min": lambda x, n: _rolling(x, n, "min"),
    "Delta": lambda x, n: x - x.shift(int(n)),
    "Corr": _corr,
    "Greater": _greater, "Less": _less,
    "Abs": lambda x: x.abs(),
    "Log": lambda x: np.log(x.where(x > 0)),
}


# ---------------------------------------------------------------- 解析
def _tokenize(s: str) -> list[str]:
    out, pos = [], 0
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            if s[pos].isspace():
                pos += 1
                continue
            raise ValueError(f"无法解析的字符 {s[pos]!r} @ {pos}: {s}")
        out.append(m.group(1))
        pos = m.end()
    return out


class _Parser:
    """递归下降：expr -> term -> unary -> atom。产出闭包树（求值时才碰数据）。"""

    def __init__(self, tokens: list[str]):
        self.toks, self.i = tokens, 0

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self):
        t = self._peek()
        self.i += 1
        return t

    def parse(self):
        node = self._expr()
        if self._peek() is not None:
            raise ValueError(f"多余 token: {self.toks[self.i:]}")
        return node

    def _expr(self):
        node = self._term()
        while self._peek() in ("+", "-"):
            op = self._next()
            node = _binop(op, node, self._term())
        return node

    def _term(self):
        node = self._unary()
        while self._peek() in ("*", "/"):
            op = self._next()
            node = _binop(op, node, self._unary())
        return node

    def _unary(self):
        if self._peek() == "-":
            self._next()
            operand = self._unary()
            return lambda f, a=operand: -a(f)
        return self._atom()

    def _atom(self):
        t = self._next()
        if t is None:
            raise ValueError("表达式意外结束")
        if t == "(":
            node = self._expr()
            if self._next() != ")":
                raise ValueError("缺少右括号")
            return node
        if t.startswith("$"):
            name = t[1:]
            return lambda f, n=name: f[n]
        if re.fullmatch(r"\d+\.?\d*(?:[eE][+-]?\d+)?", t):
            v = float(t)
            return lambda f, v=v: v
        if t in _FUNCS and self._peek() == "(":
            self._next()
            args = [self._expr()]
            while self._peek() == ",":
                self._next()
                args.append(self._expr())
            if self._next() != ")":
                raise ValueError(f"{t} 缺少右括号")
            return lambda f, fn=_FUNCS[t], a=args: fn(*[x(f) for x in a])
        raise ValueError(f"未知 token: {t}")


def _binop(op, na, nb):
    return lambda f, op=op, na=na, nb=nb: _OPS[op](na(f), nb(f))


_PARSE_CACHE: dict = {}


def evaluate(expr: str, fields: dict) -> pd.Series:
    """求值表达式。fields: {"close": Series, "volume": Series, ...}（不带 $ 前缀）。

    返回与输入对齐的 Series（±inf 已替换为 NaN）。
    """
    node = _PARSE_CACHE.get(expr)
    if node is None:
        node = _Parser(_tokenize(expr)).parse()
        _PARSE_CACHE[expr] = node
    out = node(fields)
    if isinstance(out, pd.Series):
        return out.replace([np.inf, -np.inf], np.nan)
    return pd.Series(out)
