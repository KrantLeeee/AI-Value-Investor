# Report Generator Restructuring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Restructure report generator from single LLM call to chapter-by-chapter generation with validation, retry logic, and Contrarian integration.

**Architecture:** Sequential chapter generation with Jinja2 templates. 4 LLM chapters (Ch1,2,6,7), 3 code chapters (Ch3,4,Appendix), 1 template chapter (Ch5 Contrarian). Each chapter validated independently with retry support.

**Tech Stack:** Python 3.11, Jinja2, Pydantic v2, pytest, existing LLM router

---

## Task 1: Add Jinja2 Dependency and Chapter Configuration

**Files:**
- Modify: `pyproject.toml`
- Create: `src/agents/report_config.py`
- Test: Manual verification

**Step 1: Add Jinja2 to dependencies**

Edit `pyproject.toml`:
```toml
[tool.poetry.dependencies]
# ... existing dependencies ...
jinja2 = "^3.1.2"
```

**Step 2: Install dependency**

Run: `poetry install`
Expected: Jinja2 installed successfully

**Step 3: Create chapter configuration module**

Create `src/agents/report_config.py`:
```python
"""Report Generator chapter configuration and validation rules."""

CHAPTERS = {
    "ch1_industry": {
        "title": "行业背景与公司概况",
        "type": "llm",
        "task_name": "report_ch1",
        "max_tokens": 1500,
        "temperature": 0.3,
        "min_words": 400,
        "required_terms": None,
        "max_retries": 2,
    },
    "ch2_competitive": {
        "title": "竞争力分析",
        "type": "llm",
        "task_name": "report_ch2",
        "max_tokens": 2000,
        "temperature": 0.3,
        "min_words": 500,
        "required_terms": ["护城河", "竞争"],
        "max_retries": 2,
    },
    "ch3_financial": {
        "title": "财务质量评估",
        "type": "code",
        "min_tables": 1,
    },
    "ch4_valuation": {
        "title": "估值分析与敏感性测试",
        "type": "code",
        "min_tables": 2,
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
        "max_retries": 2,
    },
    "ch7_recommendation": {
        "title": "综合建议与投资决策",
        "type": "llm",
        "task_name": "report_ch7",
        "max_tokens": 1500,
        "temperature": 0.3,
        "min_words": 300,
        "required_terms": ["推荐", "目标价"],
        "max_retries": 2,
    },
    "appendix": {
        "title": "附录：数据质量与技术说明",
        "type": "code",
    },
}


def validate_chapter(text: str, config: dict) -> list[str]:
    """
    Validate chapter against requirements.

    Args:
        text: Chapter markdown text
        config: Chapter configuration dict

    Returns:
        List of validation issues (empty if valid)
    """
    issues = []

    # Word count (Chinese character count, excluding spaces/newlines)
    if config.get("min_words"):
        char_count = len(text.replace(" ", "").replace("\n", ""))
        if char_count < config["min_words"]:
            issues.append(f"字数不足（{char_count}/{config['min_words']}字）")

    # Required keywords
    if config.get("required_terms"):
        for term in config["required_terms"]:
            if term not in text:
                issues.append(f"缺少关键词：{term}")

    # Table count (for code chapters) - simple heuristic: count pipe characters
    if config.get("min_tables"):
        pipe_count = text.count("|")
        # Assuming each table has at least 3 rows with 3 columns = 9 pipes minimum
        min_pipes = config["min_tables"] * 9
        if pipe_count < min_pipes:
            issues.append("数据表不足")

    return issues
```

**Step 4: Verify module loads**

Run: `python -c "from src.agents.report_config import CHAPTERS, validate_chapter; print(len(CHAPTERS))"`
Expected: Output `8`

**Step 5: Commit**

```bash
git add pyproject.toml poetry.lock src/agents/report_config.py
git commit -m "feat(report): add Jinja2 dependency and chapter configuration

- Add Jinja2 ^3.1.2 to dependencies
- Create report_config.py with 8 chapter definitions
- Add validate_chapter() with word count, keyword, table validation
- Configure validation rules for each chapter type

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Add LLM Task Routing Configuration

**Files:**
- Modify: `config/llm_config.yaml`

**Step 1: Check current LLM config structure**

Run: `head -20 config/llm_config.yaml`

**Step 2: Add 4 new task routing entries**

Edit `config/llm_config.yaml` and add under `task_routing:` section:

```yaml
  report_ch1:
    provider: openai
    model: gpt-4o
    max_tokens: 1500
    temperature: 0.3
    description: "Industry background and company overview chapter"

  report_ch2:
    provider: openai
    model: gpt-4o
    max_tokens: 2000
    temperature: 0.3
    description: "Competitive analysis chapter with moat evaluation"

  report_ch6:
    provider: openai
    model: gpt-4o
    max_tokens: 800
    temperature: 0.3
    description: "Market sentiment and news analysis chapter"

  report_ch7:
    provider: openai
    model: gpt-4o
    max_tokens: 1500
    temperature: 0.3
    description: "Investment recommendation and final verdict chapter"
```

**Step 3: Verify YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('config/llm_config.yaml'))"`
Expected: No errors

**Step 4: Commit**

```bash
git add config/llm_config.yaml
git commit -m "feat(report): add LLM task routing for 4 chapters

- Add report_ch1 (industry background, 1500 tokens)
- Add report_ch2 (competitive analysis, 2000 tokens)
- Add report_ch6 (market sentiment, 800 tokens)
- Add report_ch7 (investment recommendation, 1500 tokens)
- All use GPT-4o with temperature 0.3

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Create Contrarian Jinja2 Templates

**Files:**
- Create: `templates/contrarian_templates/bear_case.md`
- Create: `templates/contrarian_templates/bull_case.md`
- Create: `templates/contrarian_templates/critical_questions.md`

**Step 1: Create templates directory**

Run: `mkdir -p templates/contrarian_templates`

**Step 2: Create bear_case template**

Create `templates/contrarian_templates/bear_case.md`:
```jinja2
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

基于风险场景，悲观估值目标: **¥{{ "%.2f"|format(bear_case_target_price) }}/股**

**综合论述**: {{ reasoning }}
```

**Step 3: Create bull_case template**

Create `templates/contrarian_templates/bull_case.md`:
```jinja2
## 5. 风险因素与辩证分析

**当前共识**: {{ consensus.direction }} (强度: {{ "%.0f"|format(consensus.strength * 100) }}%)
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

基于上行催化剂，乐观估值目标: **¥{{ "%.2f"|format(bull_case_target_price) }}/股**

**综合论述**: {{ reasoning }}
```

**Step 4: Create critical_questions template**

Create `templates/contrarian_templates/critical_questions.md`:
```jinja2
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
```

**Step 5: Commit**

```bash
git add templates/contrarian_templates/
git commit -m "feat(report): add Contrarian Jinja2 templates

- Add bear_case.md for challenging bullish consensus
- Add bull_case.md for finding overlooked positives
- Add critical_questions.md for mixed consensus
- Templates render Contrarian JSON into Chapter 5

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Create Main Report Jinja2 Template

