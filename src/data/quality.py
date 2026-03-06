"""Data quality validation layer (P0-①).

Provides infrastructure for validating data completeness, freshness,
and logical consistency across all data sources.
"""

from src.data.models import QualityFlag


def _calculate_quality_score(flags: list[QualityFlag]) -> float:
    """Calculate overall quality score using multiplicative risk model.

    Each flag reduces the score multiplicatively:
    - critical: × 0.70
    - warning:  × 0.90
    - info:     × 1.0 (no impact)

    This means multiple risks compound independently:
    - 1 critical = 0.70
    - 2 critical = 0.49
    - 1 critical + 1 warning = 0.63

    Args:
        flags: List of quality issues detected

    Returns:
        Quality score in [0.0, 1.0], where 1.0 = perfect quality
    """
    score = 1.0

    for flag in flags:
        if flag.severity == "critical":
            score *= 0.70
        elif flag.severity == "warning":
            score *= 0.90
        # info severity doesn't affect score (× 1.0)

    return score
