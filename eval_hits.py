#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命中信号的后续走势验证（回测）。
对 Stock_output 里各策略 CSV 中「最近 N 天」的命中，
以信号日收盘价为买入价，统计其后 5/10/20 个交易日及至今的涨跌。

⚠️ 仅为形态信号的历史表现统计，不构成任何投资建议。
"""
import argparse
import os
import pandas as pd
from datetime import timedelta

from scanner import load_series   # 复用数据加载

OUT = "/Users/bruce/Documents/Stock_output"
STRATS = [
    ("strategy1_platform_pullback.csv", "① 平台突破缩量回踩"),
    ("strategy3_annual_line.csv",       "③ 年线放量突破"),
    ("strategy4_macd_divergence.csv",   "④ MACD 底背离"),
    ("strategy5_prevhigh_retest.csv",   "⑤ 放量突破前高回踩"),
]
HORIZONS = [5, 10, 20]


def fwd_stats(g, sig_date):
    """g: 单只股票序列(按日期升序)。返回买入价与各周期收益。"""
    idx = g.index[g["Date"] == sig_date]
    if len(idx) == 0:
        return None
    i = idx[0]
    entry = g["Close"].iloc[i]
    res = {"Entry": round(entry, 4)}
    n = len(g)
    for k in HORIZONS:
        if i + k < n:
            res[f"R{k}d%"] = round((g["Close"].iloc[i + k] / entry - 1) * 100, 2)
        else:
            res[f"R{k}d%"] = None
    # 至今
    res["R_now%"] = round((g["Close"].iloc[-1] / entry - 1) * 100, 2)
    res["Bars_after"] = n - 1 - i
    # 其后20日内(或可得范围)最大涨、最大回撤
    end = min(i + 20, n - 1)
    seg = g["Close"].iloc[i:end + 1]
    res["MaxGain%"] = round((seg.max() / entry - 1) * 100, 2)
    res["MaxDD%"] = round((seg.min() / entry - 1) * 100, 2)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("--recent", type=int, default=90)
    args = ap.parse_args()

    series = load_series(args.data)
    data_max = max(df["Date"].max() for df in series.values())
    cut = data_max - timedelta(days=args.recent)

    report = [f"# 最近 {args.recent} 天命中信号 · 后续走势验证\n",
              f"- 数据最新日: {data_max.date()}（信号需 ≥ {cut.date()}）",
              f"- 买入价 = 信号日收盘；收益 = 之后 5/10/20 交易日及至今 vs 买入价",
              "- ⚠️ 仅历史表现统计，不构成投资建议\n"]

    all_rows = []
    for fname, label in STRATS:
        path = os.path.join(OUT, fname)
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        if df.empty:
            report.append(f"## {label}\n\n最近 {args.recent} 天无命中。\n")
            continue
        df["SignalDate"] = pd.to_datetime(df["SignalDate"])
        recent = df[df["SignalDate"] >= cut]
        if recent.empty:
            report.append(f"## {label}\n\n最近 {args.recent} 天无命中。\n")
            continue

        rows = []
        for _, h in recent.iterrows():
            tk = h["Ticker"]
            g = series.get(tk)
            if g is None:
                continue
            s = fwd_stats(g, h["SignalDate"])
            if s is None:
                continue
            row = {"Ticker": tk, "SignalDate": h["SignalDate"].date(), **s}
            rows.append(row)
            all_rows.append({"策略": label, **row})
        rr = pd.DataFrame(rows)
        report.append(f"## {label}  （{len(rr)} 个命中）\n")
        report.append(rr.to_markdown(index=False))
        # 小结：以"至今收益>0"为上涨
        up = (rr["R_now%"] > 0).sum()
        report.append(f"\n**至今上涨 {up}/{len(rr)}**，"
                      f"平均至今收益 {rr['R_now%'].mean():.2f}%，"
                      f"其后20日内平均最大涨幅 {rr['MaxGain%'].mean():.2f}%，"
                      f"平均最大回撤 {rr['MaxDD%'].mean():.2f}%\n")

    # 总体
    if all_rows:
        allr = pd.DataFrame(all_rows)
        up = (allr["R_now%"] > 0).sum()
        report.insert(4, f"\n**总体：{len(allr)} 个命中，至今上涨 {up}（{up/len(allr)*100:.0f}%），"
                          f"平均至今收益 {allr['R_now%'].mean():.2f}%**\n")

    text = "\n".join(report)
    with open(os.path.join(OUT, "recent_eval.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