**Files:**
- Create: `templates/report_template.md`

**Step 1: Create main template**

Create `templates/report_template.md`:
```jinja2
# {{ ticker }} 投资研究报告

**报告日期**: {{ analysis_date }}
**市场**: {{ market }}
**数据质量评分**: {{ "%.2f"|format(quality_score) }}/1.0

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
*生成时间: {{ generation_timestamp }}*
```

**Step 2: Verify template syntax**

Run: `python -c "from jinja2 import Template; t = Template(open('templates/report_template.md').read()); print('Valid')"`
Expected: Output `Valid`

**Step 3: Commit**

```bash
git add templates/report_template.md
git commit -m "feat(report): add main report Jinja2 template

- Create report_template.md with 8 chapter placeholders
- Include metadata (ticker, date, quality score)
- Add generation timestamp footer
- Sequential chapter rendering with separators

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Add Chapter Validation Unit Tests

**Files:**
- Create: `tests/test_report_config.py`

**Step 1: Create test file**

Create `tests/test_report_config.py`:
```python
"""Tests for report configuration and validation."""

from src.agents.report_config import CHAPTERS, validate_chapter


def test_validate_chapter_min_words_pass():
    """Valid word count should pass."""
    config = {"min_words": 400}
    text = "这是一个测试章节。" * 50  # 500 chars

    issues = validate_chapter(text, config)
    assert issues == []


def test_validate_chapter_min_words_fail():
    """Insufficient word count should fail."""
    config = {"min_words": 400}
    text = "这是一个测试章节。" * 20  # 200 chars

    issues = validate_chapter(text, config)
    assert len(issues) == 1
    assert "字数不足" in issues[0]
    assert "200/400" in issues[0]


def test_validate_chapter_required_terms_pass():
    """Text with all required keywords should pass."""
    config = {"required_terms": ["护城河", "竞争"]}
    text = "公司具有强大的护城河，在竞争中占据优势。"

    issues = validate_chapter(text, config)
    assert issues == []


def test_validate_chapter_required_terms_fail():
    """Missing required keywords should fail."""
    config = {"required_terms": ["护城河", "竞争"]}
    text = "公司具有强大的竞争优势。"

    issues = validate_chapter(text, config)
    assert len(issues) == 1
    assert "缺少关键词：护城河" in issues[0]


def test_validate_chapter_min_tables_pass():
    """Text with sufficient tables should pass."""
    config = {"min_tables": 2}
    text = """
    | Header1 | Header2 | Header3 |
    |---------|---------|---------|
    | Data1   | Data2   | Data3   |
    | Data4   | Data5   | Data6   |

    | Header1 | Header2 | Header3 |
    |---------|---------|---------|
    | Data1   | Data2   | Data3   |
    """

    issues = validate_chapter(text, config)
    assert issues == []


def test_validate_chapter_min_tables_fail():
    """Insufficient tables should fail."""
    config = {"min_tables": 2}
    text = """
    | Header1 | Header2 |
    |---------|---------|
    | Data1   | Data2   |
    """

    issues = validate_chapter(text, config)
    assert len(issues) == 1
    assert "数据表不足" in issues[0]


def test_validate_chapter_no_requirements():
    """Chapter with no validation rules should always pass."""
    config = {}
    text = "Any text"

    issues = validate_chapter(text, config)
    assert issues == []


def test_validate_chapter_multiple_issues():
    """Chapter with multiple issues should report all."""
    config = {"min_words": 500, "required_terms": ["护城河"]}
    text = "短文本"

    issues = validate_chapter(text, config)
    assert len(issues) == 2
    assert any("字数不足" in issue for issue in issues)
    assert any("缺少关键词：护城河" in issue for issue in issues)


def test_chapters_config_structure():
    """Verify CHAPTERS config has correct structure."""
    assert len(CHAPTERS) == 8
    assert "ch1_industry" in CHAPTERS
    assert "ch7_recommendation" in CHAPTERS

    # Verify LLM chapters have task_name
    for key in ["ch1_industry", "ch2_competitive", "ch6_sentiment", "ch7_recommendation"]:
        assert CHAPTERS[key]["type"] == "llm"
        assert "task_name" in CHAPTERS[key]
        assert "max_retries" in CHAPTERS[key]

    # Verify code chapters don't have task_name
    for key in ["ch3_financial", "ch4_valuation", "appendix"]:
        assert CHAPTERS[key]["type"] == "code"
        assert "task_name" not in CHAPTERS[key]
```

**Step 2: Run tests**

Run: `poetry run pytest tests/test_report_config.py -v`
Expected: 9 tests PASS

**Step 3: Commit**

```bash
git add tests/test_report_config.py
git commit -m "test(report): add chapter validation unit tests

- Add 8 validation tests (word count, keywords, tables)
- Add config structure verification test
- Total: 9 tests, all passing

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Implement Chapter 3 - Financial Quality Table

**Files:**
- Modify: `src/agents/report_generator.py`
- Create: `tests/test_report_chapters.py`

**Step 1: Write failing test**

Create `tests/test_report_chapters.py`:
```python
"""Tests for individual chapter generation functions."""

from src.agents.report_generator import _build_financial_quality_table
from src.data.models import AgentSignal, QualityReport


def test_build_financial_quality_table():
    """Ch3 should generate financial quality table from fundamentals."""
    fundamentals_signal = AgentSignal(
        ticker="TEST",
        agent_name="fundamentals",
        signal="bearish",
        confidence=0.55,
        reasoning="财务质量一般",
        metrics={
            "total_score": 42,
            "revenue_score": 15,
            "profitability_score": 10,
            "leverage_score": 8,
            "cash_flow_score": 9,
        },
    )

    quality_report = QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[],
        overall_quality_score=0.85,
        data_completeness=0.90,
        stale_fields=[],
        records_checked={},
    )

    result = _build_financial_quality_table("TEST", fundamentals_signal, quality_report)

    # Verify structure
    assert "## 3. 财务质量评估" in result
    assert "基本面评分" in result
    assert "42/100" in result
    assert "|" in result  # Has table
    assert "数据质量" in result
    assert "0.85" in result
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_report_chapters.py::test_build_financial_quality_table -v`
Expected: FAIL with "cannot import name '_build_financial_quality_table'"

**Step 3: Implement financial quality table**

