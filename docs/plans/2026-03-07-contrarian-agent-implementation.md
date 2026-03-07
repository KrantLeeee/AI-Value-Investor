# Contrarian Agent Implementation Plan - P0-②

> **Version**: MVP (Option A) | **Date**: 2026-03-07
> **Scope**: Core dialectical analysis with 3-mode dynamic switching
> **Reference**: [contrarian-agent-design.md](../../References/Docs/Tech Design/contrarian-agent-design.md)

---

## Overview

Implement the Contrarian Agent - a dialectical analysis system that dynamically challenges consensus by switching between three modes based on front-running agent signals.

**Core Innovation**: Unlike fixed bear case analysis, Contrarian adapts its perspective:
- When consensus is bullish (≥60%) → BEAR_CASE mode (challenge bulls)
- When consensus is bearish (≥60%) → BULL_CASE mode (challenge bears)
- When consensus is mixed (<60%) → CRITICAL_QUESTIONS mode (identify key uncertainties)

**MVP Scope (P0-②)**:
- ✅ All 3 modes with dynamic switching
- ✅ Consensus calculation logic
- ✅ LLM integration with structured JSON output
- ✅ Registry integration (Phase 2.5)
- ✅ Pass signal to report_generator (rendering deferred to P0-③)
- ❌ Database schema migrations (deferred)
- ❌ Report Chapter 5 rendering (deferred to P0-③)
- ❌ Benchmark infrastructure (deferred)

---

## Architecture

### File Structure

```
src/agents/contrarian.py          # Main agent logic (~250 lines)
├─ _determine_consensus()         # Analyze front-running agent signals
├─ _select_mode()                 # Choose BEAR/BULL/QUESTIONS based on consensus
├─ _build_prompt()                # Dynamic prompt construction per mode
├─ _call_llm()                    # LLM invocation with structured output
├─ _validate_json()               # Ensure JSON schema compliance
└─ run()                          # Public API: returns AgentSignal

src/llm/prompts.py                # Add 6 new prompt templates
├─ CONTRARIAN_BEAR_CASE_SYSTEM    # System instruction for bear mode
├─ CONTRARIAN_BEAR_CASE_USER      # User template for bear mode
├─ CONTRARIAN_BULL_CASE_SYSTEM    # System instruction for bull mode
├─ CONTRARIAN_BULL_CASE_USER      # User template for bull mode
├─ CONTRARIAN_CRITICAL_QUESTIONS_SYSTEM
└─ CONTRARIAN_CRITICAL_QUESTIONS_USER

config/llm_config.yaml            # Add task routing
└─ contrarian_analysis:           # GPT-4o, max_tokens=2500, temp=0.3

tests/test_contrarian.py          # Unit and integration tests
```

### Design Principles

1. **Stateless** - Each run() call is independent, no persistent state
2. **Fail-safe** - Returns neutral signal if LLM fails or consensus unclear
3. **Type-safe** - Full Pydantic validation on inputs and outputs
4. **Observable** - Comprehensive logging at each decision point

---

## Core Logic

### Consensus Calculation

```python
def _determine_consensus(signals: dict[str, AgentSignal]) -> tuple[str, float]:
    """
    Analyze front-running agents to determine consensus direction.

    Args:
        signals: {"fundamentals": AgentSignal, "valuation": AgentSignal, ...}

    Returns:
        ("bullish" | "bearish" | "mixed", strength: 0.0-1.0)

    Rules:
        - bullish ratio ≥ 60% → ("bullish", ratio)
        - bearish ratio ≥ 60% → ("bearish", ratio)
        - otherwise → ("mixed", max_ratio)
    """
    bull = sum(1 for s in signals.values() if s and s.signal == "bullish")
    bear = sum(1 for s in signals.values() if s and s.signal == "bearish")
    total = sum(1 for s in signals.values() if s)

    if total == 0:
        return "mixed", 0.0

    bull_ratio = bull / total
    bear_ratio = bear / total

    if bull_ratio >= 0.6:
        return "bullish", bull_ratio
    if bear_ratio >= 0.6:
        return "bearish", bear_ratio
    return "mixed", max(bull_ratio, bear_ratio)
```

