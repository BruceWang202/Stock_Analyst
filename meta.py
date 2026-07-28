#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个股基本信息：所属行业、总股本、流通股本、总市值、流通市值。
来源 yfinance .info（当前快照）。写 Stock_output/metadata.csv。

⚠️ 数据源行业为英文分类；市值单位=亿(本币)。仅供参考，不构成投资建议。
"""
import os
import time
import pandas as pd
import yaml
import yfinance as yf

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = "/Users/bruce/Documents/Stock_output"

# 英文行业 -> 中文(常见映射，便于识别板块；未命中则保留英文)
IND_CN = {
    "Engineering & Construction": "建筑装饰",
    "Infrastructure Operations": "基础设施",
    "Banks - Regional": "银行", "Banks - Diversified": "银行",
    "Insurance - Life": "保险", "Insurance - Diversified": "保险", "Insurance - Property & Casualty": "保险",
    "Capital Markets": "证券", "Asset Management": "资管",
    "Utilities - Independent Power Producers": "电力", "Utilities - Regulated Electric": "电力",
    "Utilities - Renewable": "新能源发电",
    "Oil & Gas Integrated": "石油", "Oil & Gas E&P": "石油开采", "Aluminum": "有色-铝",
    "Airlines": "航空", "Marine Shipping": "航运", "Integrated Freight & Logistics": "物流",
    "Railroads": "铁路", "Auto Manufacturers": "汽车", "Auto Parts": "汽车零部件",
    "Specialty Industrial Machinery": "工业机械", "Farm & Heavy Construction Machinery": "工程机械",
    "Telecom Services": "电信", "Waste Management": "环保",
    "Semiconductors": "半导体", "Software - Infrastructure": "软件", "Consumer Electronics": "消费电子",
    "Internet Retail": "电商", "Internet Content & Information": "互联网", "Restaurants": "餐饮",
    "Specialty Retail": "零售", "Leisure": "文娱", "Real Estate - Development": "房地产",
    "Building Materials": "建材",
    "Electronic Components": "电子元件", "Metal Fabrication": "金属制品",
    "Drug Manufacturers - Specialty & Generic": "医药", "Packaged Foods": "食品",
    "Thermal Coal": "煤炭", "Utilities - Regulated Gas": "燃气",
    "Oil & Gas Equipment & Services": "油服", "Steel": "钢铁",
}


def main():
    from scanner import name_map
    global _NAMES
    _NAMES = name_map()
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    tickers = []
    for m, lst in cfg["markets"].items():
        if m == "INDEX":
            continue
        for t in lst:
            tickers.append((t, m))

    rows = []
    for i, (t, m) in enumerate(tickers, 1):
        try:
            info = yf.Ticker(t).info
        except Exception:
            info = {}
        ind = info.get("industry") or ""
        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        shares = info.get("sharesOutstanding")
        floatsh = info.get("floatShares")
        mktcap = info.get("marketCap")
        fmc = (floatsh * price) if (floatsh and price) else None
        rows.append({
            "Ticker": t, "名称": _NAMES.get(t, ""), "Market": m,
            "行业": IND_CN.get(ind, ind),
            "行业(原)": ind,
            "总股本(亿)": round(shares / 1e8, 2) if shares else None,
            "流通股本(亿)": round(floatsh / 1e8, 2) if floatsh else None,
            "总市值(亿)": round(mktcap / 1e8, 1) if mktcap else None,
            "流通市值(亿)": round(fmc / 1e8, 1) if fmc else None,
        })
        print(f"[{i}/{len(tickers)}] {t:10} {rows[-1]['行业']}")
        time.sleep(0.4)

    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "metadata.csv"), index=False)
    print(f"\n已写 {OUT}/metadata.csv  共 {len(df)} 只")
    print("\n建筑装饰板块：")
    print(df[df["行业"] == "建筑装饰"][["Ticker", "行业(原)"]].to_string(index=False))


if __name__ == "__main__":
    main()