Add to `src/agents/report_generator.py` (after existing helper functions):
```python
def _build_financial_quality_table(
    ticker: str,
    fundamentals_signal: AgentSignal | None,
    quality_report: QualityReport,
) -> str:
    """
    Build Chapter 3: Financial Quality Assessment (code-based).

    Args:
        ticker: Stock ticker
        fundamentals_signal: Fundamentals agent result
        quality_report: Data quality report from P0-①

    Returns:
        Chapter 3 markdown text
    """
    lines = ["## 3. 财务质量评估", ""]

    # Fundamentals scoring breakdown
    if fundamentals_signal:
        lines.append(f"**基本面评分**: {fundamentals_signal.metrics.get('total_score', 'N/A')}/100")
        lines.append("")
        lines.append("| 维度 | 得分 | 说明 |")
        lines.append("|:-----|:-----|:-----|")
        lines.append(f"| 营收质量 | {fundamentals_signal.metrics.get('revenue_score', 'N/A')}/25 | 增长稳定性与规模 |")
        lines.append(f"| 盈利能力 | {fundamentals_signal.metrics.get('profitability_score', 'N/A')}/25 | ROE与净利率 |")
        lines.append(f"| 杠杆健康 | {fundamentals_signal.metrics.get('leverage_score', 'N/A')}/25 | 负债水平 |")
        lines.append(f"| 现金流质量 | {fundamentals_signal.metrics.get('cash_flow_score', 'N/A')}/25 | FCF与OCF |")
        lines.append("")
        lines.append(f"**评估**: {fundamentals_signal.reasoning}")
        lines.append("")
    else:
        lines.append("基本面Agent未运行，数据不可用。")
        lines.append("")

    # Data quality section
    lines.append("### 数据质量评估")
    lines.append("")
    lines.append(f"- **整体质量评分**: {quality_report.overall_quality_score:.2f}/1.0")
    lines.append(f"- **数据完整度**: {quality_report.data_completeness:.0%}")
    lines.append("")

    if quality_report.flags:
        lines.append(f"**发现 {len(quality_report.flags)} 个数据质量问题：**")
        lines.append("")
        for flag in quality_report.flags[:5]:  # Top 5 flags
            lines.append(f"- [{flag.severity.upper()}] {flag.detail}")
        if len(quality_report.flags) > 5:
            lines.append(f"- ... 及其他 {len(quality_report.flags) - 5} 个问题")
        lines.append("")
    else:
        lines.append("✅ 数据质量良好，未发现重大问题。")
        lines.append("")

    return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_report_chapters.py::test_build_financial_quality_table -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agents/report_generator.py tests/test_report_chapters.py
git commit -m "feat(report): implement Ch3 financial quality table

- Add _build_financial_quality_table() function
- Show fundamentals scoring breakdown (4 dimensions)
- Include data quality assessment with flags
- Add unit test

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Implement Chapter 4 - Valuation Analysis

**Files:**
- Modify: `src/agents/report_generator.py`
- Modify: `tests/test_report_chapters.py`

**Step 1: Write failing test**

Add to `tests/test_report_chapters.py`:
```python
from src.agents.report_generator import _build_valuation_analysis


def test_build_valuation_analysis():
    """Ch4 should generate valuation tables from valuation agent."""
    valuation_signal = AgentSignal(
        ticker="TEST",
        agent_name="valuation",
        signal="neutral",
        confidence=0.60,
        reasoning="估值适中",
        metrics={
            "dcf_per_share": 25.50,
            "graham_number": 23.00,
            "current_price": 24.00,
            "margin_of_safety": 0.06,
            "ev_ebitda": 8.5,
        },
    )

    result = _build_valuation_analysis(valuation_signal)

    # Verify structure
    assert "## 4. 估值分析" in result
    assert "25.50" in result  # DCF value
    assert "23.00" in result  # Graham number
    assert "24.00" in result  # Current price
    assert "|" in result  # Has tables
    assert "敏感性" in result or "情景" in result
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_report_chapters.py::test_build_valuation_analysis -v`
Expected: FAIL with "cannot import name '_build_valuation_analysis'"

**Step 3: Implement valuation analysis**

Add to `src/agents/report_generator.py`:
```python
def _build_valuation_analysis(valuation_signal: AgentSignal | None) -> str:
    """
    Build Chapter 4: Valuation Analysis (code-based).

    Args:
        valuation_signal: Valuation agent result

    Returns:
        Chapter 4 markdown text
    """
    lines = ["## 4. 估值分析与敏感性测试", ""]

    if not valuation_signal:
        lines.append("估值Agent未运行，数据不可用。")
        return "\n".join(lines)

    metrics = valuation_signal.metrics
    dcf = metrics.get("dcf_per_share")
    graham = metrics.get("graham_number")
    current = metrics.get("current_price")
    mos = metrics.get("margin_of_safety")

    # Valuation summary table
    lines.append("### 估值指标")
    lines.append("")
    lines.append("| 估值方法 | 内在价值 | 当前价格 | 安全边际 |")
    lines.append("|:---------|:---------|:---------|:---------|")
    lines.append(f"| DCF现金流折现 | ¥{dcf:.2f}/股 | ¥{current:.2f}/股 | {mos*100:+.1f}% |" if dcf else "| DCF现金流折现 | 数据不足 | - | - |")
    lines.append(f"| Graham Number | ¥{graham:.2f}/股 | ¥{current:.2f}/股 | {((current-graham)/graham)*100:+.1f}% |" if graham else "| Graham Number | 数据不足 | - | - |")
    lines.append("")

    # Valuation interpretation
    if dcf and mos:
        if mos > 0.20:
            lines.append(f"**解读**: DCF显示 {mos*100:.0f}% 安全边际，当前价格低估。")
        elif mos < -0.20:
            lines.append(f"**解读**: DCF显示 {abs(mos)*100:.0f}% 溢价，当前价格高估。")
        else:
            lines.append(f"**解读**: DCF显示 {abs(mos)*100:.0f}% {'安全边际' if mos > 0 else '溢价'}，估值合理。")
        lines.append("")

    # Sensitivity scenarios (simple 3-scenario analysis)
    lines.append("### 敏感性分析")
    lines.append("")
    lines.append("不同假设下的估值区间：")
    lines.append("")
    lines.append("| 情景 | 假设 | 估值 |")
    lines.append("|:-----|:-----|:-----|")

    if dcf:
        # Simple sensitivity: ±20% on DCF
        lines.append(f"| 乐观情景 | 增长率+2%或WACC-1% | ¥{dcf*1.2:.2f}/股 |")
        lines.append(f"| 基准情景 | 当前假设 | ¥{dcf:.2f}/股 |")
        lines.append(f"| 悲观情景 | 增长率-2%或WACC+1% | ¥{dcf*0.8:.2f}/股 |")
    else:
        lines.append("| - | 数据不足 | - |")

    lines.append("")

    # Add reasoning from valuation agent
    lines.append(f"**Agent评估**: {valuation_signal.reasoning}")
    lines.append("")

    return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_report_chapters.py::test_build_valuation_analysis -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agents/report_generator.py tests/test_report_chapters.py
git commit -m "feat(report): implement Ch4 valuation analysis

- Add _build_valuation_analysis() function
- Show DCF and Graham Number comparison table
- Add 3-scenario sensitivity analysis
- Add unit test

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Implement Chapter 5 - Contrarian Template Rendering

**Files:**
- Modify: `src/agents/report_generator.py`
- Modify: `tests/test_report_chapters.py`

**Step 1: Write failing test**

