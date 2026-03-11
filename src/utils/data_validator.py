"""P0-2: Multi-source data cross-validation.

Tracks data observations from multiple sources and validates consistency.
"""

from collections import defaultdict
from datetime import datetime

from src.data.models import DataValidationStatus
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MultiSourceValidator:
    """
    Validates data by comparing observations from multiple sources.

    Usage:
        validator = MultiSourceValidator(tolerance_pct=5.0)
        validator.add_observation("net_income", 1e9, "akshare")
        validator.add_observation("net_income", 1.02e9, "eastmoney")
        status = validator.validate("net_income")
        # status.is_validated == True (within 5% tolerance)
    """

    def __init__(self, tolerance_pct: float = 5.0):
        """
        Args:
            tolerance_pct: Max allowed discrepancy (%) for validation to pass
        """
        self.tolerance_pct = tolerance_pct
        self._observations: dict[str, list[tuple[float, str]]] = defaultdict(list)

    def add_observation(self, field_name: str, value: float, source: str) -> None:
        """Add an observed value for a field from a source."""
        self._observations[field_name].append((value, source))
        logger.debug("Observation: %s = %.2f from %s", field_name, value, source)

    def validate(self, field_name: str) -> DataValidationStatus:
        """
        Validate a field by checking source agreement.

        Returns:
            DataValidationStatus with is_validated=True if ≥2 sources agree
        """
        observations = self._observations.get(field_name, [])

        if not observations:
            return DataValidationStatus(
                field_name=field_name,
                value=0.0,
                sources=[],
                is_validated=False,
                discrepancy_pct=0.0,
            )

        values = [obs[0] for obs in observations]
        sources = [obs[1] for obs in observations]

        # Use first value as reference
        ref_value = values[0]

        # Calculate max discrepancy
        if ref_value != 0:
            discrepancies = [abs(v - ref_value) / abs(ref_value) * 100 for v in values]
            max_discrepancy = max(discrepancies)
        else:
            max_discrepancy = 0.0 if all(v == 0 for v in values) else 100.0

        # Validated if ≥2 sources and within tolerance
        is_validated = len(sources) >= 2 and max_discrepancy <= self.tolerance_pct

        return DataValidationStatus(
            field_name=field_name,
            value=ref_value,
            sources=sources,
            is_validated=is_validated,
            discrepancy_pct=round(max_discrepancy, 2),
        )

    def get_validation_summary(self) -> dict[str, DataValidationStatus]:
        """Validate all observed fields and return summary."""
        return {
            field: self.validate(field)
            for field in self._observations.keys()
        }

    def clear(self) -> None:
        """Clear all observations."""
        self._observations.clear()
