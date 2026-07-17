---
name: agent-design
description: Investment agent design patterns for AI Value Investor - LLM task routing, confidence calculation, signal output schemas, and quality integration
user-invocable: false
---

# Investment Agent Design Patterns

**Project Context**: AI Value Investor uses multi-agent architecture to analyze stocks from different perspectives (Fundamentals, Valuation, Buffett, Graham, Sentiment, Contrarian). Each agent produces signals that are aggregated with confidence-weighted voting.

---

## Standard Agent Structure

All investment agents MUST follow this 5-part structure:

### 1. Input Schema
```python
from src.data.models import QualityReport

def analyze(ticker: str, market: str) -> AgentSignal:
    # Fetch data from database
    prices = get_price_data(ticker)
    financials = get_financial_data(ticker)
    quality_report = get_quality_report(ticker)  # P0-① integration
```

**Required inputs:**
- `ticker`: Stock ticker (e.g., "601808.SH")
- `market`: Market type ("a_share", "hk", "us")
- `quality_report`: QualityReport object from P0-① data quality layer

### 2. Analysis Logic

**Two-tier approach:**

**Tier 1 - Quantitative (Code-based):**
```python
# Calculate metrics from raw data
roe = net_income / shareholder_equity
de_ratio = total_debt / total_equity
fcf_yield = free_cash_flow / market_cap
```

**Tier 2 - Qualitative (LLM-based):**
```python
from src.llm.router import route_llm_task

llm_output = route_llm_task(
    task_name="buffett_analysis",  # Registered in llm_config.yaml
    ticker=ticker,
    metrics={"roe": roe, "de_ratio": de_ratio},
    context=quality_report.to_dict()
)
```

### 3. Confidence Calculation (P1-④)

**Formula (from confidence.py):**
```python
from src.agents.confidence import calculate_confidence

confidence = calculate_confidence(
    signal_strength=0.7,        # 0.0-1.0: How strong is the signal
    indicator_agreement=0.8,    # 0.0-1.0: Do sub-indicators agree
    quality_score=quality_report.overall_quality_score  # From P0-①
)
# Result: min(0.85, max(0.10, 0.7*0.5 + 0.8*0.5)) * quality_score
```

**Agent-specific strength/agreement metrics:**

| Agent | signal_strength | indicator_agreement |
|-------|----------------|---------------------|
| Fundamentals | abs(score - 57.5) / 57.5 | 4 sub-dimensions directional consistency |
| Valuation | abs(margin_of_safety) | DCF/Graham/EV-EBITDA agreement |
| Buffett | ROE consistency + NI stability | Code-based vs LLM agreement |
| Graham | Standards passed ratio | Cross-standard directional consistency |
| Sentiment | Positive/negative news ratio extremity | Multi-source sentiment agreement |
| Contrarian | Depends on consensus mode | N/A (uses different formula) |

**Critical rules:**
- ✅ Confidence capped at 0.85 (Tetlock superforecasting research)
- ✅ Minimum 0.10 (some data > no data)
- ✅ Quality score is multiplicative penalty (not additive)
- ❌ NEVER manually set confidence to 0.9+ without historical calibration

### 4. Signal Output Schema

```python
from src.data.models import AgentSignal

return AgentSignal(
    agent_name="buffett_agent",
    ticker=ticker,
    signal="bullish" | "bearish" | "neutral",
    confidence=confidence,  # 0.10-0.85 from step 3
    reasoning="Clear explanation of why this signal was chosen",
    metrics={
        "roe": 0.18,
        "de_ratio": 0.45,
        # ... other relevant metrics
    },
    flags=quality_report.flags  # Pass through quality issues
)
```

### 5. Error Handling

```python
try:
    # Agent logic
except InsufficientDataError:
    return AgentSignal(
        agent_name="buffett_agent",
        ticker=ticker,
        signal="neutral",
        confidence=0.10,  # Minimum confidence
        reasoning="Insufficient data: " + str(missing_fields),
        flags=[{"flag": "insufficient_data", "severity": "critical"}]
    )
```

---

## LLM Task Registration

### Step 1: Define in config/llm_config.yaml

```yaml
task_routing:
  buffett_analysis:           # Task name (use in route_llm_task)
    provider: openai
    model: gpt-4o
    max_tokens: 2000
    temperature: 0.2          # Lower for analytical tasks

  contrarian_analysis:        # P0-② Contrarian agent
    provider: openai
    model: gpt-4o
    max_tokens: 2500
    temperature: 0.3

  report_ch1:                 # P0-③ Report chapters
    provider: openai
    model: gpt-4o
    max_tokens: 1500
    temperature: 0.3
```

**Token budget guidelines:**
- Simple analysis: 1000-1500 tokens
- Complex reasoning: 2000-2500 tokens
- Report chapters: 1500-2000 tokens

**Temperature guidelines:**
- 0.1: Metric interpretation (deterministic)
- 0.2: Structured analysis (minimal creativity)
- 0.3: Report writing (moderate creativity)

### Step 2: Create prompt in src/llm/prompts.py

