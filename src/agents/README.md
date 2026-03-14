<!-- Last Updated: 2026-03-14 -->
# Agent 分析层

## 执行顺序（registry.py 编排）
```
fundamentals → valuation → warren_buffett → ben_graham → sentiment
                                    ↓
                              contrarian（辩证分析）
                                    ↓
                            report_generator
```

## 各 Agent 职责速查

| Agent | 文件 | 类型 | 输出 |
|-------|------|------|------|
| fundamentals | `fundamentals.py` | 纯代码 | 100分制基本面评分 |
| valuation | `valuation.py` | 代码+可选LLM | 目标价+安全边际 |
| warren_buffett | `warren_buffett.py` | LLM | 护城河+管理层评估 |
| ben_graham | `ben_graham.py` | 代码+LLM | 7项安全准则 |
| sentiment | `sentiment.py` | Tavily+LLM | 新闻情绪分析 |
| contrarian | `contrarian.py` | LLM | 辩证挑战共识 |
| report_generator | `report_generator.py` | LLM | 8章中文研报 |

## 信号输出格式（所有 Agent 统一）
```python
AgentSignal(
    agent_name="xxx_agent",
    ticker=ticker,
    signal="bullish" | "bearish" | "neutral",
    confidence=0.10~0.85,  # 0.85 封顶
    reasoning="解释",
    metrics={...},
    flags=[...]  # 数据质量问题
)
```

## 关键支撑模块

| 模块 | 文件 | 用途 |
|------|------|------|
| 置信度计算 | `confidence.py` | `calculate_confidence()` |
| 行业分类 V2 | `industry_classifier.py` | 行业识别+参数获取 |
| **行业引擎 V3** | `industry_engine.py` | 三层漏斗：硬规则→LLM→fallback |
| **估值配置** | `valuation_config.py` | ValuationConfig 模型+权重归一化 |
| WACC计算 | `wacc.py` | DCF 折现率 |
| 信号聚合 | `signal_aggregator.py` | 加权聚合最终推荐 |
| 章节上下文 | `chapter_context.py` | 报告章节间信息传递 |

## 添加新 Agent 检查清单
- [ ] 创建 `src/agents/xxx.py`
- [ ] 返回 `AgentSignal` 格式
- [ ] 使用 `calculate_confidence()` 计算置信度
- [ ] 处理数据缺失（返回 neutral + 0.10 置信度）
- [ ] 注册到 `registry.py:run_all_agents()`
- [ ] 如需 LLM：在 `config/llm_config.yaml` 添加任务路由
- [ ] 如需 Prompt：在 `src/llm/prompts.py` 添加

## 行业权重配置
Agent 权重在 `config/industry_profiles.yaml` 的 `weights` 节，不在代码里硬编码：
```yaml
banking:
  weights:
    fundamentals: 0.30
    valuation: 0.25
    ...
```

## V3.0 行业引擎（Feature Flag）
启用环境变量 `USE_INDUSTRY_ENGINE_V3=true` 使用新三层架构：
```
Layer 1: 硬规则 (银行/保险/地产/困境/品牌护城河/创新药)
Layer 2: LLM 动态路由 (DeepSeek-Reasoner + 缓存)
Layer 3: 安全回退 (generic 体系)
```
关键文件：
- `industry_engine.py` — `get_valuation_config()` 入口
- `valuation_config.py` — `ValuationConfig` 模型