**Agents included in consensus:**
- fundamentals
- valuation
- warren_buffett
- ben_graham
- sentiment

### Mode Selection Matrix

| Consensus Direction | Strength | Selected Mode | Signal Output |
|---------------------|----------|---------------|---------------|
| bullish | ≥0.60 | BEAR_CASE | bearish |
| bearish | ≥0.60 | BULL_CASE | bullish |
| mixed | <0.60 | CRITICAL_QUESTIONS | neutral |

### Prompt Construction Strategy

For each mode, the prompt dynamically includes:

1. **Strongest Arguments Extraction**
   - For bullish consensus → extract bullish agent reasoning (first 200 chars)
   - For bearish consensus → extract bearish agent reasoning
   - For mixed → extract all agent reasoning

2. **Quality Context**
   - Include QualityReport flags and score
   - Add data quality warnings to prompt

3. **Mode-Specific Instructions**
   - BEAR_CASE: Challenge assumptions, construct risk scenarios
   - BULL_CASE: Find overlooked positives, analyze price-in
   - CRITICAL_QUESTIONS: Identify core contradictions

---

## JSON Output Schemas

### BEAR_CASE Mode

```json
{
    "mode": "bear_case",
    "consensus": {
        "direction": "bullish",
        "strength": 0.75
    },
    "assumption_challenges": [
        {
            "original_claim": "安全边际36%",
            "assumption": "WACC=10%",
            "challenge": "中海油服Beta=1.3，应适用12%WACC",
            "impact_if_wrong": "安全边际缩至8%",
            "severity": "high"
        }
    ],
    "risk_scenarios": [
        {
            "scenario": "油价跌至60美元/桶",
            "probability": "20-30%",
            "impact": "-25%至-35%营收",
            "precedent": "2020年Q1油价战期间该股跌幅40%"
        }
    ],
    "bear_case_target_price": 12.50,
    "reasoning": "综合论述..."
}
```

### BULL_CASE Mode

```json
{
    "mode": "bull_case",
    "consensus": {
        "direction": "bearish",
        "strength": 0.70
    },
    "overlooked_positives": [
        {
            "factor": "行业政策转向",
            "description": "海上风电补贴重启",
            "potential_impact": "+15%至+20%订单增长"
        }
    ],
    "priced_in_analysis": "当前PE处于历史10%分位，坏消息已充分反映",
    "survival_advantage": "现金储备120亿，资产负债率35%，优于同行平均55%",
    "bull_case_target_price": 28.00,
    "reasoning": "综合论述..."
}
```

### CRITICAL_QUESTIONS Mode

```json
{
    "mode": "critical_questions",
    "consensus": {
        "direction": "mixed",
        "strength": 0.45
    },
    "core_contradiction": "基本面指标看空但市场情绪极度乐观",
    "questions": [
        {
            "question": "油价能否持续在70美元/桶以上？",
            "preliminary_judgment": "OPEC减产协议不稳定，概率50-60%",
            "evidence_needed": "OPEC会议纪要、美国页岩油产量数据"
        }
    ],
    "reasoning": "综合论述..."
}
```

---

## Integration Points

### Registry Integration (Phase 2.5)

```python
# Insert in src/agents/registry.py after line 133 (after sentiment agent)

# ── Phase 2.5: Contrarian Agent ────────────────────────────────────────
try:
    from src.agents import contrarian
    logger.info("[Registry] Running Contrarian Agent...")
    signals["contrarian"] = contrarian.run(
        ticker=ticker,
        market=market,
        signals=signals,  # Pass all front-running agent signals
        quality_report=quality_report,  # Pass data quality context
        use_llm=_use_llm,
    )
except Exception as e:
    logger.error("[Registry] Contrarian Agent failed: %s", e)
```

### Report Generator Integration

**MVP Approach:**
- Contrarian signal is passed to report_generator in the signals dict
- Report generator receives it but **does not render it yet**
- P0-③ will implement Chapter 5 rendering using Contrarian's structured JSON

