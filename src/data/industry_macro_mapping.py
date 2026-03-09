"""
industry_macro_mapping.py
─────────────────────────────────────────────────────────
行业类型 → 关联宏观指标 的映射配置

使用方式：
    from src.data.industry_macro_mapping import get_macro_prompt_context, INDUSTRY_PROFILES

每个 IndustryMacroProfile 定义：
  - primary_indicators : 对该行业影响最直接的宏观指标
  - pmi_sensitivity    : PMI变化对该行业的传导速度 ("fast"=1-2月 / "medium"=3-6月 / "slow">6月)
  - ppi_direction      : PPI上行对该行业是利好(+1)还是利空(-1)还是中性(0)
  - macro_risk_description : 用于生成风险提示文本的模板
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.macro_data import MacroSnapshot

PmiSensitivity = Literal["fast", "medium", "slow", "na"]
PpiEffect = Literal["positive", "negative", "neutral", "na"]


@dataclass
class IndustryMacroProfile:
    industry_type: str
    name_cn: str
    description_cn: str

    # 哪些宏观指标与该行业最相关
    primary_indicators: list[str]   # 可选值: "nbs_mfg_pmi","caixin_mfg_pmi","nbs_svc_pmi","caixin_svc_pmi","ppi"

    pmi_sensitivity: PmiSensitivity
    ppi_effect: PpiEffect           # PPI上行对本行业是好事还是坏事

    # 景气度上行时对个股的典型影响（注入Prompt用）
    tailwind_cn: str
    # 景气度下行时对个股的典型影响
    headwind_cn: str

    # A股代表标的（用于示例和测试）
    example_tickers: list[str]


# ─── 八大行业配置 ─────────────────────────────────────────────────────────────

INDUSTRY_PROFILES: dict[str, IndustryMacroProfile] = {

    "industrial_automation": IndustryMacroProfile(
        industry_type="industrial_automation",
        name_cn="工业自动化/设备制造",
        description_cn="机床、工业机器人、PLC、传感器等制造业核心设备",
        primary_indicators=["nbs_mfg_pmi", "caixin_mfg_pmi", "ppi"],
        pmi_sensitivity="medium",
        ppi_effect="negative",  # PPI上行→原材料成本上升→利空
        tailwind_cn="制造业PMI扩张通常带动设备采购需求增加，有利于订单增长和毛利率修复",
        headwind_cn="制造业PMI收缩导致下游资本开支收缩，设备订单可能延迟或取消",
        example_tickers=["300124.SZ"]  # 汇川技术
    ),

    "energy_services": IndustryMacroProfile(
        industry_type="energy_services",
        name_cn="能源服务/油气服务",
        description_cn="石油钻探、油田服务、能源工程技术服务",
        primary_indicators=["ppi"],   # 油价通过PPI传导
        pmi_sensitivity="slow",
        ppi_effect="positive",        # PPI/油价上行→行业景气上行→利好
        tailwind_cn="油价上行带动勘探开发支出增加，油田服务需求扩张，收入增速改善",
        headwind_cn="油价下行导致甲方削减开支，订单减少，议价能力下降，利润率承压",
        example_tickers=["601808.SH"]  # 中海油服
    ),

    "consumer_brand": IndustryMacroProfile(
        industry_type="consumer_brand",
        name_cn="消费品/品牌消费",
        description_cn="食品饮料、家电、服装等有品牌护城河的消费类企业",
        primary_indicators=["nbs_svc_pmi", "caixin_svc_pmi"],
        pmi_sensitivity="medium",
        ppi_effect="negative",        # PPI上行→原料成本上升→短期利空，但强品牌可转嫁
        tailwind_cn="服务业PMI扩张对应消费者信心改善，终端需求和动销数据有望回升",
        headwind_cn="服务业PMI收缩对应消费降级压力，高端品类可能承受量价双降",
        example_tickers=["600519.SH", "603288.SH"]  # 茅台, 海天味业
    ),

    "financial_insurance": IndustryMacroProfile(
        industry_type="financial_insurance",
        name_cn="金融/银行/保险",
        description_cn="商业银行、寿险、财险、券商",
        primary_indicators=["nbs_mfg_pmi", "nbs_svc_pmi"],
        pmi_sensitivity="slow",
        ppi_effect="neutral",
        tailwind_cn="经济景气度上行改善银行信贷需求、降低不良率，保险行业保费增速加快",
        headwind_cn="PMI持续收缩可能引发信用风险担忧，增加银行拨备压力",
        example_tickers=["601318.SH", "600036.SH"]  # 平安, 招行
    ),

    "ai_tech_loss": IndustryMacroProfile(
        industry_type="ai_tech_loss",
        name_cn="AI/科技（亏损期）",
        description_cn="处于大规模研发投入期、尚未实现持续盈利的AI和科技公司",
        primary_indicators=["nbs_svc_pmi", "caixin_svc_pmi"],
        pmi_sensitivity="slow",
        ppi_effect="neutral",
        tailwind_cn="服务业景气改善加速企业数字化投入，政府/企业IT预算扩张有利于To-B业务落地",
        headwind_cn="经济下行压力下企业和政府IT预算收缩，可能延迟AI项目采购决策",
        example_tickers=["002230.SZ"]  # 科大讯飞
    ),

    "ai_tech_profitable": IndustryMacroProfile(
        industry_type="ai_tech_profitable",
        name_cn="AI/科技（盈利期）",
        description_cn="已实现稳定盈利的科技成长股，具有规模化商业模式",
        primary_indicators=["nbs_svc_pmi", "caixin_svc_pmi"],
        pmi_sensitivity="slow",
        ppi_effect="neutral",
        tailwind_cn="服务业扩张推动下游数字化升级需求，利好SaaS和平台型科技公司",
        headwind_cn="服务业收缩导致企业客户缩减IT开支，订单增速可能不及预期",
        example_tickers=["300124.SZ", "688169.SH"]  # 汇川, 石头
    ),

    "pharma_biotech": IndustryMacroProfile(
        industry_type="pharma_biotech",
        name_cn="医药/生物科技",
        description_cn="创新药、仿制药、CXO、医疗器械",
        primary_indicators=["nbs_svc_pmi"],  # 医疗服务景气
        pmi_sensitivity="slow",
        ppi_effect="negative",
        tailwind_cn="医疗支出需求刚性，PMI波动对医药基本面影响有限；但经济景气时创新药融资更顺畅",
        headwind_cn="医保控费在经济收缩期可能加强，仿制药降价压力增大",
        example_tickers=["600276.SH", "300750.SZ"]
    ),

    "utility_infrastructure": IndustryMacroProfile(
        industry_type="utility_infrastructure",
        name_cn="公用事业/基础设施",
        description_cn="水电、火电、燃气、高速公路",
        primary_indicators=["nbs_mfg_pmi", "ppi"],
        pmi_sensitivity="medium",
        ppi_effect="negative",  # PPI上行→燃料成本上升→利空非水电
        tailwind_cn="制造业扩张带动电力需求增长，利用率提升",
        headwind_cn="制造业收缩致用电量减少，固定成本摊薄压力增大",
        example_tickers=["600900.SH"]  # 长江电力
    ),

    "cyclical_materials": IndustryMacroProfile(
        industry_type="cyclical_materials",
        name_cn="周期性资源/材料",
        description_cn="钢铁、煤炭、有色金属、化工原料",
        primary_indicators=["nbs_mfg_pmi", "caixin_mfg_pmi", "ppi"],
        pmi_sensitivity="fast",
        ppi_effect="positive",  # PPI上行=价格上行=直接利好
        tailwind_cn="制造业扩张推动大宗商品需求，叠加PPI上行，量价齐升是最佳组合",
        headwind_cn="PMI收缩+PPI负增长是周期股最恶劣的宏观组合，注意周期底部定价逻辑切换",
        example_tickers=["600519.SH"]
    ),
}

# ─── 股票代码 → 行业类型 快速映射 ────────────────────────────────────────────

# 可根据实际覆盖标的扩展
TICKER_TO_INDUSTRY: dict[str, str] = {
    # 工业自动化
    "300124.SZ": "industrial_automation",   # 汇川技术
    "002463.SZ": "industrial_automation",   # 沪电股份
    # 能源服务
    "601808.SH": "energy_services",          # 中海油服
    "600583.SH": "energy_services",          # 海油工程
    # 消费品
    "600519.SH": "consumer_brand",           # 贵州茅台
    "603288.SH": "consumer_brand",           # 海天味业
    "000858.SZ": "consumer_brand",           # 五粮液
    # 金融
    "601318.SH": "financial_insurance",      # 中国平安
    "600036.SH": "financial_insurance",      # 招商银行
    "601628.SH": "financial_insurance",      # 中国人寿
    # AI科技-亏损期
    "002230.SZ": "ai_tech_loss",             # 科大讯飞
    "688111.SH": "ai_tech_loss",             # 金山办公（部分亏损期）
    # AI科技-盈利期
    "688169.SH": "ai_tech_profitable",       # 石头科技
    "688036.SH": "ai_tech_profitable",       # 传音控股
    # 医药
    "600276.SH": "pharma_biotech",           # 恒瑞医药
    "300750.SZ": "pharma_biotech",           # 宁德时代（暂归此类）
    # 公用事业
    "600900.SH": "utility_infrastructure",   # 长江电力
    "601985.SH": "utility_infrastructure",   # 中国核电
    # 数据中心（特殊行业）
    "603881.SH": "utility_infrastructure",   # 数据港（IDC按公用事业估值）
}


def get_industry_type(ticker: str) -> str:
    """根据股票代码获取行业类型，未知时返回 'unknown'"""
    return TICKER_TO_INDUSTRY.get(ticker, "unknown")


def get_relevant_indicators(ticker: str) -> list[str]:
    """返回该标的最相关的宏观指标列表"""
    industry_type = get_industry_type(ticker)
    if industry_type == "unknown":
        return ["nbs_mfg_pmi", "ppi"]   # 默认返回最通用的两个
    profile = INDUSTRY_PROFILES.get(industry_type)
    return profile.primary_indicators if profile else []


def get_macro_prompt_context(ticker: str, macro_snapshot: "MacroSnapshot") -> str:
    """
    主接口：给定股票代码和宏观快照，
    返回专为该行业定制的宏观上下文文本，供注入 Ch1 行业背景 Prompt。

    macro_snapshot: MacroSnapshot 对象（来自 macro_data.py）
    """
    if not macro_snapshot.available:
        return macro_snapshot.to_prompt_context()

    industry_type = get_industry_type(ticker)
    profile = INDUSTRY_PROFILES.get(industry_type)

    # 基础数据块
    base_text = macro_snapshot.to_prompt_context()

    if not profile:
        return base_text

    # 拼接行业专属解读
    lines = [base_text, ""]
    lines.append(f"【该行业（{profile.name_cn}）的宏观传导分析】")
    lines.append(f"  关键指标: {', '.join(profile.primary_indicators)}")
    lines.append(f"  PMI传导速度: {profile.pmi_sensitivity}")

    mfg_signal = macro_snapshot.manufacturing_signal
    ppi_signal = macro_snapshot.ppi_signal

    # 判断顺风/逆风
    is_mfg_relevant = any(
        ind in profile.primary_indicators
        for ind in ["nbs_mfg_pmi", "caixin_mfg_pmi"]
    )
    is_svc_relevant = any(
        ind in profile.primary_indicators
        for ind in ["nbs_svc_pmi", "caixin_svc_pmi"]
    )

    if is_mfg_relevant and mfg_signal == "expanding":
        lines.append(f"  ✓ 当前顺风：{profile.tailwind_cn}")
    elif is_mfg_relevant and mfg_signal == "contracting":
        lines.append(f"  ✗ 当前逆风：{profile.headwind_cn}")

    if "ppi" in profile.primary_indicators:
        ppi_is_up = ppi_signal in ("inflation",)
        ppi_is_down = ppi_signal in ("deflation", "mild_deflation")
        if ppi_is_up and profile.ppi_effect == "positive":
            lines.append(f"  ✓ PPI上行对本行业利好：{profile.tailwind_cn}")
        elif ppi_is_down and profile.ppi_effect == "negative":
            lines.append(f"  ✓ PPI下行降低本行业成本压力")
        elif ppi_is_up and profile.ppi_effect == "negative":
            lines.append(f"  ✗ PPI上行增加本行业原材料成本压力")
        elif ppi_is_down and profile.ppi_effect == "positive":
            lines.append(f"  ✗ PPI下行压低本行业产品价格")

    lines.append(
        "  [行业背景写作指引] 请在行业背景章节中简要提及上述宏观景气度，"
        "并说明其对公司的潜在影响方向。"
        "若宏观信号与个股基本面矛盾，不要在此章节解决矛盾，留到风险章节辩证讨论。"
    )

    return "\n".join(lines)