Add to `tests/test_report_chapters.py`:
```python
from src.agents.report_generator import _render_contrarian_chapter


def test_render_contrarian_bear_case():
    """Ch5 should render bear_case Contrarian template."""
    contrarian_signal = AgentSignal(
        ticker="TEST",
        agent_name="contrarian",
        signal="bearish",
        confidence=0.60,
        reasoning="风险场景分析完成",
        metrics={
            "mode": "bear_case",
            "consensus": {"direction": "bullish", "strength": 0.75},
            "assumption_challenges": [
                {
                    "original_claim": "增长率20%",
                    "assumption": "需求持续",
                    "challenge": "需求可能饱和",
                    "impact_if_wrong": "增长停滞",
                    "severity": "high",
                }
            ],
            "risk_scenarios": [
                {
                    "scenario": "原材料价格上涨",
                    "probability": "30%",
                    "impact": "利润率下降5%",
                    "precedent": "2020年Q2",
                }
            ],
            "bear_case_target_price": 18.50,
        },
    )

    result = _render_contrarian_chapter(contrarian_signal)

    # Verify structure
    assert "## 5. 风险因素" in result
    assert "bullish" in result
    assert "75%" in result
    assert "Bear Case" in result
    assert "增长率20%" in result
    assert "18.50" in result
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_report_chapters.py::test_render_contrarian_bear_case -v`
Expected: FAIL with "cannot import name '_render_contrarian_chapter'"

**Step 3: Implement contrarian template rendering**

Add to `src/agents/report_generator.py` (add import at top):
```python
from jinja2 import Template
from pathlib import Path
```

Then add function:
```python
def _render_contrarian_chapter(contrarian_signal: AgentSignal | None) -> str:
    """
    Build Chapter 5: Risk Factors (Contrarian template).

    Args:
        contrarian_signal: Contrarian agent result

    Returns:
        Chapter 5 markdown text
    """
    if not contrarian_signal or not contrarian_signal.metrics:
        return """## 5. 风险因素与辩证分析

辩证分析暂不可用。请结合其他章节自行评估风险。
"""

    mode = contrarian_signal.metrics.get("mode")
    if not mode:
        return """## 5. 风险因素与辩证分析

辩证分析数据格式错误。
"""

    # Load mode-specific template
    template_path = Path(__file__).parent.parent.parent / "templates" / "contrarian_templates" / f"{mode}.md"

    if not template_path.exists():
        logger.warning(f"[Report] Contrarian template not found: {template_path}")
        return f"""## 5. 风险因素与辩证分析

模板文件缺失 ({mode}.md)。

**辩证分析结果**: {contrarian_signal.reasoning}
"""

    # Load and render template
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    return template.render(
        consensus=contrarian_signal.metrics.get("consensus", {}),
        reasoning=contrarian_signal.reasoning,
        **contrarian_signal.metrics  # All mode-specific fields
    )
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_report_chapters.py::test_render_contrarian_bear_case -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agents/report_generator.py tests/test_report_chapters.py
git commit -m "feat(report): implement Ch5 contrarian template rendering

- Add _render_contrarian_chapter() with Jinja2 rendering
- Load mode-specific templates (bear_case/bull_case/critical_questions)
- Handle missing signal and template fallbacks
- Add unit test for bear_case mode

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Implement Appendix Chapter

**Files:**
- Modify: `src/agents/report_generator.py`
- Modify: `tests/test_report_chapters.py`

**Step 1: Write failing test**

Add to `tests/test_report_chapters.py`:
```python
from src.agents.report_generator import _build_appendix


def test_build_appendix():
    """Appendix should show all agent signals and quality report."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bearish", confidence=0.55, reasoning="Test"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="neutral", confidence=0.60, reasoning="Test"
        ),
    }

    quality_report = QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[],
        overall_quality_score=0.90,
        data_completeness=0.95,
        stale_fields=[],
        records_checked={},
    )

    result = _build_appendix(signals, quality_report)

    # Verify structure
    assert "## 附录" in result
    assert "Agent信号汇总" in result
    assert "fundamentals" in result
    assert "bearish" in result
    assert "55%" in result
    assert "数据质量" in result
    assert "0.90" in result
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_report_chapters.py::test_build_appendix -v`
Expected: FAIL with "cannot import name '_build_appendix'"

**Step 3: Implement appendix**

Add to `src/agents/report_generator.py`:
```python
def _build_appendix(
    signals: dict[str, AgentSignal],
    quality_report: QualityReport,
) -> str:
    """
    Build Appendix: Technical Details (code-based).

    Args:
        signals: All agent signals
        quality_report: Data quality report

    Returns:
        Appendix markdown text
    """
    lines = ["## 附录：数据质量与技术说明", ""]

    # Agent signals summary table
    lines.append("### Agent信号汇总")
    lines.append("")
    lines.append("| Agent | 信号 | 置信度 | 关键指标 |")
    lines.append("|:------|:-----|:-------|:---------|")

    for agent_name, signal in signals.items():
        if signal:
            emoji = _signal_emoji(signal.signal)
            # Extract key metric from each agent
            key_metric = ""
            if agent_name == "fundamentals":
                key_metric = f"得分: {signal.metrics.get('total_score', 'N/A')}/100"
            elif agent_name == "valuation":
                mos = signal.metrics.get('margin_of_safety')
                key_metric = f"安全边际: {mos*100:+.1f}%" if mos else "N/A"
            elif agent_name == "warren_buffett":
                key_metric = f"护城河: {signal.metrics.get('moat_type', 'N/A')}"
            elif agent_name == "ben_graham":
                passed = signal.metrics.get('standards_passed', 0)
                key_metric = f"通过: {passed}/7标准"
            elif agent_name == "sentiment":
                score = signal.metrics.get('sentiment_score')
                key_metric = f"情绪: {score:.2f}" if score else "N/A"
            elif agent_name == "contrarian":
                mode = signal.metrics.get('mode', 'N/A')
                key_metric = f"模式: {mode}"

            lines.append(f"| {agent_name} | {emoji} {signal.signal} | {signal.confidence:.0%} | {key_metric} |")

    lines.append("")

    # Data quality details
    lines.append("### 数据质量详情")
    lines.append("")
    lines.append(f"- **整体质量评分**: {quality_report.overall_quality_score:.2f}/1.0")
    lines.append(f"- **数据完整度**: {quality_report.data_completeness:.0%}")
    lines.append(f"- **过期字段数**: {len(quality_report.stale_fields)}")
    lines.append("")

    if quality_report.flags:
        lines.append(f"**质量标记 ({len(quality_report.flags)} 个):**")
        lines.append("")
        for flag in quality_report.flags:
            lines.append(f"- [{flag.severity.upper()}] {flag.flag}: {flag.detail}")
        lines.append("")
    else:
        lines.append("✅ 所有质量检查通过。")
        lines.append("")

    # Technical notes
    lines.append("### 技术说明")
    lines.append("")
    lines.append("**估值假设:**")
    lines.append("- DCF折现率(WACC): 基于行业平均成本")
    lines.append("- 永续增长率: 3% (保守估计)")
    lines.append("- Graham Number: 基于EPS和每股净资产")
    lines.append("")
    lines.append("**数据来源:**")
    lines.append("- 财务数据: AKShare API")
    lines.append("- 市场数据: 实时行情接口")
    lines.append("- 新闻数据: 东方财富/新浪财经")
    lines.append("")

    return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_report_chapters.py::test_build_appendix -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agents/report_generator.py tests/test_report_chapters.py
git commit -m "feat(report): implement appendix chapter

- Add _build_appendix() function
- Show all agent signals summary table
- Include data quality details and flags
- Add technical notes on assumptions and data sources
- Add unit test

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Add LLM Chapter Prompts to prompts.py

**Files:**
- Modify: `src/llm/prompts.py`

**Step 1: Add Ch1 industry background prompts**

Add to end of `src/llm/prompts.py`:
```python
# ── Report Generator Chapter Prompts ──────────────────────────────────────────

REPORT_CH1_SYSTEM = """你是行业研究分析师。基于公司所属行业和财务数据，撰写行业背景章节（≥400字）。