**Why defer rendering:**
- P0-③ is restructuring the entire report generation (7-chapter format)
- Contrarian output will become Chapter 5 (Risk Factors / Dialectical Analysis)
- Allows us to validate Contrarian logic independently first

### LLM Configuration

Add to `config/llm_config.yaml`:

```yaml
task_routing:
  contrarian_analysis:
    model: gpt-4o
    max_tokens: 2500
    temperature: 0.3
    description: "Dialectical analysis - challenges consensus with structured counterarguments"
```

---

## Error Handling

| Error Condition | Handling Strategy | Output |
|----------------|-------------------|--------|
| No signals available | Return neutral signal | confidence=0.20, reasoning="无可用信号" |
| LLM call fails | Graceful degradation | confidence=0.30, reasoning="LLM调用失败" |
| JSON parse fails | Extract text reasoning | confidence=0.40, log error |
| Invalid JSON schema | Use fallback structure | confidence=0.40, log validation errors |
| All agents failed (total=0) | Return neutral | confidence=0.20, note in metrics |

**Logging Strategy:**
- INFO: Consensus calculation results, mode selection
- WARNING: JSON validation failures, LLM errors
- ERROR: Unexpected exceptions, agent failures

---

## Confidence Calculation

**MVP Approach (Pre-P1-④):**
- Fixed confidence: **0.60**
- Clearly marked as "未校准" in logs and metrics
- Will be replaced by proper confidence engine in P1-④

**Post-P1-④:**
- Integrate with confidence.py
- Calculate based on:
  - Consensus strength
  - Data quality score
  - Historical calibration (if available)

---

## Testing Strategy

### Unit Tests (`tests/test_contrarian.py`)

**Consensus Calculation:**
1. `test_consensus_bullish()` - 4/5 agents bullish → ("bullish", 0.80)
2. `test_consensus_bearish()` - 3/4 agents bearish → ("bearish", 0.75)
3. `test_consensus_mixed()` - 2 bullish, 2 bearish → ("mixed", 0.50)
4. `test_consensus_threshold()` - 3/5 (60%) triggers consensus
5. `test_consensus_no_signals()` - Empty dict → ("mixed", 0.0)

**Mode Selection:**
6. `test_mode_bear_case()` - bullish consensus → BEAR_CASE mode
7. `test_mode_bull_case()` - bearish consensus → BULL_CASE mode
8. `test_mode_critical_questions()` - mixed → CRITICAL_QUESTIONS mode

**Prompt Construction:**
9. `test_prompt_extracts_strongest_args()` - Verify bullish args extracted
10. `test_prompt_includes_quality_context()` - QualityReport flags in prompt

**JSON Validation:**
11. `test_validate_bear_case_json()` - Valid schema passes
12. `test_validate_bull_case_json()` - Valid schema passes
13. `test_validate_critical_questions_json()` - Valid schema passes
14. `test_validate_invalid_json()` - Invalid schema caught

**Error Handling:**
15. `test_no_signals_returns_neutral()` - Empty signals dict
16. `test_llm_failure_graceful()` - Mock LLM exception
17. `test_json_parse_failure()` - Invalid JSON string

### Integration Tests

18. `test_contrarian_in_registry()` - Full registry.run_all_agents() with contrarian
19. `test_contrarian_output_structure()` - Verify AgentSignal format
20. `test_all_three_modes_end_to_end()` - Mock different consensus scenarios

### Manual Testing Checklist

- [ ] Run with 601808.SH (expected: bullish consensus → BEAR_CASE)
- [ ] Verify JSON output in `signals["contrarian"].metrics`
- [ ] Check logs for consensus calculation: "Consensus: bullish (0.75)"
- [ ] Verify mode selection: "Selected mode: bear_case"
- [ ] Test with --quick flag (should skip Contrarian)
- [ ] Test with LLM unavailable (graceful degradation)

---

## Implementation Phases

