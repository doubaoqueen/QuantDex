# -*- coding: utf-8 -*-
"""
数据可视化（Pillow 静态图）：把行情 / 回测 / 因子结果翻译成能看懂的 PNG 图。

为什么不用 matplotlib：本仓库环境（pandas 3.0.6 + numpy 2.4.6 / Windows）下
matplotlib 的 Agg 渲染存在原生崩溃（canvas.draw 无声退出，已实测验证），
而 Pillow 绘图路径完整可用。本模块用 Pillow + 微软雅黑实现研究所需的
5 张核心图，零额外依赖，输出 PNG。

所有函数把图保存为 PNG 文件（默认 data/store/plots/，可用 out_dir 覆盖），
并返回保存路径。出图失败不应影响研究主流程（调用方自行 try/except）。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from config import STORE_DIR

PLOTS_DIR = STORE_DIR / "plots"

# A股配色：红涨绿跌
RED = "#c0392b"
GREEN = "#27ae60"
GRAY = "#7f8c8d"
DARK = "#333333"
GRID = "#e8e8e8"
AXIS = "#999999"
QCOLORS = ["#1e8a3c", "#74a83c", "#b8b45a", "#e08850", "#c0392b"]  # Q1..Q5

_FONT_FILE = None


def _font(size: int):
    """微软雅黑 -> 黑体 -> 默认字体。"""
    global _FONT_FILE
    if _FONT_FILE is None:
        for p in (r"C:WindowsFontsmsyh.ttc", r"C:WindowsFontssimhei.ttf"):
            if Path(p).exists():
                _FONT_FILE = p
                break
        if _FONT_FILE is None:
            _FONT_FILE = ""
    if not _FONT_FILE:
        return ImageFont.load_default()
    return ImageFont.truetype(_FONT_FILE, size)


def _out_dir(out_dir) -> Path:
    d = Path(out_dir) if out_dir else PLOTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(img, name: str, out_dir) -> Path:
    path = _out_dir(out_dir) / name
    img.save(path)
    return path


# ---------------------------------------------------------------- 绘图小部件
def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """返回 [lo, hi] 内约 n 个好看刻度。"""
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return [lo]
    span = hi - lo
    step_raw = span / max(n - 1, 1)
    mag = 10.0 ** np.floor(np.log10(step_raw))
    step = step_raw
    for m in (1, 2, 2.5, 5, 10):
        if step_raw / mag <= m:
            step = m * mag
            break
    start = np.ceil(lo / step) * step
    out = []
    k = 0
    while start + k * step <= hi + step * 0.5:
        v = start + k * step
        if v >= lo - step * 0.5:
            out.append(float(v))
        k += 1
    return out


def _points(xs, ys, rect, x_min, x_max, y_min, y_max) -> list[tuple[int, int]]:
    """数据坐标 -> 像素点。rect=(x0,y0,x1,y1)，y 轴向上。"""
    if x_max <= x_min:
        x_max = x_min + 1.0
    if y_max <= y_min:
        y_max = y_min + 1.0
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    pts = []
    for x, y in zip(xs, ys):
        px = rect[0] + (x - x_min) / (x_max - x_min) * w
        py = rect[3] - (y - y_min) / (y_max - y_min) * h
        pts.append((int(round(px)), int(round(py))))
    return pts


def _draw_axes(draw, rect, x_min, x_max, y_min, y_max,
               y_fmt=None, y_n=5, x_labels=None, grid=True,
               font_size=14, grid_x=False):
    """坐标轴 + 刻度 + 网格。y_fmt: 刻度数值 -> 标签字符串。"""
    if y_fmt is None:
        y_fmt = lambda t: f"{t:.2f}"
    x0, y0, x1, y1 = rect
    f = _font(font_size)
    draw.rectangle([x0, y0, x1, y1], outline=AXIS, width=1)
    for t in _nice_ticks(y_min, y_max, y_n):
        py = y1 - (t - y_min) / (y_max - y_min) * (y1 - y0)
        label = y_fmt(t)
        w = draw.textlength(label, font=f)
        draw.text((x0 - w - 6, int(py) - font_size // 2), label, fill=DARK, font=f)
        if grid:
            draw.line([x0, int(py), x1, int(py)], fill=GRID, width=1)
    if x_labels:
        for pos, label in x_labels:
            px = x0 + (pos - x_min) / (x_max - x_min) * (x1 - x0)
            w = draw.textlength(label, font=f)
            draw.text((int(px) - w / 2, y1 + 4), label, fill=DARK, font=f)
            if grid_x:
                draw.line([int(px), y0, int(px), y1], fill=GRID, width=1)


def _ns_index(series) -> np.ndarray:
    """datetime 序列 -> 纳秒 int64。

    注意：不同数据源写出的 parquet 日期精度不同（新浪源是 datetime64[ms]），
    直接 astype("int64") 会得到毫秒，与 Timestamp.value（恒为纳秒）不一致，
    混用会导致坐标放大 1e6 倍。这里统一强制转换为纳秒。
    """
    return np.asarray(pd.to_datetime(series).values,
                      dtype="datetime64[ns]").astype("int64")


def _date_labels(dates, n: int = 7) -> list[tuple[int, str]]:
    """在日期序列上均匀取 n 个刻度（位置=纳秒整数，标签=YYYY-MM）。

    dates 可以是 Series / DatetimeIndex / 数组。
    """
    vals = np.asarray(pd.to_datetime(dates).values, dtype="datetime64[ns]")
    if len(vals) == 0:
        return []
    idx = np.linspace(0, len(vals) - 1, n).astype(int)
    out = []
    for i in idx:
        ts = pd.Timestamp(vals[i])
        out.append((int(ts.value), ts.strftime("%Y-%m")))
    return out


def _title(img, draw, text: str, font_size: int = 24) -> int:
    """顶部居中标题，返回标题区底部的 y。"""
    f = _font(font_size)
    w = draw.textlength(text, font=f)
    draw.text(((img.width - w) / 2, 12), text, fill=DARK, font=f)
    return 12 + font_size + 10


def _legend(img, draw, items, x: int, y: int, font_size: int = 14):
    """items: [(label, color, kind)]，kind='line'|'bar'。"""
    f = _font(font_size)
    cx = x
    for label, color, kind in items:
        w = draw.textlength(label, font=f) + 26
        mid = y + font_size // 2
        if kind == "line":
            draw.line([cx, mid, cx + 16, mid], fill=color, width=2)
        else:
            draw.rectangle([cx, mid - 5, cx + 16, mid + 5], fill=color)
        draw.text((cx + 20, y), label, fill=DARK, font=f)
        cx += w + 18


def _heat_color(v: float, vmax: float) -> tuple[int, int, int]:
    """RdYlGn：t=0 绿 -> 0.5 浅黄 -> 1 红。"""
    t = (v / vmax + 1) / 2 if vmax > 0 else 0.5
    t = max(0.0, min(1.0, t))
    stops = [(0.0, (26, 150, 65)), (0.5, (255, 255, 191)), (1.0, (215, 25, 28))]
    for i in range(1, len(stops)):
        if t <= stops[i][0]:
            t0, c0 = stops[i - 1]
            t1, c1 = stops[i]
            fr = (t - t0) / (t1 - t0)
            return tuple(int(c0[k] + (c1[k] - c0[k]) * fr) for k in range(3))
    return stops[-1][1]


def _new_canvas(w: int, h: int, bg: str = "white"):
    img = Image.new("RGB", (w, h), bg)
    return img, ImageDraw.Draw(img)


# ---------------------------------------------------------------- 1. 行情与均线
def plot_kline(df: pd.DataFrame, ma_windows=(5, 20, 60),
               name: str | None = None, title: str = "",
               out_dir=None) -> Path:
    """收盘价 + 均线。df 为标准日线（含 date/close 列）。"""
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    if d.empty:
        raise ValueError("plot_kline: 数据为空")
    x = _ns_index(d["date"])
    series = [("收盘价", d["close"].values, DARK, 2.0)]
    macolors = {5: "#e0a020", 20: "#2060c0", 60: "#c04040", 120: "#808080", 250: "#00a0a0"}
    for n in ma_windows:
        if len(d) > n:
            series.append((f"MA{n}", d["close"].rolling(n).mean().values,
                           macolors.get(n, "gray"), 1.6))
    ys = [s[1] for s in series]
    y_min = float(np.nanmin([np.nanmin(y) for y in ys]))
    y_max = float(np.nanmax([np.nanmax(y) for y in ys]))
    pad = (y_max - y_min) * 0.06
    y_min, y_max = y_min - pad, y_max + pad

    img, draw = _new_canvas(1100, 520)
    top = _title(img, draw, title or "收盘价与均线")
    rect = (70, top + 6, 1080, top + 6 + 400)
    _draw_axes(draw, rect, float(x.min()), float(x.max()), y_min, y_max,
               y_fmt=lambda t: f"{t:.0f}", y_n=6, x_labels=_date_labels(d["date"]))
    for label, y, color, lw in series:
        mask = ~np.isnan(y)
        draw.line(_points(x[mask], y[mask], rect, float(x.min()), float(x.max()), y_min, y_max),
                  fill=color, width=int(lw * 1.6))
    _legend(img, draw, [(s[0], s[2], "line") for s in series], 90, rect[3] + 12)
    return _save(img, f"kline_{name}.png" if name else "kline.png", out_dir)


# ---------------------------------------------------------------- 2. 回测净值与回撤
def plot_backtest(result: pd.DataFrame, title: str = "",
                  name: str | None = None, out_dir=None) -> Path:
    """回测结果图：策略净值 vs 基准 + 回撤。result 为 engine.run_backtest 的输出。"""
    d = result.copy()
    d["date"] = pd.to_datetime(d["date"])
    if d.empty:
        raise ValueError("plot_backtest: 数据为空")
    x = _ns_index(d["date"])
    x_min, x_max = float(x.min()), float(x.max())
    nav = d["nav"].values
    bench = d["benchmark_nav"].values if "benchmark_nav" in d.columns else None
    dd = d["drawdown"].values

    img, draw = _new_canvas(1100, 660)
    top = _title(img, draw, title or "策略净值 vs 基准")
    rect1 = (70, top + 6, 1080, top + 6 + 380)
    all_y = [nav] + ([bench] if bench is not None else [])
    y_min = min(float(np.nanmin(y)) for y in all_y)
    y_max = max(float(np.nanmax(y)) for y in all_y)
    pad = (y_max - y_min) * 0.06 or 0.05
    _draw_axes(draw, rect1, x_min, x_max, y_min - pad, y_max + pad,
               y_fmt=lambda t: f"{t:.2f}", y_n=6, x_labels=_date_labels(d["date"]))
    draw.line([rect1[0], rect1[3], rect1[2], rect1[3]], fill=GRID, width=1)  # 净值=1 参考线
    draw.line(_points(x, nav, rect1, x_min, x_max, y_min - pad, y_max + pad),
              fill=RED, width=3)
    if bench is not None:
        draw.line(_points(x, bench, rect1, x_min, x_max, y_min - pad, y_max + pad),
                  fill=GRAY, width=2)
    items = [("策略净值", RED, "line")]
    if bench is not None:
        items.append(("基准（买入持有）", GRAY, "line"))
    _legend(img, draw, items, 90, rect1[3] + 10)

    rect2 = (70, rect1[3] + 46, 1080, rect1[3] + 46 + 150)
    dd_min = float(np.nanmin(dd))
    _draw_axes(draw, rect2, x_min, x_max, dd_min, 0.0,
               y_fmt=lambda t: f"{t*100:.0f}%", y_n=4, x_labels=_date_labels(d["date"]))
    pts = _points(x, dd, rect2, x_min, x_max, dd_min, 0.0)
    zero_y = rect2[3]
    draw.polygon([(pts[0][0], zero_y)] + pts + [(pts[-1][0], zero_y)],
                 fill="#f2b8b0")
    draw.line(pts, fill=RED, width=2)
    draw.text((rect2[0] + 8, rect2[1] - 20), "回撤", fill=DARK, font=_font(16))
    return _save(img, f"backtest_{name}.png" if name else "backtest.png", out_dir)


# ---------------------------------------------------------------- 3. 因子 IC 热力图
def plot_ic_heatmap(ic_table: pd.DataFrame, metric: str = "ic_mean",
                    annot: str = "nw_tstat", title: str = "",
                    out_dir=None) -> Path:
    """因子 IC 热力图：行=因子，列=持有期；格内标注数值，* 表示 |NW t|>2。"""
    tbl = ic_table.pivot(index="factor", columns="horizon", values=metric)
    sig = None
    if annot and annot in ic_table.columns:
        sig = ic_table.pivot(index="factor", columns="horizon", values=annot)
    vmax = float(np.nanmax(np.abs(tbl.values))) if tbl.size else 1.0

    cell_w, cell_h = 96, 46
    left, top_m = 120, 90
    w = left + cell_w * tbl.shape[1] + 80
    h = top_m + cell_h * tbl.shape[0] + 70
    img, draw = _new_canvas(w, h)
    top = _title(img, draw, title or f"因子 RankIC 热力图（* 表示 |NW t|>2）", font_size=22)
    f_label = _font(16)
    f_cell = _font(15)
    # 列标签（持有期）
    for j, col in enumerate(tbl.columns):
        draw.text((left + j * cell_w + cell_w / 2 - draw.textlength(str(col), font=f_label) / 2,
                   top + 14), str(col), fill=DARK, font=f_label)
    # 行标签（因子） + 单元格
    for i, idx in enumerate(tbl.index):
        lab = str(idx)
        draw.text((left - draw.textlength(lab, font=f_label) - 10,
                   top + 34 + i * cell_h + cell_h / 2 - 9), lab, fill=DARK, font=f_label)
        for j in range(tbl.shape[1]):
            v = tbl.values[i, j]
            x0 = left + j * cell_w
            y0 = top + 34 + i * cell_h
            if np.isnan(v):
                draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill="#eeeeee", outline="#cccccc")
                continue
            color = _heat_color(float(v), vmax)
            draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=color, outline="#ffffff")
            star = "*" if sig is not None and abs(sig.values[i, j]) > 2 else ""
            text = f"{v:.3f}{star}"
            tw = draw.textlength(text, font=f_cell)
            draw.text((x0 + (cell_w - tw) / 2, y0 + cell_h / 2 - 10), text, fill="#111111", font=f_cell)
    # 图例（渐变条）
    bar_x0, bar_y0 = left, top + 34 + cell_h * tbl.shape[0] + 18
    bar_w = cell_w * tbl.shape[1]
    for k in range(bar_w):
        v = -vmax + 2 * vmax * k / max(bar_w - 1, 1)
        draw.line([bar_x0 + k, bar_y0, bar_x0 + k, bar_y0 + 12], fill=_heat_color(v, vmax))
    draw.text((bar_x0 - 4, bar_y0 + 16), f"{vmax:.3f}", fill=DARK, font=_font(13), anchor="ra")
    draw.text((bar_x0 + bar_w + 4, bar_y0 + 16), f"+{vmax:.3f}", fill=DARK, font=_font(13))
    return _save(img, "ic_heatmap.png", out_dir)


# ---------------------------------------------------------------- 4. 分层净值
def plot_quantile_nav(nav: pd.DataFrame, title: str = "",
                      name: str | None = None, out_dir=None) -> Path:
    """分层回测各组净值曲线（nav 的列为 Q1..Q5）。"""
    d = nav.copy()
    d.index = pd.to_datetime(d.index)
    if d.empty:
        raise ValueError("plot_quantile_nav: 数据为空")
    qcols = sorted(d.columns)
    colors = QCOLORS[:len(qcols)]
    x = _ns_index(d.index)
    x_min, x_max = float(x.min()), float(x.max())
    y_min = float(np.nanmin(d.values))
    y_max = float(np.nanmax(d.values))
    pad = (y_max - y_min) * 0.06 or 0.05

    img, draw = _new_canvas(1100, 500)
    top = _title(img, draw, title or "分层回测净值（Q1 最低组 → Q5 最高组）")
    rect = (70, top + 6, 1080, top + 6 + 380)
    _draw_axes(draw, rect, x_min, x_max, y_min - pad, y_max + pad,
               y_fmt=lambda t: f"{t:.2f}", y_n=6, x_labels=_date_labels(d.index))
    for i, c in enumerate(qcols):
        y = d[c].values
        mask = ~np.isnan(y)
        draw.line(_points(x[mask], y[mask], rect, x_min, x_max, y_min - pad, y_max + pad),
                  fill=colors[i], width=3)
    _legend(img, draw, [(c, colors[i], "line") for i, c in enumerate(qcols)], 90, rect[3] + 12)
    return _save(img, f"quantile_{name}.png" if name else "quantile.png", out_dir)


# ---------------------------------------------------------------- 5. 分年度收益
def plot_yearly_returns(df: pd.DataFrame, title: str = "",
                        name: str | None = None, out_dir=None) -> Path:
    """分年度收益柱状图（红涨绿跌）。"""
    d = df.copy()
    d["year"] = pd.to_datetime(d["date"]).dt.year
    ret = d.groupby("year")["close"].apply(lambda s: s.iloc[-1] / s.iloc[0] - 1)
    if len(ret) == 0:
        raise ValueError("plot_yearly_returns: 数据为空")
    y_min = float(min(0.0, ret.min()))
    y_max = float(max(0.0, ret.max()))
    pad = (y_max - y_min) * 0.08 or 0.02

    img, draw = _new_canvas(1100, 460)
    top = _title(img, draw, title or "分年度收益")
    rect = (70, top + 6, 1080, top + 6 + 340)
    n = len(ret)
    _draw_axes(draw, rect, -0.5, n - 0.5, y_min - pad, y_max + pad,
               y_fmt=lambda t: f"{t*100:+.0f}%", y_n=6, grid=True)
    f_year = _font(14)
    f_val = _font(13)
    zero_y = rect[3] - (0 - (y_min - pad)) / (y_max + pad - (y_min - pad)) * (rect[3] - rect[1])
    draw.line([rect[0], int(zero_y), rect[2], int(zero_y)], fill=DARK, width=1)
    step = (rect[2] - rect[0]) / n
    bar_w = step * 0.62
    for i, (yr, v) in enumerate(ret.items()):
        x0 = rect[0] + i * step + (step - bar_w) / 2
        v_top = rect[3] - (v - (y_min - pad)) / (y_max + pad - (y_min - pad)) * (rect[3] - rect[1])
        y_top, y_bot = (v_top, zero_y) if v >= 0 else (zero_y, v_top)
        color = RED if v >= 0 else GREEN
        draw.rectangle([x0, y_top, x0 + bar_w, y_bot], fill=color)
        lab = str(yr)
        draw.text((x0 + bar_w / 2 - draw.textlength(lab, font=f_year) / 2, rect[3] + 4),
                  lab, fill=DARK, font=f_year)
        vlab = f"{v:+.0%}"
        ty = y_top - 20 if v >= 0 else y_bot + 4
        draw.text((x0 + bar_w / 2 - draw.textlength(vlab, font=f_val) / 2, ty),
                  vlab, fill=DARK, font=f_val)
    return _save(img, f"yearly_{name}.png" if name else "yearly.png", out_dir)
