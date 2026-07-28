#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合优选：
  #1 每只股票在 牛/震荡/熊 下的优选策略
  #2 每个策略的精选股票池
基于 trades_s{1,4,5}.csv 的逐笔回测（③已弃用不纳入）。

策略—行情适配（依据形态本质 + 回测表现）：
  ① 平台突破缩量回踩 / ⑤ 放量突破前高回踩 = 顺势突破 → 牛市
  ④ MACD 底背离                          = 超跌反转 → 震荡 / 熊末
判定阈值：某股某策略需 笔数≥2 且 平均收益>0；盈亏比(PF)≥2 记为"强"，≥1.3 记为"可"。

⚠️ 仅历史回测统计，不构成投资建议。
"""
import os
import pandas as pd

from scanner import industry_map, name_map

OUT = "/Users/bruce/Documents/Stock_output"
STRATS = {"s1": "① 平台突破", "s4": "④ MACD底背离", "s5": "⑤ 前高回踩", "s6": "⑥ 缩量十日线↑"}
STRONG, OK = 2.0, 1.3
RECENT_DAYS = 7   # #3 "当前选出"的近期信号窗口(自然日)


def load_stats():
    """返回 {(strat,ticker): dict(n,pf,avg)}"""
    st = {}
    for key in STRATS:
        path = os.path.join(OUT, f"trades_{key}.csv")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        for tk, g in df.groupby("Ticker"):
            n = len(g)
            gains = g.loc[g["Ret%"] > 0, "Ret%"].sum()
            losses = g.loc[g["Ret%"] < 0, "Ret%"].sum()
            pf = gains / abs(losses) if losses < 0 else float("inf")
            st[(key, tk)] = {"n": n, "pf": pf, "avg": round(float(g["Ret%"].mean()), 1)}
    return st


def grade(s):
    if s is None or s["n"] < 2 or s["avg"] <= 0:
        return 0
    if s["pf"] >= STRONG:
        return 2
    if s["pf"] >= OK:
        return 1
    return 0


def pf_str(s):
    if not s:
        return "-"
    p = "∞" if s["pf"] == float("inf") else round(s["pf"], 1)
    return f"PF{p}/{s['n']}笔/{s['avg']:+.0f}%"


def main():
    st = load_stats()
    tickers = sorted({tk for (_, tk) in st})
    imap = industry_map()
    nmap = name_map()

    # ---------- #2 每策略精选池 ----------
    rep = ["# 策略优选（基于历史回测，③已弃用）\n",
           "⚠️ 仅历史回测统计，不构成投资建议\n",
           "## #2 每个策略的精选股票池\n",
           "入选：笔数≥2、平均收益>0、盈亏比≥2，按盈亏比排序。\n"]
    for key, label in STRATS.items():
        rows = []
        for tk in tickers:
            s = st.get((key, tk))
            if s and s["n"] >= 2 and s["avg"] > 0 and s["pf"] >= STRONG:
                rows.append({"股票": tk, "名称": nmap.get(tk, ""), "行业": imap.get(tk, ""),
                             "笔数": s["n"],
                             "盈亏比": ("∞" if s["pf"] == float("inf") else round(s["pf"], 1)),
                             "平均收益%": s["avg"]})
        rr = pd.DataFrame(rows)
        if not rr.empty:
            rr = rr.sort_values("平均收益%", ascending=False)
        rep.append(f"### {label}  精选 {len(rr)} 只\n")
        rep.append(rr.to_markdown(index=False) if not rr.empty else "（无达标股票）")
        rep.append("")

    # ---------- #1 每股票 × 行情 优选策略 ----------
    rep.append("\n## #1 每只股票在不同行情下的优选策略\n")
    rep.append("牛市看顺势突破(①⑤)、震荡/熊看反转(④)；标注该股该策略的盈亏比。\n")
    rows = []
    for tk in tickers:
        s1, s4, s5 = st.get(("s1", tk)), st.get(("s4", tk)), st.get(("s5", tk))
        s6 = st.get(("s6", tk))
        # 牛市：⑤、① 里选达标且最强的(最多2个)
        bull = sorted([(k, s) for k, s in [("⑤前高回踩", s5), ("①平台突破", s1)] if grade(s) >= 1],
                      key=lambda x: -(x[1]["pf"] if x[1]["pf"] != float("inf") else 999))
        bull_txt = "、".join(f"{k}({pf_str(s)})" for k, s in bull[:2]) or "持有为主/观望"
        # 震荡：④底背离 / ⑥缩量十日线↑ 达标则用
        rp = []
        if grade(s4) >= 1:
            rp.append(f"④底背离({pf_str(s4)})")
        if grade(s6) >= 1:
            rp.append(f"⑥缩量十日线({pf_str(s6)})")
        range_txt = "、".join(rp) or "观望/波段"
        # 熊市：④ 强(PF≥2)才抄反弹，否则空仓
        bear_txt = f"④底背离·严止损({pf_str(s4)})" if grade(s4) >= 2 else "空仓观望"
        rows.append({"股票": tk, "名称": nmap.get(tk, ""), "行业": imap.get(tk, ""),
                     "牛市": bull_txt, "震荡": range_txt, "熊市": bear_txt})
    rep.append(pd.DataFrame(rows).to_markdown(index=False))

    # ---------- #3 各策略当前选出的股票(依据最新数据的近期买入信号 + 历史证据) ----------
    SIG_FILES = {
        "s1": "strategy1_platform_pullback.csv",
        "s4": "strategy4_macd_divergence.csv",
        "s5": "strategy5_prevhigh_retest.csv",
        "s6": "strategy6_vol_shrink_ma10.csv",
    }
    rep.append("\n## #3 各策略当前选出的股票（依据最新数据的近期买入信号）\n")
    rep.append("即各策略依据「昨天及之前」的数据触发买入信号、当前选出的股票。"
               "历史证据 = 该股该策略回测的 盈亏比PF/笔数/平均收益%。\n")
    sigs, alldates = {}, []
    for key, fn in SIG_FILES.items():
        p = os.path.join(OUT, fn)
        if os.path.exists(p):
            d = pd.read_csv(p)
            if not d.empty:
                d["SignalDate"] = pd.to_datetime(d["SignalDate"])
                sigs[key] = d
                alldates.append(d["SignalDate"].max())
    if not alldates:
        rep.append("（暂无信号数据，请先运行扫描）")
    else:
        dmax = max(alldates)
        cut = dmax - pd.Timedelta(days=RECENT_DAYS)
        rep.append(f"（最新信号日 {dmax.date()}；近 {RECENT_DAYS} 天窗口：{cut.date()} 起。"
                   f"⚠️最新一日若为盘中未收盘数据，信号需收盘后再确认）\n")
        for key, label in STRATS.items():
            d = sigs.get(key)
            recent = d[d["SignalDate"] >= cut] if d is not None else None
            if recent is None or recent.empty:
                rep.append(f"### {label}   近{RECENT_DAYS}天：没有\n")
                continue
            recent = recent.sort_values("SignalDate").groupby("Ticker").tail(1)
            rows = []
            for _, r in recent.sort_values("SignalDate", ascending=False).iterrows():
                tk = r["Ticker"]
                rows.append({"代码": tk, "名称": nmap.get(tk, ""), "行业": imap.get(tk, ""),
                             "信号日": r["SignalDate"].date(),
                             "历史证据": pf_str(st.get((key, tk)))})
            rep.append(f"### {label}   近{RECENT_DAYS}天选出 {len(rows)} 只\n")
            rep.append(pd.DataFrame(rows).to_markdown(index=False))

    text = "\n".join(rep)
    with open(os.path.join(OUT, "策略优选.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
