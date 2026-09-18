# Warden — Implementation Guide

Build spec for an autonomous-agent coding session (Claude Code). Follow phases in order; each should end in a runnable, testable state before moving on.

## Tech stack

- **Filter core:** Rust — the Ribbon filter and ring buffer are implemented as a small crate, exposed to Python via PyO3 bindings. Rust's manual memory control fits a bit-packed structure like Ribbon well, and avoids the class of pointer bugs C++ is prone to in a banded solver.
- **Simulation/orchestration:** Python (grid simulation, robot movement, scenario configs), calling into the Rust core through PyO3.
- **Visualization:** a web frontend (HTML canvas + vanilla JS, or a small React app) driven by a Python backend (FastAPI or a simple WebSocket server) streaming simulation state.
- **No real network needed:** the "network call" for the confirm-check is simulated with an artificial delay, not actual sockets — this project is about the algorithm/architecture, not real distributed systems plumbing.

## Repo structure

```
warden/
├── core-rs/                     (Rust crate: filter core)
│   ├── src/
│   │   ├── ribbon_filter.rs
│   │   ├── ring_buffer.rs
│   │   └── lib.rs                (PyO3 bindings exposing both to Python)
│   └── Cargo.toml
├── core/
│   ├── robot.py                (robot agent: position, target, local filter instance)
│   ├── coordinator.py          (holds ring buffer, handles confirm-check requests)
│   └── grid_world.py           (grid state, movement rules, collision detection ground truth)
├── sim/
│   ├── simulator.py            (tick loop: moves robots, runs filter checks, logs stats)
│   └── scenarios.py            (density presets, naive-mode toggle, robot count/speed configs)
├── server/
│   └── ws_server.py            (streams simulation ticks to the frontend over WebSocket)
├── web/
│   ├── index.html
│   ├── grid_view.js            (renders the grid, robots, color-coded flashes)
│   └── style.css
├── tests/
│   ├── core-rs/tests/          (Rust unit tests: ribbon_filter, ring_buffer)
│   └── test_grid_world.py      (Python: collision correctness against naive ground truth)
├── requirements.txt
└── README.md
```

## Phase 1 — Grid world and ground-truth collision detection

Goal: a correct, filter-free simulation baseline.

1. `grid_world.py`: an NxN grid (N configurable, default 30) as a 2D array of cell states (`free` / `occupied(robot_id)`). Robots are agents with `(x, y)` position, a `(target_x, target_y)`, and a unique `robot_id`. Each tick, a robot attempts to move one cell toward its target using simple greedy movement (reduce whichever of dx/dy is larger first); pathing quality isn't the point of this project, so no A\* or obstacle avoidance is needed beyond other robots.
2. **Tick loop mechanics**: define a tick as one discrete simulation step. Within a tick, resolve moves in a fixed, deterministic order (e.g. by `robot_id`) so results are reproducible for a given random seed — this matters later for the Phase 4 benchmark and the Phase 5 split-screen mode needing identical conditions on both sides.
3. **Ground-truth collision check**: before finalizing any tick, verify no two robots ended up claiming the same cell simultaneously. Implement this as a simple post-tick assertion (build a set of all final positions, assert its size equals the robot count) — if the naive simulation ever allows this, that's a bug to fix here, not downstream.
4. **Target reassignment**: once a robot reaches its target, assign it a new random target cell so the simulation runs indefinitely (needed for Phase 4's sustained benchmark runs and Phase 5's live demo).
5. **Config surface**: expose grid size, robot count, and random seed as constructor parameters on `grid_world.py` from the start — these get reused directly by `scenarios.py` in Phase 4 and the frontend controls in Phase 5.
6. `robot.py` / `coordinator.py` stubs: robots don't yet check anything before moving — this phase establishes the physics and correctness baseline only.

