# Report Generator Restructuring - Design Document

> **Date**: 2026-03-07
> **Project**: AI Value Investment - P0-③ Report Generator
> **Status**: Approved

## Overview

Restructure the report generator from a single LLM call to a **chapter-by-chapter generation system** with validation, retry logic, and quality guarantees. This addresses the v1.0 core problem: "报告过短（1082字符），缺结构、缺风险章节、缺敏感性分析".

## Goals

1. **Structured 7-chapter reports** with guaranteed minimum lengths
2. **Quality validation** with automatic retry for failed chapters
3. **Integration of all agents** including the new Contrarian Agent (P0-②)
4. **Transparent data quality** via appendix section
5. **Cost efficiency**: ~$0.05/report (4 LLM calls instead of 1 large call)

## Non-Goals (Deferred)

- Async/parallel LLM calls (requires LLM router refactor)
- Multiple report templates (single template for MVP)
- PDF generation (Markdown only)
- Real-time streaming (batch generation)

---

## Architecture

### Approach: Jinja2 Template + Sequential Pipeline

**Selected Approach:** Approach 1 - Jinja2 templating with sequential chapter generation

**Rationale:**
- ✅ Matches existing codebase patterns (single-file agents)
- ✅ Simple to debug (sequential execution)
- ✅ Easy retry logic per chapter
- ✅ Natural error isolation
- ✅ Performance acceptable (20-30s vs 10s async - not critical for research reports)

### File Structure

```
src/agents/
  └── report_generator.py      (~450 lines, refactored)
templates/
  └── report_template.md        (~200 lines, new Jinja2 template)
  └── contrarian_templates/     (new directory)
      ├── bear_case.md          (Contrarian bear case template)
      ├── bull_case.md          (Contrarian bull case template)
      └── critical_questions.md (Contrarian questions template)
config/
  └── watchlist.yaml            (add industry_context field)
  └── llm_config.yaml           (add 4 new tasks)
tests/
  └── test_report_generator.py  (~15 tests)
```

### Chapter Configuration

Define metadata for all 8 sections:

```python
CHAPTERS = {
    "ch1_industry": {
        "title": "行业背景与公司概况",
        "type": "llm",
        "task_name": "report_ch1",
        "max_tokens": 1500,
        "temperature": 0.3,
        "min_words": 400,
        "required_terms": None,
        "max_retries": 2
    },
    "ch2_competitive": {
        "title": "竞争力分析",
        "type": "llm",
        "task_name": "report_ch2",
        "max_tokens": 2000,
        "temperature": 0.3,
        "min_words": 500,
        "required_terms": ["护城河", "竞争"],
        "max_retries": 2
    },
    "ch3_financial": {
        "title": "财务质量评估",
        "type": "code",
        "min_tables": 1,
    },
    "ch4_valuation": {
        "title": "估值分析与敏感性测试",
        "type": "code",
        "min_tables": 2,  # DCF table + sensitivity matrix
    },
    "ch5_risks": {
        "title": "风险因素与辩证分析",
        "type": "contrarian_template",
        "min_scenarios": 1,
    },
    "ch6_sentiment": {
        "title": "市场情绪与舆情分析",
        "type": "llm",
        "task_name": "report_ch6",
        "max_tokens": 800,
        "temperature": 0.3,
        "min_words": 200,
        "required_terms": None,
        "max_retries": 2
    },
    "ch7_recommendation": {
        "title": "综合建议与投资决策",
        "type": "llm",
        "task_name": "report_ch7",
        "max_tokens": 1500,
        "temperature": 0.3,
        "min_words": 300,
        "required_terms": ["推荐", "目标价"],
        "max_retries": 2
    },
    "appendix": {
        "title": "附录：数据质量与技术说明",
        "type": "code",
    }
}
```

---

## Data Flow

### Input Pipeline