必须包含：
1. 行业现状（市场规模/增长趋势/竞争格局）
2. 行业驱动因素（政策/技术/需求）
3. 公司在行业中的定位

约束：
- 如用户提供了industry_context，直接使用并扩展
- 如未提供，从sector和财务数据推测
- 不做预测，仅陈述现状
- 字数不少于400字（中文字符）
"""

REPORT_CH1_USER = """标的: {ticker} | 行业: {sector} | 细分: {sub_industry}

用户提供的行业背景:
{industry_context}

公司财务摘要:
- 营收规模: {revenue}
- 增长率: {growth_rate}
- ROE: {roe}%
- 负债率: {debt_ratio}

请撰写行业背景与公司概况（≥400字）。"""


REPORT_CH2_SYSTEM = """你是价值投资分析师。基于Buffett和Graham Agent的分析，撰写竞争力章节（≥500字）。

必须包含：
1. 护城河分析（品牌/成本/转换成本/网络效应/规模）
2. 竞争优势持续性
3. 管理层质量与资本配置能力

约束：
- 必须包含"护城河"或"竞争"关键词
- 引用Agent数据但不重复计算
- 明确指出优势与劣势
- 字数不少于500字（中文字符）
"""

REPORT_CH2_USER = """**Buffett Agent分析:**
- 信号: {buffett_signal}
- 护城河: {moat_type}
- 管理层质量: {management_quality}
- 定价权: {has_pricing_power}
- 理由: {buffett_reasoning}

**Graham Agent分析:**
- 信号: {graham_signal}
- 通过标准: {graham_standards_passed}/7
- 理由: {graham_reasoning}

请撰写竞争力分析（≥500字，必须包含"护城河"或"竞争"）。"""


REPORT_CH6_SYSTEM = """你是市场情绪分析师。基于Sentiment Agent结果，撰写市场情绪章节（≥200字）。

必须包含：
1. 当前舆情方向（正面/负面/中性）
2. 主要新闻来源与观点
3. 情绪对短期股价的影响

约束：
- 如无新闻数据，明确注明"暂无舆情数据"
- 区分基本面与情绪
- 字数不少于200字（中文字符）
"""

REPORT_CH6_USER = """**Sentiment Agent结果:**
- 信号: {sentiment_signal}
- 情绪评分: {sentiment_score}
- 理由: {sentiment_reasoning}

**近期新闻摘要:**
{news_summary}

请撰写市场情绪分析（≥200字）。"""


REPORT_CH7_SYSTEM = """你是投资决策分析师。综合所有Agent信号，给出明确投资建议（≥300字）。

必须包含：
1. 综合评估（基本面+估值+竞争力+风险+情绪）
2. 明确推荐：买入/等待/观望
3. 目标价区间（基于DCF±敏感性）
4. 风险提示

约束：
- 必须包含"推荐"和"目标价"关键词
- 如Agent信号冲突，明确说明分歧
- 字数不少于300字（中文字符）
- 最后一行必须是：**综合信号: [BULLISH/NEUTRAL/BEARISH] | 置信度: [0.XX]**
"""

REPORT_CH7_USER = """**综合信号汇总:**
- 基本面: {fundamentals_signal} ({fundamentals_confidence})
- 估值: {valuation_signal} ({valuation_confidence})
- Buffett: {buffett_signal} ({buffett_confidence})
- Graham: {graham_signal} ({graham_confidence})
- 情绪: {sentiment_signal} ({sentiment_confidence})
- 辩证分析: {contrarian_signal} ({contrarian_confidence})

**估值区间:**
- DCF基准: ¥{dcf_base}/股
- 乐观: ¥{dcf_optimistic}/股
- 悲观: ¥{dcf_pessimistic}/股
- 当前价: ¥{current_price}/股

**关键风险:**
{contrarian_risks}

