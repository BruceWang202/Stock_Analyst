# Stock Analyst · 行情下载 + 形态策略分析 + 仪表盘

一套自用的股票技术分析系统：每天自动下载 **美股 / 港股 / 新加坡 / A股 / 韩股 / 指数** 行情，
扫描 6 种形态策略、带止损回测、按行业与市场状态分析，并提供一个**本地 Web 仪表盘**执行与查看，含**模拟盘**。

> ⚠️ 本系统仅做行情数据处理与形态的**技术识别 / 历史回测统计**，**不构成任何投资建议**。

## 功能

- **数据下载**（`downloader/`）：yfinance 免密数据源，按交易日期分文件夹存 CSV，自动去重、失败重试、剔除节假日/盘中未收盘的坏数据。
- **形态策略扫描**（`scanner.py`）：
  - ① 平台突破缩量回踩　④ MACD 底背离　⑤ 放量突破前高回踩
  - ⑥ 缩量(地量)+十日线向上 → 天量(按股自适应分位)卖　（③ 年线放量已弃用）
- **带止损回测 / 全历史分析**（`backtest.py` / `analyze.py`）：每策略按形态失效点止损，按年份/个股/行业拆解，逐笔明细导出。
- **优选合成**（`synthesize.py`）：每股在牛/震荡/熊下的优选策略；每策略精选池；当前信号选股（名称+证据）。
- **行业胜率**（`sector_stats.py`）：哪些板块适合哪个形态。
- **市场状态**（`regime.py`）：各指数牛/熊/震荡判定 + 最近 N 日明细（升降/振幅/量/MA5/10/20/年线）。
- **高抛低吸波段**（`band_trade.py`）：面向高股息股，按市场状态切换打法。
- **成交量参考**（`vol_stats.py`）：每股 360/120/90/30 日 最低/均/最高量 + 地量/天量提示。
- **个股信息**（`meta.py`）：行业、总/流通股本、总/流通市值。
- **Web 仪表盘**（`app.py`）：点按钮执行、看渲染报告与数据表、模拟盘、天量分位滑块、一键全跑。

## 目录结构

```
.
├── app.py                  # 本地 Web 仪表盘
├── scanner.py              # 形态扫描(6策略) + 参数 PARAMS
├── backtest.py analyze.py  # 带止损回测 / 全历史分析
├── synthesize.py sector_stats.py  # 优选合成 / 行业胜率
├── regime.py band_trade.py # 市场状态 / 高抛低吸
├── vol_stats.py meta.py    # 成交量参考 / 个股信息
├── portfolio.py            # 模拟盘数据层
├── run_weekly.sh           # 每周全流程(launchd 调用)
├── com.bruce.stockweekly.plist
├── 启动仪表盘.command       # 双击启动仪表盘
├── downloader/             # 下载程序(每日 launchd)：download.py / run.sh / config.yaml / plist
├── docs/策略.md            # 策略定义
└── requirements.txt
```

## 环境与运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 首次下载数据(下载程序在 downloader/)
downloader/.venv/bin/python downloader/download.py --start 2023-01-01   # 或 ./downloader/run.sh

# 启动仪表盘
.venv/bin/python app.py            # 浏览器开 http://127.0.0.1:8765
```

仪表盘里首次建议顺序：① 下载 → ② 扫描 → ③ 分析 → ⑥ 优选，或直接「🚀 一键全跑」。

## 数据与路径

- **行情数据、分析输出、日志、`.venv`、模拟持仓等不入库**（见 `.gitignore`）；代码里的路径为作者本机绝对路径，他人使用需按需修改。
- **自选清单** 在 `downloader/config.yaml`（增删股票/行业、排除名单、参数）。
- macOS 上为绕开 Documents 目录的隐私限制(TCC)，数据放非保护目录并用符号链接；下载程序与分析程序分处两目录（详见 `docs/` 与代码注释）。

## 定时任务（macOS launchd）

- 每日 07:00 增量下载：`downloader/com.bruce.stockdownloader.plist`
- 每周一 08:30 全流程刷新：`com.bruce.stockweekly.plist`

## 免责声明

本项目为个人技术研究用途，所有输出均为历史数据的技术统计，**不构成任何投资建议**；据此交易风险自负。
