#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全历史命中分析（2023 年以来）。
对每个策略的全部命中做带止损回测，并按 年份 / 个股 拆解，
导出逐笔明细 CSV，汇总成 analysis_all.md。

⚠️ 仅历史形态回测统计，不构成任何投资建议。
"""
import argparse
import os
import pandas as pd

from scanner import load_series, add_indicators, industry_map, name_map
from backtest import STRATS, make_stop, run_trade

OUT = "/Users/bruce/Documents/Stock_output"


def brief(rr):
    n = len(rr)
    w = int((rr["Ret%"] > 0).sum())
    gains = rr.loc[rr["Ret%"] > 0, "Ret%"].sum()
    losses = rr.loc[rr["Ret%"] < 0, "Ret%"].sum()
    pf = gains / abs(losses) if losses < 0 else float("inf")
    return pd.Series({
        "笔数": n,
        "胜率%": round(w / n * 100) if n else 0,
        "平均收益%": round(float(rr["Ret%"].mean()), 2) if n else 0,
        "平均盈利%": round(float(rr.loc[rr["Ret%"] > 0, "Ret%"].mean()), 2) if w else 0,
        "平均亏损%": round(float(rr.loc[rr["Ret%"] < 0, "Ret%"].mean()), 2) if (rr["Ret%"] < 0).any() else 0,
        "盈亏比": (round(float(pf), 2) if pf != float("inf") else "∞"),
        "止损离场": int((rr["Reason"] == "止损").sum()),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("--max-hold", type=int, default=40)
    args = ap.parse_args()

    series = load_series(args.data)
    series = {tk: add_indicators(df) for tk, df in series.items()}
    data_max = max(df["Date"].max() for df in series.values())
    data_min = min(df["Date"].min() for df in series.values())
    imap = industry_map()
    nmap = name_map()

    rep = [f"# 全历史命中分析（{data_min.date()} ~ {data_max.date()}）\n",
           f"- 回测规则：入场=信号日收盘；止损=各策略形态失效点；否则持有到 {args.max_hold} 交易日或数据末",
           f"- 股票池：{len(series)} 只 ({', '.join(series.keys())})",
           "- ⚠️ 仅历史回测统计，不构成任何投资建议\n"]

    overall = []
    for fname, label, strat, stopdesc in STRATS:
        path = os.path.join(OUT, fname)
        if not os.path.exists(path):
            continue
        hits = pd.read_csv(path)
        if hits.empty:
            overall.append({"策略": label, "笔数": 0})
            continue
        hits["SignalDate"] = pd.to_datetime(hits["SignalDate"])

        rows = []
        for _, h in hits.iterrows():
            g = series.get(h["Ticker"])
            if g is None:
                continue
            t = run_trade(g, h["SignalDate"], make_stop(strat, h), args.max_hold)
            if t:
                rows.append({"Ticker": h["Ticker"], "Market": h.get("Market", ""),
                             "SignalDate": h["SignalDate"].date(), **t})
        rr = pd.DataFrame(rows)
        if rr.empty:
            continue
        rr["Year"] = pd.to_datetime(rr["SignalDate"]).dt.year

        # 导出逐笔明细
        csv_path = os.path.join(OUT, f"trades_{strat}.csv")
        rr.drop(columns=["Year"]).to_csv(csv_path, index=False)

        b = brief(rr)
        overall.append({"策略": label, "止损位": stopdesc, **b.to_dict()})

        # 分块报告
        rep.append(f"## {label}   （共 {len(rr)} 笔，逐笔见 trades_{strat}.csv）\n")
        rep.append("**按年份**\n")
        by_year = rr.groupby("Year").apply(brief, include_groups=False).reset_index()
        rep.append(by_year.to_markdown(index=False))
        rep.append("\n**按个股**\n")
        by_tk = rr.groupby("Ticker").apply(brief, include_groups=False).reset_index()
        by_tk.insert(1, "名称", by_tk["Ticker"].map(nmap).fillna(""))
        by_tk.insert(2, "行业", by_tk["Ticker"].map(imap).fillna(""))
        by_tk = by_tk.sort_values("平均收益%", ascending=False)
        rep.append(by_tk.to_markdown(index=False))

        # 最佳/最差 5 笔
        cols = ["Ticker", "SignalDate", "Entry", "ExitDate", "ExitPx", "Ret%", "Bars", "Reason"]
        best = rr.nlargest(5, "Ret%")[cols]
        worst = rr.nsmallest(5, "Ret%")[cols]
        rep.append("\n**最佳 5 笔**\n")
        rep.append(best.to_markdown(index=False))
        rep.append("\n**最差 5 笔**\n")
        rep.append(worst.to_markdown(index=False))
        rep.append("")

    rep.insert(4, "## 各策略总览\n\n" + pd.DataFrame(overall).to_markdown(index=False) + "\n")

    text = "\n".join(rep)
    with open(os.path.join(OUT, "analysis_all.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
