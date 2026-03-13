---
name: update-docs
description: 更新模块级 README 文档，保持与代码同步
user-invocable: true
---

# 更新模块文档

## 触发场景
- 添加了新 Agent / 数据源 / LLM 任务
- 修改了模块的核心接口
- 每个 milestone 结束时
- 用户主动调用 `/update-docs`

## 更新范围
可选择更新全部或指定模块：
- `/update-docs` — 更新全部模块 README
- `/update-docs agents` — 只更新 src/agents/README.md
- `/update-docs data` — 只更新 src/data/README.md
- `/update-docs llm` — 只更新 src/llm/README.md
- `/update-docs config` — 只更新 config/README.md

## 执行步骤

### 1. 检测变更
对每个目标模块，执行以下检测：

```bash
# 列出模块内所有 .py 文件
ls src/agents/*.py

# 提取函数/类定义（用于检测新增）
grep -h "^def \|^class " src/agents/*.py
```

### 2. 对比现有 README
读取现有 README，识别需要更新的部分：
- 新增的文件 → 添加到文件列表
- 删除的文件 → 从列表移除
- 新增的关键函数 → 添加到函数速查

### 3. 增量更新（非全量重写）
**关键原则**：使用 `Edit` 工具做精准修改，不要 `Write` 全量覆盖。

每个模块的更新焦点：

#### src/agents/README.md
- 更新「各 Agent 职责速查」表
- 更新「关键支撑模块」表
- 检查执行顺序是否变化

#### src/data/README.md
- 更新「数据源文件说明」表
- 更新「数据源优先级」链
- 检查表结构是否有新增

#### src/llm/README.md
- 更新「现有任务列表」
- 检查 prompts.py 的新函数

#### config/README.md
- 检查是否有新配置文件
- 更新示例 YAML 片段（如有重大变化）

### 4. 输出变更摘要
完成后输出：
```
✅ 更新完成
- src/agents/README.md: 添加 xxx_agent
- src/data/README.md: 无变化
- src/llm/README.md: 添加 xxx_analysis 任务
- config/README.md: 无变化
```

## 不要做的事
- ❌ 不要全量重写 README（浪费 token）
- ❌ 不要更新 PROJECT_MAP.md（那是架构级文档，单独维护）
- ❌ 不要添加与代码无关的描述性文字

## 检测新增内容的命令参考

```bash
# Agents: 检测新 Agent 文件
ls src/agents/*.py | grep -v __init__ | grep -v __pycache__

# Data: 检测新数据源
ls src/data/*_source.py

# LLM: 检测 prompts.py 中的函数
grep "^def get_.*_prompt" src/llm/prompts.py

# Config: 检测配置文件
ls config/*.yaml
```

## 版本标记
更新完成后，在 README 顶部添加/更新时间戳：
```markdown
<!-- Last Updated: 2026-03-13 -->
```
