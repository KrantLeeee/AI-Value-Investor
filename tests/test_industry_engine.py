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
