# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Complete. All planned phases (grid world, naive coordinator, Warden filter path, benchmark harness, live visualization) are built and tested. See `README.md` for how to run it and what it demonstrates.

## Commands

```bash
# one-time setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
source "$HOME/.cargo/env"   # if rustup was just installed
(cd core-rs && maturin develop)   # builds the Rust extension and installs it into .venv

# everyday
pytest                       # full suite (Python + the PyO3-exposed Rust modules)
pytest tests/test_grid_world.py -v   # a single file
cargo test                   # from core-rs/: Rust-only unit tests (ribbon_filter, ring_buffer)
python -m sim.scenarios      # full benchmark harness; writes results/benchmark_results.csv + coordinator_load.png
```

After editing any `.rs` file under `core-rs/src/`, rerun `maturin develop` before the next `pytest` — the Python side imports a compiled extension, not the source.

## Architecture

Two-tier fast-reject-then-confirm collision avoidance, per the build spec: a per-robot Ribbon filter (`core-rs/src/ribbon_filter.rs`, wraps the `ribbon-filter` crate rather than reimplementing the SIGMOD 2021 banded-matrix construction) for the fast "maybe claimed" check, and a coordinator-held ring buffer (`core-rs/src/ring_buffer.rs`, hand-written) as exact ground truth. Both are exposed to Python via PyO3 (`core-rs/src/lib.rs`) as `warden_core.RibbonFilter` / `warden_core.RingBuffer`.

**Python side, by layer:**
- `core/grid_world.py` — the shared physics: NxN grid, greedy movement, and the ground-truth collision guarantee. `GridWorld.tick(policy=None)` takes an optional move-gating hook (`policy.can_move(robot, next_cell) -> bool`) so Naive and Warden modes share identical movement logic; the live occupancy check inside `tick()` is always the final authority regardless of what a policy says. Also owns the anti-deadlock escalation (`AXIS_FLIP_THRESHOLD_TICKS`, `RANDOM_ESCAPE_THRESHOLD_TICKS`) — greedy single-axis movement with no escape hatch provably deadlocks the whole fleet (see git history on `core/grid_world.py` / `tests/test_no_permanent_gridlock`), so this isn't optional.
- `core/coordinator.py` — `Coordinator` (naive mode: gates every move through a simulated 1–3 tick confirm-check round trip against the ring buffer) and `WardenCoordinator` (wraps `Coordinator`; local filter says free → instant move; filter says maybe → delegates to the wrapped `Coordinator`). Both are duck-typed `GridWorld` policies.
- `sim/simulator.py` — drives the tick loop; `naive_mode=True` / `warden_mode=True` pick the policy.
- `sim/scenarios.py` — the density-preset benchmark harness (`python -m sim.scenarios`).
- `core/robot.py` — the shared `Robot` dataclass (position, target, `consecutive_blocked_ticks`).

**Key invariant:** the Ribbon filter must never produce a false negative — that's what `tests/test_ribbon_filter.py::test_no_false_negatives` and `tests/test_no_permanent_gridlock`/`test_no_collisions_*` exist to guard. Filter *staleness* (a robot's local filter being out of date) is a different, accepted risk — see `docs/staleness-finding.md` for why it can't actually cause a collision in this simulation (the live occupancy check has no propagation delay of its own) and what a "near-miss" means as a result.

`docs/benchmark-findings.md` has the measured (not estimated) traffic-reduction and CPU figures, including where they diverge from the project's original aspirational framing — read it before quoting a number from this project anywhere.
