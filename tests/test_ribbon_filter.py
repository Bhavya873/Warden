import pytest

from warden_core import RibbonFilter


def _cells(n):
    return [(i, i * 2) for i in range(n)]


def test_no_false_negatives():
    keys = _cells(500)
    rf = RibbonFilter(keys, 0.01)
    for key in keys:
        assert rf.contains(key), f"false negative for {key}"


@pytest.mark.parametrize("n", [100, 1000, 5000])
def test_measured_fp_rate_near_target(n):
    target_fpr = 0.01
    keys = _cells(n)
    rf = RibbonFilter(keys, target_fpr)

    probes = [(-1_000_000 - i, -1_000_000 - i * 2) for i in range(5000)]
    false_positives = sum(1 for probe in probes if rf.contains(probe))
    measured_fpr = false_positives / len(probes)

    # Loose bound: statistical check on a probabilistic structure, not an exact one.
    assert measured_fpr < target_fpr * 3.0, (
        f"fill n={n}: measured FP rate {measured_fpr} far exceeds target {target_fpr}"
    )


def test_empty_key_set():
    rf = RibbonFilter([], 0.01)
    assert rf.contains((0, 0)) is False
