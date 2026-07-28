#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按行业汇总各策略的胜率/盈亏比 —— 看哪些板块最适合哪个形态。
数据：trades_s{1,4,5}.csv（③已弃用）+ metadata.csv 的行业。写 Stock_output/行业胜率.md。

⚠️ 仅历史回测统计，不构成投资建议。
"""
import os
import pandas as pd
from scanner import industry_map

OUT = "/Users/bruce/Documents/Stock_output"
STRATS = {"s1": "① 平台突破", "s4": "④ MACD底背离", "s5": "⑤ 前高回踩", "s6": "⑥ 缩量十日线↑"}
MIN_N = 3   # 行业内至少 N 笔才纳入统计(避免偶然)


def brief(g):
    n = len(g)
    w = int((g["Ret%"] > 0).sum())
    gains = g.loc[g["Ret%"] > 0, "Ret%"].sum()
    losses = g.loc[g["Ret%"] < 0, "Ret%"].sum()
    pf = gains / abs(losses) if losses < 0 else float("inf")
    return pd.Series({"笔数": n, "胜率%": round(w / n * 100),
                      "平均收益%": round(float(g["Ret%"].mean()), 1),
                      "盈亏比": ("∞" if pf == float("inf") else round(pf, 1))})


def main():
    imap = industry_map()
    rep = ["# 按行业汇总策略胜率（哪些板块适合哪个形态）\n",
           f"- 每行业至少 {MIN_N} 笔才纳入；③ 年线策略已弃用不计",
           "- ⚠️ 仅历史回测统计，不构成投资建议\n"]

    pivot = {}   # 行业 -> {策略: 平均收益%}
    for key, label in STRATS.items():
        path = os.path.join(OUT, f"trades_{key}.csv")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        if df.empty:
            continue
        df["行业"] = df["Ticker"].map(imap).fillna("未知")
        g = df.groupby("行业").apply(brief, include_groups=False).reset_index()
        g = g[g["笔数"] >= MIN_N].copy()
        # 排序：按平均收益%
        g = g.sort_values("平均收益%", ascending=False)
        rep.append(f"\n## {label}  （按行业）\n")
        rep.append(g.to_markdown(index=False) if not g.empty else "（无足够样本的行业）")
        for _, r in g.iterrows():
            pivot.setdefault(r["行业"], {})[label] = r["平均收益%"]

    # 汇总矩阵：行业 × 策略 平均收益%
    rep.append("\n## 汇总矩阵：行业 × 策略（平均收益%，空=样本不足）\n")
    mat = pd.DataFrame(pivot).T
    mat = mat.reindex(columns=list(STRATS.values()))
    mat["最适策略"] = mat.idxmax(axis=1)
    mat = mat.sort_index()
    rep.append(mat.reset_index().rename(columns={"index": "行业"}).to_markdown(index=False))

    # 结论：每策略最佳3行业
    rep.append("\n## 速览：每个策略最擅长的行业(按平均收益)\n")
    for key, label in STRATS.items():
        cols = [c for c in [label] if c in mat.columns]
        if not cols:
            continue
        top = mat[label].dropna().sort_values(ascending=False).head(3)
        rep.append(f"- **{label}**：" + "、".join(f"{ind}({v:+.0f}%)" for ind, v in top.items()))

    text = "\n".join(rep)
    with open(os.path.join(OUT, "行业胜率.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
