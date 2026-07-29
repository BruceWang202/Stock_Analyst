#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场宽度指标（StockBee 式）—— 判断"涨的是不是普遍"，给牛熊状态做预警叠加。

指标定义移植自 xang1234/stock-screener 的 BreadthCalculatorService (Apache-2.0)，
仓库副本在 _ref/stock-screener；本文件改成直接吃本项目的本地日线数据，
并补上原项目没有的 %在MA50/MA200之上（T2108 类）与 52周新高-新低。

为什么有用：指数由权重股决定，可以在多数股票已经转弱时继续新高。
宽度指标数的是"家数"，所以往往比指数提前几周示警——这正是 regime.py 看不见的。

⚠️ 本项目股票池是**自选股**（US 13 / HK 58 / SG 3 …），不是全市场，
   所以这里算的严格说是**池内宽度**：反映"我关注的这批股票的内部温度"。
   HK 58 只样本尚可参考；US/SG 样本太小，读数仅作辅助，别当全市场宽度用。
   若想要真正的市场宽度，需把成分股（如标普500全体）加入 config.yaml 下载。

用法：
  python breadth.py                # 算各市场宽度，写 breadth.md / breadth.csv
  python breadth.py -n 15          # 明细显示最近 15 个交易日

输出（/Users/bruce/Documents/Stock_output/）：
  breadth.csv   每市场每日全部宽度读数
  breadth.md    最新读数 + 预警 + 最近N日明细
