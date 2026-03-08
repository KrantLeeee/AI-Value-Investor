## 5. 风险因素与辩证分析

**当前共识**: {{ consensus.direction }} (强度: {{ "%.0f"|format(consensus.strength * 100) }}%)
**辩证模式**: 挑战多头论点 (Bear Case)

### 论点质疑

{% for challenge in assumption_challenges %}
**{{ loop.index }}. 原始论断**: {{ challenge.original_claim }}
- **依赖假设**: {{ challenge.assumption }}
- **质疑理由**: {{ challenge.challenge }}
- **若假设不成立**: {{ challenge.impact_if_wrong }}
- **严重性**: {{ challenge.severity }}

{% endfor %}

### 下行风险场景

{% for scenario in risk_scenarios %}
**场景{{ loop.index }}**: {{ scenario.scenario }}
- 触发概率: {{ scenario.probability }}
- 预期影响: {{ scenario.impact }}
- 历史先例: {{ scenario.precedent }}

{% endfor %}

### 悲观目标价

{% if bear_case_target_price is defined and bear_case_target_price %}
基于风险场景，悲观估值目标: **¥{{ "%.2f"|format(bear_case_target_price) }}/股**
{% else %}
悲观目标价暂未测算。
{% endif %}

**综合论述**: {{ reasoning }}
