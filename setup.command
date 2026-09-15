#!/bin/bash
# macOS 首次安装入口: 在访达双击运行(自动打开终端窗口)
# 安装 Python(缺才装, 需输一次管理员密码) -> 建虚拟环境 -> 装依赖
# 之后日常启动用双击 start.command, 停止用双击 stop.command
cd "$(dirname "$0")"
bash scripts/setup.sh
echo ""
read -n 1 -s -r -p "(完成或报错都看上面) 按任意键关闭窗口..."
