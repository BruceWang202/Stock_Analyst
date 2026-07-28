#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高抛低吸（分批20%）波段回测 —— 面向高股息个股。

信号（基础）：以 20 日均线乖离带 + 5 日均线拐头 择时
  低吸(买20%): 收盘低于 MA20 达 B%  且 MA5 企稳(今日≥昨日)
  高抛(卖20%): 收盘高于 MA20 达 B%  且 MA5 转弱(今日≤昨日)
每次买/卖一个 20% 份额（满仓=5份）。

过滤器（只对"低吸"生效，避免下跌趋势/弱市抄底）：
  年线过滤 : 仅当 收盘 > MA250(年线) 才低吸
  指数过滤 : 仅当 对应市场指数 收盘 > 指数MA20 才低吸

对比变体：A 无过滤 / B 年线 / C 指数 / D 年线+指数。
基准：买入持有(期初满仓)。

⚠️ 仅历史回测统计，不构成投资建议。价格用未复权收盘(不含股息)，
   高股息个股实际持有还会额外收股息，对"多持有"的策略更有利。
"""
import argparse
import os
import pandas as pd

from scanner import load_series
from regime import compute_regime_series

OUT = "/Users/bruce/Documents/Stock_output"
HIGH_DIV = ["0836.HK", "0883.HK", "0939.HK", "0941.HK", "1088.HK", "1398.HK", "D05.SI", "Z74.SI"]
IDX_OF = {"HK": "^HSI", "SG": "^STI", "US": "^GSPC"}
VARIANTS = {"A_无过滤": (False, False), "B_年线": (True, False),
            "C_指数": (False, True), "D_年线+指数": (True, True)}


def prep(df):
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA250"] = df["Close"].rolling(250).mean()
    df["Bias20"] = (df["Close"] / df["MA20"] - 1) * 100
    return df


def simulate(df, B, use_year, use_idx, start_frac=0.0, tranche=20.0, cap=100.0,
             lo=None, hi=None, regime_mode=False):
    """start_frac: 期初仓位(0=空仓起步, 1=满仓起步在其上高抛低吸)。
    lo/hi: 仓位下/上限(份额比例0~1)，默认 0~1。
    regime_mode: 按市场状态切换——牛市只买不卖(持有)、熊市不买且跌破年线减仓、震荡正常高抛低吸。
    返回 策略收益%,买入持有%,交易次数,平均仓位%。"""
    lo = 0.0 if lo is None else lo
    hi = 1.0 if hi is None else hi
    start = df["MA250"].first_valid_index()
    if start is None:
        start = df["MA20"].first_valid_index() or 0
    start = max(start, 1)

    p0 = df["Close"].iloc[start]
    shares = (cap * start_frac) / p0
    cash = cap - shares * p0
    trades = 0
    exp_sum, exp_n = 0.0, 0
    for i in range(start, len(df)):
        price = df["Close"].iloc[i]
        bias = df["Bias20"].iloc[i]
        ma5_up = df["MA5"].iloc[i] >= df["MA5"].iloc[i - 1]
        equity_i = cash + shares * price
        pos_frac = (shares * price) / equity_i if equity_i > 0 else 0
        exp_sum += pos_frac; exp_n += 1

        buy = bias <= -B and ma5_up
        if buy and use_year:
            buy = buy and (df["Close"].iloc[i] > df["MA250"].iloc[i])
        if buy and use_idx and "IdxClose" in df:
            buy = buy and (df["IdxClose"].iloc[i] > df["IdxMA20"].iloc[i])
        sell = bias >= B and (df["MA5"].iloc[i] <= df["MA5"].iloc[i - 1])

        # 按市场状态切换
        if regime_mode and "Regime" in df:
            reg = df["Regime"].iloc[i]
            if reg == "bull":            # 牛市：持有，不高抛(只允许低吸补回)
                sell = False
            elif reg == "bear":          # 熊市：停止低吸(不逆势抄底)，保留原始高抛
                buy = False
            # range 震荡：维持原始高抛低吸

        if buy and cash >= tranche - 1e-9 and pos_frac < hi - 1e-9:
            shares += tranche / price; cash -= tranche; trades += 1
        elif sell and shares > 0 and pos_frac > lo + 1e-9:
            sell_sh = min(shares, tranche / price)
            cash += sell_sh * price; shares -= sell_sh; trades += 1
    equity = cash + shares * df["Close"].iloc[-1]
    bnh = df["Close"].iloc[-1] / p0 - 1
    avg_exp = (exp_sum / exp_n * 100) if exp_n else 0
    return (equity / cap - 1) * 100, bnh * 100, trades, avg_exp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("-B", type=float, default=5.0, help="乖离带阈值%%，默认5")
    ap.add_argument("--tickers", help="逗号分隔，覆盖默认高股息池")
    args = ap.parse_args()

    series = load_series(args.data, markets=("US", "HK", "SG", "INDEX"))
    series = {tk: prep(df) for tk, df in series.items()}
    tickers = args.tickers.split(",") if args.tickers else HIGH_DIV

    # 给每只股票并入对应市场指数的 收盘 与 MA20
    for tk in tickers:
        g = series.get(tk)
        if g is None:
            continue
        mkt = g["Market"].iloc[0]
        idx = series.get(IDX_OF.get(mkt))
        if idx is not None:
            im = idx[["Date", "Close", "MA20"]].rename(columns={"Close": "IdxClose", "MA20": "IdxMA20"})
            g = g.merge(im, on="Date", how="left")
            reg = compute_regime_series(idx)            # 该市场指数的每日状态
            g = g.merge(reg, on="Date", how="left")
            g["Regime"] = g["Regime"].fillna("range")
            series[tk] = g

    rep = [f"# 高抛低吸波段回测（分批20%，乖离带 B={args.B}%）\n",
           f"- 个股池(高股息): {', '.join(tickers)}",
           "- 基础信号: MA20乖离带 + MA5拐头；过滤: 年线 / 市场指数(见各变体)",
           "- 收益为价格收益(不含股息)；基准=买入持有(期初满仓)",
           "- ⚠️ 仅历史回测统计，不构成投资建议\n"]

    valid = [tk for tk in tickers if series.get(tk) is not None and "IdxClose" in series[tk]]

    def run_model(start_frac, title, note, add_regime=False):
        rep.append(f"\n## {title}\n\n{note}\n")
        summary = []
        variants = list(VARIANTS.items())
        for vname, (uy, ui) in variants:
            rows = []
            for tk in valid:
                sret, bnh, tr, exp = simulate(series[tk], args.B, uy, ui, start_frac=start_frac)
                rows.append({"超额": sret - bnh, "策略": sret, "bnh": bnh, "tr": tr, "exp": exp})
            rr = pd.DataFrame(rows)
            win = int((rr["超额"] > 0).sum())
            summary.append({"变体": vname,
                            "平均策略收益%": round(rr["策略"].mean(), 1),
                            "平均买入持有%": round(rr["bnh"].mean(), 1),
                            "平均超额%": round(rr["超额"].mean(), 1),
                            "跑赢只数": f"{win}/{len(rr)}",
                            "平均仓位%": round(rr["exp"].mean(), 0),
                            "平均交易次数": round(rr["tr"].mean(), 1)})
        if add_regime:
            rows = []
            for tk in valid:
                sret, bnh, tr, exp = simulate(series[tk], args.B, False, False,
                                              start_frac=start_frac, regime_mode=True)
                rows.append({"超额": sret - bnh, "策略": sret, "bnh": bnh, "tr": tr, "exp": exp})
            rr = pd.DataFrame(rows)
            win = int((rr["超额"] > 0).sum())
            summary.append({"变体": "E_按状态切换",
                            "平均策略收益%": round(rr["策略"].mean(), 1),
                            "平均买入持有%": round(rr["bnh"].mean(), 1),
                            "平均超额%": round(rr["超额"].mean(), 1),
                            "跑赢只数": f"{win}/{len(rr)}",
                            "平均仓位%": round(rr["exp"].mean(), 0),
                            "平均交易次数": round(rr["tr"].mean(), 1)})
        rep.append(pd.DataFrame(summary).to_markdown(index=False))

    run_model(0.0, "模式1：空仓起步·逢低建仓（0→100%）",
              "从空仓开始，只在低吸信号买入。上涨行情里仓位低、易跑输——反映纯抄底的局限。")
    run_model(1.0, "模式2：满仓起步·高抛低吸（在底仓上增减）",
              "期初满仓，高抛卖20%、低吸买回20%（0~100%区间）。更贴近你说的「操作20%」。"
              "E_按状态切换=牛市持有(不高抛)/熊市停止低吸/震荡才高抛低吸。", add_regime=True)

    # 模式2·A变体 逐股明细
    rep.append("\n## 模式2 · A_无过滤 逐股明细\n")
    rows = []
    for tk in valid:
        sret, bnh, tr, exp = simulate(series[tk], args.B, False, False, start_frac=1.0)
        rows.append({"Ticker": tk, "策略收益%": round(sret, 1), "买入持有%": round(bnh, 1),
                     "超额%": round(sret - bnh, 1), "平均仓位%": round(exp, 0), "交易次数": tr})
    rep.append(pd.DataFrame(rows).to_markdown(index=False))

    # 分年份（模式2·A）：看波段在震荡/上涨年份的差异
    rep.append("\n## 分年份对比（模式2·A_无过滤，各年独立满仓起步）\n")
    yrows = []
    for yr in [2023, 2024, 2025, 2026]:
        srets, bnhs = [], []
        for tk in valid:
            g = series[tk]
            gy = g[pd.to_datetime(g["Date"]).dt.year == yr].reset_index(drop=True)
            if len(gy) < 30:
                continue
            gy = prep(gy)  # 年内重算均线(近似)
            if "IdxClose" not in gy:
                gy["IdxClose"] = gy["Close"]; gy["IdxMA20"] = gy["MA20"]
            sret, bnh, tr, exp = simulate(gy, args.B, False, False, start_frac=1.0)
            srets.append(sret); bnhs.append(bnh)
        if srets:
            import statistics as st
            yrows.append({"年份": yr, "平均策略%": round(st.mean(srets), 1),
                          "平均买入持有%": round(st.mean(bnhs), 1),
                          "平均超额%": round(st.mean(srets) - st.mean(bnhs), 1)})
    rep.append(pd.DataFrame(yrows).to_markdown(index=False))

    text = "\n".join(rep)
    with open(os.path.join(OUT, "band_trade.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
