#!/bin/bash
# macOS 首次安装入口: 在访达双击运行(自动打开终端窗口)
# 安装 Python(缺才装, 需输一次管理员密码) -> 建虚拟环境 -> 装依赖
# 之后日常启动用双击桌面 "Son NiuBi" 图标; 关桌宠会自动停后台
cd "$(dirname "$0")"
bash scripts/setup.sh
echo ""
read -n 1 -s -r -p "(完成或报错都看上面) 按任意键关闭窗口..."
