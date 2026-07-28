#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票策略仪表盘 —— 本地 Web 界面：点按钮执行流程、查看渲染好的报告与数据表。
运行：  ~/stock_downloader/.venv/bin/python app.py     然后浏览器开 http://127.0.0.1:8765
（一般由 StockDashboard.app 或 启动仪表盘.command 自动打开）
"""
import glob
import os
import re
import subprocess
import sys
import threading
import webbrowser

import markdown as md
import pandas as pd
from flask import Flask, jsonify, request

import portfolio

BASE = os.path.dirname(os.path.abspath(__file__))          # 分析/仪表盘目录
PY = os.path.join(BASE, ".venv", "bin", "python")
DL_DIR = os.path.expanduser("~/stock_downloader")          # 下载程序(定时任务)目录，不动
DL_PY = os.path.join(DL_DIR, ".venv", "bin", "python")
OUT = "/Users/bruce/Documents/Stock_output"
PORT = 8765

# 可执行的流程：id -> (中文名, 命令参数)
ACTIONS = {
    "download": ("① 增量下载(今日/最近)", ["download.py"]),
    "scan":     ("② 扫描形态信号", ["scanner.py"]),
    "analyze":  ("③ 全历史分析(带止损)", ["analyze.py"]),
    "regime":   ("④ 市场状态(牛/熊/震荡)", ["regime.py"]),
    "band":     ("⑤ 高抛低吸波段回测", ["band_trade.py", "-B", "5"]),
    "synth":    ("⑥ 优选合成(每股/每策略)", ["synthesize.py"]),
    "backtest": ("⑦ 带止损回测", ["backtest.py", "--max-hold", "40"]),
    "meta":     ("⑧ 刷新个股信息(行业/市值)", ["meta.py"]),
    "sector":   ("⑨ 按行业汇总策略胜率", ["sector_stats.py"]),
    "vol":      ("⑩ 成交量参考(缩量/放量)", ["vol_stats.py"]),
}
# 一键全跑的步骤顺序：(名称, 命令, 是否用下载程序目录)
PIPELINE = [
    ("⑧ 个股信息", ["meta.py"], False),
    ("① 下载", ["download.py"], True),
    ("② 扫描", ["scanner.py"], False),
    ("③ 全分析", ["analyze.py"], False),
    ("⑥ 优选合成", ["synthesize.py"], False),
    ("⑨ 行业胜率", ["sector_stats.py"], False),
    ("⑩ 成交量参考", ["vol_stats.py"], False),
]
# 报告：中文名 -> 文件
REPORTS = {
    "市场状态": "regime.md",
    "策略优选": "策略优选.md",
    "全历史分析": "analysis_all.md",
    "扫描汇总": "summary.md",
    "行业胜率": "行业胜率.md",
    "成交量参考": "成交量参考.md",
    "带止损回测": "backtest.md",
    "高抛低吸": "band_trade.md",
    "持有上限对比": "compare_hold.md",
    "最近命中验证": "recent_eval.md",
}

app = Flask(__name__)


def run_cmd(args, extra=None, cwd=None, py=None):
    cmd = [py or PY] + args + (extra or [])
    try:
        p = subprocess.run(cmd, cwd=cwd or BASE, capture_output=True, text=True, timeout=900)
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.returncode and p.stderr else "")
        out = "\n".join(l for l in out.splitlines()
                        if "NotOpenSSLWarning" not in l and "warnings.warn" not in l)
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 1, "超时(>15分钟)"


PAGE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>股票策略仪表盘</title><style>
:root{--bg:#0f1115;--panel:#1a1d24;--line:#2a2f3a;--fg:#e6e9ef;--mut:#98a2b3;--acc:#4c8bf5;--ok:#2fbf71;--warn:#e0a020}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,system-ui,"PingFang SC",sans-serif;background:var(--bg);color:var(--fg)}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;flex-wrap:wrap}
header h1{font-size:16px;margin:0}#state{display:flex;gap:8px;flex-wrap:wrap}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:3px 12px;font-size:12px;color:var(--mut)}
.wrap{display:flex;min-height:calc(100vh - 52px)}
aside{width:230px;border-right:1px solid var(--line);padding:14px;flex-shrink:0}
aside h3{font-size:12px;color:var(--mut);text-transform:uppercase;margin:16px 0 8px}
button{display:block;width:100%;text-align:left;margin:5px 0;padding:8px 10px;background:var(--panel);
 color:var(--fg);border:1px solid var(--line);border-radius:8px;cursor:pointer;font-size:13px}
button:hover{border-color:var(--acc)}button.rep{background:transparent}
main{flex:1;padding:20px;overflow:auto}
#status{color:var(--mut);margin-bottom:12px;min-height:20px}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--acc);
 border-radius:50%;animation:s .8s linear infinite;vertical-align:-2px;margin-right:6px}@keyframes s{to{transform:rotate(360deg)}}
pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;overflow:auto;white-space:pre-wrap}
table{border-collapse:collapse;margin:12px 0;font-size:13px;width:100%}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:right}th{background:var(--panel);color:var(--mut)}
td:first-child,th:first-child{text-align:left}
h1,h2,h3{line-height:1.3}h2{border-bottom:1px solid var(--line);padding-bottom:6px;margin-top:24px}
a{color:var(--acc)}.note{color:var(--warn)}select{width:100%;padding:7px;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px}
</style></head><body>
<header><h1>📈 股票策略仪表盘</h1><div id=state></div></header>
<div class=wrap>
<aside>
<h3>一键</h3>
<button onclick="runAll()" style="border-color:var(--ok);color:var(--ok);font-weight:600">🚀 一键全跑</button>
<h3>执行流程</h3><div id=acts></div>
<h3>历史回补</h3>
<input id=start type=date style="width:100%;padding:6px;margin:3px 0;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px">
<button onclick="backfill()">回补至今</button>
<h3>模拟盘</h3>
<input id=psearch placeholder="🔎 按名称搜索 如 泡泡玛特" oninput="searchName(this.value)" style="width:100%;padding:6px;margin:3px 0;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px">
<div id=sugg style="max-height:160px;overflow:auto"></div>
<input id=ptk placeholder="代码 如 0700.HK" style="width:100%;padding:6px;margin:3px 0;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px">
<input id=psh type=number placeholder="股数" style="width:100%;padding:6px;margin:3px 0;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px">
<input id=pcost type=number placeholder="买入价" style="width:100%;padding:6px;margin:3px 0;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px">
<button onclick="addPos()">加入持仓</button>
<button class=rep onclick="viewPort()">📊 查看模拟盘</button>
<h3>⑥ 天量分位</h3>
<input id=sky type=range min=0.90 max=0.995 step=0.005 value=0.99 oninput="skyLbl()" style="width:100%">
<div id=skylbl style="font-size:12px;color:var(--mut);margin:2px 0">分位 0.99（越高越少卖天量）</div>
<button onclick="s6sky(0)">重算看效果</button>
<button onclick="s6sky(1)">应用为默认</button>
<h3>查看报告</h3><div id=reps></div>
<h3>数据表(CSV)</h3><select id=csv onchange="loadCsv(this.value)"><option value="">选择…</option></select>
</aside>
<main><div id=status></div><div id=content><p class=note>点左侧按钮执行流程或查看报告。首次建议顺序：①下载 → ②扫描 → ③分析 → ⑥优选。</p></div></main>
</div>
<script>
const ACTS=__ACTS__, REPS=__REPS__;
const $=s=>document.querySelector(s);
function busy(t){$('#status').innerHTML='<span class=spin></span>'+t}
function done(t){$('#status').textContent=t}
ACTS.forEach(a=>{const b=document.createElement('button');b.textContent=a[1];b.onclick=()=>showAction(a[0],a[1]);$('#acts').append(b)});
async function showAction(id,name){busy('加载「'+name+'」最近结果…');const r=await fetch('/view/'+id);const j=await r.json();done('');
 const c=$('#content');c.innerHTML='';
 const bar=document.createElement('div');bar.style.cssText='margin-bottom:10px;display:flex;align-items:center;gap:10px';
 const b=document.createElement('button');b.textContent='▶ 执行流程';b.style.cssText='width:auto;padding:7px 16px;border-color:var(--ok);color:var(--ok);font-weight:600;margin:0';b.onclick=()=>rerun(id,name);
 const hint=document.createElement('span');hint.style.cssText='color:var(--mut);font-size:12px';hint.textContent='下面是最近一次结果；点「执行流程」在后台重新运行';
 bar.append(b);bar.append(hint);
 const d=document.createElement('div');d.innerHTML=j.html;c.append(bar);c.append(d)}
const POLL={};
function pollJob(id,name){if(POLL[id])clearInterval(POLL[id]);
 POLL[id]=setInterval(async()=>{const r=await fetch('/job/'+id);const j=await r.json();
  if(j.status==='running'){busy('后台执行「'+name+'」…（可随意切换查看其他，完成会提示）');}
  else{clearInterval(POLL[id]);POLL[id]=null;done((j.status==='done'?'✅ ':'⚠️ ')+name+' 执行完成');
   if(id==='all'||id==='download'){$('#content').innerHTML='<h2>'+name+'</h2><pre>'+esc(j.output||'')+'</pre>';}
   else{showAction(id,name);} refreshState();}},2000)}
async function rerun(id,name){busy('「'+name+'」已在后台开始…');const r=await fetch('/runasync/'+id,{method:'POST'});const j=await r.json();
 if(j.running)busy('「'+name+'」正在后台运行中…');pollJob(id,name)}
Object.keys(REPS).forEach(k=>{const b=document.createElement('button');b.className='rep';b.textContent='📄 '+k;b.onclick=()=>report(k);$('#reps').append(b)});
async function runAll(){busy('🚀 一键全跑 已在后台开始（个股信息→下载→扫描→分析→优选→行业胜率）…');
 await fetch('/runasync/all',{method:'POST'});pollJob('all','🚀 一键全跑')}
async function backfill(){const s=$('#start').value;if(!s){alert('请选开始日期');return}
 busy('回补 '+s+' 至今…（较慢，请耐心）');
 const r=await fetch('/backfill?start='+s,{method:'POST'});const j=await r.json();
 done('✅ 回补完成');$('#content').innerHTML='<h2>历史回补</h2><pre>'+esc(j.output)+'</pre>'}
async function report(k){busy('加载报告…');const r=await fetch('/report?k='+encodeURIComponent(k));const j=await r.json();
 done('');$('#content').innerHTML=j.ok?j.html:'<p class=note>'+esc(j.err)+'</p>'}
async function loadCsv(name){if(!name)return;busy('加载 '+name);const r=await fetch('/csv?f='+encodeURIComponent(name));
 const j=await r.json();done('');$('#content').innerHTML='<h2>'+name+'</h2>'+(j.ok?j.html:'<p class=note>'+esc(j.err)+'</p>')}
function skyLbl(){$('#skylbl').textContent='分位 '+$('#sky').value+'（越高越少卖天量）'}
async function s6sky(save){const p=$('#sky').value;busy('重算 ⑥ 天量分位 '+p+'…（首次载入数据稍慢）');
 const r=await fetch('/s6sky?pct='+p+(save?'&save=1':''));const j=await r.json();
 done('');$('#content').innerHTML=j.ok?j.html:'<p class=note>'+esc(j.err)+'</p>'}
async function searchName(q){const box=$('#sugg');if(!q||q.length<1){box.innerHTML='';return}
 const r=await fetch('/search?q='+encodeURIComponent(q));const j=await r.json();
 box.innerHTML=j.items.map(it=>'<div class=sg data-tk="'+it.ticker+'" style="padding:5px 8px;cursor:pointer;border:1px solid var(--line);border-radius:6px;margin:2px 0;font-size:12px">'+it.ticker+' — '+esc(it.name)+'</div>').join('')||'<div style="color:var(--mut);font-size:12px;padding:4px">无匹配</div>';
 box.onclick=e=>{const d=e.target.closest('.sg');if(d)pick(d.getAttribute('data-tk'))}}
function pick(t){$('#ptk').value=t;$('#sugg').innerHTML='';$('#psearch').value='';$('#psh').focus()}
async function viewPort(){busy('加载模拟盘…');const r=await fetch('/portfolio');const j=await r.json();
 done('');$('#content').innerHTML=j.html}
async function addPos(){const t=$('#ptk').value.trim(),s=$('#psh').value,c=$('#pcost').value;
 if(!t||!s||!c){alert('请填代码/股数/买入价');return}
 busy('加入持仓…');await fetch('/portfolio/add?ticker='+encodeURIComponent(t)+'&shares='+s+'&cost='+c,{method:'POST'});
 $('#ptk').value='';$('#psh').value='';$('#pcost').value='';done('✅ 已加入');viewPort()}
async function delPos(i){if(!confirm('删除该持仓?'))return;await fetch('/portfolio/del?idx='+i,{method:'POST'});viewPort()}
async function refreshState(){const r=await fetch('/state');const j=await r.json();
 const wk=j.weekly?'<span class=chip style="border-color:var(--acc);color:var(--acc)">🗓 '+esc(j.weekly)+'</span>':'';
 $('#state').innerHTML=wk+j.chips.map(c=>'<span class=chip>'+esc(c)+'</span>').join('');
 const sel=$('#csv');sel.innerHTML='<option value="">选择…</option>'+j.csvs.map(c=>'<option>'+c+'</option>').join('')}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
refreshState();
</script></body></html>"""