```
registry.run_all_agents()
    ↓
Collects:
  - signals: dict[str, AgentSignal]
      ├── fundamentals
      ├── valuation
      ├── warren_buffett
      ├── ben_graham
      ├── sentiment
      └── contrarian (NEW from P0-②)
  - quality_report: QualityReport (from P0-①)
  - ticker, market, analysis_date
    ↓
report_generator.run(ticker, market, signals, quality_report, use_llm=True)
    ↓
Generate 8 chapters sequentially
    ↓
Render Jinja2 template
    ↓
Save to output/reports/{ticker}_{date}.md
```

### Chapter Generation Functions

**1. LLM Chapters** (Ch1, Ch2, Ch6, Ch7):

```python
def _generate_llm_chapter(
    chapter_key: str,
    ticker: str,
    market: str,
    signals: dict[str, AgentSignal],
    quality_report: QualityReport,
    industry_context: str,
) -> str:
    """
    Generate a single LLM chapter with validation and retry.

    Returns:
        Chapter markdown text (with warning marker if validation failed)
    """
    config = CHAPTERS[chapter_key]

    # Build prompts (chapter-specific)
    system_prompt = _build_system_prompt(chapter_key)
    user_prompt = _build_user_prompt(chapter_key, ticker, market, signals,
                                      quality_report, industry_context)

    # Retry loop
    for attempt in range(config["max_retries"] + 1):
        text = call_llm(config["task_name"], system_prompt, user_prompt)
        issues = _validate_chapter(text, config)

        if not issues:
            logger.info(f"[Report] {chapter_key} passed validation")
            return text

        if attempt < config["max_retries"]:
            logger.warning(f"[Report] {chapter_key} retry {attempt+1}: {issues}")
            user_prompt += f"\n\n[重试要求] 上次输出未通过验证: {', '.join(issues)}"

    # Failed after all retries
    logger.error(f"[Report] {chapter_key} validation failed after retries")
    return text + f"\n\n> ⚠️ 质量验证未通过: {', '.join(issues)}"
```

**2. Code Chapters** (Ch3, Ch4, Appendix):

```python
def _generate_code_chapter(
    chapter_key: str,
    ticker: str,
    signals: dict[str, AgentSignal],
    quality_report: QualityReport,
) -> str:
    """Generate pure-code chapters (tables, no LLM)."""

    if chapter_key == "ch3_financial":
        return _build_financial_quality_table(ticker, signals["fundamentals"],
                                               quality_report)

    elif chapter_key == "ch4_valuation":
        return _build_valuation_analysis(signals["valuation"])

    elif chapter_key == "appendix":
        return _build_appendix(signals, quality_report)

    else:
        raise ValueError(f"Unknown code chapter: {chapter_key}")
```

**3. Contrarian Template Chapter** (Ch5):

```python
def _generate_contrarian_chapter(contrarian_signal: AgentSignal) -> str:
    """Render Contrarian JSON output via Jinja2 templates."""

    if not contrarian_signal or not contrarian_signal.metrics:
        return "辩证分析暂不可用。请结合其他章节自行评估风险。"

    mode = contrarian_signal.metrics.get("mode")

    # Load mode-specific template
    template_path = f"templates/contrarian_templates/{mode}.md"
    with open(template_path) as f:
        template = Template(f.read())

    # Render with Contrarian JSON data
    return template.render(
        mode=mode,
        consensus=contrarian_signal.metrics.get("consensus"),
        reasoning=contrarian_signal.reasoning,
        **contrarian_signal.metrics  # All other mode-specific fields
    )
```

---

## Validation Rules

```python
def _validate_chapter(text: str, config: dict) -> list[str]:
    """
    Validate chapter against requirements.

    Returns:
        List of validation issues (empty if valid)
    """
    issues = []

    # Word count (Chinese character count)
    if config.get("min_words"):
        char_count = len(text.replace(" ", "").replace("\n", ""))
        if char_count < config["min_words"]:
            issues.append(f"字数不足（{char_count}/{config['min_words']}字）")

    # Required keywords
    if config.get("required_terms"):
        for term in config["required_terms"]:
            if term not in text:
                issues.append(f"缺少关键词：{term}")

    # Table count (for code chapters)
    if config.get("min_tables"):
        table_count = text.count("|")  # Simple heuristic
        if table_count < config["min_tables"] * 3:  # 3 pipes per table row
            issues.append(f"数据表不足")

    return issues
```

