#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minervini 趋势模板（Stage 2 上升趋势筛选）+ 自动止损位 + 熊市过滤。

规则与止损放置移植自 RyanJHamby/stock-screener (MIT)，
仓库副本在 _ref/minervini-screener；本文件改成吃本项目本地日线，
第 8 条由原项目的"Phase==2"换成**真正的 RS 相对强度排名**（因为我们有全池数据）。

八条模板（≥7 条通过即算入选，Minervini 原标准）：
  1. 价 > MA150 且 价 > MA200
  2. MA150 > MA200
  3. MA200 上行（比 20 日前高）
  4. MA50 > MA150
  5. 价 > MA50
  6. 价距 52 周低点 ≥ +30%
  7. 价距 52 周高点 ≤ 25%
  8. RS 排名 ≥ 70（池内同市场横截面百分位，IBD 式多周期加权收益）

止损位（Stage 2 规则）：
  近10日最低价×0.995 与 MA50×0.99 取**较高者**（更紧的那个），
  再夹到 3%~10% 风险区间；另给 2×ATR20 作为参考。

熊市过滤：信号日所属市场若为熊市则标记出来（默认剔除，--keep-bear 可保留）——
这是趋势跟随策略最重要的一层开关，逆势做多趋势突破胜率会腰斩。

用法：
  python trend.py                  # 回溯最近 500 根出历史信号 + 当前入选名单
  python trend.py --history 0      # 只看当前名单（快）
  python trend.py --keep-bear      # 不剔除熊市期间的信号（用于对比熊市过滤的效果）

输出（/Users/bruce/Documents/Stock_output/）：
  strategyT_trend_template.csv   趋势模板买点信号（可被 backtest.py 回测）
  trend_current.csv              当前通过模板的名单（含各条明细与止损位）
  trend.md                       汇总报告