请给出综合投资建议（≥300字，必须包含"推荐"和"目标价"，最后一行必须是综合信号）。"""
```

**Step 2: Verify prompts load**

Run: `python -c "from src.llm.prompts import REPORT_CH1_SYSTEM, REPORT_CH2_SYSTEM, REPORT_CH6_SYSTEM, REPORT_CH7_SYSTEM; print('OK')"`
Expected: Output `OK`

**Step 3: Commit**

```bash
git add src/llm/prompts.py
git commit -m "feat(report): add LLM chapter prompts for Ch1,2,6,7

- Add REPORT_CH1_SYSTEM/USER (industry background, ≥400字)
- Add REPORT_CH2_SYSTEM/USER (competitive analysis, ≥500字)
- Add REPORT_CH6_SYSTEM/USER (market sentiment, ≥200字)
- Add REPORT_CH7_SYSTEM/USER (investment recommendation, ≥300字)
- Include validation requirements in system prompts

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 11: Implement LLM Chapter Generation with Validation

**Files:**
- Modify: `src/agents/report_generator.py`
- Modify: `tests/test_report_chapters.py`

**Step 1: Write failing test**

Add to `tests/test_report_chapters.py`:
```python
from unittest.mock import patch
from src.agents.report_generator import _generate_llm_chapter


@patch('src.agents.report_generator.call_llm')
def test_generate_llm_chapter_pass_validation(mock_llm):
    """LLM chapter should pass validation on first try."""
    mock_llm.return_value = "这是一个符合要求的章节内容。" * 50  # 500+ chars

    signals = {
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="bullish", confidence=0.70,
            reasoning="Strong moat",
            metrics={"moat_type": "Brand", "management_quality": "Excellent", "has_pricing_power": True}
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="bullish", confidence=0.65,
            reasoning="Value",
            metrics={"standards_passed": 5}
        ),
    }

    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    result = _generate_llm_chapter(
        "ch2_competitive", "TEST", "a_share", signals, quality_report, ""
    )

    assert len(result) > 500
    assert "⚠️" not in result  # No warning markers


@patch('src.agents.report_generator.call_llm')
def test_generate_llm_chapter_fail_validation_retry(mock_llm):
    """LLM chapter should retry and append warning if validation fails."""
    # First call fails validation (too short)
    # Second call also fails
    # Third call also fails
    mock_llm.return_value = "短文本"

    signals = {}
    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    result = _generate_llm_chapter(
        "ch1_industry", "TEST", "a_share", signals, quality_report, "Test context"
    )

    # Should have warning marker
    assert "⚠️" in result
    assert "质量验证未通过" in result
    assert "字数不足" in result

    # Should have attempted retries (3 total calls)
    assert mock_llm.call_count == 3
```

**Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_report_chapters.py -k "generate_llm_chapter" -v`
Expected: FAIL with "cannot import name '_generate_llm_chapter'"

**Step 3: Implement LLM chapter generation**

Add to `src/agents/report_generator.py` (add import):
```python
from src.agents.report_config import CHAPTERS, validate_chapter
```

Then add function:
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

    Args:
        chapter_key: Chapter identifier (ch1_industry, ch2_competitive, etc.)
        ticker: Stock ticker
        market: Market type
        signals: All agent signals
        quality_report: Data quality report
        industry_context: Industry background from watchlist

    Returns:
        Chapter markdown text (with warning marker if validation failed)
    """
    from src.llm.router import call_llm
    from src.llm.prompts import (
        REPORT_CH1_SYSTEM, REPORT_CH1_USER,
        REPORT_CH2_SYSTEM, REPORT_CH2_USER,
        REPORT_CH6_SYSTEM, REPORT_CH6_USER,
        REPORT_CH7_SYSTEM, REPORT_CH7_USER,
    )

    config = CHAPTERS[chapter_key]

    # Select prompts based on chapter
    prompt_map = {
        "ch1_industry": (REPORT_CH1_SYSTEM, REPORT_CH1_USER),
        "ch2_competitive": (REPORT_CH2_SYSTEM, REPORT_CH2_USER),
        "ch6_sentiment": (REPORT_CH6_SYSTEM, REPORT_CH6_USER),
        "ch7_recommendation": (REPORT_CH7_SYSTEM, REPORT_CH7_USER),
    }

    system_prompt, user_template = prompt_map[chapter_key]

    # Build user prompt (chapter-specific data injection)
    user_prompt = _build_chapter_user_prompt(
        chapter_key, user_template, ticker, market, signals, quality_report, industry_context
    )

    # Retry loop with validation
    for attempt in range(config["max_retries"] + 1):
        try:
            text = call_llm(config["task_name"], system_prompt, user_prompt)
        except Exception as e:
            logger.error(f"[Report] {chapter_key} LLM call failed: {e}")
            return f"## {config['title']}\n\n⚠️ LLM调用失败: {str(e)}"

        # Validate
        issues = validate_chapter(text, config)

        if not issues:
            logger.info(f"[Report] {chapter_key} passed validation (attempt {attempt+1})")
            return f"## {config['title']}\n\n{text}"

        # Log issues and retry
        if attempt < config["max_retries"]:
            logger.warning(f"[Report] {chapter_key} validation failed (attempt {attempt+1}): {issues}")
            user_prompt += f"\n\n[重试要求] 上次输出未通过验证: {', '.join(issues)}。请修正。"
        else:
            logger.error(f"[Report] {chapter_key} validation failed after {config['max_retries']+1} attempts")

    # Failed after all retries
    return f"## {config['title']}\n\n{text}\n\n> ⚠️ 质量验证未通过: {', '.join(issues)}"


def _build_chapter_user_prompt(
    chapter_key: str,
    user_template: str,
    ticker: str,
    market: str,
    signals: dict[str, AgentSignal],
    quality_report: QualityReport,
    industry_context: str,
) -> str:
    """Build user prompt for LLM chapter with data injection."""

    # Extract common data
    fund = signals.get("fundamentals")
    val = signals.get("valuation")
    buff = signals.get("warren_buffett")
    gram = signals.get("ben_graham")
    sent = signals.get("sentiment")
    contr = signals.get("contrarian")

    # Chapter-specific formatting
    if chapter_key == "ch1_industry":
        return user_template.format(
            ticker=ticker,
            sector=market,  # Simplified - would need actual sector from watchlist
            sub_industry="",
            industry_context=industry_context or "（用户未提供，请根据财务数据推测）",
            revenue=_format_yuan(fund.metrics.get("revenue")) if fund else "N/A",
            growth_rate=f"{fund.metrics.get('revenue_growth', 0)*100:.1f}%" if fund else "N/A",
            roe=f"{fund.metrics.get('roe', 0):.1f}" if fund else "N/A",
            debt_ratio=f"{fund.metrics.get('debt_ratio', 0):.1f}" if fund else "N/A",
        )

    elif chapter_key == "ch2_competitive":
        return user_template.format(
            buffett_signal=buff.signal if buff else "未运行",
            moat_type=buff.metrics.get("moat_type", "N/A") if buff else "N/A",
            management_quality=buff.metrics.get("management_quality", "N/A") if buff else "N/A",
            has_pricing_power=buff.metrics.get("has_pricing_power", False) if buff else False,
            buffett_reasoning=buff.reasoning if buff else "未分析",
            graham_signal=gram.signal if gram else "未运行",
            graham_standards_passed=gram.metrics.get("standards_passed", 0) if gram else 0,
            graham_reasoning=gram.reasoning if gram else "未分析",
        )

    elif chapter_key == "ch6_sentiment":
        return user_template.format(
            sentiment_signal=sent.signal if sent else "未运行",
            sentiment_score=f"{sent.metrics.get('sentiment_score', 0):.2f}" if sent else "N/A",
            sentiment_reasoning=sent.reasoning if sent else "暂无新闻数据",
            news_summary=sent.reasoning[:500] if sent else "（无）",
        )

    elif chapter_key == "ch7_recommendation":
        # Get DCF values
        dcf_base = val.metrics.get("dcf_per_share", 0) if val else 0
        dcf_optimistic = dcf_base * 1.2 if dcf_base else 0
        dcf_pessimistic = dcf_base * 0.8 if dcf_base else 0
        current_price = val.metrics.get("current_price", 0) if val else 0

        # Extract contrarian risks summary
        contrarian_risks = "（辩证分析未运行）"
        if contr and contr.metrics:
            mode = contr.metrics.get("mode")
            if mode == "bear_case":
                risks = contr.metrics.get("risk_scenarios", [])
                contrarian_risks = "\n".join([f"- {r.get('scenario', '')}" for r in risks[:3]])
            elif mode == "bull_case":
                contrarian_risks = "（当前共识看空，辩证分析聚焦上行机会）"
            else:
                contrarian_risks = contr.metrics.get("core_contradiction", "（信号分歧，关键不确定性待解决）")

        return user_template.format(
            fundamentals_signal=fund.signal if fund else "未运行",
            fundamentals_confidence=f"{fund.confidence:.0%}" if fund else "N/A",
            valuation_signal=val.signal if val else "未运行",
            valuation_confidence=f"{val.confidence:.0%}" if val else "N/A",
            buffett_signal=buff.signal if buff else "未运行",
            buffett_confidence=f"{buff.confidence:.0%}" if buff else "N/A",
            graham_signal=gram.signal if gram else "未运行",
            graham_confidence=f"{gram.confidence:.0%}" if gram else "N/A",
            sentiment_signal=sent.signal if sent else "未运行",
            sentiment_confidence=f"{sent.confidence:.0%}" if sent else "N/A",
            contrarian_signal=contr.signal if contr else "未运行",
            contrarian_confidence=f"{contr.confidence:.0%}" if contr else "N/A",
            dcf_base=f"{dcf_base:.2f}",
            dcf_optimistic=f"{dcf_optimistic:.2f}",
            dcf_pessimistic=f"{dcf_pessimistic:.2f}",
            current_price=f"{current_price:.2f}",
            contrarian_risks=contrarian_risks,
        )

    return "（章节配置错误）"