---

## Error Handling

### Failure Strategies

**1. Chapter Generation Failure:**

Each chapter wrapped in try-except:
```python
try:
    chapters[key] = _generate_chapter(key, ...)
except Exception as e:
    logger.error(f"[Report] {key} failed: {e}")
    chapters[key] = _fallback_chapter(key, e)
```

**Fallback content by chapter type:**

- **LLM chapters**: Placeholder with error notice + available data
  ```markdown
  ## 2. 竞争力分析

  ⚠️ LLM分析暂不可用（API超时）

  **可用数据：**
  - Buffett Agent: bearish (55% confidence)
  - Graham Agent: bullish (50% confidence)
  ```

- **Code chapters**: Minimal table or "数据不足" notice

- **Contrarian chapter**: "辩证分析暂不可用" notice

**2. Validation Failure After Retries:**

- Keep the best attempt
- Append warning: `> ⚠️ 质量验证未通过: {issues}`
- Log warning but don't block report

**3. Missing Industry Context:**

If `industry_context` not in `watchlist.yaml`:
```python
industry_context = (
    watchlist.get("industry_context") or
    f"{ticker}所属{sector}行业，基于现有数据推测行业特征..."
)
```
LLM infers from sector + signals.

**4. Template Rendering Failure (Critical):**

Fall back to existing `_quick_report()`:
```python
try:
    return template.render(chapters=chapters, metadata=metadata)
except Exception as e:
    logger.critical(f"Template rendering failed: {e}")
    return _quick_report(ticker, market, signals, analysis_date)
```

---

## Prompt Design

### Ch1: Industry Background (行业背景)

**System Prompt:**
```
你是行业研究分析师。基于公司所属行业和财务数据，撰写行业背景章节（≥400字）。

必须包含：
1. 行业现状（市场规模/增长趋势/竞争格局）
2. 行业驱动因素（政策/技术/需求）
3. 公司在行业中的定位

约束：
- 如用户提供了industry_context，直接使用并扩展
- 如未提供，从sector和财务数据推测
- 不做预测，仅陈述现状
```

**User Prompt:**
```
标的: {ticker} | 行业: {sector} | 细分: {sub_industry}

用户提供的行业背景:
{industry_context}

公司财务摘要:
- 营收规模: {revenue}
- 增长率: {growth_rate}
- 主要指标: {key_metrics}

请撰写行业背景与公司概况（≥400字）。
```

### Ch2: Competitive Analysis (竞争力分析)

**System Prompt:**
```
你是价值投资分析师。基于Buffett和Graham Agent的分析，撰写竞争力章节（≥500字）。

必须包含：
1. 护城河分析（品牌/成本/转换成本/网络效应/规模）
2. 竞争优势持续性
3. 管理层质量与资本配置能力

约束：
- 必须包含"护城河"或"竞争"关键词
- 引用Agent数据但不重复计算
- 明确指出优势与劣势
```

**User Prompt:**
```
**Buffett Agent分析:**
- 信号: {buffett_signal}
- 护城河: {moat_type}
- 管理层质量: {management_quality}
- 定价权: {has_pricing_power}
- 理由: {buffett_reasoning}

**Graham Agent分析:**
- 信号: {graham_signal}
- 通过标准: {graham_standards_passed}
- 理由: {graham_reasoning}

请撰写竞争力分析（≥500字，必须包含"护城河"或"竞争"）。
```

### Ch6: Market Sentiment (市场情绪)

**System Prompt:**
```
你是市场情绪分析师。基于Sentiment Agent结果，撰写市场情绪章节（≥200字）。

必须包含：
1. 当前舆情方向（正面/负面/中性）
2. 主要新闻来源与观点
3. 情绪对短期股价的影响

约束：
- 如无新闻数据，明确注明"暂无舆情数据"
- 区分基本面与情绪
```

### Ch7: Investment Recommendation (综合建议)

