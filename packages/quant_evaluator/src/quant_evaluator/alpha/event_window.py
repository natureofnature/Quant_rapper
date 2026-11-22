"""Event-window returns. Pure arithmetic: no data access, no selection of sessions."""

from statistics import fmean, variance

from quant_contracts.evaluation import GroupComparison, ReturnPair


def simple_return(start: float, end: float) -> float:
    if start <= 0 or end <= 0:
        raise ValueError("prices must be positive")
    return end / start - 1.0


def return_pair(subject_start: float, subject_end: float,
                benchmark_start: float, benchmark_end: float) -> ReturnPair:
    """Returns of subject and benchmark over one span; ``relative`` is their difference."""
    return ReturnPair(subject=simple_return(subject_start, subject_end),
                      benchmark=simple_return(benchmark_start, benchmark_end))


def compare_groups(label: str, a: list[float], b: list[float]) -> GroupComparison:
    """Difference of the two group means with its unequal-variance standard error."""
    mean_a, mean_b = (fmean(g) if g else None for g in (a, b))
    both = mean_a is not None and mean_b is not None
    error = (variance(a) / len(a) + variance(b) / len(b)) ** 0.5 if len(a) > 1 and len(b) > 1 else None
    return GroupComparison(label=label, n_a=len(a), n_b=len(b), mean_a=mean_a, mean_b=mean_b,
                           difference=mean_a - mean_b if both else None, standard_error=error)