"""
import argparse
import os

import pandas as pd
import yaml

from scanner import CONFIG_PATH, DEFAULT_OUT, load_series

MKT_NAME = {"US": "美股", "HK": "港股", "SG": "新加坡", "CN": "A股", "KR": "韩股"}
MIN_TICKERS = 8          # 少于这么多只就不算宽度(没意义)

# 预警阈值
RATIO5_WEAK = 0.5        # 5日 4%涨/4%跌 比值 < 此值 → 短期卖压占优
RATIO5_STRONG = 2.0      # > 此值 → 短期买盘占优
MA200_WEAK = 0.40        # 在年线上方的比例 < 40% → 中期转弱
MA200_STRONG = 0.60


def wide_close(series, tickers):
    """把多只股票拼成 日期×代码 的收盘价宽表。"""
    cols = {}
    for tk in tickers:
        g = series.get(tk)
        if g is None:
            continue
        cols[tk] = g.set_index("Date")["Close"]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index()


def compute_breadth(close):
    """
    输入收盘价宽表，输出每日宽度读数 DataFrame。

    列：
      家数         当日有数据的股票数
      涨4%/跌4%    单日涨跌 ≥4% 的家数（StockBee 的核心动量计数）
      比值5日      近5日 涨4%家数合计 / 跌4%家数合计   (>1 买盘占优)
      比值10日     同上 10 日
      月涨25%      21 个交易日涨幅 ≥25% 的家数
      季涨25%      63 个交易日涨幅 ≥25% 的家数
      34日涨13%    IBD 式中期动量家数
      %在50线上    收盘 > MA50 的占比
      %在年线上    收盘 > MA250 的占比（T2108 同类，中期趋势健康度）
      新高-新低    创250日新高家数 - 创250日新低家数
    """
    if close.empty:
        return pd.DataFrame()
    valid = close.notna()
    cnt = valid.sum(axis=1)

    r1 = close.pct_change(fill_method=None)
    up4 = (r1 >= 0.04).sum(axis=1)
    dn4 = (r1 <= -0.04).sum(axis=1)

    def _sum_ratio(win):
        u = up4.rolling(win).sum()
        d = dn4.rolling(win).sum()
        return (u / d.replace(0, float("nan"))).astype(float)

    r21, r63, r34 = (close.pct_change(n, fill_method=None) for n in (21, 63, 34))
    ma50, ma250 = close.rolling(50).mean(), close.rolling(250).mean()
    above50 = (close > ma50).sum(axis=1) / ma50.notna().sum(axis=1).replace(0, float("nan"))
    above250 = (close > ma250).sum(axis=1) / ma250.notna().sum(axis=1).replace(0, float("nan"))

    hi250, lo250 = close.rolling(250).max(), close.rolling(250).min()
    nh = (close >= hi250).sum(axis=1)
    nl = (close <= lo250).sum(axis=1)

    out = pd.DataFrame({
        "家数": cnt,
        "涨4%": up4, "跌4%": dn4,
        "比值5日": _sum_ratio(5).round(2),
        "比值10日": _sum_ratio(10).round(2),
        "月涨25%": (r21 >= 0.25).sum(axis=1),
        "季涨25%": (r63 >= 0.25).sum(axis=1),
        "34日涨13%": (r34 >= 0.13).sum(axis=1),
        "%在50线上": (above50 * 100).round(0),
        "%在年线上": (above250 * 100).round(0),
        "新高-新低": nh - nl,
    })
    # 数据不全的日子(节假日错位)剔除：有效家数不足峰值的一半
    peak = out["家数"].max()
    return out[out["家数"] >= max(MIN_TICKERS, peak * 0.5)]


def warnings_of(row):
    """按最新一行读数给出预警/确认文字列表。"""
    msgs = []
    r5 = row.get("比值5日")
    a250 = row.get("%在年线上")
    a50 = row.get("%在50线上")

    if r5 == r5 and r5 is not None:
        if r5 < RATIO5_WEAK:
            msgs.append(f"⚠️ 5日比值 {r5} < {RATIO5_WEAK}：跌4%的家数明显多于涨4%，短期卖压占优")
        elif r5 > RATIO5_STRONG:
            msgs.append(f"✅ 5日比值 {r5} > {RATIO5_STRONG}：普涨，短期买盘占优")
    if a250 == a250 and a250 is not None:
        if a250 < MA200_WEAK * 100:
            msgs.append(f"⚠️ 仅 {a250:.0f}% 的股票在年线上方（<{MA200_WEAK*100:.0f}%）：中期趋势转弱")
        elif a250 > MA200_STRONG * 100:
            msgs.append(f"✅ {a250:.0f}% 的股票在年线上方：中期趋势健康")
    if a50 == a50 and a250 == a250 and a50 is not None and a50 < 30 and a250 > 50:
        msgs.append("⚠️ 短期(50线)已破但中期(年线)尚好：典型的回调初期，注意新开仓")
    if row.get("新高-新低", 0) < -3:
        msgs.append(f"⚠️ 新高-新低 = {int(row['新高-新低'])}：创新低的比创新高的多")
    return msgs or ["— 无明显偏向"]


def divergence(close, bdf, lookback=20):
    """
    指数/宽度背离检查：价格创新高但宽度没跟上 = 最值得警惕的形态。
    用池内等权指数(所有股票平均价格指数)代替指数本身，避免依赖指数数据。
    """
    if bdf.empty or len(bdf) < lookback + 1:
        return None
    eq = close.div(close.iloc[0]).mean(axis=1).reindex(bdf.index)   # 等权池指数
    now_px, prev_px = eq.iloc[-1], eq.iloc[-lookback - 1:-1].max()
    a250 = bdf["%在年线上"]
    now_b, prev_b = a250.iloc[-1], a250.iloc[-lookback - 1:-1].max()
    if now_px >= prev_px and now_b == now_b and prev_b == prev_b and now_b < prev_b - 10:
        return (f"⚠️ 背离：池内等权指数已创 {lookback} 日新高，但在年线上方的家数占比"
                f"从 {prev_b:.0f}% 降到 {now_b:.0f}% —— 上涨在变窄")
    return None


def main():
    ap = argparse.ArgumentParser(description="市场宽度指标（StockBee 式）")
    ap.add_argument("--data", default="/Users/bruce/Documents/Stock")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("-n", type=int, default=10, help="明细显示最近 N 个交易日")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    etf_set = set(cfg.get("etf_tickers", []))
    markets = [m for m in cfg.get("markets", {}) if m != "INDEX"]

    series = load_series(args.data, markets=tuple(markets))
    if not series:
        print("没有数据。")
        return

    # 按市场分组，剔除 ETF（ETF 本身是篮子，计入会重复计权）
    by_mkt = {}
    for tk, g in series.items():
        if tk in etf_set:
            continue
        by_mkt.setdefault(g["Market"].iloc[0], []).append(tk)

    md = ["# 市场宽度（池内宽度）", "",
          "- 指标：StockBee 式 4%涨跌家数 / 多周期动量家数 + %在50线&年线上方 + 新高新低",
          "- **⚠️ 本项目是自选股池不是全市场**，读数反映「我关注这批股票的内部温度」；",
          "  港股 (约58只) 尚可参考，美股/新加坡样本太小仅作辅助。",
          "- 用途：指数由权重股决定，宽度数家数，通常比指数**提前**转弱。", ""]

    all_rows = []
    summary = []
    for mkt in ["US", "HK", "SG", "CN", "KR"]:
        tks = by_mkt.get(mkt, [])
        if len(tks) < MIN_TICKERS:
            if tks:
                md.append(f"## {MKT_NAME.get(mkt, mkt)}：仅 {len(tks)} 只（<{MIN_TICKERS}），跳过\n")
            continue
        close = wide_close(series, tks)
        bdf = compute_breadth(close)
        if bdf.empty:
            continue
        bdf = bdf.copy()
        bdf.insert(0, "市场", mkt)
        all_rows.append(bdf.reset_index().rename(columns={"index": "Date"}))

        last = bdf.iloc[-1]
        summary.append({"市场": MKT_NAME.get(mkt, mkt), "日期": bdf.index[-1].date(),
                        "家数": int(last["家数"]),
                        "涨4%": int(last["涨4%"]), "跌4%": int(last["跌4%"]),
                        "比值5日": last["比值5日"], "比值10日": last["比值10日"],
                        "%在50线上": last["%在50线上"], "%在年线上": last["%在年线上"],
                        "新高-新低": int(last["新高-新低"])})

        md.append(f"## {MKT_NAME.get(mkt, mkt)}（{len(tks)} 只个股）\n")
        for w in warnings_of(last):
            md.append(f"- {w}")
        d = divergence(close, bdf)
        if d:
            md.append(f"- {d}")
        md.append("")
        tail = bdf.drop(columns=["市场"]).tail(args.n).iloc[::-1]
        tail.index = [i.date() for i in tail.index]
        md.append(tail.to_markdown())
        md.append("")

    if summary:
        md.insert(7, "## 最新读数一览\n")
        md.insert(8, pd.DataFrame(summary).to_markdown(index=False))
        md.insert(9, "")

    os.makedirs(args.out, exist_ok=True)
    if all_rows:
        pd.concat(all_rows, ignore_index=True).to_csv(
            os.path.join(args.out, "breadth.csv"), index=False)
    text = "\n".join(md)
    with open(os.path.join(args.out, "breadth.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
