# Warden

Collision-free warehouse robot coordination — a two-tier fast-reject-then-confirm
scheme, benchmarked and demoed live against a naive always-ask baseline.

A fleet of robots shares one warehouse floor grid. Before entering a cell, a robot
needs to know whether another robot is about to occupy it. Asking a central
coordinator before *every* move (naive mode) is exact but floods the coordinator as
the fleet grows. Warden mode gives each robot a local **Ribbon filter** — "cells I
believe are claimed" — so it can proceed instantly on the common case (nobody's
there) and only pay for a real confirm-check against the coordinator's **ring
buffer** (exact ground truth) when the filter says "maybe."

This is a portfolio/demo project — a working browser demo and an honestly-measured
benchmark, not a production distributed system. See [Scope](#scope--whats-not-here)
below.

## Architecture

- **`core-rs/`** (Rust, via PyO3) — the two data structures the whole scheme rests on:
  - `ribbon_filter.rs` — per-robot local filter, near-optimal memory per key, zero
    false negatives against what it's been told.
  - `ring_buffer.rs` — coordinator-held, fixed-capacity, self-expiring log of real
    claims. O(1) insert/evict.
- **`core/`** (Python) — the physics and coordination logic:
  - `grid_world.py` — NxN grid, greedy movement, and the ground-truth collision
    guarantee (the live occupancy check is always the final authority, independent
    of whatever a policy approved).
  - `coordinator.py` — `Coordinator` (naive: every move goes through a simulated
    1–3 tick confirm-check) and `WardenCoordinator` (filter says free → instant
    move; filter says maybe → delegates to `Coordinator`).
  - `robot.py` — the `Robot` dataclass (position, target, blocked-tick counter).
- **`sim/`** — `simulator.py` drives the tick loop; `scenarios.py` is the
  density-preset benchmark harness.
- **`server/ws_server.py`** — WebSocket server that drives the sim and streams tick
  state to the browser.
- **`web/`** — the live demo frontend (canvas floor view + Chart.js dashboard).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Rust toolchain, if not already installed
source "$HOME/.cargo/env"   # after rustup install, if it was just installed

# builds the Rust extension and installs it into .venv
(cd core-rs && maturin develop)
```

After editing any `.rs` file under `core-rs/src/`, rerun `maturin develop` before
the next `pytest` — the Python side imports a compiled extension, not the source.

## Running the tests

```bash
pytest                                # full suite (Python + PyO3-exposed Rust modules)
pytest tests/test_grid_world.py -v    # a single file
cargo test                            # from core-rs/: Rust-only unit tests
```

## Running the live demo

```bash
python -m server.ws_server
```

Then open `web/index.html` directly in a browser (no build step, no dev server —
it connects to `ws://localhost:8765`). You'll see two floor views side by side —
naive baseline vs. Warden — fed identical spawn positions and movement seed, with
a live dashboard of instant moves / confirmed checks / conflicts avoided and a
rolling coordinator-load chart. Controls: robot count, grid size, and a
broadcast-lag preset (normal / degraded / adversarial) that widens the window in
which a robot's local filter can be stale.

## Running the benchmark harness

```bash
python -m sim.scenarios
```

Runs 5 seeds × 3 density presets (10 / 50 / 120 robots) × naive/Warden, 1,500 ticks
each, on a fixed 30×30 grid — over 1.6M simulated moves. Writes
`results/benchmark_results.csv` and `results/coordinator_load.png`.

## Measured results

Headline numbers, measured (not estimated) via `python -m sim.scenarios` — 5 seeds
× 3 density presets × naive/Warden, 1,500 ticks each, over 1.6M simulated moves.
Traffic reduction narrows under heavy contention, it is not flat across density:

| Density | Robots | Warden instant % | Reduction vs. naive |
| --- | --- | --- | --- |
| sparse | 10 | 94.1% | 84.0% |
| moderate | 50 | 71.2% | 46.9% |
| crowded | 120 | 19.1% | 17.1% |

Coordinator queue depth is lower for Warden at every density tested, by a shrinking
margin as contention rises (see `results/coordinator_load.png`).

## The staleness finding

Because a robot's local filter only refreshes periodically, it can say "definitely
free" for a cell another robot claimed moments ago, whose broadcast hasn't
propagated yet — that path skips the confirm-check entirely. A dedicated adversarial
test (`tests/test_staleness.py`, filter frozen for 5,000 ticks) exercises this
deliberately: near-misses are common under adversarial settings (~33% of moves), but
**zero collisions**, because the grid's live occupancy check is the final authority
on every move regardless of what any policy approved — filter/ring-buffer staleness
can produce a wrong "go ahead," never a wrong outcome.

Honest limitation: that guarantee is a property of this single-process simulation's
synchronous ground truth (checking and updating occupancy is one in-process
operation with no propagation delay) — not a claim about what a real distributed
deployment's guarantee would be, where "ground truth" would itself be spread across
robots and a coordinator, each with its own network delay.

## Scope — what's not here

By design, not oversight (portfolio/demo piece, not a production system):

- No real networking — the coordinator's "network call" is a simulated tick delay.
- Single coordinator, no sharding — correct at any fleet size this demo animates
  (up to ~150 robots).
- No rigorous academic-grade filter benchmarking (memory/FP-rate sweeps) — one
  credible, honestly-measured number per metric, not a research sweep.
