#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日行情下载器 —— 美股 / 港股 / 新加坡股

数据源：
  主源 : Yahoo Finance (yfinance)  —— 统一覆盖三个市场
  备源 : Stooq                      —— 仅美股，在主源失败时兜底

用法：
  python download.py                # 用 config.yaml 里的清单下载所有市场
  python download.py --market US    # 只下载指定市场 (US / HK / SG)
  python download.py --ticker AAPL  # 只下载单个代码 (可重复)
  python download.py --full         # 抓取全部历史 (首次建库时用)

输出：
  data/<市场>/<代码>.csv    每个代码一个 CSV，按日期追加、自动去重
  logs/download_YYYYMMDD.log 运行日志
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import yaml
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

# 统一的列，保存到 CSV 里的字段
# 保存到 CSV 的字段。前段是行情原始值，中段是衍生指标，末段是标识信息。
COLUMNS = [
    "Date", "Open", "High", "Low", "Close", "AdjClose", "Volume",
    "Change",      # 涨跌额  = 今收 - 前收
    "ChangePct",   # 涨跌幅% = (今收/前收 - 1) * 100
    "Amplitude",   # 振幅%   = (最高 - 最低) / 前收 * 100
    "Turnover",    # 换手率% = 成交量 / 股本 * 100（用当前股本近似，指数留空）
    "DivYield",    # 股息率% = 每股年股息 / 当日收盘 * 100（快照，指数/全量留空）
    "ForwardPE",   # 动态市盈率 = 当日收盘 / 预期每股收益（快照，指数/全量留空）
    "PB",          # 市净率    = 当日收盘 / 每股净资产（快照，指数/全量留空）
    "Ticker", "Market", "Source",
]

# 非全量模式下，向窗口起点之前多抓的缓冲天数，用来给窗口首日计算“前一日收盘”，
# 避免每天窗口边缘的行因缺少前收而把已存好的衍生指标覆盖成空。
BUFFER_DAYS = 12