### Phase 1: Core Agent Logic
**Files**: `src/agents/contrarian.py`
**Tasks**:
- Implement consensus calculation
- Implement mode selection
- Add logging and error handling
- Define JSON schemas (inline for MVP)

### Phase 2: Prompt Engineering
**Files**: `src/llm/prompts.py`
**Tasks**:
- Write BEAR_CASE system and user prompts
- Write BULL_CASE system and user prompts
- Write CRITICAL_QUESTIONS system and user prompts
- Include dynamic argument extraction logic

### Phase 3: LLM Integration
**Files**: `src/agents/contrarian.py`, `config/llm_config.yaml`
**Tasks**:
- Add LLM routing config
- Implement _call_llm() with structured output
- Add JSON validation
- Handle LLM errors gracefully

### Phase 4: Registry Integration
**Files**: `src/agents/registry.py`
**Tasks**:
- Add Phase 2.5 section
- Pass signals and quality_report
- Add error handling
- Verify signal propagation to report_generator

### Phase 5: Testing & Validation
**Files**: `tests/test_contrarian.py`
**Tasks**:
- Write all unit tests (17 tests)
- Write integration tests (3 tests)
- Manual testing checklist
- Fix any bugs found

---

## Success Criteria

**Functional:**
- ✅ All 3 modes work correctly (BEAR_CASE, BULL_CASE, CRITICAL_QUESTIONS)
- ✅ Consensus calculation triggers correct mode (60% threshold)
- ✅ Structured JSON output validates against schema
- ✅ Integrates cleanly with registry Phase 2.5
- ✅ Graceful degradation on errors

**Quality:**
- ✅ All 20 tests pass
- ✅ Type-safe (mypy --strict compliant)
- ✅ Comprehensive logging (INFO/WARNING/ERROR levels)
- ✅ No regressions in existing agents

**Documentation:**
- ✅ Code comments explain mode selection logic
- ✅ Docstrings for all public functions
- ✅ Usage examples in contrarian.py module docstring

---

## Future Enhancements (Post-MVP)

**P0-③ Report Restructuring:**
- Render Contrarian JSON as Chapter 5 (Risk Factors)
- Add severity icons (🔴 high, 🟡 medium, 🟢 low)
- Format risk scenarios as structured sections

**P1-④ Confidence Engine:**
- Replace fixed 0.60 with dynamic confidence calculation
- Factor in consensus strength and data quality
- Add historical calibration (if available)

**P2 Database Integration:**
- Add report_metadata table
- Track consensus direction and mode selection
- Enable historical analysis of Contrarian effectiveness

**P3 Benchmark Infrastructure:**
- Track Contrarian predictions vs. actual outcomes
- Measure BULL_CASE accuracy when consensus is bearish
- Measure BEAR_CASE accuracy when consensus is bullish

---

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Prompts too long (>2500 tokens) | LLM truncation | Limit agent reasoning to 200 chars each |
| LLM doesn't return valid JSON | Parse failure | Add JSON validation + fallback to text extraction |
| Consensus calculation wrong | Wrong mode selected | Extensive unit tests + manual validation |
| Mode selection unclear to users | Confusion in logs | Clear logging: "Consensus: X (Y%), Mode: Z" |
| Integration breaks existing flow | Regression | Error handling + existing agents still run if Contrarian fails |

---

## Dependencies

**Required:**
- P0-① Data Quality Layer (completed) ✅
- Existing LLM router (src/llm/router.py) ✅
- AgentSignal model (src/data/models.py) ✅

**Optional:**
- P1-④ Confidence Engine (will integrate later)
- P0-③ Report Restructuring (will use Contrarian output)

---

## Estimated Effort

- Phase 1 (Core Logic): 3-4 hours
- Phase 2 (Prompts): 2-3 hours
- Phase 3 (LLM Integration): 2-3 hours
- Phase 4 (Registry): 1-2 hours
- Phase 5 (Testing): 3-4 hours

**Total**: 11-16 hours (1.5-2 days)

---

*Design Document Version: 1.0 | Last Updated: 2026-03-07*
