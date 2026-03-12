#!/usr/bin/env python3
"""
检测重要业务文件是否被修改，如果是，则在终端输出提示，提醒 AI 或开发者更新 PROJECT_MAP.md。
被 Claude Code 的 PostToolUse hook 调用。
"""

import sys
import re
from pathlib import Path

# 定义哪些目录的改动应该触发提醒
WATCHED_DIRS = [
    "src/agents/",
    "src/data/",
    "src/strategy/",
    "config/"
]

def main():
    if len(sys.argv) < 2:
        return

    file_path = sys.argv[1]
    
    # 将绝对路径转换为相对路径（如果是的话）
    try:
        rel_path = str(Path(file_path).relative_to(Path.cwd()))
    except ValueError:
        rel_path = file_path
        
    # 如果修改的就是地图本身，就不提示
    if "PROJECT_MAP.md" in rel_path:
        return

    # 检查是否属于监听的核心目录
    is_core_file = any(rel_path.startswith(d) for d in WATCHED_DIRS)
    
    # 也检查根目录的重要文件
    is_main_file = rel_path in ["src/main.py"]
    
    if is_core_file or is_main_file:
        print("\n\033[93m" + "═" * 60)
        print("🗺️  [PROJECT_MAP HINT]")
        print(f"检测到核心文件修改: {rel_path}")
        print("如果本次修改涉及：\n  1. 添加/删除了 Agent，或修改了核心职责\n  2. 更改了数据流、架构或重大依赖关系\n  3. 增加了新的配置参数或阈值字典")
        print("请记得同步更新根目录的 PROJECT_MAP.md ！")
        print("你可以运行: `/update-project-map` 自动更新它。")
        print("═" * 60 + "\033[0m\n")

if __name__ == "__main__":
    main()