@app.route("/")
def index():
    import json
    acts = [[k, v[0]] for k, v in ACTIONS.items()]
    html = PAGE.replace("__ACTS__", json.dumps(acts, ensure_ascii=False))
    html = html.replace("__REPS__", json.dumps(REPORTS, ensure_ascii=False))
    return html


@app.route("/run/<aid>", methods=["POST"])
def run_action(aid):
    if aid not in ACTIONS:
        return jsonify(ok=False, output="未知操作")
    if aid == "download":          # 下载走 ~/stock_downloader(定时任务程序)
        rc, out = run_cmd(ACTIONS[aid][1], cwd=DL_DIR, py=DL_PY)
    else:
        rc, out = run_cmd(ACTIONS[aid][1])
    return jsonify(ok=(rc == 0), output=out)


# ---------- 异步任务：后台线程执行，前端轮询 ----------
JOBS = {}   # aid -> {"status": running/done/error, "output": str}


def _job_one(aid):
    try:
        if aid == "download":
            rc, out = run_cmd(ACTIONS[aid][1], cwd=DL_DIR, py=DL_PY)
        else:
            rc, out = run_cmd(ACTIONS[aid][1])
        JOBS[aid] = {"status": "done" if rc == 0 else "error", "output": out}
    except Exception as e:
        JOBS[aid] = {"status": "error", "output": str(e)}


