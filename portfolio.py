#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟盘数据层：持仓存 portfolio.json，用最新收盘价算盈亏。"""
import glob
import json
import os

import pandas as pd

from scanner import industry_map, name_map

BASE = os.path.dirname(os.path.abspath(__file__))
PORT_FILE = os.path.join(BASE, "portfolio.json")
DATA = "/Users/bruce/Documents/Stock"     # 符号链接 -> ~/StockData


def load():
    if os.path.exists(PORT_FILE):
        try:
            return json.load(open(PORT_FILE, encoding="utf-8"))
        except Exception:
            return []
    return []


def save(pos):
    json.dump(pos, open(PORT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def add(ticker, shares, cost, note=""):
    pos = load()
    pos.append({"ticker": ticker.strip(), "shares": float(shares),
                "cost": float(cost), "note": note})
    save(pos)


def delete(idx):
    pos = load()
    if 0 <= idx < len(pos):
        pos.pop(idx)
        save(pos)


def latest_prices(n=12):
    """扫最近 n 个日期文件夹，取每个代码最近一次收盘价。返回 {代码:(日期,收盘)}。"""
    if not os.path.isdir(DATA):
        return {}
    folders = sorted([d for d in os.listdir(DATA) if d.isdigit()], reverse=True)[:n]
    px = {}
    for d in folders:
        for f in glob.glob(os.path.join(DATA, d, "*.csv")):
            try:
                df = pd.read_csv(f)
            except Exception:
                continue
            for _, r in df.iterrows():
                t = r.get("Ticker")
                if t and t not in px and pd.notna(r.get("Close")):
                    px[t] = (str(r["Date"]), float(r["Close"]))
    return px


def compute():
    """返回 (每笔持仓明细列表, 汇总dict)。"""
    pos = load()
    px = latest_prices()
    imap = industry_map()
    nmap = name_map()
    rows = []
    tot_cost = tot_val = 0.0
    for i, p in enumerate(pos):
        t = p["ticker"]; sh = p["shares"]; c = p["cost"]
        pr = px.get(t)
        price = pr[1] if pr else None
        date = pr[0] if pr else "-"
        cost = sh * c
        val = sh * price if price is not None else None
        pnl = (val - cost) if val is not None else None
        pnlpct = (pnl / cost * 100) if (pnl is not None and cost) else None
        rows.append({
            "idx": i, "代码": t, "名称": nmap.get(t, ""), "行业": imap.get(t, ""),
            "股数": sh, "买入价": round(c, 4),
            "现价": round(price, 4) if price is not None else None,
            "更新日": date,
            "成本": round(cost, 2), "市值": round(val, 2) if val is not None else None,
            "盈亏": round(pnl, 2) if pnl is not None else None,
            "盈亏%": round(pnlpct, 2) if pnlpct is not None else None,
        })
        tot_cost += cost
        tot_val += val if val is not None else cost
    tot_pnl = tot_val - tot_cost
    totals = {
        "成本": round(tot_cost, 2), "市值": round(tot_val, 2),
        "盈亏": round(tot_pnl, 2),
        "盈亏%": round(tot_pnl / tot_cost * 100, 2) if tot_cost else 0,
    }
    return rows, totals
