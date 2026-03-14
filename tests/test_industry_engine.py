"""Unit tests for V3.0 Industry Engine."""

import pytest


class TestBalanceSheetScanner:
    """Tests for balance_sheet_scanner.py."""

    def test_bank_detection_with_loan_loss_provision(self):
        """Bank balance sheet items trigger has_loan_loss_provision."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        bank_items = [
            "货币资金", "发放贷款及垫款", "贷款损失准备",
            "吸收存款", "向中央银行借款", "总资产"
        ]
        flags = extract_industry_flags(bank_items)
        assert flags["has_loan_loss_provision"] is True
        assert flags["has_insurance_reserve"] is False

    def test_non_bank_no_false_positive(self):
        """Normal company balance sheet should not trigger bank flag."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        normal_items = [
            "货币资金", "存货", "固定资产", "应付账款", "总资产"
        ]
        flags = extract_industry_flags(normal_items)
        assert flags["has_loan_loss_provision"] is False
        assert flags["has_insurance_reserve"] is False

    def test_insurance_detection_with_reserves(self):
        """Insurance balance sheet items trigger has_insurance_reserve."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        insurance_items = [
            "货币资金", "未到期责任准备金", "寿险责任准备金",
            "保户储金及投资款", "总资产"
        ]
        flags = extract_industry_flags(insurance_items)
        assert flags["has_insurance_reserve"] is True
        assert flags["has_loan_loss_provision"] is False

    def test_empty_input_returns_false_flags(self):
        """Empty input should return all False flags."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        flags = extract_industry_flags([])
        assert flags["has_loan_loss_provision"] is False
        assert flags["has_insurance_reserve"] is False

    def test_single_keyword_not_enough(self):
        """Single keyword match is not enough to trigger flag (avoid false positives)."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        # Only one bank keyword
        items = ["货币资金", "贷款损失准备", "固定资产"]
        flags = extract_industry_flags(items)
        assert flags["has_loan_loss_provision"] is False


class TestBalanceSheetModel:
    """Tests for BalanceSheet model V3 fields."""

    def test_balance_sheet_has_new_v3_fields(self):
        """BalanceSheet model should have V3 industry detection fields."""
        from datetime import date
        from src.data.models import BalanceSheet

        bs = BalanceSheet(
            ticker="601398.SH",
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            total_assets=10_000_000_000,
            inventory=500_000_000,
            advance_receipts=200_000_000,
            fixed_assets=1_000_000_000,
            has_loan_loss_provision=True,
            has_insurance_reserve=False,
            source="test",
        )
        assert bs.inventory == 500_000_000
        assert bs.advance_receipts == 200_000_000
        assert bs.fixed_assets == 1_000_000_000
        assert bs.has_loan_loss_provision is True
        assert bs.has_insurance_reserve is False

    def test_balance_sheet_v3_fields_default_none(self):
        """V3 fields should default to None/False for backward compatibility."""
        from datetime import date
        from src.data.models import BalanceSheet

        bs = BalanceSheet(
            ticker="000001.SZ",
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            source="test",
        )
        assert bs.inventory is None
        assert bs.advance_receipts is None
        assert bs.fixed_assets is None
        assert bs.has_loan_loss_provision is False
        assert bs.has_insurance_reserve is False


class TestValuationConfig:
    """Tests for ValuationConfig Pydantic model."""

    def test_method_importance_converts_to_weights(self):
        """method_importance scores should auto-normalize to weights."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="test_regime",
            primary_methods=["pe", "ev_ebitda", "dcf"],
            method_importance={"pe": 8, "ev_ebitda": 5, "dcf": 2},
            source="llm",
        )
        # Total = 15, so pe=8/15, ev_ebitda=5/15, dcf=2/15
        assert abs(config.weights["pe"] - 0.5333) < 0.01
        assert abs(config.weights["ev_ebitda"] - 0.3333) < 0.01
        assert abs(sum(config.weights.values()) - 1.0) < 0.001

    def test_weights_sum_to_one(self):
        """Weights should always sum to exactly 1.0."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="test",
            primary_methods=["pe", "pb", "dcf"],
            method_importance={"pe": 3, "pb": 3, "dcf": 3},
            source="llm",
        )
        assert sum(config.weights.values()) == 1.0

    def test_explicit_weights_used_directly(self):
        """If weights provided, method_importance is ignored."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="bank",
            primary_methods=["pb_roe", "ddm"],
            weights={"pb_roe": 0.6, "ddm": 0.4},
            method_importance={"pb_roe": 1, "ddm": 9},  # Should be ignored
            source="hard_rule",
        )
        assert config.weights == {"pb_roe": 0.6, "ddm": 0.4}

    def test_empty_weights_and_importance_uses_equal_distribution(self):
        """No weights or importance → equal distribution."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="generic",
            primary_methods=["pe", "pb", "ev_ebitda"],
            source="fallback",
        )
        assert len(config.weights) == 3
        assert abs(sum(config.weights.values()) - 1.0) < 0.001

    def test_invalid_method_raises_error(self):
        """Invalid valuation method should raise ValueError."""
        from src.agents.valuation_config import ValuationConfig

        with pytest.raises(ValueError, match="非法估值方法"):
            ValuationConfig(
                regime="test",
                primary_methods=["invalid_method"],
                source="llm",
            )


class TestHardRuleDetection:
    """Tests for detect_special_regime() hard rules."""

    def test_bank_detection(self):
        """High DE + loan loss provision → bank regime."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "de_ratio": 12.0,
            "has_loan_loss_provision": True,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "bank"
        assert result.confidence >= 0.90

    def test_insurance_detection(self):
        """DE > 4 + insurance reserve → insurance regime."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "de_ratio": 6.0,
            "has_loan_loss_provision": False,
            "has_insurance_reserve": True,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "insurance"
        assert result.confidence >= 0.90

    def test_real_estate_detection(self):
        """High inventory + advance + asset-light → real_estate regime."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "total_assets": 100_000_000_000,
            "inventory": 50_000_000_000,      # 50%
            "advance_receipts": 15_000_000_000,  # 15%
            "fixed_assets": 3_000_000_000,    # 3% (asset-light)
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "real_estate"

    def test_heavy_manufacturing_not_real_estate(self):
        """High inventory + advance but heavy fixed assets → NOT real_estate."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "total_assets": 100_000_000_000,
            "inventory": 45_000_000_000,      # 45%
            "advance_receipts": 12_000_000_000,  # 12%
            "fixed_assets": 30_000_000_000,   # 30% (heavy assets)
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        # Should NOT match real_estate due to high fixed_assets
        assert result is None or result.regime != "real_estate"

    def test_brand_moat_detection(self):
        """High gross margin + high ROE + stable FCF → brand_moat."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "gross_margin": 75.0,
            "roe_5yr_avg": 22.0,
            "fcf_positive_years": 5,
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "brand_moat"

    def test_no_match_returns_none(self):
        """Normal company metrics should return None (go to LLM)."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "de_ratio": 1.5,
            "gross_margin": 35.0,
            "roe_5yr_avg": 12.0,
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is None