"""
test_macro_module.py
─────────────────────────────────────────────────────────
不依赖网络的单元测试（使用 mock 数据）
运行方式：python -m pytest tests/test_macro_module.py -v
"""

import pytest
from src.data.macro_data import (
    MacroSnapshot, PmiPoint, PpiPoint,
    _build_manufacturing_signal, _build_summary_cn, _classify_ppi_trend,
    _to_dict, _from_dict
)
from src.data.industry_macro_mapping import (
    get_industry_type, get_relevant_indicators, get_macro_prompt_context,
    INDUSTRY_PROFILES
)


# ─── 测试数据工厂 ─────────────────────────────────────────────────────────────


def make_expanding_snapshot() -> MacroSnapshot:
    """制造业扩张 + PPI轻微通胀的快照"""
    s = MacroSnapshot(
        available=True,
        fetch_time="2026-03-08T10:00:00",
        nbs_manufacturing_pmi=PmiPoint("2026-02", 50.2, +0.3, True),
        nbs_services_pmi=PmiPoint("2026-02", 52.1, -0.2, True),
        caixin_manufacturing_pmi=PmiPoint("2026-02", 50.8, +0.5, True),
        ppi=PpiPoint("2026-02", yoy=0.8, mom=0.1, trend="stable"),
        manufacturing_signal="expanding",
        ppi_signal="stable",
        periods_fetched=4,
    )
    s.summary_cn = _build_summary_cn(s)
    return s


def make_contracting_snapshot() -> MacroSnapshot:
    """制造业收缩 + PPI通缩的快照（周期股最恶劣场景）"""
    s = MacroSnapshot(
        available=True,
        fetch_time="2026-03-08T10:00:00",
        nbs_manufacturing_pmi=PmiPoint("2026-02", 48.9, -0.6, False),
        nbs_services_pmi=PmiPoint("2026-02", 50.3, +0.1, True),
        caixin_manufacturing_pmi=PmiPoint("2026-02", 48.2, -0.8, False),
        ppi=PpiPoint("2026-02", yoy=-2.1, mom=-0.3, trend="mild_deflation"),
        manufacturing_signal="contracting",
        ppi_signal="mild_deflation",
        periods_fetched=4,
    )
    s.summary_cn = _build_summary_cn(s)
    return s


def make_unavailable_snapshot() -> MacroSnapshot:
    return MacroSnapshot(
        available=False,
        errors=["NBS PMI获取失败", "财新PMI获取失败"],
    )


# ─── 测试用例 ─────────────────────────────────────────────────────────────────


class TestPmiSignal:
    def test_both_expanding(self):
        nbs = PmiPoint("2026-02", 50.5, 0.2, True)
        caixin = PmiPoint("2026-02", 50.8, 0.3, True)
        assert _build_manufacturing_signal(nbs, caixin) == "expanding"

    def test_both_contracting(self):
        nbs = PmiPoint("2026-02", 49.5, -0.2, False)
        caixin = PmiPoint("2026-02", 49.2, -0.3, False)
        assert _build_manufacturing_signal(nbs, caixin) == "contracting"

    def test_divergent_signals(self):
        nbs = PmiPoint("2026-02", 50.5, 0.2, True)
        caixin = PmiPoint("2026-02", 49.5, -0.3, False)
        assert _build_manufacturing_signal(nbs, caixin) == "neutral"

    def test_only_nbs(self):
        nbs = PmiPoint("2026-02", 50.5, 0.2, True)
        assert _build_manufacturing_signal(nbs, None) == "expanding"

    def test_both_none(self):
        assert _build_manufacturing_signal(None, None) == "unknown"


class TestPpiClassification:
    def test_deflation(self):
        assert _classify_ppi_trend(-4.0) == "deflation"

    def test_mild_deflation(self):
        assert _classify_ppi_trend(-1.5) == "mild_deflation"

    def test_stable(self):
        assert _classify_ppi_trend(0.8) == "stable"

    def test_inflation(self):
        assert _classify_ppi_trend(3.2) == "inflation"


