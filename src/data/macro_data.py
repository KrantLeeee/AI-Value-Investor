"""
macro_data.py
─────────────────────────────────────────────────────────
行业景气度数据模块

数据源：
  - 国家统计局 PMI（制造业 + 非制造业）  via AKShare
  - 财新 PMI（制造业 + 服务业）           via AKShare
  - PPI 生产者价格指数（同比）            via AKShare

设计原则：
  - 只读最新 N 期数据，不做全量历史缓存
  - 所有 API 调用都有 try/except，失败时返回 MacroSnapshot(available=False)
  - 结果对象是 dataclass，方便序列化成 JSON 注入 Prompt
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger
from src.utils.config import get_project_root

logger = get_logger(__name__)

# ─── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class PmiPoint:
    period: str          # "2025-12" 格式
    value: float
    mom_change: float    # 环比变化（与上月差值）
    above_50: bool       # 是否处于扩张区间


@dataclass
class PpiPoint:
    period: str
    yoy: float           # 同比（%）
    mom: float           # 环比（%）
    trend: str           # "deflation" / "mild_deflation" / "stable" / "inflation"


@dataclass
class MacroSnapshot:
    """完整宏观快照，注入 Prompt 时序列化为 JSON"""
    available: bool = False
    fetch_time: str = ""
    periods_fetched: int = 0

    # 国家统计局 PMI
    nbs_manufacturing_pmi: Optional[PmiPoint] = None
    nbs_services_pmi: Optional[PmiPoint] = None

    # 财新 PMI
    caixin_manufacturing_pmi: Optional[PmiPoint] = None
    caixin_services_pmi: Optional[PmiPoint] = None

    # PPI
    ppi: Optional[PpiPoint] = None

    # 综合景气信号（供 Prompt 直接引用）
    manufacturing_signal: str = "unknown"   # "expanding" / "contracting" / "neutral"
    ppi_signal: str = "unknown"             # "inflationary" / "deflationary" / "stable"
    summary_cn: str = ""                    # 中文一句话摘要，供 Prompt 直接粘贴

    errors: list[str] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """生成可直接注入 Prompt 的中文文本块"""
        if not self.available:
            reasons = "; ".join(self.errors) if self.errors else "数据获取失败"
            return f"[宏观数据] 获取失败（{reasons}），本报告宏观景气度分析从略。"

        lines = ["【宏观景气度数据】（供行业背景分析参考，不直接影响估值结论）"]

        if self.nbs_manufacturing_pmi:
            p = self.nbs_manufacturing_pmi
            arrow = "▲" if p.mom_change > 0 else ("▼" if p.mom_change < 0 else "→")
            zone = "扩张区间" if p.above_50 else "收缩区间"
            lines.append(
                f"  • 国家统计局制造业PMI（{p.period}）: {p.value} {arrow}{abs(p.mom_change):.1f} "
                f"[{zone}]"
            )

        if self.caixin_manufacturing_pmi:
            p = self.caixin_manufacturing_pmi
            arrow = "▲" if p.mom_change > 0 else ("▼" if p.mom_change < 0 else "→")
            zone = "扩张区间" if p.above_50 else "收缩区间"
            lines.append(
                f"  • 财新制造业PMI（{p.period}）: {p.value} {arrow}{abs(p.mom_change):.1f} "
                f"[{zone}]"
            )

        if self.nbs_services_pmi:
            p = self.nbs_services_pmi
            zone = "扩张" if p.above_50 else "收缩"
            lines.append(
                f"  • 国家统计局非制造业PMI（{p.period}）: {p.value} [{zone}]"
            )

        if self.ppi:
            p = self.ppi
            lines.append(
                f"  • PPI同比（{p.period}）: {p.yoy:+.1f}%，环比{p.mom:+.1f}%  [{p.trend}]"
            )

        lines.append(f"  综合判断：{self.summary_cn}")
        lines.append(
            "  [注意] 以上宏观数据仅作为行业背景参考。"
            "若宏观信号与个股基本面矛盾，请在风险因素章节说明，不应自动下调估值置信度。"
        )
        return "\n".join(lines)

    def to_risk_factor_text(self, industry_type: str) -> str:
        """生成供风险章节使用的宏观风险因子描述"""
        if not self.available:
            return ""
        risks = []
        if self.manufacturing_signal == "contracting":
            risks.append(
                f"宏观制造业PMI处于收缩区间（<50），"
                f"对{industry_type}类股票的订单和盈利能力构成潜在压力"
            )
        if self.ppi_signal in ("deflation", "mild_deflation"):
            risks.append(
                f"PPI持续负增长，上游原材料价格下行可能影响{industry_type}行业的营收规模"
            )
        if not risks:
            return ""
        return (
            "宏观景气度风险：" + "；".join(risks) +
            "。（注：个股基本面可能优于行业整体，需结合具体数据判断）"
        )


# ─── AKShare 数据获取 ──────────────────────────────────────────────────────────


def _classify_ppi_trend(yoy: float) -> str:
    if yoy <= -3.0:
        return "deflation"
    elif yoy < 0:
        return "mild_deflation"
    elif yoy < 2.0:
        return "stable"
    else:
        return "inflation"


def _compute_mom(series: list[float]) -> float:
    """计算最新两期的环比差值"""
    if len(series) < 2:
        return 0.0
    return round(series[-1] - series[-2], 2)


def fetch_nbs_manufacturing_pmi(n: int = 3) -> Optional[PmiPoint]:
    """
    国家统计局制造业PMI
    AKShare: ak.macro_china_pmi_yearly()
    返回最新一期
    """
    try:
        import akshare as ak
        df = ak.macro_china_pmi_yearly()
        # 列名：月份, 制造业-指数, 制造业-同比增长, 非制造业-指数, ...
        # 取最近 n 行
        df = df.tail(n).reset_index(drop=True)
        values = df["制造业-指数"].astype(float).tolist()
        latest = values[-1]
        period = str(df.iloc[-1]["月份"])[:7]  # "YYYY-MM"
        mom = _compute_mom(values)
        return PmiPoint(period=period, value=latest, mom_change=mom, above_50=latest >= 50.0)
    except Exception as e:
        logger.warning(f"NBS Manufacturing PMI fetch failed: {e}")
        return None


def fetch_nbs_services_pmi(n: int = 3) -> Optional[PmiPoint]:
    """国家统计局非制造业PMI"""
    try:
        import akshare as ak
        df = ak.macro_china_pmi_yearly()
        df = df.tail(n).reset_index(drop=True)
        values = df["非制造业-指数"].astype(float).tolist()
        latest = values[-1]
        period = str(df.iloc[-1]["月份"])[:7]
        mom = _compute_mom(values)
        return PmiPoint(period=period, value=latest, mom_change=mom, above_50=latest >= 50.0)
    except Exception as e:
        logger.warning(f"NBS Services PMI fetch failed: {e}")
        return None


def fetch_caixin_manufacturing_pmi(n: int = 3) -> Optional[PmiPoint]:
    """
    财新制造业PMI
    AKShare: ak.macro_china_cx_pmi_yearly()
    """
    try:
        import akshare as ak
        df = ak.macro_china_cx_pmi_yearly()
        df = df.tail(n).reset_index(drop=True)
        # 财新列名通常为 "今值" 或 "制造业PMI"
        col = [c for c in df.columns if "今值" in c or "pmi" in c.lower() or "PMI" in c]
        if not col:
            col = df.columns[1:2].tolist()
        values = df[col[0]].astype(float).tolist()
        latest = values[-1]
        period = str(df.iloc[-1].iloc[0])[:7]
        mom = _compute_mom(values)
        return PmiPoint(period=period, value=latest, mom_change=mom, above_50=latest >= 50.0)
    except Exception as e:
        logger.warning(f"Caixin Manufacturing PMI fetch failed: {e}")
        return None


def fetch_caixin_services_pmi(n: int = 3) -> Optional[PmiPoint]:
    """
    财新服务业PMI
    AKShare: ak.macro_china_cx_services_pmi_yearly()
    """
    try:
        import akshare as ak
        df = ak.macro_china_cx_services_pmi_yearly()
        df = df.tail(n).reset_index(drop=True)
        col = [c for c in df.columns if "今值" in c or "pmi" in c.lower() or "PMI" in c]
        if not col:
            col = df.columns[1:2].tolist()
        values = df[col[0]].astype(float).tolist()
        latest = values[-1]
        period = str(df.iloc[-1].iloc[0])[:7]
        mom = _compute_mom(values)
        return PmiPoint(period=period, value=latest, mom_change=mom, above_50=latest >= 50.0)
    except Exception as e:
        logger.warning(f"Caixin Services PMI fetch failed: {e}")
        return None


def fetch_ppi(n: int = 3) -> Optional[PpiPoint]:
    """
    PPI 生产者价格指数（同比 + 环比）
    AKShare: ak.macro_china_ppi()
    """
    try:
        import akshare as ak
        df = ak.macro_china_ppi()
        df = df.tail(n).reset_index(drop=True)
        # 列名：月份, 当月, 当月同比, 当月环比  (或类似)
        # 先找同比列
        yoy_col = [c for c in df.columns if "同比" in c]
        mom_col = [c for c in df.columns if "环比" in c]
        period_col = df.columns[0]

        yoy = float(df.iloc[-1][yoy_col[0]]) if yoy_col else 0.0
        mom = float(df.iloc[-1][mom_col[0]]) if mom_col else 0.0
        period = str(df.iloc[-1][period_col])[:7]

        return PpiPoint(
            period=period,
            yoy=yoy,
            mom=mom,
            trend=_classify_ppi_trend(yoy)
        )
    except Exception as e:
        logger.warning(f"PPI fetch failed: {e}")
        return None


def _build_manufacturing_signal(
    nbs: Optional[PmiPoint],
    caixin: Optional[PmiPoint]
) -> str:
    """综合两个PMI给出制造业景气信号"""
    signals = []
    if nbs:
        signals.append("expanding" if nbs.above_50 else "contracting")
    if caixin:
        signals.append("expanding" if caixin.above_50 else "contracting")
    if not signals:
        return "unknown"
    # 两个都扩张 → expanding；两个都收缩 → contracting；否则 neutral
    if all(s == "expanding" for s in signals):
        return "expanding"
    elif all(s == "contracting" for s in signals):
        return "contracting"
    return "neutral"


def _build_summary_cn(snapshot: MacroSnapshot) -> str:
    """生成一句话中文摘要"""
    parts = []
    mfg = snapshot.manufacturing_signal
    if mfg == "expanding":
        parts.append("制造业景气度处于扩张区间")
    elif mfg == "contracting":
        parts.append("制造业景气度处于收缩区间")
    else:
        parts.append("制造业景气度信号分歧")

    ppi = snapshot.ppi_signal
    if ppi == "inflation":
        parts.append("PPI正增长（上游价格上行）")
    elif ppi in ("deflation", "mild_deflation"):
        parts.append("PPI负增长（上游价格承压）")
    else:
        parts.append("PPI基本稳定")

    return "；".join(parts) + "。"


# ─── 主入口 ──────────────────────────────────────────────────────────────────


def get_macro_snapshot(use_cache: bool = True, cache_ttl_hours: int = 4) -> MacroSnapshot:
    """
    获取完整宏观快照。

    use_cache=True 时会先检查本地 JSON 缓存（默认 4 小时 TTL），
    避免每次生成报告都重复调用 API。

    缓存路径：data/cache/macro_snapshot.json
    """
    cache_path = get_project_root() / "data" / "cache" / "macro_snapshot.json"

    # ── 读缓存 ──
    if use_cache and cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            fetch_time = datetime.fromisoformat(cached.get("fetch_time", "2000-01-01"))
            if datetime.now() - fetch_time < timedelta(hours=cache_ttl_hours):
                logger.info(f"[Macro] Using cached snapshot from {fetch_time}")
                return _from_dict(cached)
        except Exception as e:
            logger.warning(f"[Macro] Cache read failed, re-fetching: {e}")

    # ── 实时获取 ──
    snap = MacroSnapshot(
        available=False,
        fetch_time=datetime.now().isoformat()
    )
    errors = []

    snap.nbs_manufacturing_pmi = fetch_nbs_manufacturing_pmi()
    if not snap.nbs_manufacturing_pmi:
        errors.append("NBS制造业PMI获取失败")

    snap.nbs_services_pmi = fetch_nbs_services_pmi()
    if not snap.nbs_services_pmi:
        errors.append("NBS非制造业PMI获取失败")

    snap.caixin_manufacturing_pmi = fetch_caixin_manufacturing_pmi()
    if not snap.caixin_manufacturing_pmi:
        errors.append("财新制造业PMI获取失败")

    snap.caixin_services_pmi = fetch_caixin_services_pmi()
    # 财新服务业PMI 可选，失败不算错

    snap.ppi = fetch_ppi()
    if not snap.ppi:
        errors.append("PPI获取失败")

    # ── 综合信号 ──
    snap.manufacturing_signal = _build_manufacturing_signal(
        snap.nbs_manufacturing_pmi,
        snap.caixin_manufacturing_pmi
    )
    snap.ppi_signal = snap.ppi.trend if snap.ppi else "unknown"
    snap.errors = errors

    # 只要有任意一个数据获取成功，就标记为 available
    snap.available = any([
        snap.nbs_manufacturing_pmi,
        snap.caixin_manufacturing_pmi,
        snap.ppi
    ])
    snap.periods_fetched = sum([
        snap.nbs_manufacturing_pmi is not None,
        snap.nbs_services_pmi is not None,
        snap.caixin_manufacturing_pmi is not None,
        snap.caixin_services_pmi is not None,
        snap.ppi is not None,
    ])

    snap.summary_cn = _build_summary_cn(snap)

    # ── 写缓存 ──
    if snap.available and use_cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(_to_dict(snap), f, ensure_ascii=False, indent=2)
            logger.info(f"[Macro] Cached snapshot to {cache_path}")
        except Exception as e:
            logger.warning(f"[Macro] Cache write failed: {e}")

    return snap


# ─── 序列化辅助 ──────────────────────────────────────────────────────────────


def _to_dict(snap: MacroSnapshot) -> dict:
    return asdict(snap)


def _from_dict(d: dict) -> MacroSnapshot:
    """从 dict 反序列化（用于缓存读取）"""
    def _pmi(x):
        return PmiPoint(**x) if x else None
    def _ppi(x):
        return PpiPoint(**x) if x else None

    s = MacroSnapshot(
        available=d.get("available", False),
        fetch_time=d.get("fetch_time", ""),
        periods_fetched=d.get("periods_fetched", 0),
        nbs_manufacturing_pmi=_pmi(d.get("nbs_manufacturing_pmi")),
        nbs_services_pmi=_pmi(d.get("nbs_services_pmi")),
        caixin_manufacturing_pmi=_pmi(d.get("caixin_manufacturing_pmi")),
        caixin_services_pmi=_pmi(d.get("caixin_services_pmi")),
        ppi=_ppi(d.get("ppi")),
        manufacturing_signal=d.get("manufacturing_signal", "unknown"),
        ppi_signal=d.get("ppi_signal", "unknown"),
        summary_cn=d.get("summary_cn", ""),
        errors=d.get("errors", [])
    )
    return s
