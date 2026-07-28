#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
目标单回测：次日开盘建仓 → 窗口内收盘触及目标%则止盈 / 跌破止损% / 到期离场。
输出每策略：达标率(止盈占比)、胜率、平均收益、盈亏比、平均持有天数。
目标%、窗口、止损% 可调。

⚠️ 仅历史回测统计，不构成任何投资建议。
"""
import argparse
import os
import pandas as pd
from scanner import load_series, name_map, industry_map

OUT = "/Users/bruce/Documents/Stock_output"
STRATS = [
    ("① 平台突破", "strategy1_platform_pullback.csv"),
    ("④ MACD底背离", "strategy4_macd_divergence.csv"),
    ("⑤ 前高回踩", "strategy5_prevhigh_retest.csv"),
    ("⑥ 缩量十日线↑", "strategy6_vol_shrink_ma10.csv"),
]


def run_signals(series, hits, target, window, stop):
    """target/stop 为比例(0.15=+15%, 0.08=-8%)；window 交易日。返回逐笔列表。"""
    rows = []
    for _, h in hits.iterrows():
        g = series.get(h["Ticker"])
        if g is None:
            continue
        idx = g.index[g["Date"] == pd.to_datetime(h["SignalDate"])]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        if i + 1 >= len(g):
            continue
        entry = g["Open"].iloc[i + 1]
        if pd.isna(entry) or entry <= 0:
            continue
        tp, sl = entry * (1 + target), entry * (1 - stop) if stop else None
        exit_px, reason, bars = None, None, 0
        end = min(i + window, len(g) - 1)
        for j in range(i + 1, end + 1):
            cl = g["Close"].iloc[j]
            bars = j - i
            if cl >= tp:
                exit_px, reason = cl, "止盈"; break
            if sl is not None and cl <= sl:
                exit_px, reason = cl, "止损"; break
        if exit_px is None:
            exit_px, reason = g["Close"].iloc[end], "到期"; bars = end - i
        rows.append({"Ticker": h["Ticker"], "Ret%": round((exit_px / entry - 1) * 100, 2),
                     "Bars": bars, "Reason": reason})
    return pd.DataFrame(rows)


def stat(rr):
    n = len(rr)
    if n == 0:
        return {"样本": 0}
    tp = (rr["Reason"] == "止盈").sum()
    w = (rr["Ret%"] > 0).sum()
    gains = rr.loc[rr["Ret%"] > 0, "Ret%"].sum()
    loss = rr.loc[rr["Ret%"] < 0, "Ret%"].sum()
    pf = gains / abs(loss) if loss < 0 else float("inf")
    return {
        "样本": n,
        "达标率": f"{tp}/{n} ({tp/n*100:.0f}%)",
        "胜率": f"{w/n*100:.0f}%",
        "平均收益%": round(float(rr["Ret%"].mean()), 2),
        "盈亏比": ("∞" if pf == float("inf") else round(float(pf), 2)),
        "平均持有": round(float(rr["Bars"].mean()), 1),
        "止损离场%": round(float((rr["Reason"] == "止损").mean() * 100)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("--target", type=float, default=0.15, help="止盈目标(比例)，如0.15=+15%%")
    ap.add_argument("--window", type=int, default=30, help="持有窗口(交易日)")
    ap.add_argument("--stop", type=float, default=0.08, help="止损(比例)，0=不止损")
    args = ap.parse_args()
    series = load_series(args.data)

    rows = []
    for label, fn in STRATS:
        p = os.path.join(OUT, fn)
        if not os.path.exists(p):
            continue
        hits = pd.read_csv(p)
        if hits.empty:
            continue
        rr = run_signals(series, hits, args.target, args.window, args.stop)
        rows.append({"策略": label, **stat(rr)})

    rep = ["# 目标单回测\n",
           f"- 规则：次日开盘建仓 → {args.window}日内收盘 ≥ +{args.target*100:.0f}% 止盈 / "
           f"≤ -{args.stop*100:.0f}% 止损 / 到期收盘离场",
           "- 达标率 = 止盈(触及目标)占比；⚠️ 仅历史回测统计，不构成投资建议\n",
           pd.DataFrame(rows).to_markdown(index=False)]
    text = "\n".join(rep)
    with open(os.path.join(OUT, "目标单回测.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
