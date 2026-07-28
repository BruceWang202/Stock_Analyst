# 每日行情下载器（美股 / 港股 / 新加坡股）

每天定时从数据源下载美股、港股、新加坡股的**日线行情**（开高低收 + 成交量），
按**交易日期**分文件夹存成 CSV，自动去重。

## 数据源

| 市场 | 主数据源 | 备用源 | 代码格式示例 |
|------|----------|--------|--------------|
| 美股 US | Yahoo Finance | Stooq | `AAPL` |
| 港股 HK | Yahoo Finance | — | `0700.HK` |
| 新加坡 SG | Yahoo Finance | — | `D05.SI` |

都是免费公开数据源，无需 API Key。

## 位置说明（重要）

- **程序**在 `~/stock_downloader/`
- **数据**在 `~/Documents/Stock/`（这是个**符号链接**，真实数据存在 `~/StockData/`）

> 为什么这样：macOS 隐私保护（TCC）默认禁止 launchd 后台任务写入 `~/Documents`。
> 把真实数据放在非保护目录 `~/StockData`，再在 `Documents/Stock` 做符号链接指过去，
> 定时任务就能正常写入，你也照常在 `Documents/Stock` 看数据——无需授予「完全磁盘访问权限」。
> **不要把 `Documents/Stock` 这个链接删掉换成普通文件夹**，否则定时任务会因权限失败。

## 数据存放格式

```
~/Documents/Stock/           (→ ~/StockData)
├── 20260722/
│   ├── US.csv               # 当天美股：每个代码一行
│   ├── HK.csv               # 当天港股
│   └── SG.csv               # 当天新加坡
├── 20260723/
│   └── ...
└── 20260724/
    └── ...
```

每个 CSV 按 `Ticker` 去重（保留最新一次抓取），列如下：

| 列 | 含义 |
|----|------|
| `Date` | 交易日期 |
| `Open` `High` `Low` `Close` | 开 / 高 / 低 / 收（收盘为未复权原始价） |
| `AdjClose` | 复权收盘价（含分红、拆股调整，长期回测用） |
| `Volume` | 成交量 |
| `Change` | 涨跌额 = 今收 − 前收 |
| `ChangePct` | 涨跌幅% = (今收/前收 − 1) × 100 |
| `Amplitude` | 振幅% = (最高 − 最低) / 前收 × 100 |
| `Turnover` | 换手率% = 成交量 / 股本 × 100 |
| `DivYield` | 股息率% = 每股年股息 / 当日收盘 × 100 |
| `ForwardPE` | 动态市盈率 = 当日收盘 / 预期每股收益 |
| `PB` | 市净率 = 当日收盘 / 每股净资产 |
| `Ticker` `Market` `Source` | 代码 / 市场 / 数据源 |

> 说明：
> - 涨跌幅/振幅基于**前一交易日收盘**，程序会多抓 12 天缓冲来保证窗口首日也算得出。
> - **换手率**用 yfinance 提供的**当前股本**计算，故对久远历史是近似值，对近期准确；
>   **股指没有股本，换手率留空**。
> - **股息率 / 动态市盈率 / 市净率**来自 yfinance 的 `info` **当前快照**（数据源不提供历史每日估值）：
>   - 每天定时下载会逐日记录当天的值，长期自然积累成正确的历史序列；
>   - 程序用**当日收盘价**折算，使窗口内每天的估值对应各自当天股价（而非一律套同一快照）；
>   - **`--full` 全量历史模式下这三列留空**（旧日期套用今天的基本面会误导）；
>   - **股指、以及不分红/取不到基本面的股票，对应列留空**（如特斯拉无股息）。
> - stooq 备用源（仅美股兜底）无复权价/股本/估值：`AdjClose` 用收盘价占位，其余相关列留空。

## 目录结构（程序）

```
~/stock_downloader/
├── config.yaml          # 自选股清单 + 参数（改这个文件即可增删股票）
├── download.py          # 主程序
├── run.sh               # 调度入口（launchd 调用）
├── com.bruce.stockdownloader.plist   # macOS 定时任务配置
├── requirements.txt
├── .venv/               # 虚拟环境（已装好依赖）
└── logs/                # 运行日志（每天一个文件）
```

## 手动运行

```bash
cd ~/stock_downloader

# 下载全部市场（config.yaml 里的清单），最近几天并去重
./run.sh

# 只下载某个市场
./run.sh --market US

# 只下载指定代码
./run.sh --ticker AAPL --ticker 0700.HK

# 抓取全部历史（注意：会生成成千上万个日期文件夹，谨慎使用）
./run.sh --full
```

## 增删股票

编辑 `config.yaml`，在对应市场下增删代码即可。代码用 Yahoo Finance 规则：
- 美股：直接写代码，如 `NVDA`
- 港股：`数字.HK`，如 `9988.HK`
- 新加坡：`代码.SI`，如 `O39.SI`

## 定时任务（macOS launchd）

**已安装并运行**：每天早上 **07:00（新加坡时间）**自动下载——此时美股（前一交易日）、
港股、新加坡股都已收盘。程序有 7 天回看窗口，漏跑会自动补齐。

### 常用命令

```bash
# 立即手动触发一次（不用等到 7 点）
launchctl start com.bruce.stockdownloader

# 查看是否已加载
launchctl list | grep stockdownloader

# 修改运行时间：编辑 plist 里的 Hour/Minute 后重载
launchctl unload ~/Library/LaunchAgents/com.bruce.stockdownloader.plist
cp ~/stock_downloader/com.bruce.stockdownloader.plist ~/Library/LaunchAgents/
launchctl load   ~/Library/LaunchAgents/com.bruce.stockdownloader.plist

# 卸载定时任务
launchctl unload ~/Library/LaunchAgents/com.bruce.stockdownloader.plist
rm ~/Library/LaunchAgents/com.bruce.stockdownloader.plist
```

> 注意：launchd 需要电脑在设定时间处于开机/唤醒状态。若常合盖睡眠，
> 可在「系统设置 → 电池 → 计划」里设一个唤醒时间。漏跑当天，
> 下次运行会通过 7 天回看窗口自动补上。

## 日志

- 程序日志：`~/stock_downloader/logs/download_YYYYMMDD.log`（每天一个）
- launchd 输出：`~/stock_downloader/logs/launchd.out.log` / `launchd.err.log`