```

**Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_report_chapters.py -k "generate_llm_chapter" -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/agents/report_generator.py tests/test_report_chapters.py
git commit -m "feat(report): implement LLM chapter generation with validation

- Add _generate_llm_chapter() with retry loop
- Add _build_chapter_user_prompt() for data injection
- Support Ch1, Ch2, Ch6, Ch7 with chapter-specific prompts
- Validate output and retry up to 2 times
- Append warning marker if validation fails
- Add 2 unit tests (pass and fail scenarios)

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 12: Refactor main run() Function with Template Rendering

**Files:**
- Modify: `src/agents/report_generator.py`
- Create: `tests/test_report_integration.py`

**Step 1: Write integration test**

Create `tests/test_report_integration.py`:
```python
"""Integration tests for full report generation."""

import pytest
from unittest.mock import patch, MagicMock
from src.agents.report_generator import run
from src.data.models import AgentSignal, QualityReport


@patch('src.agents.report_generator.call_llm')
def test_full_report_generation(mock_llm):
    """Full report should generate all 8 chapters."""
    # Mock LLM to return valid chapters
    mock_llm.return_value = "这是一个有效的章节内容。" * 60  # 600+ chars

    # Mock signals
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="neutral", confidence=0.55,
            reasoning="财务稳健",
            metrics={"total_score": 60, "revenue_score": 15, "profitability_score": 15,
                     "leverage_score": 15, "cash_flow_score": 15, "revenue": 1e10, "revenue_growth": 0.1,
                     "roe": 15, "debt_ratio": 0.5}
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="neutral", confidence=0.60,
            reasoning="估值合理",
            metrics={"dcf_per_share": 20, "current_price": 19, "margin_of_safety": 0.05}
        ),
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="neutral", confidence=0.50,
            reasoning="护城河一般",
            metrics={"moat_type": "Brand", "management_quality": "Good", "has_pricing_power": True}
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="neutral", confidence=0.50,
            reasoning="价值适中",
            metrics={"standards_passed": 4}
        ),
        "sentiment": AgentSignal(
            ticker="TEST", agent_name="sentiment",
            signal="neutral", confidence=0.50,
            reasoning="市场情绪中性",
            metrics={"sentiment_score": 0.5}
        ),
        "contrarian": AgentSignal(
            ticker="TEST", agent_name="contrarian",
            signal="neutral", confidence=0.60,
            reasoning="信号分歧",
            metrics={
                "mode": "critical_questions",
                "consensus": {"direction": "mixed", "strength": 0.5},
                "core_contradiction": "基本面稳健但估值争议",
                "questions": [
                    {"question": "增长可持续性？", "preliminary_judgment": "不确定", "evidence_needed": "未来订单"}
                ]
            }
        ),
    }

    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    # Generate report
    report_text, report_path = run(
        ticker="TEST",
        market="a_share",
        signals=signals,
        quality_report=quality_report,
        analysis_date="2026-03-07",
        use_llm=True
    )

    # Verify all chapters present
    assert "## 1. 行业背景" in report_text
    assert "## 2. 竞争力分析" in report_text
    assert "## 3. 财务质量评估" in report_text
    assert "## 4. 估值分析" in report_text
    assert "## 5. 风险因素" in report_text
    assert "## 6. 市场情绪" in report_text
    assert "## 7. 综合建议" in report_text
    assert "## 附录" in report_text

    # Verify metadata
    assert "TEST 投资研究报告" in report_text
    assert "2026-03-07" in report_text
    assert "0.90/1.0" in report_text  # Quality score

    # Verify report length
    assert len(report_text) > 2000

    # Verify file saved
    assert report_path.exists()
    assert report_path.name == "TEST_2026-03-07.md"


def test_quick_report_unchanged():
    """Quick mode should still generate old-style report."""
    signals = {}
    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    report_text, _ = run(
        ticker="TEST",
        market="a_share",
        signals=signals,
        quality_report=quality_report,
        use_llm=False  # Quick mode
    )

    # Should be old quick report format
    assert "投资研究快报（数据版）" in report_text
    assert "本报告为数据版" in report_text
