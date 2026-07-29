#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
形态扫描器 —— 在已下载的日线数据上跑各策略，输出命中列表。

策略 ①③④⑤⑥ 详见 /Users/bruce/Documents/AICode/Stock/策略.md：
  ① 成都  平台突破后的缩量回踩
  ③ 广州  年线附近放量突破
  ④ 郑州  下跌趋势末期的日线 MACD 底背离
  ⑤ 杭州  放量突破前高后回踩前高不破
  ⑥       缩量(地量) + 十日线向上
公开形态（非上文来源）：
  ⑦ VCP 波动收缩（Minervini）：回调逐级收窄 + 量能递减 + 放量破 pivot
  ⑧ 52周新高动量（达韦斯箱体）：窄箱体整理后放量创 52 周新高
  ⑨ 一阳穿三线：低位三线粘合，放量阳线实体同时穿越 MA5/10/30

⚠️ 这些是把文字形态**简化后的量化规则**，参数在下方 PARAMS 里可调。
   结果仅为形态的技术识别，**不构成任何投资建议**。

用法：
  python scanner.py                 # 扫全部股票(US/HK/SG)，输出到默认目录
  python scanner.py --recent 60     # 额外汇总最近 60 个自然日内的信号
  python scanner.py --data DIR --out DIR

输出（默认 /Users/bruce/Documents/Stock_output/）：
  strategy1_platform_pullback.csv
  strategy4_macd_divergence.csv
  strategy5_prevhigh_retest.csv
  summary.md