class TestIndustryMapping:
    def test_known_tickers(self):
        assert get_industry_type("002230.SZ") == "ai_tech_loss"       # 科大讯飞
        assert get_industry_type("601808.SH") == "energy_services"    # 中海油服
        assert get_industry_type("601318.SH") == "financial_insurance"  # 平安
        assert get_industry_type("300124.SZ") == "industrial_automation"  # 汇川

    def test_unknown_ticker(self):
        assert get_industry_type("999999.SH") == "unknown"

    def test_relevant_indicators_for_energy(self):
        indicators = get_relevant_indicators("601808.SH")
        assert "ppi" in indicators

    def test_relevant_indicators_for_ai(self):
        indicators = get_relevant_indicators("002230.SZ")
        assert "nbs_svc_pmi" in indicators

    def test_all_profiles_have_required_fields(self):
        for name, profile in INDUSTRY_PROFILES.items():
            assert profile.tailwind_cn, f"{name} missing tailwind_cn"
            assert profile.headwind_cn, f"{name} missing headwind_cn"
            assert len(profile.primary_indicators) > 0, f"{name} has no indicators"


class TestMacroSnapshot:
    def test_expanding_snapshot_prompt(self):
        snap = make_expanding_snapshot()
        text = snap.to_prompt_context()
        assert "扩张区间" in text
        assert "PMI" in text
        assert "不应自动下调估值置信度" in text  # 确认解耦文字存在

    def test_contracting_snapshot_prompt(self):
        snap = make_contracting_snapshot()
        text = snap.to_prompt_context()
        assert "收缩区间" in text
        assert "PPI" in text

    def test_unavailable_snapshot_prompt(self):
        snap = make_unavailable_snapshot()
        text = snap.to_prompt_context()
        assert "获取失败" in text
        # 确认 unavailable 时不崩溃

    def test_risk_factor_text_contracting(self):
        snap = make_contracting_snapshot()
        risk_text = snap.to_risk_factor_text("工业自动化")
        assert "收缩区间" in risk_text
        assert "不等于个股结论" in risk_text or "个股基本面" in risk_text

    def test_risk_factor_text_expanding_no_risk(self):
        snap = make_expanding_snapshot()
        risk_text = snap.to_risk_factor_text("工业自动化")
        # 扩张时无负面风险，应返回空字符串
        assert risk_text == ""

    def test_serialization_roundtrip(self):
        snap = make_expanding_snapshot()
        d = _to_dict(snap)
        snap2 = _from_dict(d)
        assert snap2.available == snap.available
        assert snap2.manufacturing_signal == snap.manufacturing_signal
        assert snap2.nbs_manufacturing_pmi.value == snap.nbs_manufacturing_pmi.value
        assert snap2.ppi.yoy == snap.ppi.yoy


class TestMacroPromptContext:
    def test_expanding_for_industrial_automation(self):
        """汇川技术（工业自动化），制造业扩张，应该显示顺风信号"""
        snap = make_expanding_snapshot()
        text = get_macro_prompt_context("300124.SZ", snap)
        assert "顺风" in text
        assert "工业自动化" in text or "设备" in text

    def test_contracting_for_energy_services(self):
        """中海油服，制造业收缩，PPI下行，应该显示逆风信号"""
        snap = make_contracting_snapshot()
        text = get_macro_prompt_context("601808.SH", snap)
        assert "逆风" in text or "PPI" in text

    def test_expanding_for_consumer_brand(self):
        """茅台，服务业PMI扩张，消费景气改善"""
        snap = make_expanding_snapshot()
        text = get_macro_prompt_context("600519.SH", snap)
        assert "消费" in text or "品牌" in text or "服务业" in text

    def test_unknown_ticker_no_crash(self):
        """未知股票代码不崩溃，返回通用宏观数据"""
        snap = make_expanding_snapshot()
        text = get_macro_prompt_context("999999.SH", snap)
        assert "PMI" in text  # 有数据

    def test_unavailable_snapshot_for_any_ticker(self):
        snap = make_unavailable_snapshot()
        text = get_macro_prompt_context("300124.SZ", snap)
        assert "获取失败" in text


class TestDecouplingConstraint:
    """关键约束测试：确认宏观数据不改变估值结论"""

    def test_prompt_contains_decoupling_instruction(self):
        """Prompt 里必须包含解耦说明，防止 LLM 用宏观数据改变估值结论"""
        snap = make_contracting_snapshot()
        text = snap.to_prompt_context()
        # 关键解耦文字必须存在
        assert "不应自动下调估值置信度" in text

    def test_risk_text_contains_individual_stock_caveat(self):
        """风险文字必须包含「个股可能例外」的说明"""
        snap = make_contracting_snapshot()
        risk_text = snap.to_risk_factor_text("制造业")
        if risk_text:  # 只在有风险时测试
            assert "个股基本面" in risk_text or "具体数据" in risk_text
