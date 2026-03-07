## 5. 风险因素与辩证分析

**当前共识**: {{ consensus.direction }} (强度: {{ "%.0f"|format(consensus.strength * 100) }}%)
**辩证模式**: 识别关键不确定性 (Critical Questions)

### 核心矛盾

{{ core_contradiction }}

### 关键问题

{% for q in questions %}
**问题{{ loop.index }}**: {{ q.question }}
- 初步判断: {{ q.preliminary_judgment }}
- 所需证据: {{ q.evidence_needed }}

{% endfor %}

**综合论述**: {{ reasoning }}
