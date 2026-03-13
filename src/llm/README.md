<!-- Last Updated: 2026-03-13 -->
# LLM 路由层

## 核心文件
| 文件 | 职责 |
|------|------|
| `router.py` | LLM 调用统一入口，任务路由+重试+fallback |
| `prompts.py` | 所有 Agent 的 System/User Prompt |

## 调用方式
```python
from src.llm.router import call_llm

result = call_llm(
    task="buffett_analysis",    # 任务名，对应 llm_config.yaml
    system_prompt=system,
    user_prompt=user,
    max_tokens=2000,            # 可选覆盖
    temperature=0.2             # 可选覆盖
)
```

## 任务路由配置（config/llm_config.yaml ⚠️受保护）
```yaml
task_routing:
  buffett_analysis:
    provider: openai
    model: gpt-4o
    max_tokens: 2000
    temperature: 0.2
```

## Fallback 链
```
OpenAI → Anthropic → DeepSeek
```
某提供商失败自动切换下一个。

## 添加新 LLM 任务
1. **config/llm_config.yaml** 添加任务路由（需用户审批）
2. **prompts.py** 添加 Prompt 函数：
```python
def get_xxx_prompt(ticker: str, metrics: dict) -> tuple[str, str]:
    system = "..."
    user = f"..."
    return system, user
```
3. **Agent 代码** 调用：
```python
system, user = get_xxx_prompt(ticker, metrics)
result = call_llm("xxx_task", system, user)
```

## Token 预算参考
| 任务类型 | max_tokens |
|---------|------------|
| 简单分析 | 1000-1500 |
| 复杂推理 | 2000-2500 |
| 报告章节 | 1500-2000 |

## Temperature 参考
| 值 | 适用场景 |
|----|---------|
| 0.1 | 指标解读（确定性）|
| 0.2 | 结构化分析（少创意）|
| 0.3 | 报告写作（适度创意）|

## 现有任务列表
- `buffett_analysis` - 巴菲特框架分析
- `graham_analysis` - 格雷厄姆准则
- `sentiment_analysis` - 情绪分析
- `contrarian_analysis` - 辩证分析
- `report_ch1` ~ `report_ch6` - 报告各章节
- `industry_analysis` - 行业分析
- `company_lookup` - 公司信息查询
