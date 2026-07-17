from src.screening.batch_valuation import _choose_action


def test_choose_action_strong_candidate():
    action, reason = _choose_action(
        margin_of_safety=0.35,
        confidence=0.70,
        quality_score=0.80,
        data_completeness=0.75,
        valid_method_count=3,
        degraded=False,
    )

    assert action == "strong_candidate"
    assert "达标" in reason


def test_choose_action_deep_research_when_methods_degraded_but_discounted():
    action, reason = _choose_action(
        margin_of_safety=0.42,
        confidence=0.45,
        quality_score=0.60,
        data_completeness=0.65,
        valid_method_count=1,
        degraded=True,
    )

    assert action == "deep_research"
    assert "有效估值方法不足" in reason


def test_choose_action_rejects_low_completeness():
    action, reason = _choose_action(
        margin_of_safety=0.80,
        confidence=0.90,
        quality_score=0.90,
        data_completeness=0.20,
        valid_method_count=4,
        degraded=False,
    )

    assert action == "reject"
    assert "数据完整度" in reason


def test_choose_action_rejects_negative_margin():
    action, reason = _choose_action(
        margin_of_safety=-0.15,
        confidence=0.80,
        quality_score=0.90,
        data_completeness=0.90,
        valid_method_count=3,
        degraded=False,
    )

    assert action == "reject"
    assert "高于" in reason


def test_choose_action_rejects_negative_margin_before_degraded_watch():
    action, reason = _choose_action(
        margin_of_safety=-0.15,
        confidence=0.80,
        quality_score=0.90,
        data_completeness=0.90,
        valid_method_count=1,
        degraded=True,
    )

    assert action == "reject"
    assert "高于" in reason
