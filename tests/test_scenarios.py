"""Fast, small-scale tests for the benchmark harness's own logic (metrics shape,
aggregation math, the traffic-reduction formula, output writing) — not a
reproduction of the full production benchmark run, which is slow and produces the
real headline figures via `python -m sim.scenarios` (see tasks/benchmark-findings.md).
"""

import csv

from sim.scenarios import (
    aggregate,
    plot_coordinator_load,
    run_all_scenarios,
    run_scenario,
    traffic_reduction_pct,
    write_csv,
)


def test_run_scenario_shape():
    for naive_mode in (True, False):
        result = run_scenario(robot_count=5, seed=1, ticks=100, naive_mode=naive_mode)
        assert result["mode"] == ("naive" if naive_mode else "warden")
        total = result["instant_moves"] + result["confirmed_checks"]
        assert total > 0
        assert abs(result["instant_pct"] + result["confirmed_pct"] - 100.0) < 1e-9
        assert result["avg_queue_depth"] >= 0
        assert result["peak_queue_depth"] >= 0
        assert result["wall_clock_seconds"] > 0

    naive_result = run_scenario(robot_count=5, seed=1, ticks=100, naive_mode=True)
    assert naive_result["instant_moves"] == 0
    assert naive_result["instant_pct"] == 0.0


def test_aggregate_mean_min_max():
    runs = [{"metric": 2.0}, {"metric": 4.0}, {"metric": 9.0}]
    stats = aggregate(runs, "metric")
    assert stats["mean"] == 5.0
    assert stats["min"] == 2.0
    assert stats["max"] == 9.0


def test_traffic_reduction_formula():
    assert traffic_reduction_pct(warden_confirmed=20, naive_confirmed=100) == 80.0
    assert traffic_reduction_pct(warden_confirmed=100, naive_confirmed=100) == 0.0
    assert traffic_reduction_pct(warden_confirmed=0, naive_confirmed=0) == 0.0


def test_write_csv_and_plot(tmp_path):
    results = run_all_scenarios(seeds=[1, 2], ticks=100)

    csv_path = tmp_path / "results.csv"
    write_csv(results, csv_path)
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    # 3 density presets x 2 modes = 6 rows
    assert len(rows) == 6
    assert {row["mode"] for row in rows} == {"naive", "warden"}
    assert {row["density"] for row in rows} == {"sparse", "moderate", "crowded"}

    png_path = tmp_path / "chart.png"
    plot_coordinator_load(results, png_path)
    assert png_path.exists()
    assert png_path.stat().st_size > 0
