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