"""

import argparse
import os
import re
from datetime import datetime, timedelta

import pandas as pd
import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
DEFAULT_OUT = "/Users/bruce/Documents/Stock_output"
STOCK_MARKETS = ("US", "HK", "SG", "CN", "KR")   # 指数不参与选股形态

# 每个策略扫描的标的类型：'stock'=个股, 'etf'=ETF。
# ① 平台突破缩量回踩在 ETF 上表现差(ETF难走出爆发形态) -> 只扫个股；其余个股+ETF。
STRATEGY_KINDS = {
    "s1": {"stock"},
    "s3": {"stock", "etf"},
    "s4": {"stock", "etf"},
    "s5": {"stock", "etf"},
    "s6": {"stock", "etf"},
    "s7": {"stock"},          # VCP 是成长股形态，ETF 极少走出逐级收缩
    "s8": {"stock", "etf"},
    "s9": {"stock", "etf"},
}


def kind_of(ticker, etf_set):
    return "etf" if ticker in etf_set else "stock"


def name_map():
    """从 config.yaml 注释解析 {代码: 名称}，供各列表加"股票名称"。"""
    out = {}
    try:
        for line in open(CONFIG_PATH, encoding="utf-8"):
            m = re.match(r"\s*-\s*(\S+)\s*#\s*(.+)", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    except Exception:
        pass
    return out


def industry_map():
    """从 metadata.csv 读 {代码: 行业}；不存在则空。供扫描/分析/优选带上行业列。"""
    try:
        m = pd.read_csv(os.path.join(DEFAULT_OUT, "metadata.csv"))
        return dict(zip(m["Ticker"], m["行业"].fillna("")))
    except Exception:
        return {}


def scope_label(allowed):
    if allowed == {"stock", "etf"}:
        return "个股+ETF"
    if allowed == {"stock"}:
        return "仅个股"
    if allowed == {"etf"}:
        return "仅ETF"
    return "/".join(sorted(allowed))

# ---- 可调参数 ----
PARAMS = {
    # ① 平台突破缩量回踩
    "s1_box_win": 40,        # 平台横盘窗口(交易日) ~2个月
    "s1_box_max_range": 0.35,  # 平台内(高-低)/低 上限, 越小越"窄"
    "s1_vol_mult": 1.5,      # 突破日放量倍数(相对20日均量)
    # ③ 年线放量突破
    "s3_below_win": 126,     # "年线下方半年"回看窗口(交易日)
    "s3_below_frac": 0.8,    # 该窗口内收盘价<年线 的占比下限
    "s3_vol_mult": 1.5,      # 上穿年线当日放量倍数
    # ④ MACD 底背离
    "s4_swing_w": 5,         # 摆动低点识别半窗
    # ⑤ 放量突破前高回踩
    "s5_hi_win": 60,         # 前高回看窗口
    "s5_gap": 10,            # 前高至少距突破日的间隔(避免拿刚形成的高点)
    "s5_vol_mult": 1.5,      # 突破日放量倍数
    "s5_retest_days": 12,    # 突破后多少天内完成回踩
    "s5_tol": 0.02,          # 前高线容差(±2%)
    # ⑥ 缩量(地量) + 十日线向上(买) / 近最大量(卖)
    "s6_low_win": 120,       # 缩量参考的"最低量"回看窗口
    "s6_near_low": 1.08,     # 地量阈值：当日量 ≤ 近low_win日最低量 × 此值(越小越严, 越接近真地量)
    "s6_cooldown": 10,       # 冷却：两次买入信号至少间隔的交易日数(避免密集重复)
    "s6_ma": 10,             # 均线周期(十日线)
    "s6_maxwin": 120,        # 卖出参考的"最大量"回看窗口
    "s6_sell_max_frac": 0.9, # 当日量 ≥ 窗口最大量 × 此值 → 卖出(近最大量)
    # 天量卖点：按股票自身历史自适应——当日量 ≥ 该股近 sky_win 日成交量的 sky_pct 分位数
    "s6_sky_win": 250,       # 天量分位数的回看窗口(该股自身历史)
    "s6_sky_pct": 0.99,      # 天量分位阈值(0.99=进入该股最高1%的量=真天量)；调低更早卖
    "s6_stop": 0.08,         # 保护性止损：跌破买入价此比例
    # ⑦ VCP 波动收缩(Minervini)
    "s7_base_win": 120,      # 基底回看窗口(交易日)，收缩序列在此窗口内识别
    "s7_swing_w": 5,         # 摆动高/低点识别半窗(太小会把日常噪音当成收缩)
    "s7_min_depth": 0.03,    # 回调深度小于此值视为噪音，不计入收缩序列
    "s7_min_contractions": 2,  # 末段逐级收窄的最少段数
    "s7_max_contractions": 4,  # 末段逐级收窄的最多段数(超过说明是长期阴跌，不是收缩)
    "s7_shrink": 0.8,        # 每次回调深度须 ≤ 上一次 × 此值(逐级收窄)
    "s7_last_max": 0.10,     # 最后一次回调深度上限(收得够紧才算 VCP)
    "s7_vol_mult": 1.5,      # 突破日放量倍数
    "s7_near_high": 0.75,    # 突破日收盘须 ≥ 近250日最高 × 此值(不在深坑里)
    "s7_cooldown": 20,       # 同股两次信号最小间隔
    # ⑧ 52周新高动量(达韦斯箱体)
    "s8_hi_win": 250,        # "52周高点"回看窗口
    "s8_box_win": 20,        # 突破前的箱体整理窗口
    "s8_box_max_range": 0.20,  # 箱体(高-低)/低 上限，越小越"紧"
    "s8_vol_mult": 1.5,      # 突破日放量倍数
    "s8_cooldown": 20,       # 同股两次信号最小间隔(创新高常连续出现)
    # ⑨ 一阳穿三线
    "s9_ma": (5, 10, 30),    # 被穿越的三条均线
    "s9_cohesion": 0.03,     # 三线粘合度：(最高线-最低线)/最低线 上限
    "s9_vol_mult": 2.0,      # 当日放量倍数(资料普遍要求 2 倍以上)
    "s9_hi_win": 250,        # 判断"低位"的高点回看窗口
    "s9_from_high": 0.15,    # 收盘须 ≤ 窗口最高 × (1-此值)，即距高点至少回落这么多
    "s9_cooldown": 10,       # 同股两次信号最小间隔
}

# 参数覆盖：仪表盘滑块保存的值(如天量分位)会写到此文件，导入时合并进 PARAMS
try:
    import json as _json
    _ovr = os.path.join(DEFAULT_OUT, "params_override.json")
    if os.path.exists(_ovr):
        PARAMS.update(_json.load(open(_ovr, encoding="utf-8")))
except Exception:
    pass


# ----------------------------------------------------------------------
# 数据加载：把按日期分文件夹的数据还原成每只股票的时间序列
# ----------------------------------------------------------------------
def load_series(data_dir, markets=STOCK_MARKETS):
    frames = []
    for name in sorted(os.listdir(data_dir)):
        folder = os.path.join(data_dir, name)
        if not name.isdigit() or not os.path.isdir(folder):
            continue
        for m in markets:
            f = os.path.join(folder, f"{m}.csv")
            if os.path.exists(f):
                try:
                    frames.append(pd.read_csv(f))
                except Exception:
                    pass
    if not frames:
        return {}
    alldf = pd.concat(frames, ignore_index=True)
    alldf["Date"] = pd.to_datetime(alldf["Date"])
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        alldf[c] = pd.to_numeric(alldf[c], errors="coerce")

    # 排除名单（即便历史数据里有也忽略）
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            exclude = set(yaml.safe_load(f).get("exclude_tickers", []) or [])
    except Exception:
        exclude = set()

    series = {}
    for tk, g in alldf.groupby("Ticker"):
        if tk in exclude:
            continue
        g = (g.dropna(subset=["Close"])
               .drop_duplicates(subset=["Date"])
               .sort_values("Date")
               .reset_index(drop=True))
        # 剔除 0 成交量的非交易日(节假日占位行, 开=高=低=收)；指数常无量故不剔
        if not g.empty and g["Market"].iloc[0] != "INDEX":
            g = g[g["Volume"] > 0].reset_index(drop=True)
            # 丢弃疑似"当日盘中未收盘"的最后一根(量 < 前20日中位数25%)，避免半天数据误判缩量
            if len(g) > 21:
                med = g["Volume"].iloc[-21:-1].median()
                if med > 0 and g["Volume"].iloc[-1] < med * 0.25:
                    g = g.iloc[:-1].reset_index(drop=True)
        if len(g) >= 60:
            series[tk] = g
    return series


def add_indicators(df):
    df = df.copy()
    df["VolMA20"] = df["Volume"].rolling(20).mean()
    df["MA250"] = df["Close"].rolling(250).mean()
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD"] = 2 * (df["DIF"] - df["DEA"])   # 中国习惯的 MACD 柱
    return df


# ----------------------------------------------------------------------
# ① 平台突破后的缩量回踩
# ----------------------------------------------------------------------
def scan_s1(df):
    p = PARAMS
    win, max_rng, vmult = p["s1_box_win"], p["s1_box_max_range"], p["s1_vol_mult"]
    hits, n = [], len(df)
    for i in range(win, n - 3):
        box = df.iloc[i - win:i]
        box_high, box_low = box["High"].max(), box["Low"].min()
        if box_low <= 0 or (box_high - box_low) / box_low > max_rng:
            continue                                   # 平台不够窄
        r = df.iloc[i]
        vma = r["VolMA20"]
        if pd.isna(vma) or vma <= 0:
            continue
        # 放量阳线突破平台高点
        if not (r["Close"] > box_high and r["Close"] > r["Open"] and r["Volume"] >= vmult * vma):
            continue
        mid = r["Open"] + 0.5 * (r["Close"] - r["Open"])   # 突破阳线实体中点
        d1, d2, d3 = df.iloc[i + 1], df.iloc[i + 2], df.iloc[i + 3]
        # 之后3天：缩量(均小于突破量) + 回踩不破中点
        if not (d1["Low"] >= mid and d2["Low"] >= mid and d3["Low"] >= mid):
            continue
        if not (d1["Volume"] < r["Volume"] and d2["Volume"] < r["Volume"] and d3["Volume"] < r["Volume"]):
            continue
        hits.append({
            "SignalDate": d3["Date"], "BreakoutDate": r["Date"],
            "BoxHigh": round(box_high, 4), "BreakoutClose": round(r["Close"], 4),
            "BodyMid": round(mid, 4), "BuyClose": round(d3["Close"], 4),
        })
    return hits


# ----------------------------------------------------------------------
# ③ 年线附近放量突破
# ----------------------------------------------------------------------
def scan_s3(df):
    p = PARAMS
    below_win, below_frac, vmult = p["s3_below_win"], p["s3_below_frac"], p["s3_vol_mult"]
    slope_up_req = p.get("s3_slope_up", False)   # 要求年线上行(真趋势反转)
    max_ext = p.get("s3_max_ext", None)          # 只在靠近年线时买(不追高)，如0.05=+5%内
    hits, n = [], len(df)
    for i in range(below_win, n - 3):
        ma, ma_prev = df["MA250"].iloc[i], df["MA250"].iloc[i - 1]
        if pd.isna(ma) or pd.isna(ma_prev):
            continue
        r = df.iloc[i]
        vma = r["VolMA20"]
        if pd.isna(vma) or vma <= 0:
            continue
        # 前半年多数时间在年线下方(NaN 的 MA250 自动记为"未在下方")
        prior = df.iloc[i - below_win:i]
        below = (prior["Close"] < prior["MA250"]).mean()
        if pd.isna(below) or below < below_frac:
            continue
        # 放量上穿年线(昨收在年线下/上，今收站上)
        if not (df["Close"].iloc[i - 1] <= ma_prev and r["Close"] > ma and r["Volume"] >= vmult * vma):
            continue
        # 年线上行(可选)：MA250 较20日前抬高
        if slope_up_req and not (ma > df["MA250"].iloc[i - 20]):
            continue
        # 不追高(可选)：上穿当日收盘不超过年线 max_ext
        if max_ext is not None and r["Close"] > ma * (1 + max_ext):
            continue
        # 之后连续3天收盘不跌破年线
        d1, d2, d3 = df.iloc[i + 1], df.iloc[i + 2], df.iloc[i + 3]
        if not (d1["Close"] >= d1["MA250"] and d2["Close"] >= d2["MA250"] and d3["Close"] >= d3["MA250"]):
            continue
        hits.append({
            "SignalDate": d3["Date"], "CrossDate": r["Date"],
            "MA250": round(ma, 4), "CrossClose": round(r["Close"], 4),
            "BelowFrac": round(float(below), 3), "BuyClose": round(d3["Close"], 4),
        })
    return hits


# ----------------------------------------------------------------------
# ④ 下跌末期 MACD 底背离
# ----------------------------------------------------------------------
def _swing_lows(df, w):
    lows, low = [], df["Low"].values
    for i in range(w, len(df) - w):
        if low[i] == low[i - w:i + w + 1].min():
            lows.append(i)
    return lows


def scan_s4(df):
    w = PARAMS["s4_swing_w"]
    hits = []
    lows = _swing_lows(df, w)
    for a, b in zip(lows, lows[1:]):
        la, lb = df["Low"].iloc[a], df["Low"].iloc[b]
        da, db = df["DIF"].iloc[a], df["DIF"].iloc[b]
        # 价格创新低、DIF 未创新低(抬高) => 底背离；且处于零轴下方(下跌末期)
        if lb < la and db > da and db < 0:
            hits.append({
                "SignalDate": df["Date"].iloc[b], "PrevLowDate": df["Date"].iloc[a],
                "PrevLow": round(la, 4), "NewLow": round(lb, 4),
                "DIF_prev": round(da, 4), "DIF_new": round(db, 4),
            })
    return hits


# ----------------------------------------------------------------------
# ⑤ 放量突破前高后回踩前高不破
# ----------------------------------------------------------------------
def scan_s5(df):
    p = PARAMS
    hi_win, gap, vmult = p["s5_hi_win"], p["s5_gap"], p["s5_vol_mult"]
    rt_days, tol = p["s5_retest_days"], p["s5_tol"]
    hits, n = [], len(df)
    for i in range(hi_win, n):
        window = df.iloc[i - hi_win:i - gap + 1]
        if window.empty:
            continue
        prev_high = window["High"].max()
        r = df.iloc[i]
        vma = r["VolMA20"]
        if pd.isna(vma) or vma <= 0:
            continue
        if not (r["Close"] > prev_high and r["Volume"] >= vmult * vma):
            continue                                   # 放量突破前高
        # 突破后 rt_days 内首次回踩前高线
        for j in range(i + 1, min(i + 1 + rt_days, n)):
            rj = df.iloc[j]
            if rj["Close"] < prev_high * (1 - tol):    # 收盘跌破前高线 -> 失败
                break
            if rj["Low"] <= prev_high * (1 + tol):     # 踩到线附近
                if rj["Close"] >= prev_high * (1 - tol):   # 且守住不破
                    hits.append({
                        "SignalDate": rj["Date"], "BreakoutDate": r["Date"],
                        "PrevHigh": round(prev_high, 4), "RetestLow": round(rj["Low"], 4),
                        "RetestClose": round(rj["Close"], 4),
                    })
                break
    return hits


# ----------------------------------------------------------------------
# ⑥ 缩量 + 十日线向上(买入时机)；卖出见 backtest 的近最大量退出
# ----------------------------------------------------------------------
def scan_s6(df):
    p = PARAMS
    low_win = p.get("s6_low_win", 120)
    near = p.get("s6_near_low", 1.08)
    cooldown = p.get("s6_cooldown", 10)
    maw = p.get("s6_ma", 10)
    vol = df["Volume"]
    ma = df["Close"].rolling(maw).mean()
    vmin = vol.rolling(low_win).min()       # 近 low_win 日最低量(地量参照)
    avg30 = vol.rolling(30).mean()
    hits, n = [], len(df)
    prev_sig, last_i = False, -10 ** 9
    for i in range(low_win, n):
        v, lo = vol.iloc[i], vmin.iloc[i]
        if pd.isna(lo) or lo <= 0 or pd.isna(ma.iloc[i]) or pd.isna(ma.iloc[i - 1]):
            prev_sig = False
            continue
        shrunk = v <= lo * near                        # 接近地量(近low_win日最低)
        ma_up = ma.iloc[i] > ma.iloc[i - 1]            # 十日线向上
        sig = shrunk and ma_up
        if sig and not prev_sig and (i - last_i) >= cooldown:   # 新信号 + 冷却期外
            last_i = i
            a = avg30.iloc[i]
            hits.append({
                "SignalDate": df["Date"].iloc[i],
                "Vol": int(v), "Min120": int(lo), "距地量%": round((v / lo - 1) * 100, 1),
                "量比30": round(v / a, 2) if (a and a > 0) else None,
                "Max120": int(vol.iloc[max(0, i - 120 + 1):i + 1].max()),
                "BuyClose": round(df["Close"].iloc[i], 4),
            })
        prev_sig = sig
    return hits


# ----------------------------------------------------------------------
# ⑦ VCP 波动收缩（Minervini）
#    上升趋势中，回调一次比一次浅、量一次比一次小，最后放量突破最后一个高点。
# ----------------------------------------------------------------------
def _swing_highs(df, w):
    highs, high = [], df["High"].values
    for i in range(w, len(df) - w):
        if high[i] == high[i - w:i + w + 1].max():
            highs.append(i)
    return highs


def _contractions(df, sh, sl, lo_b, hi_b):
    """在 [lo_b, hi_b] 区间内按「高点→其后第一个低点」配对，返回(回调深度序列, 对应高点价序列)。"""
    pts = sorted([(j, "H") for j in sh if lo_b <= j <= hi_b]
                 + [(j, "L") for j in sl if lo_b <= j <= hi_b])
    depths, peaks = [], []
    k = 0
    while k < len(pts):
        if pts[k][1] != "H":
            k += 1
            continue
        m = k + 1
        while m < len(pts) and pts[m][1] != "L":
            m += 1
        if m >= len(pts):
            break
        hv = df["High"].iloc[pts[k][0]]
        lv = df["Low"].iloc[pts[m][0]]
        if hv > 0:
            depths.append((hv - lv) / hv)
            peaks.append(hv)
        k = m + 1
    return depths, peaks


def scan_s7(df):
    p = PARAMS
    win, w, vmult = p["s7_base_win"], p["s7_swing_w"], p["s7_vol_mult"]
    shrink, last_max, min_depth = p["s7_shrink"], p["s7_last_max"], p["s7_min_depth"]
    min_c, max_c = p["s7_min_contractions"], p["s7_max_contractions"]
    near_hi, cooldown = p["s7_near_high"], p["s7_cooldown"]
    n = len(df)
    ma50 = df["Close"].rolling(50).mean()
    ma150 = df["Close"].rolling(150).mean()
    ma200 = df["Close"].rolling(200).mean()
    hi250 = df["High"].rolling(250, min_periods=60).max()
    # 摆动点用 ±w 窗口识别，故第 j 根要到 j+w 才能确认；下面只取 j ≤ i-1-w 的点，避免用到未来数据
    sh, sl = _swing_highs(df, w), _swing_lows(df, w)
    hits, last_i = [], -10 ** 9
    for i in range(win, n):
        r = df.iloc[i]
        vma = r["VolMA20"]
        if pd.isna(vma) or vma <= 0 or pd.isna(ma200.iloc[i]):
            continue
        # 趋势过滤：价在 MA50 上，且 MA50 > MA150 > MA200（多头排列）
        if not (r["Close"] > ma50.iloc[i] > ma150.iloc[i] > ma200.iloc[i]):
            continue
        h250 = hi250.iloc[i]
        if pd.isna(h250) or r["Close"] < h250 * near_hi:
            continue
        depths, peaks = _contractions(df, sh, sl, i - win, i - 1 - w)
        # 滤掉噪音级别的浅回调，再从末尾往前数「一段比一段浅」的连续长度
        seq = [(d, pk) for d, pk in zip(depths, peaks) if d >= min_depth]
        if len(seq) < min_c:
            continue
        t, run = len(seq) - 1, 1
        while t > 0 and seq[t][0] <= seq[t - 1][0] * shrink:
            run += 1
            t -= 1
        if not (min_c <= run <= max_c):
            continue                                    # 末段没有逐级收窄
        if seq[-1][0] > last_max:
            continue                                    # 最后一段收得不够紧
        depths, pivot = [d for d, _ in seq[-run:]], seq[-1][1]
        # 放量阳线突破最后一个收缩高点(pivot)
        if not (r["Close"] > pivot and r["Close"] > r["Open"] and r["Volume"] >= vmult * vma):
            continue
        # 基底期量能递减：后 1/3 均量 < 前 1/3 均量
        base = df["Volume"].iloc[i - win:i]
        third = max(1, len(base) // 3)
        if not (base.iloc[-third:].mean() < base.iloc[:third].mean()):
            continue
        if i - last_i < cooldown:
            continue
        last_i = i
        hits.append({
            "SignalDate": r["Date"], "Pivot": round(pivot, 4),
            "收缩次数": len(depths),
            "收缩序列%": "→".join(f"{d*100:.0f}" for d in depths),
            "量比": round(r["Volume"] / vma, 2),
            "距52周高%": round((r["Close"] / h250 - 1) * 100, 1),
            "BuyClose": round(r["Close"], 4),
        })
    return hits


# ----------------------------------------------------------------------
# ⑧ 52 周新高动量（达韦斯箱体）
#    窄箱体整理后放量创 52 周新高 —— 动量效应是少数被反复验证的异象。
# ----------------------------------------------------------------------
def scan_s8(df):
    p = PARAMS
    hi_win, box_win = p["s8_hi_win"], p["s8_box_win"]
    box_max, vmult, cooldown = p["s8_box_max_range"], p["s8_vol_mult"], p["s8_cooldown"]
    n = len(df)
    prior_hi = df["High"].rolling(hi_win).max().shift(1)   # 不含当日，避免自比
    hits, last_i = [], -10 ** 9
    for i in range(hi_win + box_win, n):
        r = df.iloc[i]
        vma, ph = r["VolMA20"], prior_hi.iloc[i]
        if pd.isna(vma) or vma <= 0 or pd.isna(ph) or ph <= 0:
            continue
        # 放量阳线创 52 周新高
        if not (r["Close"] > ph and r["Close"] > r["Open"] and r["Volume"] >= vmult * vma):
            continue
        box = df.iloc[i - box_win:i]
        bh, bl = box["High"].max(), box["Low"].min()
        if bl <= 0 or (bh - bl) / bl > box_max:
            continue                                    # 突破前箱体不够紧
        if i - last_i < cooldown:
            continue
        last_i = i
        hits.append({
            "SignalDate": r["Date"], "PrevHigh52w": round(ph, 4),
            "BoxHigh": round(bh, 4), "BoxLow": round(bl, 4),
            "箱体幅%": round((bh / bl - 1) * 100, 1),
            "量比": round(r["Volume"] / vma, 2),
            "BuyClose": round(r["Close"], 4),
        })
    return hits


# ----------------------------------------------------------------------
# ⑨ 一阳穿三线
#    低位三线粘合，一根放量阳线的实体同时穿越 MA5/MA10/MA30。
# ----------------------------------------------------------------------
def scan_s9(df):
    p = PARAMS
    f, m, s = p["s9_ma"]
    coh, vmult = p["s9_cohesion"], p["s9_vol_mult"]
    hi_win, from_hi, cooldown = p["s9_hi_win"], p["s9_from_high"], p["s9_cooldown"]
    n = len(df)
    m1 = df["Close"].rolling(f).mean()
    m2 = df["Close"].rolling(m).mean()
    m3 = df["Close"].rolling(s).mean()
    hi = df["High"].rolling(hi_win, min_periods=60).max()
    hits, last_i = [], -10 ** 9
    for i in range(s + 1, n):
        a, b, c = m1.iloc[i], m2.iloc[i], m3.iloc[i]
        if pd.isna(a) or pd.isna(b) or pd.isna(c):
            continue
        lo3, hi3 = min(a, b, c), max(a, b, c)
        if lo3 <= 0 or (hi3 - lo3) / lo3 > coh:
            continue                                    # 三线不够粘合
        r = df.iloc[i]
        vma = r["VolMA20"]
        if pd.isna(vma) or vma <= 0:
            continue
        # 阳线实体自三线下方穿到三线上方
        if not (r["Open"] < lo3 and r["Close"] > hi3):
            continue
        if r["Volume"] < vmult * vma:
            continue
        h = hi.iloc[i]
        if pd.isna(h) or r["Close"] > h * (1 - from_hi):
            continue                                    # 不在低位，是追高
        if i - last_i < cooldown:
            continue
        last_i = i
        hits.append({
            "SignalDate": r["Date"],
            "MA5": round(a, 4), "MA10": round(b, 4), "MA30": round(c, 4),
            "粘合度%": round((hi3 / lo3 - 1) * 100, 2),
            "量比": round(r["Volume"] / vma, 2),
            "距高点%": round((r["Close"] / h - 1) * 100, 1),
            "BuyClose": round(r["Close"], 4),
        })
    return hits


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
SCANS = [
    ("s1", "strategy1_platform_pullback", "① 平台突破缩量回踩", scan_s1),
    ("s3", "strategy3_annual_line",       "③ 年线放量突破",      scan_s3),
    ("s4", "strategy4_macd_divergence",   "④ MACD 底背离",      scan_s4),
    ("s5", "strategy5_prevhigh_retest",   "⑤ 放量突破前高回踩",  scan_s5),
    ("s6", "strategy6_vol_shrink_ma10",   "⑥ 缩量+十日线向上",    scan_s6),
    ("s7", "strategy7_vcp",               "⑦ VCP 波动收缩",      scan_s7),
    ("s8", "strategy8_52w_high",          "⑧ 52周新高动量",      scan_s8),
    ("s9", "strategy9_ma_pierce",         "⑨ 一阳穿三线",        scan_s9),
]


def main():
    ap = argparse.ArgumentParser(description="形态扫描器 (策略 ①④⑤)")
    ap.add_argument("--data", help="数据目录，默认取 config.yaml 的 output_dir")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"输出目录，默认 {DEFAULT_OUT}")
    ap.add_argument("--recent", type=int, default=90, help="汇总最近 N 个自然日的信号，默认 90")
    ap.add_argument("--all-kinds", action="store_true",
                    help="忽略按策略的标的类型限制，所有策略都扫个股+ETF。")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    data_dir = args.data or cfg.get("output_dir", "data")
    etf_set = set(cfg.get("etf_tickers", []))
    os.makedirs(args.out, exist_ok=True)

    print(f"读取数据: {data_dir}")
    series = load_series(data_dir)
    print(f"载入 {len(series)} 只股票: {', '.join(series.keys())}")
    if not series:
        print("没有数据可扫描。"); return
    series = {tk: add_indicators(df) for tk, df in series.items()}

    data_max = max(df["Date"].max() for df in series.values())
    recent_cut = data_max - timedelta(days=args.recent)

    summary_lines = [
        "# 形态扫描结果",
        "",
        f"- 生成时间(数据最新日): {data_max.date()}",
        f"- 股票池: {len(series)} 只 ({', '.join(series.keys())})",
        "- 策略: " + " / ".join(label for _, _, label, _ in SCANS),
        "- ⚠️ 简化量化规则，仅形态技术识别，**不构成投资建议**。",
        "",
    ]

    imap = industry_map()
    nmap = name_map()
    for key, fname, label, fn in SCANS:
        allowed = {"stock", "etf"} if args.all_kinds else STRATEGY_KINDS.get(key, {"stock", "etf"})
        rows = []
        for tk, df in series.items():
            if kind_of(tk, etf_set) not in allowed:
                continue
            for h in fn(df):
                h = {"Ticker": tk, "名称": nmap.get(tk, ""), "行业": imap.get(tk, ""),
                     "Market": df["Market"].iloc[0], **h}
                rows.append(h)
        out = pd.DataFrame(rows)
        if not out.empty:
            # 同一股票同一信号日只保留一条(取最早的突破日), 去掉重叠噪音
            sort_keys = ["Ticker", "SignalDate"] + (["BreakoutDate"] if "BreakoutDate" in out else [])
            out = (out.sort_values(sort_keys)
                      .drop_duplicates(subset=["Ticker", "SignalDate"], keep="first")
                      .sort_values(["SignalDate", "Ticker"]).reset_index(drop=True))
        path = os.path.join(args.out, fname + ".csv")
        out.to_csv(path, index=False)

        recent = out[out["SignalDate"] >= recent_cut] if not out.empty else out
        scope = scope_label(allowed)
        print(f"{label} [{scope}]: 共 {len(out)} 个信号, 最近{args.recent}天 {len(recent)} 个 -> {fname}.csv")

        summary_lines.append(f"## {label}  （扫描范围：{scope}）")
        summary_lines.append("")
        summary_lines.append(f"- 历史命中总数: **{len(out)}**")
        summary_lines.append(f"- 最近 {args.recent} 天命中: **{len(recent)}**")
        if not recent.empty:
            summary_lines.append("")
            summary_lines.append(f"最近命中：")
            summary_lines.append("")
            cols = [c for c in recent.columns if c not in ("Market",)]
            tbl = recent[cols].copy()
            tbl["SignalDate"] = tbl["SignalDate"].dt.date
            if "BreakoutDate" in tbl:
                tbl["BreakoutDate"] = pd.to_datetime(tbl["BreakoutDate"]).dt.date
            if "PrevLowDate" in tbl:
                tbl["PrevLowDate"] = pd.to_datetime(tbl["PrevLowDate"]).dt.date
            summary_lines.append(tbl.to_markdown(index=False))
        summary_lines.append("")

    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))
    print(f"\n汇总已写: {os.path.join(args.out, 'summary.md')}")


if __name__ == "__main__":
    main()
