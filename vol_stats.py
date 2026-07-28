#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成交量参考表：每只股票最近 360/120/90/30 天的 最低/平均/最大 成交量，
并标注当前量能位置(当日量 / 各窗口均量、距最低/最高的位置)。
配合策略⑥(缩量买、放巨量卖)使用。输出 Stock_output/成交量参考.csv + .md。

⚠️ 仅供参考，不构成投资建议。
"""
import os
import pandas as pd
from scanner import load_series, industry_map, name_map

OUT = "/Users/bruce/Documents/Stock_output"
WINDOWS = [360, 120, 90, 30]


def fmt(v):
    return f"{v/1e8:.2f}亿" if v >= 1e8 else (f"{v/1e4:.0f}万" if v >= 1e4 else f"{v:.0f}")


def main():
    series = load_series("/Users/bruce/Documents/Stock")
    imap = industry_map()
    nmap = name_map()
    rows = []
    for tk, df in series.items():
        vol = df["Volume"].dropna()
        if vol.empty:
            continue
        # 最后一根疑似当日未收盘(量<前5日均量30%)则剔除，用最近完整交易日
        if len(vol) > 6 and vol.iloc[-1] < vol.iloc[-6:-1].mean() * 0.3:
            vol = vol.iloc[:-1]
        if vol.empty:
            continue
        cur = float(vol.iloc[-1])
        row = {"代码": tk, "名称": nmap.get(tk, ""), "行业": imap.get(tk, ""), "当日量": fmt(cur)}
        pos = min120v = None
        for w in WINDOWS:
            seg = vol.tail(w)
            mn, mx, av = float(seg.min()), float(seg.max()), float(seg.mean())
            row[f"{w}d最低"] = fmt(mn)
            row[f"{w}d均"] = fmt(av)
            row[f"{w}d最高"] = fmt(mx)
            if w == 120:
                min120v = mn
                pos = round((cur - mn) / (mx - mn) * 100) if mx > mn else 0
                row["120d位置%"] = pos
                row["量比(/120均)"] = round(cur / av, 2) if av else None
        # 信号提示(口径同策略⑥)：天量卖点 > 近最大量 > 地量买点
        sky_thr = float(vol.tail(250).quantile(0.99))        # 天量:该股近250日成交量99分位(自适应)
        if cur >= sky_thr:
            row["提示"] = "⬆天量卖点"
        elif pos is not None and pos >= 85:
            row["提示"] = "⬆近最大量"
        elif min120v and cur <= min120v * 1.15:              # 地量:当日量≤120日最低×1.15
            row["提示"] = "⬇地量买点"
        else:
            row["提示"] = ""
        rows.append(row)

    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "成交量参考.csv"), index=False)

    # markdown：精简列
    show = ["代码", "名称", "行业", "当日量", "360d最低", "120d最低", "90d最低", "30d最低",
            "120d均", "120d最高", "120d位置%", "量比(/120均)", "提示"]
    md = ["# 成交量参考（配合策略⑥：缩量买 / 近最大量卖）\n",
          "- 位置%：当前量在近120日[最低,最高]中的位置(越低越缩量)；量比：当日量/120日均量",
          "- ⬇缩量区(位置≤20) 关注买点(需配合十日线向上)；⬆近最大量(位置≥85) 关注卖点",
          "- 完整360/120/90/30窗口见 成交量参考.csv；⚠️ 仅供参考，不构成投资建议\n"]
    d2 = df[show].sort_values("120d位置%")
    md.append(d2.to_markdown(index=False))
    with open(os.path.join(OUT, "成交量参考.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"已写 成交量参考.csv / .md  共 {len(df)} 只")
    print("\n当前缩量区(位置≤20, 关注⑥买点)的股票：")
    print(df[df["提示"] == "⬇缩量区"][["代码", "行业", "120d位置%", "量比(/120均)"]].to_string(index=False))


if __name__ == "__main__":
    main()