def _job_all():
    log = []
    for name, args, use_dl in PIPELINE:
        JOBS["all"] = {"status": "running", "output": "\n".join(log + [f"▶ 正在 {name} …"])}
        rc, out = run_cmd(args, cwd=DL_DIR, py=DL_PY) if use_dl else run_cmd(args)
        log.append(f"{'✅' if rc == 0 else '⚠️'} {name} —— {'成功' if rc == 0 else '失败'}")
        if rc != 0:
            log.append("   " + (out[-300:] or ""))
    log.append("\n全部完成。可在「查看报告」看结果。")
    JOBS["all"] = {"status": "done", "output": "\n".join(log)}


@app.route("/runasync/<aid>", methods=["POST"])
def runasync(aid):
    if aid != "all" and aid not in ACTIONS:
        return jsonify(ok=False, err="未知操作")
    if (JOBS.get(aid) or {}).get("status") == "running":
        return jsonify(ok=True, running=True)
    JOBS[aid] = {"status": "running", "output": ""}
    target = _job_all if aid == "all" else (lambda: _job_one(aid))
    threading.Thread(target=target, daemon=True).start()
    return jsonify(ok=True, started=True)


@app.route("/job/<aid>")
def job(aid):
    j = JOBS.get(aid) or {"status": "idle", "output": ""}
    return jsonify(status=j.get("status", "idle"), output=(j.get("output") or "")[-4000:])


