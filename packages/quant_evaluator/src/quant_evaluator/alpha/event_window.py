"""Event-window returns. Pure arithmetic: no data access, no selection of sessions."""

from quant_contracts.evaluation import ReturnPair


def simple_return(start: float, end: float) -> float:
    if start <= 0 or end <= 0:
        raise ValueError("prices must be positive")
    return end / start - 1.0


def return_pair(subject_start: float, subject_end: float,
                benchmark_start: float, benchmark_end: float) -> ReturnPair:
    """Returns of subject and benchmark over one span; ``relative`` is their difference."""
    return ReturnPair(subject=simple_return(subject_start, subject_end),
                      benchmark=simple_return(benchmark_start, benchmark_end))
