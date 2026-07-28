#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
带止损规则的回测 —— 对扫描器命中的信号，按各策略「形态失效点」止损，
未止损则持有到 max_hold 或数据末，统计胜率、盈亏比等。

止损规则（忠于原文形态失效点）：
  ① 平台突破缩量回踩 : 收盘跌破 突破阳线实体一半(BodyMid) -> 止损
  ③ 年线放量突破     : 收盘跌破 年线(MA250)               -> 止损
  ④ MACD 底背离      : 收盘跌破 背离低点(NewLow)           -> 止损
  ⑤ 放量突破前高回踩 : 收盘跌破 前高线(PrevHigh)           -> 止损（原文"跌破无条件走人"）

入场：信号日收盘价。未触发止损则持有到 max_hold 个交易日或数据末。

⚠️ 仅为形态信号的历史回测统计，不构成任何投资建议。

用法：
  python backtest.py                 # max_hold=40，全部命中做统计 + 最近90天明细
  python backtest.py --max-hold 60 --recent 120
"""
import argparse
import os
import pandas as pd
from datetime import timedelta

from scanner import load_series, add_indicators, PARAMS

OUT = "/Users/bruce/Documents/Stock_output"

# 文件 -> (标签, 策略key, 止损位说明)
STRATS = [
    ("strategy1_platform_pullback.csv", "① 平台突破缩量回踩", "s1", "跌破阳线实体一半"),
    ("strategy3_annual_line.csv",       "③ 年线放量突破",      "s3", "跌破年线MA250"),
    ("strategy4_macd_divergence.csv",   "④ MACD 底背离",      "s4", "跌破背离低点"),
    ("strategy5_prevhigh_retest.csv",   "⑤ 放量突破前高回踩",  "s5", "跌破前高线"),
    ("strategy6_vol_shrink_ma10.csv",   "⑥ 缩量+十日线向上",    "s6", "近最大量卖出/跌破成本8%"),
]


def make_stop(strat, hit):
    """返回 stop(g, j) -> 是否在第 j 根触发离场(止损/止盈)。"""
    if strat == "s1":
        lvl = hit["BodyMid"];  return lambda g, j: g["Close"].iloc[j] < lvl
    if strat == "s4":
        lvl = hit["NewLow"];   return lambda g, j: g["Close"].iloc[j] < lvl
    if strat == "s5":
        lvl = hit["PrevHigh"]; return lambda g, j: g["Close"].iloc[j] < lvl
    if strat == "s3":
        return lambda g, j: g["Close"].iloc[j] < g["MA250"].iloc[j]
    if strat == "s6":
        maxwin = PARAMS.get("s6_maxwin", 120)
        sell_frac = PARAMS.get("s6_sell_max_frac", 0.9)
        sky_win = PARAMS.get("s6_sky_win", 250)
        sky_pct = PARAMS.get("s6_sky_pct", 0.98)
        stop_frac = PARAMS.get("s6_stop", 0.08)
        entry = hit.get("BuyClose")

        def stop_s6(g, j):
            vj = g["Volume"].iloc[j]
            volmax = g["Volume"].iloc[max(0, j - maxwin):j + 1].max()
            near_max = volmax and vj >= volmax * sell_frac                    # 近最大量→卖
            # 天量(按该股自身历史自适应)：进入近 sky_win 日成交量的高分位
            win = g["Volume"].iloc[max(0, j - sky_win):j]
            sky = len(win) >= 30 and vj >= win.quantile(sky_pct)
            loss = entry and g["Close"].iloc[j] < entry * (1 - stop_frac)     # 保护止损
            return bool(near_max or sky or loss)
        return stop_s6
    return lambda g, j: False


def run_trade(g, sig_date, stop_fn, max_hold):
    idx = g.index[g["Date"] == sig_date]
    if len(idx) == 0:
        return None
    i = int(idx[0])
    n = len(g)
    entry = g["Close"].iloc[i]
    exit_i, reason = None, None
    for j in range(i + 1, min(i + max_hold, n - 1) + 1):
        if stop_fn(g, j):
            exit_i, reason = j, "止损"
            break
    if exit_i is None:
        if i + max_hold <= n - 1:
            exit_i, reason = i + max_hold, "到期"
        else:
            exit_i, reason = n - 1, "未平仓"   # 后续数据不足，仍持有
    exit_px = g["Close"].iloc[exit_i]
    seg = g["Close"].iloc[i:exit_i + 1]
    return {
        "Entry": round(entry, 4),
        "ExitDate": g["Date"].iloc[exit_i].date(),
        "ExitPx": round(exit_px, 4),
        "Ret%": round((exit_px / entry - 1) * 100, 2),
        "Bars": exit_i - i,
        "Reason": reason,
        "MaxGain%": round((seg.max() / entry - 1) * 100, 2),
        "MaxDD%": round((seg.min() / entry - 1) * 100, 2),
    }


def agg_stats(rr):
    n = len(rr)
    closed = rr[rr["Reason"] != "未平仓"]
    wins = rr[rr["Ret%"] > 0]
    gains = rr.loc[rr["Ret%"] > 0, "Ret%"].sum()
    losses = rr.loc[rr["Ret%"] < 0, "Ret%"].sum()
    pf = (gains / abs(losses)) if losses < 0 else float("inf")
    avg_win = rr.loc[rr["Ret%"] > 0, "Ret%"].mean() if (rr["Ret%"] > 0).any() else 0.0
    avg_loss = rr.loc[rr["Ret%"] < 0, "Ret%"].mean() if (rr["Ret%"] < 0).any() else 0.0
    return {
        "笔数": n,
        "胜率": f"{len(wins)}/{n} ({len(wins)/n*100:.0f}%)" if n else "-",
        "平均收益%": round(float(rr["Ret%"].mean()), 2) if n else None,
        "平均盈利%": round(float(avg_win), 2),
        "平均亏损%": round(float(avg_loss), 2),
        "盈亏比(PF)": round(float(pf), 2) if pf != float("inf") else "∞",
        "平均持有(bar)": round(float(rr["Bars"].mean()), 1) if n else None,
        "止损离场": int((rr["Reason"] == "止损").sum()),
        "未平仓": int((rr["Reason"] == "未平仓").sum()),
    }


def stat_line(rr):
    s = agg_stats(rr)
    return (f"小结：{s['笔数']} 笔，胜率 {s['胜率']}，平均收益 {s['平均收益%']}%，"
            f"平均盈利 {s['平均盈利%']}% / 平均亏损 {s['平均亏损%']}%，"
            f"盈亏比 {s['盈亏比(PF)']}，止损离场 {s['止损离场']} 笔。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("--max-hold", type=int, default=40, help="最大持有交易日，默认40")
    ap.add_argument("--recent", type=int, default=90, help="明细展示最近N自然日的命中")
    args = ap.parse_args()

    series = load_series(args.data)
    series = {tk: add_indicators(df) for tk, df in series.items()}
    data_max = max(df["Date"].max() for df in series.values())
    cut = data_max - timedelta(days=args.recent)

    rep = [f"# 带止损回测（max_hold={args.max_hold} 交易日）\n",
           f"- 数据最新日: {data_max.date()}",
           f"- 入场=信号日收盘；止损=各策略形态失效点；否则持有到 {args.max_hold} 日或数据末",
           "- ⚠️ 仅历史回测统计，不构成投资建议\n",
           "## 各策略汇总（全部历史命中）\n"]

    summary_rows = []
    detail_blocks = []
    for fname, label, strat, stopdesc in STRATS:
        path = os.path.join(OUT, fname)
        if not os.path.exists(path):
            continue
        hits = pd.read_csv(path)
        if hits.empty:
            summary_rows.append({"策略": label, "止损位": stopdesc, "笔数": 0})
            continue
        hits["SignalDate"] = pd.to_datetime(hits["SignalDate"])

        rows = []
        for _, h in hits.iterrows():
            g = series.get(h["Ticker"])
            if g is None:
                continue
            t = run_trade(g, h["SignalDate"], make_stop(strat, h), args.max_hold)
            if t:
                rows.append({"Ticker": h["Ticker"], "SignalDate": h["SignalDate"].date(), **t})
        rr = pd.DataFrame(rows)
        if rr.empty:
            continue
        st = agg_stats(rr)
        summary_rows.append({"策略": label, "止损位": stopdesc, **st})

        # 最近N天明细
        rrd = rr[pd.to_datetime(rr["SignalDate"]) >= cut]
        if not rrd.empty:
            detail_blocks.append(f"### {label}  最近{args.recent}天（{len(rrd)}笔）\n")
            detail_blocks.append(rrd.to_markdown(index=False))
            detail_blocks.append(f"\n{stat_line(rrd)}\n")

    rep.append(pd.DataFrame(summary_rows).to_markdown(index=False))
    rep.append("\n## 最近命中明细\n")
    rep.extend(detail_blocks if detail_blocks else ["最近无命中。"])

    text = "\n".join(rep)
    with open(os.path.join(OUT, "backtest.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
