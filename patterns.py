#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图表形态库 —— 头肩底 / 双底 / VCP / 三角形（含头肩顶、双顶做持仓预警）。

几何判定规则移植自 BennyThadikaran/stock-pattern (GPL-3.0)，
仓库副本在 _ref/stock-pattern，本文件按本项目的数据结构与"信号→止损→回测"
流程重写，并补上原项目没有的**历史信号回溯**（原项目只判断"当前最后一根")。

与已有策略 ①③④⑤⑥ 的区别：
  已有策略都是"事件型"(某天放量突破/缩量)，这里是"结构型"(几根摆动高低点的形状)。

工作方式（两段）：
  1) 形态成形：在某个"截止日" t，用截止 t 已确认的摆动高低点判断形状是否成立。
     摆动点有确认滞后——第 i 根是摆动高点，要等 i+barsRight 根之后才能确认，
     所以回溯历史时不会用到未来数据。
  2) 形态触发：成形后 trigger_bars 根内收盘突破颈线/前高 → 产出可回测的买点信号，
     止损位取形态失效点（头肩底=右肩低点，双底=第二个底，VCP=D点低）。

用法：
  python patterns.py                    # 回溯最近 500 根，出信号CSV + patterns.md
  python patterns.py --history 250      # 只回溯最近 250 根(更快)
  python patterns.py --no-history       # 只看"当前"形态(最快，用于盘后看盘)

输出（/Users/bruce/Documents/Stock_output/）：
  strategyP_patterns.csv   已触发的形态买点信号（可被 backtest.py 回测）
  patterns_pending.csv     已成形但未触发的形态（待突破观察名单）
  patterns.md              汇总报告

