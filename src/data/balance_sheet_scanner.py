"""Balance sheet item scanner — extract industry flags from column names.

Scans raw balance sheet column names to detect industry-specific characteristics:
- Banks: loan loss provisions, interbank deposits
- Insurance: insurance reserves, premium income
"""

BANK_KEYWORDS = [
    "贷款和垫款", "发放贷款及垫款", "吸收存款", "向中央银行借款",
    "贷款损失准备", "贷款减值准备", "应收款项类投资", "存放同业款项",
    "拆出资金", "买入返售金融资产", "应付债券",
]

INSURANCE_KEYWORDS = [
    "未到期责任准备金", "未决赔款准备金", "寿险责任准备金",
    "长期健康险责任准备金", "保户储金及投资款", "保费收入",
    "应付赔付款", "应付保单红利",
]


def extract_industry_flags(raw_balance_sheet_items: list[str]) -> dict[str, bool]:
    """
    Scan balance sheet column names and extract industry flags.

    Args:
        raw_balance_sheet_items: List of column names from balance sheet DataFrame

    Returns:
        dict with has_loan_loss_provision, has_insurance_reserve booleans
    """
    flags = {
        "has_loan_loss_provision": False,
        "has_insurance_reserve": False,
    }

    if not raw_balance_sheet_items:
        return flags

    all_items_str = " ".join(raw_balance_sheet_items)

    # Bank: require >= 2 keyword matches (avoid false positives)
    bank_hits = sum(1 for kw in BANK_KEYWORDS if kw in all_items_str)
    flags["has_loan_loss_provision"] = bank_hits >= 2

    # Insurance: require >= 2 keyword matches
    insurance_hits = sum(1 for kw in INSURANCE_KEYWORDS if kw in all_items_str)
    flags["has_insurance_reserve"] = insurance_hits >= 2

    return flags
