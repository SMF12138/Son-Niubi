#!/bin/bash
# macOS 双击启动入口(访达双击 .command 会在终端执行)
cd "$(dirname "$0")"
exec bash start.sh
