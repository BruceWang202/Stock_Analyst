#!/bin/bash
# 双击启动股票策略仪表盘（自动打开浏览器）。关闭：本窗口按 Ctrl+C。
cd "/Users/bruce/Documents/AICode/Stock" || exit 1
echo "启动股票策略仪表盘… 浏览器将打开 http://127.0.0.1:8765"
echo "（保持此窗口开启；用完按 Ctrl+C 退出）"
exec ./.venv/bin/python app.py