⚠️ 仅形态的技术识别与历史统计，不构成任何投资建议。
"""
import argparse
import os
from datetime import timedelta

import pandas as pd
import yaml

from scanner import (CONFIG_PATH, DEFAULT_OUT, STOCK_MARKETS, industry_map,
                     kind_of, load_series, name_map)

# ---- 可调参数 ----
PARAMS = {
    "bars_left": 6,        # 摆动点左侧比较根数
    "bars_right": 6,       # 摆动点右侧比较根数(= 确认滞后根数)
    "trigger_bars": 20,    # 成形后多少根内突破算触发
    "warn_bars": 15,       # 头肩顶/双顶只报最近多少根内成形的(否则全是历史陈迹)
    "min_bars": 120,       # 至少多少根数据才参与检测
    "atr_win": 15,         # ATR 窗口(双底/双顶用)
}


# ----------------------------------------------------------------------
# 摆动高低点（枢轴）
# ----------------------------------------------------------------------
def pivots_of(df, left=None, right=None):
    """
    返回枢轴点列表，每项 dict：
      pos    该枢轴在 df 中的行号
      kind   'H' 摆动高点 / 'L' 摆动低点
      price  高点取 High，低点取 Low
      vol    当日成交量
      confirm 确认行号(pos+right)，回溯历史时只能用 confirm <= t 的枢轴
    """
    left = PARAMS["bars_left"] if left is None else left
    right = PARAMS["bars_right"] if right is None else right
    hi, lo = df["High"].values, df["Low"].values
    vol = df["Volume"].values
    out = []
    for i in range(left, len(df) - right):
        seg_hi = hi[i - left:i + right + 1]
        seg_lo = lo[i - left:i + right + 1]
        if hi[i] == seg_hi.max():
            out.append({"pos": i, "kind": "H", "price": float(hi[i]),
                        "vol": float(vol[i]), "confirm": i + right})
        if lo[i] == seg_lo.min():
            out.append({"pos": i, "kind": "L", "price": float(lo[i]),
                        "vol": float(vol[i]), "confirm": i + right})
    return out


def alternating(pivs):
    """把枢轴序列压成高低交替：连续同类型只保留更极端的那个。"""
    out = []
    for p in pivs:
        if out and out[-1]["kind"] == p["kind"]:
            better = (p["price"] > out[-1]["price"]) if p["kind"] == "H" else (p["price"] < out[-1]["price"])
            if better:
                out[-1] = p
            continue
        out.append(p)
    return out


def atr_series(df, win=None):
    win = PARAMS["atr_win"] if win is None else win
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(win).mean()


def _bar_len(df, i, j):
    """i..j 区间的中位K线长度(高-低)，作为"多远算同一水平"的尺子。"""
    if j < i:
        i, j = j, i
    seg = df.iloc[i:j + 1]
    v = float((seg["High"] - seg["Low"]).median())
    return v if v == v and v > 0 else float("nan")


# ----------------------------------------------------------------------
# 几何判定（移植自 stock-pattern 的 is_* 系列）
# ----------------------------------------------------------------------
def is_reverse_hns(a, b, c, d, e, f, bar):
    """头肩底：C 头最低，B/D 颈线同高，A/E 两肩，F(现价)仍在颈线下。"""
    return (c < min(a, e)
            and min(b, d) > max(a, e)
            and f > e
            and abs(b - d) < bar
            and abs(c - e) > round(bar * 0.6, 4))


def is_hns(a, b, c, d, e, f, bar):
    """头肩顶(持仓预警)。"""
    return (c > max(a, e)
            and max(b, d) < min(a, e)
            and f < e
            and abs(b - d) < bar
            and abs(c - e) > round(bar * 0.6, 4))


def is_double_bottom(a, b, c, d, a_vol, c_vol, bar, atr):
    """双底：A/C 两底等高、C 底缩量、B 为中间反弹高，D(现价)在 B 下方。"""
    return (b - c < atr * 4
            and abs(a - c) <= bar * 0.5
            and c_vol < a_vol
            and b > max(a, c)
            and b > d > c)


def is_double_top(a, b, c, d, a_vol, c_vol, bar, atr):
    """双顶(持仓预警)。"""
    return (c - b < atr * 4
            and abs(a - c) <= bar * 0.5
            and c_vol < a_vol
            and b < min(a, c)
            and b < d < c)


def is_bullish_vcp(a, b, c, d, e, bar):
    """看涨 VCP：A/C 顶部同高，B 为最低点、D 为次低(回撤收缩)，E(现价)在 C 下方。"""
    if c > a and abs(a - c) >= bar * 0.5:
        return False
    return (abs(a - c) <= bar
            and abs(b - d) >= bar * 0.8
            and b < min(a, c, d, e)
            and d < min(a, c, e)
            and e < c)


def is_triangle(a, b, c, d, e, f, bar):
    """三角形：a/c/e 为高点，b/d/f 为低点。返回 '上升'/'下降'/'对称' 或 None。"""
    ac_flat = abs(a - c) <= bar
    ce_flat = abs(c - e) <= bar
    if ac_flat and ce_flat and b < d < f < e:
        return "上升"
    if abs(b - d) <= bar and a > c > e > f and f >= d:
        return "下降"
    if a > c > e and b < d < f and e > f:
        return "对称"
    return None


# ----------------------------------------------------------------------
# 形态识别：给定截止行号 t，用 t 之前已确认的枢轴判断形态
# 每个检测器返回 None 或 dict(pattern, form_pos, trigger, stop, points)
#   trigger 突破触发价（收盘价越过即视为形态启动）
#   stop    形态失效价（回测止损位）
# ----------------------------------------------------------------------
def det_reverse_hns(df, pivs, t):
    """头肩底 HNSU。取最后 5 个交替枢轴 A(低)B(高)C(低)D(高)E(低)。"""
    seq = [p for p in pivs if p["confirm"] <= t]
    seq = alternating(seq)
    if len(seq) < 5:
        return None
    a, b, c, d, e = seq[-5:]
    if [p["kind"] for p in (a, b, c, d, e)] != ["L", "H", "L", "H", "L"]:
        return None
    f = float(df["Close"].iloc[t])
    bar = _bar_len(df, b["pos"], d["pos"])
    if bar != bar:
        return None
    if not is_reverse_hns(a["price"], b["price"], c["price"], d["price"], e["price"], f, bar):
        return None
    neck = min(b["price"], d["price"])
    # 颈线在成形后尚未被显著突破(否则形态已过期)
    if float(df["High"].iloc[e["pos"]:t + 1].max()) > neck + bar:
        return None
    return {"pattern": "头肩底", "code": "HNSU", "form_pos": t,
            "trigger": neck, "stop": e["price"],
            "points": f"头{c['price']:.2f} 颈线{neck:.2f} 右肩{e['price']:.2f}"}


def det_double_bottom(df, pivs, t):
    """双底 DBOT。A(低) B(高) C(低)，现价 D 在 B 下方。"""
    seq = alternating([p for p in pivs if p["confirm"] <= t])
    if len(seq) < 3:
        return None
    a, b, c = seq[-3:]
    if [p["kind"] for p in (a, b, c)] != ["L", "H", "L"]:
        return None
    d = float(df["Close"].iloc[t])
    bar = _bar_len(df, a["pos"], c["pos"])
    atr = atr_series(df).iloc[c["pos"]]
    if bar != bar or atr != atr:
        return None
    if not is_double_bottom(a["price"], b["price"], c["price"], d,
                            a["vol"], c["vol"], bar, float(atr)):
        return None
    if float(df["Close"].iloc[c["pos"]:t + 1].max()) > b["price"]:
        return None                       # 颈线已破，形态过期
    return {"pattern": "双底", "code": "DBOT", "form_pos": t,
            "trigger": b["price"], "stop": c["price"],
            "points": f"底1 {a['price']:.2f} / 底2 {c['price']:.2f} 颈线{b['price']:.2f}"}


def det_bullish_vcp(df, pivs, t):
    """看涨 VCP。A(高) B(低) C(高) D(低)，现价 E 在 C 下方。"""
    seq = alternating([p for p in pivs if p["confirm"] <= t])
    if len(seq) < 4:
        return None
    a, b, c, d = seq[-4:]
    if [p["kind"] for p in (a, b, c, d)] != ["H", "L", "H", "L"]:
        return None
    e = float(df["Close"].iloc[t])
    bar = _bar_len(df, a["pos"], c["pos"])
    if bar != bar:
        return None
    if not is_bullish_vcp(a["price"], b["price"], c["price"], d["price"], e, bar):
        return None
    if float(df["Close"].iloc[c["pos"]:t + 1].max()) > c["price"]:
        return None
    return {"pattern": "VCP收缩", "code": "VCPU", "form_pos": t,
            "trigger": c["price"], "stop": d["price"],
            "points": f"高{c['price']:.2f} 深回撤{b['price']:.2f}→浅回撤{d['price']:.2f}"}


def det_triangle(df, pivs, t):
    """三角形整理 TRNG。取最后 6 个交替枢轴，高低顺序为 H,L,H,L,H,L。"""
    seq = alternating([p for p in pivs if p["confirm"] <= t])
    if len(seq) < 6:
        return None
    six = seq[-6:]
    if [p["kind"] for p in six] != ["H", "L", "H", "L", "H", "L"]:
        return None
    a, b, c, d, e, f = [p["price"] for p in six]
    bar = _bar_len(df, six[0]["pos"], six[-1]["pos"])
    if bar != bar:
        return None
    kind = is_triangle(a, b, c, d, e, f, bar)
    if kind is None:
        return None
    upper = max(a, c, e)
    close = float(df["Close"].iloc[t])
    if close > upper:
        return None                       # 已突破，不再算"成形中"
    return {"pattern": f"{kind}三角", "code": "TRNG", "form_pos": t,
            "trigger": upper, "stop": min(b, d, f),
            "points": f"上边{upper:.2f} 下边{min(b, d, f):.2f}"}


def det_hns(df, pivs, t):
    """头肩顶（只做持仓预警，无买点）。"""
    seq = alternating([p for p in pivs if p["confirm"] <= t])
    if len(seq) < 5:
        return None
    a, b, c, d, e = seq[-5:]
    if [p["kind"] for p in (a, b, c, d, e)] != ["H", "L", "H", "L", "H"]:
        return None
    f = float(df["Close"].iloc[t])
    bar = _bar_len(df, b["pos"], d["pos"])
    if bar != bar:
        return None
    if not is_hns(a["price"], b["price"], c["price"], d["price"], e["price"], f, bar):
        return None
    return {"pattern": "头肩顶⚠️", "code": "HNSD", "form_pos": t, "bearish": True,
            "trigger": max(b["price"], d["price"]), "stop": None,
            "points": f"头{c['price']:.2f} 颈线{min(b['price'], d['price']):.2f}"}


def det_double_top(df, pivs, t):
    """双顶（持仓预警）。"""
    seq = alternating([p for p in pivs if p["confirm"] <= t])
    if len(seq) < 3:
        return None
    a, b, c = seq[-3:]
    if [p["kind"] for p in (a, b, c)] != ["H", "L", "H"]:
        return None
    d = float(df["Close"].iloc[t])
    bar = _bar_len(df, a["pos"], c["pos"])
    atr = atr_series(df).iloc[c["pos"]]
    if bar != bar or atr != atr:
        return None
    if not is_double_top(a["price"], b["price"], c["price"], d,
                         a["vol"], c["vol"], bar, float(atr)):
        return None
    return {"pattern": "双顶⚠️", "code": "DTOP", "form_pos": t, "bearish": True,
            "trigger": b["price"], "stop": None,
            "points": f"顶1 {a['price']:.2f} / 顶2 {c['price']:.2f} 颈线{b['price']:.2f}"}


DETECTORS = [det_reverse_hns, det_double_bottom, det_bullish_vcp,
             det_triangle, det_hns, det_double_top]


# ----------------------------------------------------------------------
# 扫描一只股票
# ----------------------------------------------------------------------
def scan_one(df, history=500):
    """
    返回 (signals, pending)：
      signals 已触发的买点  [{Pattern, FormDate, SignalDate, Trigger, StopLevel, BuyClose, ...}]
      pending 截止最后一根仍在"成形未触发"状态的形态
    """
    n = len(df)
    if n < PARAMS["min_bars"]:
        return [], []
    pivs = pivots_of(df)
    if not pivs:
        return [], []

    start = max(PARAMS["min_bars"], n - history) if history else n - 1
    tb = PARAMS["trigger_bars"]
    signals, pending, seen = [], [], set()

    for t in range(start, n):
        for det in DETECTORS:
            try:
                r = det(df, pivs, t)
            except Exception:
                continue
            if not r:
                continue
            # 同一形态在连续多根上会重复成立，按(形态,触发价)去重只留最早成形那根
            key = (r["code"], round(r["trigger"], 3))
            if key in seen:
                continue
            seen.add(key)
            form_date = df["Date"].iloc[t]

            if r.get("bearish"):
                # 只报最近 warn_bars 根内成形的顶部形态；更早的已是历史陈迹
                if t >= n - PARAMS["warn_bars"]:
                    pending.append({"Pattern": r["pattern"], "FormDate": form_date,
                                    "Trigger": round(r["trigger"], 4),
                                    "StopLevel": None, "状态": "预警",
                                    "关键位": r["points"], "_pos": t})
                continue

            # 向后找 tb 根内首次收盘突破 trigger
            hit = None
            for j in range(t + 1, min(t + 1 + tb, n)):
                if float(df["Close"].iloc[j]) > r["trigger"]:
                    hit = j
                    break
            if hit is not None:
                signals.append({
                    "Pattern": r["pattern"], "FormDate": form_date,
                    "SignalDate": df["Date"].iloc[hit],
                    "Trigger": round(r["trigger"], 4),
                    "StopLevel": round(r["stop"], 4) if r["stop"] is not None else None,
                    "BuyClose": round(float(df["Close"].iloc[hit]), 4),
                    "关键位": r["points"],
                })
            elif t + tb >= n - 1:      # 还在触发窗口内 → 待突破观察
                pending.append({
                    "Pattern": r["pattern"], "FormDate": form_date,
                    "Trigger": round(r["trigger"], 4),
                    "StopLevel": round(r["stop"], 4) if r["stop"] is not None else None,
                    "状态": "待突破",
                    "距触发%": round((r["trigger"] / float(df["Close"].iloc[-1]) - 1) * 100, 1),
                    "关键位": r["points"], "_pos": t,
                })
    return signals, pending


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="图表形态库（头肩底/双底/VCP/三角形）")
    ap.add_argument("--data", help="数据目录，默认取 config.yaml 的 output_dir")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--history", type=int, default=500,
                    help="回溯最近 N 根K线生成历史信号，默认 500；0=只看当前")
    ap.add_argument("--no-history", action="store_true", help="等价于 --history 0")
    ap.add_argument("--recent", type=int, default=90, help="报告中展示最近 N 自然日的信号")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    data_dir = args.data or cfg.get("output_dir", "data")
    etf_set = set(cfg.get("etf_tickers", []))
    history = 0 if args.no_history else args.history
    os.makedirs(args.out, exist_ok=True)

    print(f"读取数据: {data_dir}")
    series = load_series(data_dir, markets=STOCK_MARKETS)
    if not series:
        print("没有数据可扫描。")
        return
    print(f"载入 {len(series)} 只标的，回溯 {history or 1} 根 …")

    nmap, imap = name_map(), industry_map()
    sig_rows, pend_rows = [], []
    for tk, df in series.items():
        s, p = scan_one(df, history)
        base = {"Ticker": tk, "名称": nmap.get(tk, ""), "行业": imap.get(tk, ""),
                "Market": df["Market"].iloc[0], "类型": kind_of(tk, etf_set)}
        for r in s:
            sig_rows.append({**base, **r})
        for r in p:
            r.pop("_pos", None)
            pend_rows.append({**base, **r})

    sig = pd.DataFrame(sig_rows)
    pend = pd.DataFrame(pend_rows)
    if not sig.empty:
        # 同一标的同一天同一触发价被多个形态命中 → 合并一行(形态互相印证，如"双底+头肩底")
        sig["_trig"] = sig["Trigger"].round(2)
        keys = ["Ticker", "SignalDate", "_trig"]
        how = {c: "first" for c in sig.columns if c not in keys + ["Pattern"]}
        how["Pattern"] = lambda s: "+".join(dict.fromkeys(s))
        sig = (sig.sort_values(keys + ["Pattern"])
                  .groupby(keys, as_index=False).agg(how)
                  .drop(columns=["_trig"])
                  .sort_values(["SignalDate", "Ticker"]).reset_index(drop=True))
    if not pend.empty:
        pend = pend.sort_values(["状态", "Pattern", "Ticker"]).reset_index(drop=True)

    sig.to_csv(os.path.join(args.out, "strategyP_patterns.csv"), index=False)
    pend.to_csv(os.path.join(args.out, "patterns_pending.csv"), index=False)

    data_max = max(df["Date"].max() for df in series.values())
    cut = data_max - timedelta(days=args.recent)

    md = ["# 图表形态扫描", "",
          f"- 数据最新日: {data_max.date()}　标的 {len(series)} 只　回溯 {history or 1} 根",
          "- 形态：头肩底 / 双底 / VCP收缩 / 三角形（买点）；头肩顶 / 双顶（持仓预警）",
          "- 几何规则移植自 BennyThadikaran/stock-pattern，摆动点带确认滞后故不含未来数据",
          "- ⚠️ 仅形态技术识别，不构成投资建议", ""]

    md += ["## 一、当前观察名单（已成形，等突破）", ""]
    watch = pend[pend["状态"] == "待突破"] if not pend.empty else pend
    if watch.empty:
        md.append("无。")
    else:
        cols = ["Ticker", "名称", "行业", "Pattern", "FormDate", "Trigger", "StopLevel", "距触发%", "关键位"]
        w = watch[[c for c in cols if c in watch.columns]].copy()
        w["FormDate"] = pd.to_datetime(w["FormDate"]).dt.date
        md.append(w.to_markdown(index=False))
    md.append("")

    md += ["## 二、持仓预警（头肩顶 / 双顶）", ""]
    warn = pend[pend["状态"] == "预警"] if not pend.empty else pend
    if warn.empty:
        md.append("无。")
    else:
        cols = ["Ticker", "名称", "行业", "Pattern", "FormDate", "Trigger", "关键位"]
        w = warn[[c for c in cols if c in warn.columns]].copy()
        w["FormDate"] = pd.to_datetime(w["FormDate"]).dt.date
        md.append(w.to_markdown(index=False))
    md.append("")

    md += [f"## 三、最近 {args.recent} 天已触发买点", ""]
    if sig.empty:
        md.append("无。")
    else:
        recent = sig[sig["SignalDate"] >= cut]
        md.append(f"- 历史触发总数 **{len(sig)}**，最近 {args.recent} 天 **{len(recent)}**")
        md.append("")
        by = sig.groupby("Pattern").size().rename("次数").reset_index()
        md.append("各形态历史触发次数：")
        md.append("")
        md.append(by.to_markdown(index=False))
        md.append("")
        if not recent.empty:
            cols = ["Ticker", "名称", "行业", "Pattern", "FormDate", "SignalDate",
                    "Trigger", "StopLevel", "BuyClose", "关键位"]
            r = recent[[c for c in cols if c in recent.columns]].copy()
            r["FormDate"] = pd.to_datetime(r["FormDate"]).dt.date
            r["SignalDate"] = pd.to_datetime(r["SignalDate"]).dt.date
            md.append(r.to_markdown(index=False))

    path = os.path.join(args.out, "patterns.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"触发买点 {len(sig)} 个 -> strategyP_patterns.csv")
    print(f"观察/预警 {len(pend)} 个 -> patterns_pending.csv")
    print(f"报告已写: {path}")


if __name__ == "__main__":
    main()