@app.route("/runall", methods=["POST"])
def runall():
    log = []
    for name, args, use_dl in PIPELINE:
        rc, out = run_cmd(args, cwd=DL_DIR, py=DL_PY) if use_dl else run_cmd(args)
        log.append(f"{'✅' if rc == 0 else '⚠️'} {name} —— {'成功' if rc == 0 else '失败'}")
        if rc != 0:
            log.append("   " + (out[-400:] or ""))
    log.append("\n全部完成。")
    return jsonify(ok=True, output="\n".join(log))


SCAN_STRATS = [
    ("① 平台突破缩量回踩", "strategy1_platform_pullback.csv"),
    ("③ 年线放量突破", "strategy3_annual_line.csv"),
    ("④ MACD 底背离", "strategy4_macd_divergence.csv"),
    ("⑤ 放量突破前高回踩", "strategy5_prevhigh_retest.csv"),
    ("⑥ 缩量+十日线向上", "strategy6_vol_shrink_ma10.csv"),
]


def build_scan_html():
    """从现有扫描结果文件构建结构化视图(不重新扫描)。"""
    import pandas as pd
    parts = []
    # ① 汇总
    sp = os.path.join(OUT, "summary.md")
    if os.path.exists(sp):
        parts.append(md.markdown(open(sp, encoding="utf-8").read(), extensions=["tables"]))
    # 读各策略信号
    frames = {}
    gmax = None
    for label, fn in SCAN_STRATS:
        p = os.path.join(OUT, fn)
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p)
        if d.empty:
            continue
        d["SignalDate"] = pd.to_datetime(d["SignalDate"])
        frames[label] = d
        m = d["SignalDate"].max()
        gmax = m if gmax is None else max(gmax, m)
    # ② 昨日(最新)信号
    parts.append(f"<h2>昨日信号（最新信号日 {gmax.date() if gmax is not None else '-'}）</h2>")
    if gmax is not None:
        rows = []
        for label, d in frames.items():
            y = d[d["SignalDate"] == gmax]
            for _, r in y.iterrows():
                rows.append({"策略": label, "代码": r.get("Ticker", ""), "名称": r.get("名称", ""),
                             "行业": r.get("行业", ""), "信号日": r["SignalDate"].date()})
        if rows:
            parts.append(pd.DataFrame(rows).to_html(index=False, border=0))
        else:
            parts.append("<p>昨日无信号</p>")
    # ③ 近90天信号(按策略)
    parts.append("<h2>近90天信号（按策略）</h2>")
    if gmax is not None:
        cut = gmax - pd.Timedelta(days=90)
        for label, d in frames.items():
            recent = d[d["SignalDate"] >= cut].sort_values("SignalDate", ascending=False)
            parts.append(f"<h3>{label}　近90天 {len(recent)} 个</h3>")
            if recent.empty:
                parts.append("<p>无</p>")
                continue
            show = recent.drop(columns=[c for c in ["Market"] if c in recent.columns]).copy()
            show["SignalDate"] = show["SignalDate"].dt.date
            parts.append(show.to_html(index=False, border=0))
    return "<h2>② 扫描形态信号</h2>" + "".join(parts)