# ----------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------
def setup_logging():
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"download_{datetime.now():%Y%m%d}.log")

    logger = logging.getLogger("downloader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# ----------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------
def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ----------------------------------------------------------------------
# 数据抓取
# ----------------------------------------------------------------------
def _finalize(out, source, shares, fund, start, full, fill_valuation):
    """给原始 OHLCV 计算衍生指标，并（非全量时）裁掉缓冲期。
    out 需含列: Date, Open, High, Low, Close, AdjClose, Volume。
    fund: 估值快照 dict（forwardPE / priceToBook / dividendRate），可为 None。
    fill_valuation: 是否写入估值三列（仅日常增量为 True；全量/历史回补为 False，留空）。"""
    if out is None or out.empty:
        return pd.DataFrame(columns=COLUMNS)

    out = out.sort_values("Date").reset_index(drop=True)
    prev_close = out["Close"].shift(1)          # 前一交易日收盘
    out["Change"] = (out["Close"] - prev_close).round(4)
    out["ChangePct"] = ((out["Close"] / prev_close - 1) * 100).round(4)
    out["Amplitude"] = ((out["High"] - out["Low"]) / prev_close * 100).round(4)
    if shares:
        out["Turnover"] = (out["Volume"] / float(shares) * 100).round(6)
    else:
        out["Turnover"] = pd.NA                  # 指数或取不到股本时留空

    # --- 估值指标（市盈率/市净率/股息率）---
    # info 只给“当前快照”，无历史序列。按当日收盘价折算到每一天：
    #   基本面(每股收益/每股净资产/每股股息)短期近似恒定，故用最新收盘作参考价反推。
    # 全量历史模式下基本面早已变化，写入会误导 -> 全部留空。
    fund = fund or {}
    out["DivYield"] = pd.NA
    out["ForwardPE"] = pd.NA
    out["PB"] = pd.NA
    ref = out["Close"].iloc[-1] if len(out) else None   # 参考价 = 最新收盘
    if fill_valuation and ref:
        fwd_pe = fund.get("forwardPE")
        pb = fund.get("priceToBook")
        div_rate = fund.get("dividendRate")
        if fwd_pe:
            out["ForwardPE"] = (float(fwd_pe) * out["Close"] / ref).round(4)
        if pb:
            out["PB"] = (float(pb) * out["Close"] / ref).round(4)
        if div_rate:
            out["DivYield"] = (float(div_rate) / out["Close"] * 100).round(4)

    out["Source"] = source

    if not full:
        out = out[out["Date"] >= start.strftime("%Y-%m-%d")]
    return out.reset_index(drop=True)


def fetch_yfinance(ticker, start, end, full=False, fill_valuation=True):
    """用 yfinance 抓日线并计算衍生指标，返回标准化后的 DataFrame（可能为空）。"""
    tk = yf.Ticker(ticker)
    if full:
        df = tk.history(period="max", auto_adjust=False)
    else:
        # 向前多抓 BUFFER_DAYS 天作缓冲；end 加一天保证包含当天
        fetch_start = start - timedelta(days=BUFFER_DAYS)
        df = tk.history(start=fetch_start, end=end + timedelta(days=1), auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)

    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")

    out = pd.DataFrame()
    out["Date"] = df["Date"]
    for col in ["Open", "High", "Low", "Close"]:
        out[col] = df[col] if col in df.columns else pd.NA
    out["AdjClose"] = df["Adj Close"] if "Adj Close" in df.columns else df.get("Close")
    out["Volume"] = df["Volume"] if "Volume" in df.columns else pd.NA

    # 取股本（换手率用）与估值快照（市盈率/市净率/股息）。指数多为 None。
    shares = None
    fund = {}
    try:
        info = tk.info
    except Exception:
        info = {}
    if info:
        shares = info.get("sharesOutstanding")
        fund = {k: info.get(k) for k in ("forwardPE", "priceToBook", "dividendRate")}
    if not shares:
        try:
            fi = tk.fast_info
            shares = fi.get("shares") if hasattr(fi, "get") else fi["shares"]
        except Exception:
            shares = None

    return _finalize(out, "yfinance", shares, fund, start, full, fill_valuation)


def fetch_stooq(ticker, start, end, full=False, fill_valuation=True):
    """美股备用源：Stooq。ticker 形如 AAPL，会转成 aapl.us。
    stooq 无复权价与股本，AdjClose 用收盘价占位、换手率留空。"""
    symbol = ticker.lower() + ".us"
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        df = pd.read_csv(url)
    except Exception:
        return pd.DataFrame(columns=COLUMNS)
    if df is None or df.empty or "Date" not in df.columns:
        return pd.DataFrame(columns=COLUMNS)

    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    if not full:
        # 保留缓冲期用于算前收，_finalize 里再裁掉
        s = (start - timedelta(days=BUFFER_DAYS)).strftime("%Y-%m-%d")
        e = end.strftime("%Y-%m-%d")
        df = df[(df["Date"] >= s) & (df["Date"] <= e)]
    out = pd.DataFrame()
    out["Date"] = df["Date"]
    for col in ["Open", "High", "Low", "Close"]:
        out[col] = df[col] if col in df.columns else pd.NA
    out["AdjClose"] = df["Close"]
    out["Volume"] = df["Volume"] if "Volume" in df.columns else pd.NA

    return _finalize(out, "stooq", None, None, start, full, fill_valuation)


def fetch_one(ticker, market, start, end, full, fill_valuation, logger):
    """抓单个代码，带备用源逻辑。"""
    try:
        df = fetch_yfinance(ticker, start, end, full, fill_valuation)
    except Exception as e:
        logger.warning(f"  [{ticker}] yfinance 出错: {e}")
        df = pd.DataFrame(columns=COLUMNS)

    if df.empty and market == "US":
        logger.info(f"  [{ticker}] 主源无数据，尝试备用源 Stooq …")
        try:
            df = fetch_stooq(ticker, start, end, full, fill_valuation)
        except Exception as e:
            logger.warning(f"  [{ticker}] stooq 出错: {e}")

    if df.empty:
        return df

    df["Ticker"] = ticker
    df["Market"] = market
    return df[COLUMNS]


# ----------------------------------------------------------------------
# 落盘：按交易日期分文件夹
#   <output_dir>/<YYYYMMDD>/<市场>.csv
#   每个文件里每个代码一行，按代码去重（保留最新一次抓取）
# ----------------------------------------------------------------------
def save_rows(df, market, output_dir):
    """把单个代码的多行日线，按其交易日期分发到对应日期文件夹。
    返回 (本次新增行数, 涉及的日期列表)。"""
    total_new = 0
    dates_written = []
    for date_str, day_df in df.groupby("Date"):
        folder = os.path.join(output_dir, str(date_str).replace("-", ""))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{market}.csv")

        if os.path.exists(path):
            old = pd.read_csv(path)
            combined = pd.concat([old, day_df], ignore_index=True)
        else:
            combined = day_df

        before = len(combined)
        combined = combined.drop_duplicates(subset=["Ticker"], keep="last")
        combined = combined.sort_values("Ticker").reset_index(drop=True)
        combined = combined.reindex(columns=COLUMNS)  # 统一列顺序/补齐新列
        total_new += len(combined) - (before - len(day_df))

        combined.to_csv(path, index=False)
        dates_written.append(str(date_str))
    return total_new, sorted(dates_written)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="每日行情下载 (美股/港股/新加坡)")
    parser.add_argument("--market", action="append",
                        choices=["US", "HK", "SG", "CN", "KR", "INDEX"],
                        help="只下载指定市场，可重复。默认全部。")
    parser.add_argument("--ticker", action="append",
                        help="只下载指定代码，可重复。会覆盖 --market。")
    parser.add_argument("--full", action="store_true",
                        help="抓取全部历史（首次建库用）。")
    parser.add_argument("--start", metavar="YYYY-MM-DD",
                        help="历史回补起始日期（含）。给了它即为历史回补模式：估值三列留空。")
    parser.add_argument("--end", metavar="YYYY-MM-DD",
                        help="截止日期（含），默认今天。")
    args = parser.parse_args()

    logger = setup_logging()
    cfg = load_config()

    out = cfg.get("output_dir", "data")
    output_dir = out if os.path.isabs(out) else os.path.join(BASE_DIR, out)
    lookback = int(cfg.get("lookback_days", 7))
    delay = float(cfg.get("request_delay", 0.8))
    retries = int(cfg.get("retries", 2))            # 无数据时重试次数(应对数据源瞬时抽风)
    retry_delay = float(cfg.get("retry_delay", 3))  # 每次重试前等待秒数

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else datetime.now().date()
    backfill = bool(args.start)          # 指定了 --start 即为历史回补
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = end - timedelta(days=lookback)
    # 仅日常增量模式写入估值（当前快照）；全量 / 历史回补留空，避免旧日期套用今日基本面
    fill_valuation = (not args.full) and (not backfill)

    # 组装 (ticker, market) 任务列表
    tasks = []
    if args.ticker:
        # 按代码后缀推断市场
        for t in args.ticker:
            tu = t.upper()
            if t.startswith("^"):
                m = "INDEX"
            elif tu.endswith(".HK"):
                m = "HK"
            elif tu.endswith(".SI"):
                m = "SG"
            elif tu.endswith(".SS") or tu.endswith(".SZ"):
                m = "CN"
            elif tu.endswith(".KS"):
                m = "KR"
            else:
                m = "US"
            tasks.append((t, m))
    else:
        markets = args.market or list(cfg["markets"].keys())
        for m in markets:
            for t in cfg["markets"].get(m, []):
                tasks.append((t, m))

    if args.full:
        mode = "全部历史"
    elif backfill:
        mode = f"历史回补 {start}~{end} (估值列留空)"
    else:
        mode = f"最近 {lookback} 天"
    logger.info("=" * 60)
    logger.info(f"开始下载 | 模式: {mode} | 共 {len(tasks)} 个代码 | 窗口: {start} ~ {end}")
    logger.info("=" * 60)

    ok, fail = 0, 0
    for i, (ticker, market) in enumerate(tasks, 1):
        logger.info(f"[{i}/{len(tasks)}] {market:<3} {ticker}")
        df = fetch_one(ticker, market, start, end, args.full, fill_valuation, logger)
        # 无数据时重试(数据源常有瞬时抽风，重试多能恢复)
        attempt = 0
        while df.empty and attempt < retries:
            attempt += 1
            time.sleep(retry_delay)
            logger.info(f"  [{ticker}] 无数据，重试 {attempt}/{retries} …")
            df = fetch_one(ticker, market, start, end, args.full, fill_valuation, logger)
        if df.empty:
            logger.warning(f"  [{ticker}] 无数据 (重试{retries}次仍失败；可能停牌/休市/代码错误)")
            fail += 1
        else:
            new_rows, dates = save_rows(df, market, output_dir)
            latest = df["Date"].max()
            span = f"{dates[0]}~{dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "-")
            logger.info(f"  [{ticker}] OK  抓到 {len(df)} 行, 新增 {new_rows} 行, "
                        f"覆盖日期 {span}, 最新 {latest} ({df['Source'].iloc[0]})")
            ok += 1
        time.sleep(delay)

    logger.info("-" * 60)
    logger.info(f"完成 | 成功 {ok} | 无数据/失败 {fail}")
    logger.info("=" * 60)
    # 有失败时返回非 0，方便调度系统感知
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
