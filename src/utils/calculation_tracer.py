"""P0-2: Calculation transparency utility.

Records how derived metrics are computed, enabling full traceability.
"""

from datetime import datetime
from typing import Any

from src.data.models import CalculationTrace
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CalculationTracer:
    """
    Traces calculations for derived metrics.

    Usage:
        tracer = CalculationTracer()
        roe = tracer.trace_calculation(
            metric_name="ROE",
            formula="net_income / total_equity * 100",
            inputs={"net_income": {...}, "total_equity": {...}},
            result=25.0,
            unit="%"
        )
        # Later: tracer.explain("ROE") -> "ROE = 25.0% = 10亿 / 40亿 * 100"
    """

    def __init__(self):
        self._traces: list[CalculationTrace] = []

    def trace_calculation(
        self,
        metric_name: str,
        formula: str,
        inputs: dict[str, dict],
        result: float,
        unit: str = "",
    ) -> float:
        """
        Record a calculation and return the result.

        Args:
            metric_name: Name of the metric (e.g., "ROE")
            formula: Human-readable formula
            inputs: Dict of input names to {value, source, period}
            result: Calculated result
            unit: Unit string (e.g., "%", "days")

        Returns:
            The result value (passthrough for convenience)
        """
        trace = CalculationTrace(
            metric_name=metric_name,
            result_value=result,
            formula=formula,
            inputs=inputs,
            unit=unit,
        )
        self._traces.append(trace)
        logger.debug("Traced calculation: %s = %.2f%s", metric_name, result, unit)
        return result

    def get_traces(self) -> list[CalculationTrace]:
        """Return all recorded traces."""
        return self._traces.copy()

    def get_trace(self, metric_name: str) -> CalculationTrace | None:
        """Get trace for a specific metric."""
        for trace in self._traces:
            if trace.metric_name == metric_name:
                return trace
        return None

    def explain(self, metric_name: str) -> str:
        """
        Generate human-readable explanation of a calculation.

        Returns:
            String like "ROE = 25.0% = net_income(10亿) / total_equity(40亿) * 100"
        """
        trace = self.get_trace(metric_name)
        if not trace:
            return f"{metric_name}: No calculation trace available"

        def _format_value(v: float) -> str:
            """Format large numbers in Chinese units."""
            if abs(v) >= 1e12:
                return f"{v/1e12:.2f}万亿"
            elif abs(v) >= 1e8:
                return f"{v/1e8:.2f}亿"
            elif abs(v) >= 1e4:
                return f"{v/1e4:.2f}万"
            return f"{v:,.2f}"

        # Build explanation
        inputs_str = ", ".join(
            f"{name}={_format_value(data['value'])}[{data.get('source', '?')}]"
            for name, data in trace.inputs.items()
        )

        return (
            f"{trace.metric_name} = {trace.result_value:.2f}{trace.unit}\n"
            f"  公式: {trace.formula}\n"
            f"  输入: {inputs_str}"
        )

    def clear(self) -> None:
        """Clear all traces."""
        self._traces = []
