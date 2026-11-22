"""Event-window returns. Pure arithmetic: no data access, no selection of sessions."""

from collections.abc import Sequence
from statistics import fmean, median, variance

from quant_contracts.evaluation import GroupComparison, ReturnPair


def simple_return(start: float, end: float) -> float:
    if start <= 0 or end <= 0:
        raise ValueError("prices must be positive")
    return end / start - 1.0


def return_pair(subject_start: float, subject_end: float,
                benchmark_start: float | None = None, benchmark_end: float | None = None,
                pre_beta: float | None = None) -> ReturnPair:
    """Returns of subject and benchmark over one span; ``relative`` is their difference. Without benchmark prices, the subject alone."""
    if benchmark_start is None or benchmark_end is None:
        return ReturnPair(subject=simple_return(subject_start, subject_end))
    return ReturnPair(subject=simple_return(subject_start, subject_end),
                      benchmark=simple_return(benchmark_start, benchmark_end), pre_beta=pre_beta)


def beta_before(subject_closes: Sequence[float | None], benchmark_closes: Sequence[float | None], last: int,
                sessions: int) -> float | None:
    """The subject's beta to the benchmark from the ``sessions`` close-to-close returns ending at index ``last``.

    ``last`` is the final session used: nothing after it is read, so with ``last`` set to the
    session before an event window the estimate cannot see the window. None when the window
    is not complete, a price is missing or not positive, or the benchmark did not move.
    """
    if sessions < 2 or last - sessions < 0 or last >= len(subject_closes) or last >= len(benchmark_closes):
        return None
    s, b = subject_closes[last - sessions:last + 1], benchmark_closes[last - sessions:last + 1]
    if any(v is None or v != v or v <= 0 for v in (*s, *b)):
        return None
    rs = [y / x - 1.0 for x, y in zip(s, s[1:])]
    rb = [y / x - 1.0 for x, y in zip(b, b[1:])]
    mean_s, mean_b = fmean(rs), fmean(rb)
    spread = sum((v - mean_b) ** 2 for v in rb)
    if spread == 0:
        return None
    return sum((u - mean_s) * (v - mean_b) for u, v in zip(rs, rb)) / spread


def compare_groups(label: str, a: list[float], b: list[float]) -> GroupComparison:
    """Difference of the two group means with its unequal-variance standard error."""
    mean_a, mean_b = (fmean(g) if g else None for g in (a, b))
    both = mean_a is not None and mean_b is not None
    error = (variance(a) / len(a) + variance(b) / len(b)) ** 0.5 if len(a) > 1 and len(b) > 1 else None
    share = lambda g: sum(v > 0 for v in g) / len(g) if g else None
    return GroupComparison(label=label, n_a=len(a), n_b=len(b), mean_a=mean_a, mean_b=mean_b,
                           difference=mean_a - mean_b if both else None, standard_error=error,
                           median_a=median(a) if a else None, median_b=median(b) if b else None,
                           positive_share_a=share(a), positive_share_b=share(b))
