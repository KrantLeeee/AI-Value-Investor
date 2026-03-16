"""ValuationConfig — unified output model for the three-layer industry engine.

This model represents the valuation framework configuration determined by:
1. Hard rules (bank, insurance, real_estate, etc.)
2. LLM dynamic routing (with method_importance scores)
3. Safe fallback (generic regime)

Key design decisions:
- method_importance (1-10 scale) is normalized to weights automatically
- Pydantic V2 @model_validator ensures cross-field consistency
- Floating-point tail-diff handling for exact weight sum of 1.0
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_METHODS = {
    "pe",
    "ev_ebitda",
    "dcf",
    "ps",
    "pb",
    "pb_roe",
    "ddm",
    "peg",
    "normalized_pe",
    "pe_moat",
    "ev_sales",
    "asset_replacement",
    "net_net",
    "graham_number",
    "nav",  # BUG-FIX P1-5: NAV for real estate stocks
}


class ValuationConfig(BaseModel):
    """Valuation framework configuration — output of three-layer funnel."""

    regime: str
    primary_methods: list[str]
    weights: dict[str, float] = {}
    method_importance: dict[str, int] = {}
    disabled_methods: list[str] = []
    exempt_scoring_metrics: list[str] = []
    scoring_mode: str = "standard"
    ev_ebitda_multiple_range: tuple[float, float] = (8.0, 12.0)
    pb_multiple_cap: float | None = None  # For real_estate regime (e.g., 0.5)

    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    source: Literal["hard_rule", "llm", "fallback"] = "llm"
    rationale: str = ""
    triggered_rules: list[str] = []

    @field_validator("primary_methods")
    @classmethod
    def methods_must_be_allowed(cls, v: list[str]) -> list[str]:
        invalid = set(v) - ALLOWED_METHODS
        if invalid:
            raise ValueError(f"非法估值方法: {invalid}")
        return v

    @model_validator(mode="after")
    def auto_normalize_weights(self) -> "ValuationConfig":
        """
        Auto-normalize weights from method_importance or equal distribution.

        Priority:
        1. weights already provided → use directly (hard_rule scenario)
        2. method_importance provided → normalize to weights (LLM scenario)
        3. neither → equal distribution (fallback scenario)
        """
        if self.weights:
            # Weights provided, ensure normalized
            total = sum(self.weights.values())
            if total > 0 and abs(total - 1.0) > 0.01:
                self.weights = {k: round(v / total, 4) for k, v in self.weights.items()}
            return self

        # Priority 1: Use method_importance from LLM
        if self.method_importance:
            total = sum(self.method_importance.values())
            if total == 0:
                raise ValueError("method_importance 不能全为 0")

            normalized = {k: round(v / total, 4) for k, v in self.method_importance.items()}

            # Handle float tail-diff for exact 1.0 sum
            keys = list(normalized.keys())
            if keys:
                current_sum = sum(list(normalized.values())[:-1])
                normalized[keys[-1]] = round(1.0 - current_sum, 4)

            self.weights = normalized
            return self

        # Priority 2: Equal distribution based on primary_methods
        if self.primary_methods:
            n = len(self.primary_methods)
            base_weight = round(1.0 / n, 4)
            self.weights = {m: base_weight for m in self.primary_methods}

            # Handle tail-diff
            keys = list(self.weights.keys())
            if keys:
                current_sum = sum(list(self.weights.values())[:-1])
                self.weights[keys[-1]] = round(1.0 - current_sum, 4)

        return self