**Exit criteria:** `test_grid_world.py` passes with at least: a fixed-seed reproducibility test (same seed → identical tick-by-tick output), a boundary test (robots don't move off-grid), and a collision-assertion test; running the simulator with 50 robots for 10,000 ticks produces zero double-occupied cells.

## Phase 2 — Ring buffer and coordinator

1. `ring_buffer.py`: a fixed-capacity buffer of `(cell, robot_id, expiry_tick)` entries. Size it to comfortably exceed the max expected simultaneous claims (e.g. 4x max robot count) so it never has to evict a still-valid claim early. Expose `claim(cell, robot_id, ttl_ticks)`, `is_claimed(cell) -> bool`, and `release(cell)`; run eviction of expired entries once per tick rather than on every lookup, to keep lookups cheap.
2. `coordinator.py`: holds the ring buffer and exposes `confirm_check(cell) -> bool` (the exact answer). Simulate a network round-trip as a configurable tick delay (default 1-3 ticks, randomized within that range per call) rather than a fixed constant, since real network latency varies — this also gives the benchmark a more realistic "coordinator backlog" curve in Phase 4.
3. **Concurrent request handling**: since multiple robots can call `confirm_check` in the same tick, model the coordinator as processing a queue — track queue depth per tick as a metric now (it becomes the "coordinator load" number the Phase 5 dashboard charts and the resume's <2% CPU claim is meant to represent).
4. Wire `grid_world.py` to route every move through the coordinator's `confirm_check` (still no Ribbon filter yet) — every single move gets an exact check, so this is intentionally the slow, always-ask baseline you'll compare Warden against.
5. **Logging**: have the coordinator log per-tick: number of confirm-check calls received, current queue depth, and average response delay — these three numbers are what Phase 4's benchmark harness reads to build the naive-mode comparison.

**Exit criteria:** simulation still produces zero collisions; running with 50 robots for 10,000 ticks produces a logged confirm-check-per-tick and queue-depth series — this is the naive baseline Phase 3 and Phase 4 will be measured against.

## Phase 3 — Ribbon filter integration

1. **Fingerprint design**: each claimed cell's fingerprint is its `(x, y)` coordinate hashed into a single key — no need for a class/type dimension here, unlike the perception-dedup projects, since a cell is either claimed or not.
2. Each `robot.py` instance holds its own local Ribbon filter (from `core-rs`, via PyO3), rebuilt periodically from a broadcast of the coordinator's current ring-buffer contents. Configurable refresh interval, default every 5 ticks — tune this in testing: too frequent wastes the whole point of a local filter, too infrequent makes the filter stale and increases false "maybe" hits.
3. **Target false-positive rate**: configure the filter for a modest target (e.g. 1%) — tune this in Phase 4 alongside memory footprint; a lower rate costs more memory per key but reduces unnecessary confirm-checks.
4. Before a robot attempts a move, it checks its local filter:
   - "Definitely free" → move immediately, log as an **instant move**, no coordinator call.
   - "Maybe claimed" → call `coordinator.confirm_check(cell)`, log as a **confirmed check**, proceed or wait based on the result.
5. **Staleness handling**: because the local filter is only refreshed periodically, a robot might get "definitely free" from a filter that's a few ticks out of date. This is an accepted trade-off (documented, not silently ignored) — it's why the ring buffer's TTL and the filter's refresh interval both need to be shorter than the minimum time a robot realistically occupies a cell.
6. Track and log the mix of instant vs. confirmed moves, and confirm the ground-truth collision check from Phase 1 still never fails. **This is the most important test in the whole project** — a false negative from the filter causing a real collision would undermine the "guaranteed zero collisions" claim entirely, so run this check for at least 100,000 total simulated moves across varied densities before considering Phase 3 done.

**Exit criteria:** simulation produces zero collisions across at least 100,000 total simulated robot moves; a run with moderate robot density shows the large majority of moves resolved as "instant," with confirmed-check volume dramatically lower than the Phase 2 baseline's always-confirm count — this gap is the source of the resume's traffic-reduction figure.

## Phase 4 — Benchmark harness

1. `scenarios.py`: presets for robot density — sparse (10 robots), moderate (50), crowded (120+, matching the resume's "10 to 120+ robots" claim) — on a fixed grid size, plus a `naive_mode` flag that bypasses the Ribbon filter entirely (routes every move through Phase 2's always-confirm path).
2. **Statistical rigor**: run each density preset across multiple random seeds (at least 5) rather than a single run, and report mean ± range — a single lucky/unlucky seed shouldn't be the basis for a resume claim. Total simulated moves across all runs should exceed 100,000 to match the Phase 3 collision-guarantee claim.
3. **Metrics to log per run**: instant-move %, confirmed-check %, network traffic reduction (confirmed-checks under Warden vs. total moves under naive, as a %), average and peak coordinator queue depth (proxy for coordinator CPU load), and total simulation wall-clock time.
4. **Deriving the resume figures**: the 85% traffic-reduction number is `1 - (Warden confirm-checks / Naive confirm-checks)` at a representative density — run this explicitly and report it, don't estimate it. The "<2% CPU" claim needs an actual CPU profiling pass (e.g. Python's `cProfile` or a simple wall-clock-vs-idle-time measurement on the coordinator process) at the 120-robot density, not just queue depth as a proxy — note in the results if queue depth and real CPU usage diverge.
5. Output a results table/CSV comparing naive vs. Warden at each density level, plus a plotted chart (matplotlib, saved as PNG) showing coordinator load vs. robot count for both modes — this chart is the direct source for the Phase 5 dashboard's line-chart design and doubles as a static fallback if the live demo isn't available (e.g. attaching to a resume/portfolio page).

**Exit criteria:** results clearly show confirm-check volume staying low and roughly flat for Warden as density scales from 10 to 120+ robots, while naive mode's confirm-check volume and coordinator backlog grow sharply; the specific reduction percentage and CPU figures are captured and match (or update) the numbers used in the resume bullets.

## Phase 5 — Live visualization

1. **Factory floor renderer** (`grid_view.js`, HTML canvas): draw an NxN grid representing the floor. Grid size is adjustable via a control (e.g. 10x10 up to 40x40) — changing it re-initializes the simulation with a fresh `grid_world` of that size. Robots render as small colored circles with a short directional indicator (a line or arrow) showing current heading; occupied/claimed cells can optionally shade faintly so contested areas are visible even before a robot arrives.
2. **Robot count slider** (1 to 150): dragging it live-adjusts the robot count in the running simulation — spawns new robots at random free cells or removes existing ones, without restarting the whole sim. Debounce the slider (e.g. 150ms) so rapid dragging doesn't spam the backend with spawn/remove events.
3. **Mode toggle — Naive vs. Warden**: a switch control that swaps which coordination logic the backend uses for all robots: **Naive** routes every move through `coordinator.confirm_check()` (Phase 2's always-confirm path); **Warden** routes moves through the Ribbon filter first (Phase 3's fast-reject-then-confirm path). Both modes run against the identical `grid_world` and movement logic — only the coordination path differs — so the comparison is apples-to-apples.
4. **Split-screen comparison mode**: a third toggle state that runs Naive and Warden simultaneously, side by side, on two floor views fed the same robot spawn positions and movement seed (so both sides face literally identical conditions). This is the most convincing demo state — the visual gap between the two floors *is* the pitch.
5. **Live dashboard panel** (below or beside the floor view(s)): real-time updating charts, not just numbers — use a lightweight charting library (Chart.js, loaded via CDN) fed by the same WebSocket tick stream:
   - A stacked or grouped bar showing the live split of **instant moves / confirmed checks / conflicts avoided** for the active mode(s).
   - A rolling line chart of **coordinator load** (confirm-checks per tick) over the last N ticks — in split-screen mode, one line per mode on the same axes, so Warden's flat line versus Naive's climbing line is directly visible.
   - Running totals as large, simple numbers above the charts (total moves, total confirm-checks, total conflicts avoided) — these are the numbers a recruiter or interviewer will actually read first.
6. **Density/stress control**: a separate control (or reuse the robot count slider at its high end) explicitly framed as a stress test — crank robots toward the grid's capacity and watch Naive's coordinator-load line climb sharply while Warden's stays comparatively flat, matching the Phase 4 benchmark's headline result.
7. **Visual design**: follow the project's flat, minimal design language — no gradients or heavy shadows, a small consistent color set (e.g. green = instant move, yellow = confirmed check, red = conflict avoided), sentence-case labels, and generous whitespace so the floor view(s) and dashboard don't feel cramped at typical laptop widths. Use Claude Code's `frontend-design` skill/guidance when generating the HTML/CSS for this phase (see note below) rather than default browser styling.

**Exit criteria:** a person can open the page, adjust the robot-count slider, switch between Naive, Warden, and split-screen modes, and watch the dashboard's live charts diverge under load exactly as described in the project doc's Demo section — no dataset or setup required beyond running the server.

## Notes for the agent

- Correctness first: the ground-truth collision check from Phase 1 must never fail in later phases. A false negative from the Ribbon filter causing a real collision is the one outcome that would undermine the whole project's premise — treat any such failure as a P0 bug, not a tuning issue.
- Keep the "network delay" for confirm-checks simulated and configurable (a simple tick-count delay), not real networking — this keeps the project's complexity focused on the algorithmic pattern, not distributed-systems plumbing.
- Write the Ribbon filter and ring buffer once, in the core-rs crate, and call them from Python via PyO3 rather than reimplementing either in Python — keeps the performance-critical logic in one place and matches the resume's stated tech stack.
