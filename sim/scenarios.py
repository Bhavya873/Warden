"""Density presets and the benchmark harness (Phase 4): naive vs Warden confirm-check
volume, coordinator load, and CPU share across robot density, run across multiple seeds
for statistical rigor. Run directly (`python -m sim.scenarios`) to produce
results/benchmark_results.csv and results/coordinator_load.png.
"""

import csv
import statistics
import time
from pathlib import Path

from core.grid_world import GridWorld
from sim.simulator import Simulator

GRID_SIZE = 30  # fixed across all density presets — density varies via robot_count only

DENSITY_PRESETS = {
    "sparse": 10,
    "moderate": 50,
    "crowded": 120,
}

DEFAULT_SEEDS = [1, 2, 3, 4, 5]
DEFAULT_TICKS = 1500

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def run_scenario(robot_count: int, seed: int, ticks: int, naive_mode: bool) -> dict:
    """One (density, seed, mode) run. Returns per-run metrics — instant/confirmed move
    counts and percentages, coordinator queue depth, wall-clock time."""
    sim = Simulator(
        grid_size=GRID_SIZE,
        robot_count=robot_count,
        seed=seed,
        naive_mode=naive_mode,
        warden_mode=not naive_mode,
    )

    start = time.perf_counter()
    sim.run(ticks)
    wall_clock_seconds = time.perf_counter() - start

    if naive_mode:
        confirm_log = sim.coordinator.log
        instant_moves = 0
        confirmed_checks = sum(e["confirm_checks"] for e in confirm_log)
    else:
        confirm_log = sim.coordinator.confirm_check_log
        instant_moves = sum(e["instant_moves"] for e in sim.coordinator.log)
        confirmed_checks = sum(e["confirmed_checks"] for e in sim.coordinator.log)

    queue_depths = [e["queue_depth"] for e in confirm_log]
    total_moves = instant_moves + confirmed_checks

    return {
        "mode": "naive" if naive_mode else "warden",
        "robot_count": robot_count,
        "seed": seed,
        "ticks": ticks,
        "instant_moves": instant_moves,
        "confirmed_checks": confirmed_checks,
        "instant_pct": (instant_moves / total_moves * 100) if total_moves else 0.0,
        "confirmed_pct": (confirmed_checks / total_moves * 100) if total_moves else 0.0,
        "avg_queue_depth": statistics.fmean(queue_depths) if queue_depths else 0.0,
        "peak_queue_depth": max(queue_depths) if queue_depths else 0,
        "wall_clock_seconds": wall_clock_seconds,
    }


def run_seeds(robot_count: int, seeds: list[int], ticks: int, naive_mode: bool) -> list[dict]:
    return [run_scenario(robot_count, seed, ticks, naive_mode) for seed in seeds]


def aggregate(runs: list[dict], metric: str) -> dict:
    """Mean, min, max of `metric` across a list of same-scenario runs (different
    seeds) — a single lucky/unlucky seed shouldn't be the basis for a headline figure."""
    values = [run[metric] for run in runs]
    return {"mean": statistics.fmean(values), "min": min(values), "max": max(values)}


def traffic_reduction_pct(warden_confirmed: float, naive_confirmed: float) -> float:
    """1 - (Warden confirm-checks / Naive confirm-checks), as a % — measured, not
    estimated (build spec §6 Phase 4 step 4)."""
    if naive_confirmed == 0:
        return 0.0
    return (1 - warden_confirmed / naive_confirmed) * 100


def profile_coordinator_cpu_share(robot_count: int, seed: int, ticks: int, naive_mode: bool) -> dict:
    """CPU profiling pass (build spec §6 Phase 4 step 4): measures what share of total
    process CPU time the coordinator's own work consumes, at a specific density — an
    actual measurement, not queue depth used as a proxy for load.

    `coordinator.tick()` alone understates this: most confirm-check submission/polling
    happens inside `policy.can_move()`, called from *inside* `GridWorld.tick()`, not
    `coordinator.tick()`. So this measures by subtraction instead: a policy=None
    baseline (pure movement, same seed) vs. the full run with coordination — the
    difference is attributed to coordinator + policy overhead.
    """
    baseline_world = GridWorld(grid_size=GRID_SIZE, robot_count=robot_count, seed=seed)
    baseline_start_cpu = time.process_time()
    for _ in range(ticks):
        baseline_world.tick()
    baseline_cpu_seconds = time.process_time() - baseline_start_cpu

    sim = Simulator(
        grid_size=GRID_SIZE,
        robot_count=robot_count,
        seed=seed,
        naive_mode=naive_mode,
        warden_mode=not naive_mode,
    )
    full_start_cpu = time.process_time()
    sim.run(ticks)
    total_cpu_seconds = time.process_time() - full_start_cpu

    coordinator_cpu_seconds = max(0.0, total_cpu_seconds - baseline_cpu_seconds)
    return {
        "mode": "naive" if naive_mode else "warden",
        "robot_count": robot_count,
        "baseline_cpu_seconds": baseline_cpu_seconds,
        "coordinator_cpu_seconds": coordinator_cpu_seconds,
        "total_cpu_seconds": total_cpu_seconds,
        "coordinator_cpu_pct": (coordinator_cpu_seconds / total_cpu_seconds * 100) if total_cpu_seconds else 0.0,
    }