# 各执行流程点击后展示的"最近结果"：md报告 / 特殊视图
VIEW_REPORT = {
    "analyze": "analysis_all.md", "regime": "regime.md", "band": "band_trade.md",
    "synth": "策略优选.md", "backtest": "backtest.md", "sector": "行业胜率.md",
    "vol": "成交量参考.md",
}


@app.route("/view/<aid>")
def view(aid):
    import pandas as pd
    from datetime import datetime
    def stamp(path):
        try:
            return f"<p class=note>最近执行: {datetime.fromtimestamp(os.path.getmtime(path)):%Y-%m-%d %H:%M}</p>"
        except Exception:
            return ""
    if aid == "scan":
        p = os.path.join(OUT, "summary.md")
        return jsonify(ok=True, html=(stamp(p) if os.path.exists(p) else "") + build_scan_html())
    if aid in VIEW_REPORT:
        p = os.path.join(OUT, VIEW_REPORT[aid])
        if not os.path.exists(p):
            return jsonify(ok=True, html="<p class=note>尚无结果，请点『🔄 重新执行』生成。</p>")
        return jsonify(ok=True, html=stamp(p) + md.markdown(open(p, encoding="utf-8").read(), extensions=["tables"]))
    if aid == "meta":
        p = os.path.join(OUT, "metadata.csv")
        if not os.path.exists(p):
            return jsonify(ok=True, html="<p class=note>尚无个股信息，请点『🔄 重新执行』。</p>")
        return jsonify(ok=True, html=stamp(p) + "<h2>⑧ 个股信息</h2>" + pd.read_csv(p).to_html(index=False, border=0))
    if aid == "download":
        import glob as _g
        logs = sorted(_g.glob(os.path.expanduser("~/stock_downloader/logs/download_*.log")))
        tail = ""
        if logs:
            lines = open(logs[-1], encoding="utf-8").read().splitlines()
            tail = "\n".join(l for l in lines if "Warning" not in l and "warn" not in l)[-3000:]
        return jsonify(ok=True, html="<h2>① 下载 · 最近日志</h2><pre>" + (tail or "暂无下载日志") + "</pre>")
    return jsonify(ok=True, html="<p class=note>该项点『🔄 重新执行』运行。</p>")


