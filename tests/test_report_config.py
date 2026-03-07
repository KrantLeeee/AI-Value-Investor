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
    text = "这是一个测试章节。" * 20  # 180 chars (9 chars * 20)

    issues = validate_chapter(text, config)
    assert len(issues) == 1
    assert "字数不足" in issues[0]
    assert "180/400" in issues[0]


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
