#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略③ 年线放量突破 —— 参数网格调优。对每组参数：扫描→带止损回测→统计。"""
import pandas as pd
from scanner import load_series, add_indicators, scan_s3, PARAMS, STOCK_MARKETS

series = load_series("/Users/bruce/Documents/Stock")
series = {tk: add_indicators(df) for tk, df in series.items()}


def stop_year(buf):
    return lambda g, j: g["Close"].iloc[j] < g["MA250"].iloc[j] * (1 - buf)


def run_trade(g, sig_date, stop_fn, max_hold=40):
    idx = g.index[g["Date"] == sig_date]
    if len(idx) == 0:
        return None
    i = int(idx[0]); n = len(g); entry = g["Close"].iloc[i]
    ex = None
    for j in range(i + 1, min(i + max_hold, n - 1) + 1):
        if stop_fn(g, j):
            ex = j; break
    if ex is None:
        ex = min(i + max_hold, n - 1)
    return (g["Close"].iloc[ex] / entry - 1) * 100


def evaluate(override, buf, max_hold=40):
    base = dict(PARAMS)
    PARAMS.update({"s3_below_win": 126, "s3_below_frac": 0.8, "s3_vol_mult": 1.5,
                   "s3_slope_up": False, "s3_max_ext": None})
    PARAMS.update(override)
    rets = []
    for tk, df in series.items():
        for h in scan_s3(df):
            r = run_trade(df, h["SignalDate"], stop_year(buf), max_hold)
            if r is not None:
                rets.append(r)
    PARAMS.clear(); PARAMS.update(base)
    if not rets:
        return {"笔数": 0}
    s = pd.Series(rets)
    gains = s[s > 0].sum(); losses = s[s < 0].sum()
    pf = gains / abs(losses) if losses < 0 else float("inf")
    return {"笔数": len(s), "胜率%": round((s > 0).mean() * 100),
            "平均收益%": round(s.mean(), 2), "盈亏比": round(pf, 2) if pf != float("inf") else 999}


grid = [
    ("基线(现状) 止损跌破年线", {}, 0.0, 40),
    ("止损缓冲3%", {}, 0.03, 40),
    ("止损缓冲5%", {}, 0.05, 40),
    ("止损缓冲8%", {}, 0.08, 40),
    ("缓冲5%+放量2x", {"s3_vol_mult": 2.0}, 0.05, 40),
    ("缓冲5%+不追高8%", {"s3_max_ext": 0.08}, 0.05, 40),
    ("缓冲8%+持有60", {}, 0.08, 60),
    ("缓冲5%+持有60", {}, 0.05, 60),
    ("缓冲8%+放量2x+持有60", {"s3_vol_mult": 2.0}, 0.08, 60),
]
rows = []
for name, ov, buf, mh in grid:
    rows.append({"参数组": name, "缓冲": buf, "持有": mh, **evaluate(ov, buf, mh)})
print(pd.DataFrame(rows).to_markdown(index=False))