**System Prompt:**
```
你是投资决策分析师。综合所有Agent信号，给出明确投资建议（≥300字）。

必须包含：
1. 综合评估（基本面+估值+竞争力+风险+情绪）
2. 明确推荐：买入/等待/观望
3. 目标价区间（基于DCF±敏感性）
4. 风险提示

约束：
- 必须包含"推荐"和"目标价"关键词
- 如Agent信号冲突，明确说明分歧
- 最后一行：**综合信号: [BULLISH/NEUTRAL/BEARISH] | 置信度: [0.XX]**
```

---

## LLM Configuration

Add to `config/llm_config.yaml`:

```yaml
task_routing:
  # ... existing tasks ...

  report_ch1:
    model: gpt-4o
    max_tokens: 1500
    temperature: 0.3
    description: "Industry background and company overview chapter"

  report_ch2:
    model: gpt-4o
    max_tokens: 2000
    temperature: 0.3
    description: "Competitive analysis chapter with moat evaluation"

  report_ch6:
    model: gpt-4o
    max_tokens: 800
    temperature: 0.3
    description: "Market sentiment and news analysis chapter"

  report_ch7:
    model: gpt-4o
    max_tokens: 1500
    temperature: 0.3
    description: "Investment recommendation and final verdict chapter"
```

**Cost Estimation:**
- Ch1: ~500 tokens output → $0.0075
- Ch2: ~700 tokens output → $0.0105
- Ch6: ~300 tokens output → $0.0045
- Ch7: ~500 tokens output → $0.0075
- **Total: ~$0.03-0.05/report** (acceptable per roadmap)

---

## Contrarian Templates

### Bear Case Template (`templates/contrarian_templates/bear_case.md`)

```jinja2
## 风险因素与辩证分析

**当前共识**: {{ consensus.direction }} ({{ consensus.strength }}强度)
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

基于风险场景，悲观估值目标: **¥{{ bear_case_target_price }}/股**

**综合论述**: {{ reasoning }}
```

### Bull Case Template (`templates/contrarian_templates/bull_case.md`)

```jinja2
## 风险因素与辩证分析

**当前共识**: {{ consensus.direction }} ({{ consensus.strength }}强度)
**辩证模式**: 寻找被忽视的上行机会 (Bull Case)

### 被忽视的正面因素

{% for positive in overlooked_positives %}
**{{ loop.index }}. {{ positive.factor }}**
- 具体描述: {{ positive.description }}
- 潜在影响: {{ positive.potential_impact }}

{% endfor %}

### 悲观情绪定价分析

{{ priced_in_analysis }}

### 生存优势

{{ survival_advantage }}

### 乐观目标价

基于上行催化剂，乐观估值目标: **¥{{ bull_case_target_price }}/股**

**综合论述**: {{ reasoning }}
```

### Critical Questions Template (`templates/contrarian_templates/critical_questions.md`)

```jinja2
## 风险因素与辩证分析

**当前共识**: {{ consensus.direction }} ({{ consensus.strength }}强度)
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
```

---

## Main Report Template

`templates/report_template.md`:

```jinja2
# {{ ticker }} 投资研究报告

**报告日期**: {{ analysis_date }}
**市场**: {{ market }}
**数据质量评分**: {{ quality_score }}/1.0

---

{{ ch1_industry }}

---

{{ ch2_competitive }}

---

{{ ch3_financial }}

---

{{ ch4_valuation }}

---

{{ ch5_risks }}

---

{{ ch6_sentiment }}

---

{{ ch7_recommendation }}

---

{{ appendix }}

---

*本报告由AI Value Investor自动生成，仅供参考，不构成投资建议。*
```

---

## Testing Strategy

### Unit Tests (~15 tests)

**Validation Tests** (5 tests):
```python
def test_validate_chapter_min_words_pass()
def test_validate_chapter_min_words_fail()
def test_validate_chapter_required_terms_pass()
def test_validate_chapter_required_terms_fail()
def test_validate_chapter_no_requirements()
```

**Code Chapter Tests** (3 tests):
```python
def test_build_financial_quality_table()
def test_build_valuation_analysis()
def test_build_appendix()
```

**Contrarian Template Tests** (3 tests):
```python
def test_contrarian_bear_case_template()
def test_contrarian_bull_case_template()
def test_contrarian_critical_questions_template()
```