def run_all_scenarios(seeds: list[int] = DEFAULT_SEEDS, ticks: int = DEFAULT_TICKS) -> dict:
    """Runs every density preset in both modes across all seeds. Returns
    {(density_label, mode): [per-seed run dicts]}."""
    results = {}
    for density_label, robot_count in DENSITY_PRESETS.items():
        for naive_mode in (True, False):
            mode = "naive" if naive_mode else "warden"
            results[(density_label, mode)] = run_seeds(robot_count, seeds, ticks, naive_mode)
    return results


def write_csv(results: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "density",
        "mode",
        "robot_count",
        "seeds",
        "ticks_per_seed",
        "total_moves_across_seeds",
        "instant_pct_mean",
        "confirmed_pct_mean",
        "avg_queue_depth_mean",
        "avg_queue_depth_min",
        "avg_queue_depth_max",
        "peak_queue_depth_max",
        "wall_clock_seconds_mean",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for (density_label, mode), runs in results.items():
            queue_depth_stats = aggregate(runs, "avg_queue_depth")
            writer.writerow(
                {
                    "density": density_label,
                    "mode": mode,
                    "robot_count": runs[0]["robot_count"],
                    "seeds": len(runs),
                    "ticks_per_seed": runs[0]["ticks"],
                    "total_moves_across_seeds": sum(r["instant_moves"] + r["confirmed_checks"] for r in runs),
                    "instant_pct_mean": aggregate(runs, "instant_pct")["mean"],
                    "confirmed_pct_mean": aggregate(runs, "confirmed_pct")["mean"],
                    "avg_queue_depth_mean": queue_depth_stats["mean"],
                    "avg_queue_depth_min": queue_depth_stats["min"],
                    "avg_queue_depth_max": queue_depth_stats["max"],
                    "peak_queue_depth_max": max(r["peak_queue_depth"] for r in runs),
                    "wall_clock_seconds_mean": aggregate(runs, "wall_clock_seconds")["mean"],
                }
            )


def plot_coordinator_load(results: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    robot_counts = list(DENSITY_PRESETS.values())

    fig, ax = plt.subplots(figsize=(7, 5))
    for mode, style in (("naive", "o-"), ("warden", "s-")):
        means = [aggregate(results[(label, mode)], "avg_queue_depth")["mean"] for label in DENSITY_PRESETS]
        ax.plot(robot_counts, means, style, label=mode)

    ax.set_xlabel("Robot count")
    ax.set_ylabel("Average coordinator queue depth")
    ax.set_title("Coordinator load vs. robot count: Naive vs. Warden")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    print(f"Running {len(DEFAULT_SEEDS)} seeds x {len(DENSITY_PRESETS)} densities x 2 modes...")
    results = run_all_scenarios()

    total_moves = sum(r["instant_moves"] + r["confirmed_checks"] for runs in results.values() for r in runs)
    print(f"Total simulated moves across all runs: {total_moves:,}")
    assert total_moves > 100_000, "expected total moves across all runs to exceed 100,000"

    csv_path = RESULTS_DIR / "benchmark_results.csv"
    write_csv(results, csv_path)
    print(f"Wrote {csv_path}")

    png_path = RESULTS_DIR / "coordinator_load.png"
    plot_coordinator_load(results, png_path)
    print(f"Wrote {png_path}")

    print("\nPer-density summary (mean across seeds):")
    for density_label, robot_count in DENSITY_PRESETS.items():
        naive_confirmed = aggregate(results[(density_label, "naive")], "confirmed_checks")["mean"]
        warden_confirmed = aggregate(results[(density_label, "warden")], "confirmed_checks")["mean"]
        warden_instant_pct = aggregate(results[(density_label, "warden")], "instant_pct")["mean"]
        reduction = traffic_reduction_pct(warden_confirmed, naive_confirmed)
        print(
            f"  {density_label} ({robot_count} robots): "
            f"warden instant={warden_instant_pct:.1f}%, "
            f"traffic reduction vs naive={reduction:.1f}%"
        )

    print("\nCPU profiling pass at crowded density (120 robots):")
    for naive_mode in (True, False):
        profile = profile_coordinator_cpu_share(
            DENSITY_PRESETS["crowded"], seed=1, ticks=DEFAULT_TICKS, naive_mode=naive_mode
        )
        print(
            f"  {profile['mode']}: coordinator CPU share = {profile['coordinator_cpu_pct']:.2f}% "
            f"({profile['coordinator_cpu_seconds']:.3f}s / {profile['total_cpu_seconds']:.3f}s)"
        )


if __name__ == "__main__":
    main()
