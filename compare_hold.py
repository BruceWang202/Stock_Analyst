#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比不同 max_hold（持有上限）下各策略的回测表现。"""
import argparse
import os
import pandas as pd

from scanner import load_series, add_indicators
from backtest import STRATS, make_stop, run_trade
from analyze import brief

OUT = "/Users/bruce/Documents/Stock_output"


def run_all(series, hold):
    """返回 {strat_key: DataFrame(trades)}"""
    res = {}
    for fname, label, strat, _ in STRATS:
        path = os.path.join(OUT, fname)
        if not os.path.exists(path):
            continue
        hits = pd.read_csv(path)
        if hits.empty:
            res[strat] = pd.DataFrame()
            continue
        hits["SignalDate"] = pd.to_datetime(hits["SignalDate"])
        rows = []
        for _, h in hits.iterrows():
            g = series.get(h["Ticker"])
            if g is None:
                continue
            t = run_trade(g, h["SignalDate"], make_stop(strat, h), hold)
            if t:
                rows.append(t)
        res[strat] = pd.DataFrame(rows)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("--holds", default="40,60")
    args = ap.parse_args()
    holds = [int(x) for x in args.holds.split(",")]

    series = load_series(args.data)
    series = {tk: add_indicators(df) for tk, df in series.items()}

    per_hold = {h: run_all(series, h) for h in holds}

    label_of = {s[2]: s[1] for s in STRATS}
    rows = []
    for strat, label in label_of.items():
        row = {"策略": label}
        for h in holds:
            rr = per_hold[h].get(strat, pd.DataFrame())
            if rr is None or rr.empty:
                row[f"h{h}_笔数"] = 0
                continue
            b = brief(rr)
            row[f"h{h}_胜率%"] = b["胜率%"]
            row[f"h{h}_平均%"] = b["平均收益%"]
            row[f"h{h}_盈亏比"] = b["盈亏比"]
            # 到期占比：反映有多少交易被上限截断
            row[f"h{h}_到期%"] = round((rr["Reason"] == "到期").mean() * 100)
        rows.append(row)

    tbl = pd.DataFrame(rows)
    text = ["# max_hold 对比（40 vs 60 交易日）\n",
            "- 「到期%」= 未止损、持满上限被强制离场的比例；越高说明利润可能被上限截断",
            "- ⚠️ 仅历史回测统计，不构成投资建议\n",
            tbl.to_markdown(index=False)]
    out = "\n".join(text)
    with open(os.path.join(OUT, "compare_hold.md"), "w", encoding="utf-8") as f:
        f.write(out)
    print(out)


if __name__ == "__main__":
    main()
