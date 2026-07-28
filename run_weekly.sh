#!/bin/bash
# 每周全流程：个股信息 → 下载 → 扫描 → 全分析 → 优选 → 行业胜率
# 真身在 ~/stockapp(非保护目录)，故 launchd 可执行；输出 Stock_output 已符号链接到 ~/StockOutput。
set -uo pipefail
A="$HOME/stockapp"
DL="$HOME/stock_downloader"
LOG_DIR="$A/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/weekly_$(date +%Y%m%d).log"
PY="$A/.venv/bin/python"

echo "===== 每周全流程 $(date '+%F %T') =====" >>"$LOG"
run() { echo ">>> $1" >>"$LOG"; shift; "$@" >>"$LOG" 2>&1; echo "   退出码 $?" >>"$LOG"; }

cd "$A" || exit 1
run "① 个股信息"  "$PY" meta.py
run "② 下载"      "$DL/run.sh"
run "③ 扫描"      "$PY" scanner.py
run "④ 全分析"    "$PY" analyze.py
run "⑤ 优选合成"  "$PY" synthesize.py
run "⑥ 行业胜率"  "$PY" sector_stats.py
echo "===== 完成 $(date '+%F %T') =====" >>"$LOG"