```python
def get_buffett_analysis_prompt(ticker: str, metrics: dict, quality_context: dict) -> tuple[str, str]:
    system = """You are Warren Buffett's investment analyst.

Analyze stocks using Buffett's criteria:
1. Durable competitive advantage (moat)
2. Strong financials (high ROE, low debt)
3. Consistent earnings growth
4. Competent management

Output JSON:
{
  "moat_assessment": "string (100-200 words)",
  "financial_strength": "strong|moderate|weak",
  "signal": "bullish|bearish|neutral",
  "reasoning": "string (200-300 words)"
}
"""

    user = f"""Ticker: {ticker}

Metrics:
- ROE: {metrics['roe']:.1%}
- Debt/Equity: {metrics['de_ratio']:.2f}
- 5Y NI Growth: {metrics.get('ni_growth_5y', 'N/A')}

Data Quality Context:
{json.dumps(quality_context, indent=2)}

Analyze this stock using Buffett's framework.
"""

    return system, user
```

### Step 3: Call in agent code

```python
from src.llm.router import route_llm_task
from src.llm.prompts import get_buffett_analysis_prompt

system, user = get_buffett_analysis_prompt(ticker, metrics, quality_report.to_dict())

llm_result = route_llm_task(
    task_name="buffett_analysis",
    system_message=system,
    user_message=user
)

# Parse JSON response
analysis = json.loads(llm_result)
signal = analysis["signal"]
reasoning = analysis["reasoning"]
```

---

## Industry Adaptation (P1-⑤)

When implementing industry-specific logic:

```python
from src.agents.industry_classifier import get_industry_profile

industry_profile = get_industry_profile(ticker, sector)

# Use industry-specific thresholds
roe_thresholds = industry_profile['scoring']['roe_thresholds']
# e.g., Energy: [15, 10, 6]  vs  Consumer: [25, 20, 15]

# Apply industry weights (NOT used in individual agents)
# Weights are applied in signal_aggregator.py (P1-⑥)
```

**Important:** Individual agents compute signals independently. Industry weights are ONLY used in the aggregator, not in agent logic.

---

## Integration Checklist

Before committing a new agent:

- [ ] Imports QualityReport and uses it in analysis
- [ ] Uses calculate_confidence() from confidence.py (P1-④)
- [ ] Confidence between 0.10-0.85 (never exceed without calibration)
- [ ] Returns AgentSignal with all required fields
- [ ] LLM task registered in llm_config.yaml
- [ ] Prompt follows JSON output schema
- [ ] Handles missing data gracefully (returns neutral + low confidence)
- [ ] Added to registry.py agent execution pipeline
- [ ] Passes quality flags through to signal output

---

## File Locations Reference

| Component | File Path |
|-----------|-----------|
| Agent code | src/agents/<agent_name>.py |
| Prompts | src/llm/prompts.py |
| LLM routing | src/llm/router.py |
| LLM config | config/llm_config.yaml |
| Agent registry | src/agents/registry.py |
| Signal aggregator | src/agents/signal_aggregator.py (P1-⑥) |
| Industry profiles | config/industry_profiles.yaml (P1-⑤) |
| Confidence engine | src/agents/confidence.py (P1-④) |
| Quality layer | src/data/quality.py (P0-①) |
| Data models | src/data/models.py |

---

## Design Principles

1. **Code > LLM**: Use code for quantitative metrics, LLM only for qualitative interpretation
2. **Fail gracefully**: Missing data → neutral signal + low confidence, not errors
3. **Transparent reasoning**: Every signal must explain WHY in human-readable text
4. **Quality-aware**: All agents must consider data quality in confidence scoring
5. **Industry-agnostic agents**: Industry adaptation happens in aggregator, not agents
6. **Calibration humility**: 0.85 confidence cap until P3-⑨ validates historical accuracy

---

## Example: Minimal Agent Template

```python
"""<Agent Name> - <one-line description>"""
import json
from src.data.database import get_price_data, get_financial_data
from src.data.quality import get_quality_report
from src.data.models import AgentSignal
from src.agents.confidence import calculate_confidence
from src.llm.router import route_llm_task
from src.llm.prompts import get_<agent>_prompt

def analyze(ticker: str, market: str) -> AgentSignal:
    """Run <agent> analysis on ticker."""

    # 1. Fetch data
    prices = get_price_data(ticker, market)
    financials = get_financial_data(ticker, market)
    quality_report = get_quality_report(ticker, market)

    # 2. Calculate metrics (code-based)
    metrics = {
        "metric1": ...,
        "metric2": ...,
    }

    # 3. LLM analysis (qualitative)
    system, user = get_<agent>_prompt(ticker, metrics, quality_report.to_dict())
    llm_output = route_llm_task("<agent>_analysis", system, user)
    analysis = json.loads(llm_output)

    # 4. Calculate confidence
    signal_strength = ...  # 0.0-1.0
    indicator_agreement = ...  # 0.0-1.0
    confidence = calculate_confidence(
        signal_strength,
        indicator_agreement,
        quality_report.overall_quality_score
    )

    # 5. Return signal
    return AgentSignal(
        agent_name="<agent>_agent",
        ticker=ticker,
        signal=analysis["signal"],
        confidence=confidence,
        reasoning=analysis["reasoning"],
        metrics=metrics,
        flags=quality_report.flags
    )
```

---

## Common Pitfalls

❌ **Don't:**
- Hardcode confidence scores (use calculate_confidence())
- Ignore quality_report in confidence calculation
- Exceed 0.85 confidence without historical calibration
- Use industry weights in individual agents (only in aggregator)
- Assume all data fields exist (check for None)
- Return confidence=0 on errors (use 0.10 minimum)

✅ **Do:**
- Use data quality score as multiplicative factor
- Provide clear reasoning for every signal
- Handle missing data gracefully
- Register all LLM tasks in config before use
- Follow consistent naming: <agent>_analysis for LLM tasks
- Test agents with incomplete/stale data scenarios