⚠️ 仅技术筛选与历史统计，不构成任何投资建议。
"""
import argparse
import os
from datetime import timedelta

import pandas as pd
import yaml

from regime import IDX_OF, compute_regime_series
from scanner import (CONFIG_PATH, DEFAULT_OUT, STOCK_MARKETS, industry_map,
                     kind_of, load_series, name_map)

PARAMS = {
    "min_pass": 7,          # ≥ 多少条通过算入选（Minervini 原标准 7/8）
    "rs_min": 70,           # 第8条 RS 排名门槛（0~100）
    "low_dist_min": 30,     # 第6条 距52周低点至少 +30%
    "high_dist_max": 25,    # 第7条 距52周高点最多 -25%
    "reentry_gap": 10,      # 信号去重：连续通过视为一次，需先"掉出" N 根才算新信号
    "atr_win": 20,          # ATR 窗口
    "atr_mult": 2.0,        # ATR 止损倍数
}

# IBD 式 RS：多周期收益加权（近期权重更高）
RS_WEIGHTS = [(63, 0.4), (126, 0.3), (189, 0.2), (252, 0.1)]


def indicators(df):
    """给单只股票补齐趋势模板需要的全部指标列。"""
    d = df.copy()
    c, h, l = d["Close"], d["High"], d["Low"]
    d["MA50"] = c.rolling(50).mean()
    d["MA150"] = c.rolling(150).mean()
    d["MA200"] = c.rolling(200).mean()
    d["MA200_20ago"] = d["MA200"].shift(20)
    d["Hi52"] = c.rolling(250).max()
    d["Lo52"] = c.rolling(250).min()
    d["Low10"] = l.rolling(10).min()
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.rolling(PARAMS["atr_win"]).mean()
    # IBD 式 RS 原始分（多周期加权收益，%）
    rs = 0
    for win, w in RS_WEIGHTS:
        rs = rs + c.pct_change(win, fill_method=None).fillna(0) * w
    d["RS_raw"] = rs * 100
    return d


def rs_rank_by_market(inds, series):
    """
    横截面 RS 排名：同市场同一天，把各股 RS 原始分排成 0~100 百分位。
    这是 IBD RS Rating 的做法——相对强度只有"相对同伴"才有意义。
    """
    by_mkt = {}
    for tk, d in inds.items():
        mkt = series[tk]["Market"].iloc[0]
        by_mkt.setdefault(mkt, {})[tk] = d.set_index("Date")["RS_raw"]
    ranks = {}
    for mkt, cols in by_mkt.items():
        wide = pd.DataFrame(cols).sort_index()
        if wide.shape[1] < 5:
            # 同市场标的太少，横截面排名无意义 → 该条直接给通过(不当卡点)
            r = pd.DataFrame(100.0, index=wide.index, columns=wide.columns)
        else:
            r = wide.rank(axis=1, pct=True) * 100
        for tk in cols:
            ranks[tk] = r[tk]
    return ranks


def criteria_frame(d, rs_rank):
    """逐日算八条，返回 (各条布尔DataFrame, 通过条数Series)。"""
    c = d["Close"]
    rr = rs_rank.reindex(d["Date"]).values
    cols = {
        "c1价>150&200": (c > d["MA150"]) & (c > d["MA200"]),
        "c2 150>200": d["MA150"] > d["MA200"],
        "c3 200上行": d["MA200"] > d["MA200_20ago"],
        "c4 50>150": d["MA50"] > d["MA150"],
        "c5价>50": c > d["MA50"],
        "c6离底≥30%": (c / d["Lo52"] - 1) * 100 >= PARAMS["low_dist_min"],
        "c7离顶≤25%": (1 - c / d["Hi52"]) * 100 <= PARAMS["high_dist_max"],
        "c8 RS≥70": pd.Series(rr, index=d.index) >= PARAMS["rs_min"],
    }
    cf = pd.DataFrame(cols).fillna(False)
    return cf, cf.sum(axis=1)


def stop_levels(d, i):
    """
    返回 (结构止损, ATR止损, 风险%)。结构止损按 Minervini Stage2 规则，
    ATR 止损作参考；回测用结构止损。
    """
    px = float(d["Close"].iloc[i])
    low10 = d["Low10"].iloc[i]
    ma50 = d["MA50"].iloc[i]
    swing = float(low10) * 0.995 if low10 == low10 else px * 0.9
    sma = float(ma50) * 0.99 if ma50 == ma50 and ma50 > 0 else swing
    stop = max(swing, sma)
    risk = (px - stop) / px
    if risk < 0.03:
        stop = px * 0.97
    elif risk > 0.10:
        stop = px * 0.90
    atr = d["ATR"].iloc[i]
    atr_stop = px - PARAMS["atr_mult"] * float(atr) if atr == atr else None
    return stop, atr_stop, (px - stop) / px * 100


def regime_lookup(data_dir):
    """各市场的每日牛熊状态，用于过滤逆势信号。"""
    idx = load_series(data_dir, markets=("INDEX",))
    out = {}
    for mkt, sym in IDX_OF.items():
        g = idx.get(sym)
        if g is not None:
            out[mkt] = compute_regime_series(g).set_index("Date")["Regime"]
    return out


def main():
    ap = argparse.ArgumentParser(description="Minervini 趋势模板筛选")
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--history", type=int, default=500, help="回溯最近 N 根生成历史信号；0=只看当前")
    ap.add_argument("--keep-bear", action="store_true", help="保留熊市期间的信号(默认剔除)")
    ap.add_argument("--recent", type=int, default=90)
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    etf_set = set(cfg.get("etf_tickers", []))

    series = load_series(args.data, markets=STOCK_MARKETS)
    if not series:
        print("没有数据。")
        return
    print(f"载入 {len(series)} 只标的，计算趋势模板 …")

    inds = {tk: indicators(df) for tk, df in series.items()}
    ranks = rs_rank_by_market(inds, series)
    regimes = regime_lookup(args.data)
    nmap, imap = name_map(), industry_map()

    sig_rows, cur_rows = [], []
    for tk, d in inds.items():
        mkt = series[tk]["Market"].iloc[0]
        cf, passed = criteria_frame(d, ranks.get(tk, pd.Series(dtype=float)))
        ok = passed >= PARAMS["min_pass"]
        n = len(d)
        base = {"Ticker": tk, "名称": nmap.get(tk, ""), "行业": imap.get(tk, ""),
                "Market": mkt, "类型": kind_of(tk, etf_set)}

        # 当前是否入选
        i = n - 1
        cur = {**base, "日期": d["Date"].iloc[i].date(),
               "通过条数": int(passed.iloc[i]), "入选": bool(ok.iloc[i]),
               "RS排名": round(float(ranks[tk].iloc[-1]), 0) if tk in ranks else None,
               "收盘": round(float(d["Close"].iloc[i]), 4)}
        stop, atr_stop, risk = stop_levels(d, i)
        cur.update({"止损位": round(stop, 4), "风险%": round(risk, 1),
                    "ATR止损": round(atr_stop, 4) if atr_stop else None})
        cur.update({c: bool(cf[c].iloc[i]) for c in cf.columns})
        cur_rows.append(cur)

        # 历史信号：从"未入选"变成"入选"的那一天（reentry_gap 内不重复）
        if args.history:
            start = max(250, n - args.history)
            last_sig = -10 ** 9
            for j in range(start, n):
                if not ok.iloc[j]:
                    continue
                if ok.iloc[j - 1]:
                    continue                          # 仍在延续，不是新信号
                if j - last_sig < PARAMS["reentry_gap"]:
                    continue
                last_sig = j
                dt = d["Date"].iloc[j]
                reg = regimes.get(mkt)
                rg = reg.asof(dt) if reg is not None and len(reg) else "unknown"
                st, ast, rk = stop_levels(d, j)
                sig_rows.append({
                    **base, "SignalDate": dt, "通过条数": int(passed.iloc[j]),
                    "RS排名": round(float(ranks[tk].reindex([dt]).iloc[0]), 0) if tk in ranks else None,
                    "BuyClose": round(float(d["Close"].iloc[j]), 4),
                    "StopLevel": round(st, 4), "风险%": round(rk, 1),
                    "ATR止损": round(ast, 4) if ast else None,
                    "市场状态": rg,
                })

    cur = pd.DataFrame(cur_rows).sort_values(["入选", "通过条数"], ascending=[False, False])
    sig = pd.DataFrame(sig_rows)
    if not sig.empty:
        sig = sig.sort_values(["SignalDate", "Ticker"]).reset_index(drop=True)
        n_all = len(sig)
        n_bear = int((sig["市场状态"] == "bear").sum())
        if not args.keep_bear:
            sig = sig[sig["市场状态"] != "bear"].reset_index(drop=True)
    else:
        n_all = n_bear = 0

    os.makedirs(args.out, exist_ok=True)
    cur.to_csv(os.path.join(args.out, "trend_current.csv"), index=False)
    sig.to_csv(os.path.join(args.out, "strategyT_trend_template.csv"), index=False)

    data_max = max(df["Date"].max() for df in series.values())
    cut = data_max - timedelta(days=args.recent)

    md = ["# Minervini 趋势模板", "",
          f"- 数据最新日: {data_max.date()}　标的 {len(series)} 只　"
          f"入选门槛 ≥{PARAMS['min_pass']}/8 条",
          "- 八条：价>150&200线 / 150>200 / 200线上行 / 50>150 / 价>50线 / "
          "离52周低≥30% / 离52周高≤25% / RS排名≥70",
          "- RS 排名 = 同市场池内多周期加权收益的百分位（IBD 式）",
          f"- 熊市过滤：{'关闭(--keep-bear)' if args.keep_bear else '开启'}，"
          f"历史信号 {n_all} 个中有 {n_bear} 个发生在熊市"
          f"{'（已剔除）' if not args.keep_bear else ''}",
          "- ⚠️ 仅技术筛选，不构成投资建议", ""]

    sel = cur[cur["入选"]]
    md += [f"## 一、当前入选（{len(sel)} 只）", ""]
    if sel.empty:
        md.append("无标的通过 ≥7/8 条。下面看接近的。")
    else:
        cols = ["Ticker", "名称", "行业", "通过条数", "RS排名", "收盘", "止损位", "风险%", "ATR止损"]
        md.append(sel[cols].to_markdown(index=False))
    md.append("")

    near = cur[(~cur["入选"]) & (cur["通过条数"] >= PARAMS["min_pass"] - 1)]
    md += [f"## 二、只差一条（{len(near)} 只）", ""]
    if near.empty:
        md.append("无。")
    else:
        cf_cols = [c for c in cur.columns if c.startswith("c")]
        n2 = near[["Ticker", "名称", "通过条数", "RS排名", "收盘"] + cf_cols].copy()
        for c in cf_cols:
            n2[c] = n2[c].map({True: "✓", False: "✗"})
        md.append(n2.to_markdown(index=False))
    md.append("")

    md += ["## 三、各条通过率（全池当前）", ""]
    cf_cols = [c for c in cur.columns if c.startswith("c")]
    rate = pd.DataFrame({"条件": cf_cols,
                         "通过只数": [int(cur[c].sum()) for c in cf_cols],
                         "通过率%": [round(cur[c].mean() * 100) for c in cf_cols]})
    md.append(rate.to_markdown(index=False))
    md.append("")

    md += [f"## 四、历史信号（最近 {args.recent} 天）", ""]
    if sig.empty:
        md.append("无。")
    else:
        recent = sig[sig["SignalDate"] >= cut]
        md.append(f"- 保留信号 **{len(sig)}** 个，最近 {args.recent} 天 **{len(recent)}** 个")
        md.append("")
        if not recent.empty:
            cols = ["Ticker", "名称", "行业", "SignalDate", "通过条数", "RS排名",
                    "BuyClose", "StopLevel", "风险%", "市场状态"]
            r = recent[cols].copy()
            r["SignalDate"] = pd.to_datetime(r["SignalDate"]).dt.date
            md.append(r.to_markdown(index=False))

    text = "\n".join(md)
    with open(os.path.join(args.out, "trend.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(f"当前入选 {len(sel)} 只；历史信号 {len(sig)} 个（原始 {n_all}，熊市 {n_bear}）")
    print(f"报告已写: {os.path.join(args.out, 'trend.md')}")


if __name__ == "__main__":
    main()
