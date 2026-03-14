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
