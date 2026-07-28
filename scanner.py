#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
形态扫描器 —— 在已下载的日线数据上跑 3 个策略，输出命中列表。

策略（详见 /Users/bruce/Documents/AICode/Stock/策略.md）：
  ① 成都  平台突破后的缩量回踩
  ④ 郑州  下跌趋势末期的日线 MACD 底背离
  ⑤ 杭州  放量突破前高后回踩前高不破

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
# 主流程
# ----------------------------------------------------------------------
SCANS = [
    ("s1", "strategy1_platform_pullback", "① 平台突破缩量回踩", scan_s1),
    ("s3", "strategy3_annual_line",       "③ 年线放量突破",      scan_s3),
    ("s4", "strategy4_macd_divergence",   "④ MACD 底背离",      scan_s4),
    ("s5", "strategy5_prevhigh_retest",   "⑤ 放量突破前高回踩",  scan_s5),
    ("s6", "strategy6_vol_shrink_ma10",   "⑥ 缩量+十日线向上",    scan_s6),
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
        "- 策略: ① 平台突破缩量回踩 / ④ MACD 底背离 / ⑤ 放量突破前高回踩",
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
