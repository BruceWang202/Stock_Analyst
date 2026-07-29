#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场状态判断（牛市 / 熊市 / 震荡）—— 基于各市场指数。

量化标准（综合年线位置+年线斜率+距一年高点）：
  牛市 bull : 收盘 > 年线(MA250)  且 年线上行  且 距一年高点 > -10%
  熊市 bear : 距一年高点 <= -20%   或  (收盘 < 年线 且 年线下行)
  震荡 range: 其余（含数据不足）

可作脚本单独运行（打印当前三市场状态 + 存 regime.md），
也可被 band_trade.py 导入（compute_regime_series 按日给出状态序列）。

⚠️ 仅技术层面的状态标注，不构成投资建议。
"""
import argparse
import os
import pandas as pd

from scanner import load_series

OUT = "/Users/bruce/Documents/Stock_output"
IDX_OF = {"US": "^GSPC", "HK": "^HSI", "SG": "^STI", "CN": "000001.SS"}
IDX_NAME = {"^GSPC": "标普500", "^IXIC": "纳斯达克", "^DJI": "道琼斯",
            "^HSI": "恒生", "^STI": "海峡时报",
            "000001.SS": "上证综指", "399001.SZ": "深证成指"}
INDEX_SYMS = ["^GSPC", "^IXIC", "^DJI", "^HSI", "^STI", "000001.SS", "399001.SZ"]

NEAR_HIGH = -0.10   # 距一年高点 > -10% 视为接近高位
BEAR_DRAW = -0.20   # 从一年高点回撤 >= 20% 视为熊市
SLOPE_LB = 20       # 年线斜率回看(交易日)


def compute_regime_series(g):
    """输入单个指数序列(含 Date, Close)，返回 DataFrame[Date, Regime]。"""
    g = g.sort_values("Date").reset_index(drop=True)
    c = g["Close"]
    ma250 = c.rolling(250).mean()
    ma250_prev = ma250.shift(SLOPE_LB)
    hi1y = c.rolling(250).max()
    above = c > ma250
    slope_up = ma250 > ma250_prev
    from_hi = c / hi1y - 1

    reg = []
    for i in range(len(g)):
        if pd.isna(ma250.iloc[i]) or pd.isna(ma250_prev.iloc[i]):
            reg.append("range")            # 数据不足，按中性震荡
        elif above.iloc[i] and slope_up.iloc[i] and from_hi.iloc[i] > NEAR_HIGH:
            reg.append("bull")
        elif (from_hi.iloc[i] <= BEAR_DRAW) or ((not above.iloc[i]) and (ma250.iloc[i] < ma250_prev.iloc[i])):
            reg.append("bear")
        else:
            reg.append("range")
    return pd.DataFrame({"Date": g["Date"], "Regime": reg})


LABEL = {"bull": "牛市 🐂", "bear": "熊市 🐻", "range": "震荡 〽️"}


def snapshot(g):
    """返回该指数最新一根的各项读数与状态。"""
    g = g.sort_values("Date").reset_index(drop=True)
    c = g["Close"]
    ma250 = c.rolling(250).mean()
    px = c.iloc[-1]
    m = ma250.iloc[-1]
    m_prev = ma250.iloc[-1 - SLOPE_LB] if len(ma250) > SLOPE_LB else float("nan")
    hi = c.iloc[-250:].max()
    reg = compute_regime_series(g)["Regime"].iloc[-1]
    return {
        "现价vs年线%": round((px / m - 1) * 100, 1) if m == m else None,
        "年线斜率%": round((m / m_prev - 1) * 100, 1) if m_prev == m_prev else None,
        "距1年高%": round((px / hi - 1) * 100, 1),
        "状态": LABEL[reg],
        "date": g["Date"].iloc[-1].date(),
    }


def _fmtv(v):
    if v != v:
        return "-"
    if v >= 1e8:
        return f"{v/1e8:.1f}亿"
    if v >= 1e4:
        return f"{v/1e4:.0f}万"
    return f"{v:.0f}"


def recent_table(g, n=7):
    """返回该指数最近 n 个交易日(从新到旧)的明细表 DataFrame。"""
    g = g.sort_values("Date").reset_index(drop=True)
    c = g["Close"]
    reg = compute_regime_series(g)["Regime"]
    out = pd.DataFrame({
        "日期": g["Date"].dt.date,
        "指数": c.round(2),
        "日升降%": (c.pct_change() * 100).round(2),
        "日振幅%": ((g["High"] - g["Low"]) / c.shift(1) * 100).round(2),
        "日成交量": g["Volume"].apply(_fmtv),
        "5日线": c.rolling(5).mean().round(2),
        "10日线": c.rolling(10).mean().round(2),
        "20日线": c.rolling(20).mean().round(2),
        "年线": c.rolling(250).mean().round(2),
        "状态": reg.map(LABEL),
    })
    return out.tail(n).iloc[::-1].reset_index(drop=True)   # 从新到旧


def breadth_overlay():
    """
    读 breadth.py 产出的 breadth.csv，把宽度预警贴到市场状态报告里。

    指数是权重股加权的，可以在多数个股已经转弱时继续新高；宽度数的是家数，
    所以两者背离时应以宽度为警。breadth.csv 不存在就提示先跑 breadth.py。
    """
    path = os.path.join(OUT, "breadth.csv")
    if not os.path.exists(path):
        return ["（未找到 breadth.csv —— 先运行 `python breadth.py` 生成宽度数据）"]
    try:
        from breadth import MKT_NAME, warnings_of
        b = pd.read_csv(path)
        b["Date"] = pd.to_datetime(b["Date"])
        lines = []
        for mkt, g in b.groupby("市场"):
            last = g.sort_values("Date").iloc[-1]
            lines.append(f"**{MKT_NAME.get(mkt, mkt)}**（{last['Date'].date()}，"
                         f"池内 {int(last['家数'])} 只）")
            for w in warnings_of(last):
                lines.append(f"- {w}")
            lines.append("")
        return lines or ["（无宽度数据）"]
    except Exception as e:
        return [f"（宽度数据读取失败：{e}）"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("-n", type=int, default=7, help="显示最近 N 个交易日")
    args = ap.parse_args()
    s = load_series(args.data, markets=("INDEX",))

    # 顶部当前状态一览
    cur = []
    for sym in INDEX_SYMS:
        g = s.get(sym)
        if g is None:
            continue
        cur.append({"指数": IDX_NAME.get(sym, sym), **snapshot(g)})
    cdf = pd.DataFrame(cur)
    asof = cdf["date"].max() if not cdf.empty else "-"
    if not cdf.empty:
        cdf = cdf.drop(columns=["date"])

    text = ["# 市场状态判断\n", f"- 数据日: {asof}　| 各指数最近 {args.n} 个交易日明细(从新到旧)",
            "- 标准: 牛=价在年线上+年线升+近高点；熊=回撤≥20%或价在年线下且年线降；余为震荡",
            "- ⚠️ 仅技术状态标注，不构成投资建议\n",
            "## 当前状态一览\n", cdf.to_markdown(index=False)]

    text += ["\n## 宽度叠加（指数之外，看涨得普不普遍）\n"] + breadth_overlay()

    for sym in INDEX_SYMS:
        g = s.get(sym)
        if g is None:
            continue
        text.append(f"\n## {IDX_NAME.get(sym, sym)} {sym} · 最近{args.n}日\n")
        text.append(recent_table(g, args.n).to_markdown(index=False))

    out = "\n".join(text)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "regime.md"), "w", encoding="utf-8") as f:
        f.write(out)
    print(out)


if __name__ == "__main__":
    main()
