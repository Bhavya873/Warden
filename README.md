# Warden

A demo of collision-free warehouse robot coordination. It compares a naive
"ask before every move" coordinator against a faster two-tier scheme called
Warden.

Picture a fleet of robots sharing one warehouse floor. Before a robot steps
into a cell, it needs to know nobody else is about to step into the same
one. The naive way is to ask a central coordinator before every single move.
That's always correct, but it turns the coordinator into a bottleneck as the
fleet grows. Warden instead gives each robot a local Ribbon filter, basically
a cheat sheet of cells it thinks are claimed. Most of the time the filter
says "clearly free" and the robot just goes. Only when it says "maybe
claimed" does the robot pay for a real check against the coordinator's ring
buffer, which is the actual source of truth.

This is a portfolio project, not a production distributed system. It's a
working browser demo plus honestly measured benchmarks. See Scope below for
what's deliberately left out.

## Architecture

`core-rs/` (Rust, via PyO3) holds the two data structures the whole scheme
rests on. `ribbon_filter.rs` is the per-robot local filter, small memory
footprint per key, and it never gives a false "definitely free" for
something it was actually told about. `ring_buffer.rs` is the coordinator's
fixed-capacity, self-expiring log of real claims, with O(1) insert and
evict.

`core/` (Python) has the physics and coordination logic. `grid_world.py` is
the NxN grid, greedy movement, and the ground-truth collision guarantee (the
live occupancy check always has final say, no matter what a policy
approved). `coordinator.py` has `Coordinator` (naive: every move goes
through a simulated 1-3 tick confirm check) and `WardenCoordinator` (filter
says free means instant move, filter says maybe means it falls back to
`Coordinator`). `robot.py` is the `Robot` dataclass (position, target,
blocked-tick counter).

`sim/` has `simulator.py`, which drives the tick loop, and `scenarios.py`,
the density-preset benchmark harness.

`server/ws_server.py` is a WebSocket server that drives the sim and streams
tick state to the browser.

`web/` is the live demo frontend, a canvas floor view plus a Chart.js
dashboard.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Rust toolchain, if not already installed
source "$HOME/.cargo/env"   # after rustup install, if it was just installed

# builds the Rust extension and installs it into .venv
(cd core-rs && maturin develop)
```

After editing any `.rs` file under `core-rs/src/`, rerun `maturin develop`
before the next `pytest` run. The Python side imports a compiled extension,
not the source.

To leave the venv and clean up build artifacts:

```bash
deactivate                                              # leave the venv
find . -type d -name "__pycache__" -exec rm -rf {} +    # clear cached bytecode
rm -rf .venv                                             # optional: drop the venv entirely
```

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

Then open `web/index.html` directly in a browser. There's no build step and
no dev server needed, it just connects to `ws://localhost:8765`. You'll see
two floor views side by side, naive baseline and Warden, fed identical spawn
positions and movement seed, with a live dashboard of instant moves,
confirmed checks, and conflicts avoided, plus a rolling coordinator-load
chart. Controls include robot count, grid size, and a broadcast-lag preset
(normal, degraded, adversarial) that widens the window in which a robot's
local filter can be stale.

## Running the benchmark harness

```bash
python -m sim.scenarios
```

Runs 5 seeds times 3 density presets (10, 50, 120 robots) times naive and
Warden, 1,500 ticks each, on a fixed 30x30 grid, over 1.5M simulated moves.
Writes `results/benchmark_results.csv` and `results/coordinator_load.png`.

## Measured results

These numbers are measured, not estimated. Run `python -m sim.scenarios`
yourself and you'll get the same shape of result (5 seeds times 3 density
presets times naive and Warden, 1,500 ticks each, over 1.5M simulated moves
total). Raw data is in `results/benchmark_results.csv`, the chart is
`results/coordinator_load.png`.

| Density | Robots | Warden instant % | Reduction vs. naive |
| --- | --- | --- | --- |
| sparse | 10 | 94.9% | 85.9% |
| moderate | 50 | 85.4% | 66.0% |
| crowded | 120 | 70.7% | 48.7% |

In plain terms, with only 10 robots on the floor, almost every move is
instant. Warden's filter is right almost all the time, so the coordinator
barely gets bothered. Pack 120 robots onto the same grid and cells get
contested constantly, so more moves need a real confirm check, but Warden's
cheat-sheet approach still cuts coordinator traffic almost in half compared
to asking every time.

A CPU profiling pass at the crowded density (120 robots) shows the
coordinator's share of total CPU time rising from about 12% under naive to
about 35% under Warden. The filter checks themselves aren't free, so at high
contention Warden trades some of its traffic savings for extra local
computation. Coordinator queue depth is still lower for Warden at every
density tested (see `results/coordinator_load.png`), just by a shrinking
margin as contention rises.

## What happens when a filter goes stale?

A robot's local filter only gets refreshed every so often, so there's a
window where it can say "definitely free" about a cell another robot
claimed a moment ago, because the broadcast just hasn't caught up yet. When
that happens the robot skips the confirm check entirely, on bad
information.

There's a dedicated stress test for exactly this (`tests/test_staleness.py`,
which freezes a filter for 5,000 ticks to force the worst case). Under those
adversarial conditions, the robot gets it wrong about a third of the time,
but the result is always a near-miss (the move gets rejected), never an
actual collision. That's because the grid's live occupancy check has the
final word on every move, regardless of what any filter or coordinator
approved. A stale filter can only lead to a bad guess, never a bad outcome.

Worth being honest about the limits of that guarantee: it holds because
this is a single process, where checking and updating occupancy happen as
one atomic step with zero propagation delay. That's not a claim about how a
real distributed warehouse would behave. There, "ground truth" is spread
across many machines, each with its own network lag, and this exact
guarantee wouldn't come for free.

## Scope, what's not here

This is a portfolio piece, not a production system, and some things are
left out on purpose rather than by oversight. There's no real networking,
the coordinator's "network call" is just a simulated delay. There's one
coordinator and no sharding, which is fine at the fleet sizes this demo
animates (up to about 150 robots) but wouldn't scale further as-is. And
there's no exhaustive filter benchmarking (memory or false-positive-rate
sweeps), just one credible, honestly-measured number per metric, not a
research-grade study.
