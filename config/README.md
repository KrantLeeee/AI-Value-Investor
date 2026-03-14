<!-- Last Updated: 2026-03-14 -->
# 配置文件

## 文件列表
| 文件 | 用途 | 保护状态 |
|------|------|----------|
| `industry_profiles.yaml` | 行业分类+估值参数+Agent权重 | ✅ 可直接编辑 |
| `watchlist.yaml` | 监控股票列表 | ✅ 可直接编辑 |
| `llm_config.yaml` | LLM 任务路由配置 | ⚠️ 需用户审批 |
| `screening_rules.yaml` | 因子筛选规则 | ⚠️ 需用户审批 |

---

## industry_profiles.yaml（最常改）
调整估值参数和 Agent 权重，**无需改代码**：

```yaml
banking:
  name: "银行"

  # 估值倍数
  valuation:
    pb_multiple: [0.6, 1.0]    # 合理 P/B 范围
    pe_multiple: [5, 8]        # 合理 P/E 范围
    ev_ebitda: [6, 10]         # EV/EBITDA 范围

  # Agent 权重（加总=1.0）
  weights:
    fundamentals: 0.30
    valuation: 0.25
    warren_buffett: 0.15
    ben_graham: 0.20
    sentiment: 0.10

  # 可比公司
  comparables:
    - "601398.SH"  # 工商银行
    - "601288.SH"  # 农业银行
```

**添加新行业**：复制现有行业块，修改 key 和参数。

---

## watchlist.yaml
监控股票列表，批量操作时使用：

```yaml
stocks:
  - ticker: "601808.SH"
    name: "中海油服"
    industry: "oil_gas"

  - ticker: "000858.SZ"
    name: "五粮液"
    industry: "consumer_staples"
```

---

## llm_config.yaml ⚠️
**受保护**：修改需用户审批

```yaml
providers:
  openai:
    api_key_env: OPENAI_API_KEY
    default_model: gpt-4o

task_routing:
  buffett_analysis:
    provider: openai
    model: gpt-4o
    max_tokens: 2000
```

---

## screening_rules.yaml ⚠️
**受保护**：修改需用户审批

```yaml
rules:
  - name: "high_roe"
    field: "roe"
    operator: ">"
    threshold: 0.15

  - name: "low_debt"
    field: "debt_equity"
    operator: "<"
    threshold: 0.5
```

---

## 常见修改场景
| 我想做的事 | 改哪个文件 |
|-----------|-----------|
| 调整某行业 P/E 倍数 | `industry_profiles.yaml` |
| 调整 Agent 权重 | `industry_profiles.yaml` |
| 添加监控股票 | `watchlist.yaml` |
| 改 LLM 模型/路由 | `llm_config.yaml` ⚠️ |
| 改筛选规则 | `screening_rules.yaml` ⚠️ |

---

## Feature Flags（环境变量）
通过 `src/utils/config.py:get_feature_flags()` 获取：
| 变量 | 默认值 | 用途 |
|------|--------|------|
| `USE_INDUSTRY_ENGINE_V3` | false | 启用 V3 行业引擎 |
| `INDUSTRY_ENGINE_PARALLEL` | false | V3/V2 并行对比模式 |