**Error Handling Tests** (3 tests):
```python
def test_chapter_generation_fallback()
def test_missing_industry_context()
def test_template_rendering_fallback()
```

**Integration Test** (1 test):
```python
@patch('src.agents.report_generator.call_llm')
def test_full_report_generation(mock_llm):
    """Full pipeline with mocked LLM calls."""
    # Mock LLM returns valid chapters
    mock_llm.return_value = "有效的章节内容..." * 50

    report, path = run(ticker="601808.SH", market="a_share",
                       signals=mock_signals, quality_report=mock_quality)

    # Verify all 8 sections present
    assert "## 1. 行业背景" in report
    assert "## 2. 竞争力分析" in report
    # ... assert all 8 chapters
    assert len(report) > 2000  # Minimum total length
```

### Manual Testing Checklist

Run `poetry run invest report -t 601808.SH`:

- [ ] Report has all 8 chapters with correct titles
- [ ] Ch1 includes industry context from watchlist.yaml (or inferred)
- [ ] Ch2 contains "护城河" or "竞争" keyword
- [ ] Ch3 shows financial quality tables
- [ ] Ch4 shows DCF table + sensitivity matrix
- [ ] Ch5 renders Contrarian content (bear/bull/questions based on consensus)
- [ ] Ch6 includes sentiment analysis
- [ ] Ch7 includes "推荐" and "目标价"
- [ ] Ch7 ends with `**综合信号: XXX | 置信度: X.XX**`
- [ ] Appendix shows all agent signals + quality report
- [ ] Total length 2000-3000 words
- [ ] No visible errors or empty chapters
- [ ] File saved to `output/reports/601808_SH_2026-03-07.md`

---

## Migration Path

### Backward Compatibility

**Quick mode (`--quick`)**: Keep existing `_quick_report()` function unchanged.

**LLM mode**: New chapter-by-chapter implementation replaces single LLM call.

### Implementation Order

1. **Phase 1**: Add Jinja2 templates + chapter config (no behavior change yet)
2. **Phase 2**: Implement code chapters (Ch3, Ch4, Appendix)
3. **Phase 3**: Implement Contrarian template chapter (Ch5)
4. **Phase 4**: Implement LLM chapter generation with validation (Ch1, Ch2, Ch6, Ch7)
5. **Phase 5**: Wire all chapters into main `run()` function
6. **Phase 6**: Test and validate

### Rollback Plan

If critical issues found:
- Keep new code but add feature flag: `USE_NEW_REPORT_GENERATOR = False`
- Fall back to single LLM call if flag disabled
- Remove flag after 2 weeks of stable operation

---

## Success Metrics

**Functional Requirements:**
- ✅ All 8 chapters present in every report
- ✅ Ch1, Ch2, Ch6, Ch7 pass validation ≥90% of time (no retries needed)
- ✅ Ch3, Ch4 contain required tables
- ✅ Ch5 renders Contrarian content correctly for all 3 modes
- ✅ Total report length 2000-3000 words

**Quality Requirements:**
- ✅ No empty chapters (all have fallback content)
- ✅ LLM failures don't block report generation
- ✅ Validation warnings visible to user

**Performance Requirements:**
- ✅ Report generation completes in <60s (acceptable for batch workflow)
- ✅ Cost per report <$0.10 (target: $0.03-0.05)

---

## Open Questions & Future Work

**Deferred to Post-MVP:**
1. **Parallel LLM calls**: Requires async LLM router refactor (P1 consideration)
2. **PDF generation**: Markdown-to-PDF conversion (P2)
3. **Multiple templates**: Industry-specific report templates (P2)
4. **Historical comparison**: Compare current report to previous reports (P3)
5. **Chart generation**: Add Python-generated charts to Ch3/Ch4 (P2)

**Known Limitations:**
- Industry context requires manual entry in watchlist.yaml (acceptable for MVP)
- Word count validation uses character count (not semantic quality)
- Template rendering failure falls back to quick report (loses LLM insights)

---

**Design Version**: 1.0
**Author**: Claude Sonnet 4.5
**Approved By**: User (2026-03-07)