@app.route("/backfill", methods=["POST"])
def backfill():
    start = request.args.get("start", "")
    rc, out = run_cmd(["download.py", "--start", start], cwd=DL_DIR, py=DL_PY)
    return jsonify(ok=(rc == 0), output=out)


@app.route("/report")
def report():
    k = request.args.get("k", "")
    fn = REPORTS.get(k)
    path = os.path.join(OUT, fn) if fn else None
    if not path or not os.path.exists(path):
        return jsonify(ok=False, err=f"报告不存在：{fn}（先执行对应流程生成）")
    html = md.markdown(open(path, encoding="utf-8").read(), extensions=["tables", "fenced_code"])
    return jsonify(ok=True, html=html)


@app.route("/csv")
def csv():
    f = request.args.get("f", "")
    path = os.path.join(OUT, f)
    if not os.path.exists(path) or not f.endswith(".csv"):
        return jsonify(ok=False, err="文件不存在")
    df = pd.read_csv(path)
    return jsonify(ok=True, html=df.to_html(index=False, border=0))


def weekly_status():
    logs = sorted(glob.glob(os.path.expanduser("~/stockapp/logs/weekly_*.log")))
    if not logs:
        return "周任务: 未运行"
    try:
        txt = open(logs[-1], encoding="utf-8").read()
    except Exception:
        return "周任务: 未知"
    i = txt.rfind("每周全流程")
    if i < 0:
        return "周任务: 未运行"
    seg = txt[i:]
    m = re.search(r"每周全流程 ([\d-]+ [\d:]+)", seg)
    start = m.group(1) if m else "?"
    if "===== 完成" in seg:
        fails = [c for c in re.findall(r"退出码 (\d+)", seg) if c != "0"]
        status = "✅完成" if not fails else f"⚠️{len(fails)}步失败"
    else:
        status = "⏳进行中/未完成"
    return f"周任务 {start} {status}"


def _col(v, pct=False):
    if v is None:
        return "<td>-</td>"
    c = "var(--ok)" if v > 0 else ("#e5484d" if v < 0 else "var(--fg)")
    s = f"{v:+.2f}%" if pct else f"{v:+,.2f}"
    return f'<td style="color:{c}">{s}</td>'


@app.route("/portfolio")
def portfolio_view():
    rows, tot = portfolio.compute()
    if not rows:
        return jsonify(ok=True, html="<p>暂无持仓。用左侧「加入持仓」录入：代码 / 股数 / 买入价。</p>")
    h = ["<table><thead><tr><th>代码</th><th>名称</th><th>行业</th><th>股数</th><th>买入价</th>"
         "<th>现价</th><th>更新日</th><th>成本</th><th>市值</th><th>盈亏</th><th>盈亏%</th><th></th></tr></thead><tbody>"]
    for r in rows:
        h.append("<tr>"
                 f"<td>{r['代码']}</td><td>{r['名称']}</td><td>{r['行业']}</td>"
                 f"<td>{r['股数']:g}</td><td>{r['买入价']}</td>"
                 f"<td>{r['现价'] if r['现价'] is not None else '-'}</td><td>{r['更新日']}</td>"
                 f"<td>{r['成本']:,.2f}</td><td>{r['市值'] if r['市值'] is not None else '-'}</td>"
                 + _col(r['盈亏']) + _col(r['盈亏%'], pct=True) +
                 f'<td><button style="width:auto;padding:2px 8px" onclick="delPos({r["idx"]})">删</button></td>'
                 "</tr>")
    h.append(f"<tr><td colspan=7><b>合计</b></td><td><b>{tot['成本']:,.2f}</b></td>"
             f"<td><b>{tot['市值']:,.2f}</b></td>" + _col(tot['盈亏']).replace("<td", "<td><b").replace("</td>", "</b></td>")
             + _col(tot['盈亏%'], pct=True).replace("<td", "<td><b").replace("</td>", "</b></td>") + "<td></td></tr>")
    h.append("</tbody></table>")
    return jsonify(ok=True, html="<h2>📊 模拟盘</h2>" + "".join(h))


def name_map():
    """从 config.yaml 注释解析 (代码, 名称) 列表，供按名称搜索。"""
    out = []
    try:
        for line in open(os.path.join(BASE, "config.yaml"), encoding="utf-8"):
            m = re.match(r"\s*-\s*(\S+)\s*#\s*(.+)", line)
            if m:
                out.append((m.group(1), m.group(2).strip()))
    except Exception:
        pass
    return out