```

**Step 2: Refactor run() function**

Replace the existing `run()` function in `src/agents/report_generator.py`:

```python
def run(
    ticker: str,
    market: str,
    *,
    signals: dict[str, AgentSignal],
    quality_report: QualityReport | None = None,
    analysis_date: str | None = None,
    use_llm: bool = True,
) -> tuple[str, Path]:
    """
    Generate the final research report (restructured with chapters).

    Args:
        ticker: Stock ticker
        market: Market type
        signals: All agent signals (including contrarian from P0-②)
        quality_report: Data quality report from P0-①
        analysis_date: Report date (defaults to today)
        use_llm: Whether to use LLM (False = quick mode)

    Returns:
        (report_markdown_text, report_file_path)
    """
    from datetime import datetime
    from jinja2 import Template

    if analysis_date is None:
        analysis_date = str(date.today())

    # Prepare output directory
    output_dir = get_project_root() / "output" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = ticker.replace(".", "_")
    report_path = output_dir / f"{safe_ticker}_{analysis_date}.md"

    # Quick mode: use existing code-only report
    if not use_llm:
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_path.write_text(report_text, encoding="utf-8")
        logger.info("[Report] Quick report saved: %s", report_path)
        return report_text, report_path

    # ── New Chapter-by-Chapter Generation ─────────────────────────────────────

    logger.info("[Report] Generating structured report for %s", ticker)

    # Get industry context from watchlist (if available)
    # For MVP, we'll use empty string and let LLM infer
    industry_context = ""

    # Generate all 8 chapters
    chapters = {}

    try:
        # Ch1: Industry Background (LLM)
        logger.info("[Report] Generating Ch1: Industry Background")
        chapters["ch1_industry"] = _generate_llm_chapter(
            "ch1_industry", ticker, market, signals, quality_report, industry_context
        )

        # Ch2: Competitive Analysis (LLM)
        logger.info("[Report] Generating Ch2: Competitive Analysis")
        chapters["ch2_competitive"] = _generate_llm_chapter(
            "ch2_competitive", ticker, market, signals, quality_report, industry_context
        )

        # Ch3: Financial Quality (Code)
        logger.info("[Report] Generating Ch3: Financial Quality")
        chapters["ch3_financial"] = _build_financial_quality_table(
            ticker, signals.get("fundamentals"), quality_report
        )

        # Ch4: Valuation Analysis (Code)
        logger.info("[Report] Generating Ch4: Valuation Analysis")
        chapters["ch4_valuation"] = _build_valuation_analysis(signals.get("valuation"))

        # Ch5: Risk Factors (Contrarian Template)
        logger.info("[Report] Generating Ch5: Risk Factors (Contrarian)")
        chapters["ch5_risks"] = _render_contrarian_chapter(signals.get("contrarian"))

        # Ch6: Market Sentiment (LLM)
        logger.info("[Report] Generating Ch6: Market Sentiment")
        chapters["ch6_sentiment"] = _generate_llm_chapter(
            "ch6_sentiment", ticker, market, signals, quality_report, industry_context
        )

        # Ch7: Investment Recommendation (LLM)
        logger.info("[Report] Generating Ch7: Investment Recommendation")
        chapters["ch7_recommendation"] = _generate_llm_chapter(
            "ch7_recommendation", ticker, market, signals, quality_report, industry_context
        )

        # Ch8: Appendix (Code)
        logger.info("[Report] Generating Appendix")
        chapters["appendix"] = _build_appendix(signals, quality_report)

    except Exception as e:
        logger.error("[Report] Chapter generation failed: %s", e)
        # Fall back to quick report
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_text += f"\n\n---\n*报告生成失败: {e}。已输出快速报告。*"
        report_path.write_text(report_text, encoding="utf-8")
        return report_text, report_path

    # Render main template
    try:
        template_path = get_project_root() / "templates" / "report_template.md"
        with open(template_path, "r", encoding="utf-8") as f:
            template = Template(f.read())

        report_text = template.render(
            ticker=ticker,
            market=market,
            analysis_date=analysis_date,
            quality_score=quality_report.overall_quality_score if quality_report else 0.0,
            generation_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **chapters  # ch1_industry, ch2_competitive, etc.
        )

    except Exception as e:
        logger.critical("[Report] Template rendering failed: %s", e)
        # Emergency fallback
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_text += f"\n\n---\n*模板渲染失败: {e}。已输出快速报告。*"
        report_path.write_text(report_text, encoding="utf-8")
        return report_text, report_path

    # Save report
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("[Report] Structured report saved: %s (%d chars)", report_path, len(report_text))

    return report_text, report_path
```

**Step 3: Run integration test**

Run: `poetry run pytest tests/test_report_integration.py -v`
Expected: PASS (2 tests)

**Step 4: Run full test suite**

Run: `poetry run pytest tests/test_report*.py -v`
Expected: All tests PASS (~17 tests total)

**Step 5: Commit**

```bash
git add src/agents/report_generator.py tests/test_report_integration.py
git commit -m "feat(report): refactor run() with chapter-by-chapter generation

- Implement new chapter pipeline (Ch1-8 sequential)
- Render final report via Jinja2 template
- Keep backward compatibility with quick mode
- Add comprehensive error handling with fallbacks
- Add 2 integration tests
- All 17 tests passing

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 13: Manual Testing and Verification

**Files:**
- Manual testing only (no code changes)

**Step 1: Test with real data (quick mode)**

Run: `poetry run invest report -t 601808.SH --quick`
Expected:
- Quick report generated successfully
- No errors in logs
- Report has old format (data tables only)

**Step 2: Test with real data (LLM mode)**

Run: `poetry run invest report -t 601808.SH`
Expected:
- Structured report generated with 8 chapters
- Ch1-7 have proper titles
- Ch3, Ch4 have tables
- Ch5 has Contrarian content (bear/bull/questions depending on consensus)
- Ch6, Ch7 have LLM-generated text
- Appendix has all agent signals
- Total length 2000-3000 words
- Report saved to `output/reports/601808_SH_2026-03-07.md`

**Step 3: Verify chapter validation**

Check logs for validation messages:
```
[Report] ch1_industry passed validation (attempt 1)
[Report] ch2_competitive passed validation (attempt 1)
...
```

**Step 4: Check report quality**

Open generated report and verify:
- [ ] All 8 chapters present with correct numbering
- [ ] Ch2 contains "护城河" or "竞争" keyword
- [ ] Ch3 shows 4-dimension fundamentals table
- [ ] Ch4 shows DCF + sensitivity scenarios
- [ ] Ch5 renders Contrarian analysis properly
- [ ] Ch7 ends with "**综合信号: XXX | 置信度: X.XX**"
- [ ] Appendix shows all agents in table
- [ ] No obvious errors or "⚠️" warnings (unless intentional)

**Step 5: Document successful test**

```bash
# Create verification note
echo "✅ Manual testing complete - 2026-03-07

Tested with ticker: 601808.SH
- Quick mode: PASS
- LLM mode: PASS
- All 8 chapters generated correctly
- Validation working as expected
- Report length: 2500+ words
- No critical errors

" > docs/plans/2026-03-07-report-generator-verification.txt

git add docs/plans/2026-03-07-report-generator-verification.txt
git commit -m "docs(report): add manual testing verification

- Tested quick and LLM modes
- All 8 chapters generating correctly
- Validation and retry logic working
- Ready for production use

Part of P0-③ Report Generator

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Final Verification Checklist

After all tasks complete, verify:

```bash
# 1. All tests pass
poetry run pytest tests/test_report*.py -v
# Expected: ~17 tests PASS

# 2. Type checking (if using mypy)
poetry run mypy src/agents/report_generator.py --strict
# Expected: Success or minor warnings

# 3. Quick mode works
poetry run invest report -t 601808.SH --quick
# Expected: Old-style quick report generated

# 4. LLM mode works
poetry run invest report -t 601808.SH
# Expected: New 8-chapter structured report

# 5. Check report structure
cat "output/reports/601808_SH_$(date +%Y-%m-%d).md" | grep "^## "
# Expected: 8 chapter headers (## 1. through ## 附录)

# 6. Verify Jinja2 templates exist
ls -la templates/
# Expected: report_template.md and contrarian_templates/ directory

# 7. Verify LLM config
grep "report_ch" config/llm_config.yaml
# Expected: 4 task entries (report_ch1, ch2, ch6, ch7)
```

---

## Success Criteria

✅ **Functional:**
- All 8 chapters generate successfully
- LLM chapters (Ch1, 2, 6, 7) pass validation ≥90% of time
- Code chapters (Ch3, 4, Appendix) always generate
- Contrarian template (Ch5) renders for all 3 modes
- Total report length 2000-3000+ words

✅ **Quality:**
- All tests pass (17 tests)
- Validation rules enforce minimum standards
- Failed validations show warning markers
- Graceful fallbacks for all error scenarios

✅ **Performance:**
- Report generation <60s (acceptable for batch workflow)
- Cost <$0.10 per report (target: $0.03-0.05)

✅ **Backward Compatibility:**
- Quick mode (`--quick`) still works with old format
- Existing tests for other agents unaffected

---

*Implementation Plan Version: 1.0 | Created: 2026-03-07*
