#!/bin/bash
# 行情下载的调度入口脚本（供 launchd / cron 调用）
# 用绝对路径，保证在任何工作目录下都能正确运行。

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 用项目自带的虚拟环境
"$DIR/.venv/bin/python" "$DIR/download.py" "$@"