@app.route("/search")
def search():
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify(items=[])
    items = [{"ticker": t, "name": n} for t, n in name_map()
             if q in n.lower() or q in t.lower()][:20]
    return jsonify(items=items)


@app.route("/portfolio/add", methods=["POST"])
def portfolio_add():
    try:
        portfolio.add(request.args.get("ticker", ""), request.args.get("shares", 0),
                      request.args.get("cost", 0))
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, err=str(e))


@app.route("/portfolio/del", methods=["POST"])
def portfolio_del():
    portfolio.delete(int(request.args.get("idx", -1)))
    return jsonify(ok=True)


_S6 = {}


def _s6_series():
    if "series" not in _S6:
        from scanner import load_series, add_indicators
        s = load_series("/Users/bruce/Documents/Stock")
        _S6["series"] = {t: add_indicators(g) for t, g in s.items()}
    return _S6["series"]


@app.route("/s6sky")
def s6sky():
    import json
    import pandas as pd
    from scanner import PARAMS, name_map
    from backtest import make_stop, run_trade
    pct = float(request.args.get("pct", 0.99))
    save = request.args.get("save") == "1"
    PARAMS["s6_sky_pct"] = pct
    p = os.path.join(OUT, "strategy6_vol_shrink_ma10.csv")
    if not os.path.exists(p):
        return jsonify(ok=False, err="请先运行『②扫描』生成⑥信号")
    series = _s6_series()
    hits = pd.read_csv(p)
    hits["SignalDate"] = pd.to_datetime(hits["SignalDate"])
    rets = []
    for _, h in hits.iterrows():
        g = series.get(h["Ticker"])
        if g is None:
            continue
        t = run_trade(g, h["SignalDate"], make_stop("s6", h), 40)
        if t:
            rets.append(t["Ret%"])
    r = pd.Series(rets)
    win = (r > 0).mean() * 100 if len(r) else 0
    gains = r[r > 0].sum() if len(r) else 0
    loss = r[r < 0].sum() if len(r) else 0
    pf = gains / abs(loss) if loss < 0 else 0
    # 当前处于天量(≥该分位)的股票
    nm = name_map()
    win_col = PARAMS.get("s6_sky_win", 250)
    sky_now = []
    for tk, g in series.items():
        v = g["Volume"].dropna()
        if len(v) >= 30 and v.iloc[-1] >= v.tail(win_col).quantile(pct):
            sky_now.append(f"{tk}({nm.get(tk,'')})" if nm.get(tk) else tk)
    if save:
        json.dump({"s6_sky_pct": pct}, open(os.path.join(OUT, "params_override.json"), "w"))
    html = (f"<h2>⑥ 天量分位 = {pct}</h2>"
            f"<p>回测 {len(r)} 笔：胜率 <b>{win:.0f}%</b>、平均收益 <b>{r.mean():+.2f}%</b>、"
            f"盈亏比 <b>{pf:.2f}</b>（分位越高越少卖天量、越接近持有）</p>"
            f"<p>当前处于天量(≥自身{pct}分位)的股票 <b>{len(sky_now)}</b> 只："
            f"{'、'.join(sky_now) if sky_now else '无'}</p>")
    if save:
        html += "<p style='color:var(--ok)'>✅ 已保存为默认，全流程(回测/分析/成交量参考)将采用此分位</p>"
    return jsonify(ok=True, html=html)


@app.route("/state")
def state():
    chips = []
    idx_names = {"标普500", "纳斯达克", "道琼斯", "恒生", "海峡时报", "上证综指", "深证成指"}
    rp = os.path.join(OUT, "regime.md")
    if os.path.exists(rp):
        for line in open(rp, encoding="utf-8"):
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            # 只取"当前状态一览"表里以指数名开头的行(避免7日明细的日期行混入)
            if cells and cells[0] in idx_names and any(k in line for k in ("牛市", "熊市", "震荡")):
                chips.append(f"{cells[0]}:{cells[-1]}")
    if not chips:
        chips = ["先执行「④ 市场状态」生成"]
    csvs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(OUT, "*.csv")))
    return jsonify(chips=chips, csvs=csvs, weekly=weekly_status())


if __name__ == "__main__":
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)
