# Warden — Build Spec

Single document to build from. Combines the original design doc, the phased implementation guide, and the decisions made in the 2026-09-18 idea-refine pass. The two source docs (`Warden — Collision-Free Zone Claiming for Warehouse Robot Fleets.md`, `Implementation Guide.md`) remain as background reading, but this file is authoritative for what to build — amendments made here (marked **[amended]**) take precedence over anything they contradict in those two.

## 1. Problem

A fleet of warehouse robots shares one floor grid. Before entering a cell, a robot needs to know whether another robot is about to occupy it. The naive fix — ask every other robot, or a central coordinator, before every move — floods the network and adds latency exactly when responsiveness matters most.

The honest constraint: most moves are uncontested. The system should spend almost nothing confirming the common case (nobody's there) and only pay for a real check when there's genuine chance of conflict. Same shape of problem as the PRP duplicate-packet and Tesla perception-dedup work: a high-frequency stream of yes/no questions where most answers are "no," a false negative is unacceptable, and a false "maybe" that gets double-checked is fine.

## 2. Solution

Two-tier fast-reject-then-confirm:

- **Per-robot Ribbon filter** — local, in-memory, "cells I believe are claimed by another robot." No false negatives relative to what it's been told. Refreshed periodically from a coordinator broadcast.
- **Coordinator-held ring buffer** — exact ground truth: currently-claimed cells with short expiry. Fixed size, O(1) insert/evict.

Per move: filter says **definitely free** → proceed, no network call (the overwhelming majority of moves). Filter says **maybe claimed** → confirm-check against the ring buffer before proceeding.

| Technique | Role | Why it fits |
| --- | --- | --- |
| Ribbon filter (SIGMOD 2021) | Fast, local, low-memory "probably claimed" check per robot | Near-optimal memory per key, no false negatives against what it's been told |
| Ring buffer | Fixed-size, self-expiring ground-truth log | O(1) insert/evict, bounded memory regardless of fleet size |
| Fast-reject-then-confirm | Cheap filter first, expensive exact check only when needed | Keeps the common case nearly free, isolates coordination cost to genuine contention |
| Periodic filter refresh | Keeps local filters reasonably current | Avoids a network round-trip per move while tolerating staleness (caught by confirm) |

## 3. Design decisions from ideation

**Audience:** portfolio/demo piece — a working browser demo and a resume-worthy benchmark, not a production system. Real deployment concerns (real networking, robot certification, sharded coordinators) are out of scope by design, not by oversight.

**Direction taken:** the phased plan in §6 already sequences this correctly without needing a separate "fake filter" track — Phases 1–2 build and prove the grid, tick loop, and always-confirm baseline with *no* filter at all, and the real Ribbon filter is introduced in Phase 3 once that foundation is solid. That's the "prove the core mechanics before betting on the real data structure" idea, already built into the phase order.

**The one real amendment: staleness is a first-class, demonstrated behavior, not just a documented trade-off.** The filter's "no false negatives" guarantee is only about faithfully representing what it's been told — not about a robot's real-time view of the world. Because local filters refresh periodically while a robot's *own move* is instant once the filter says "free," there's a real (not hypothetical) window where a robot can get "definitely free" for a cell another robot claimed moments ago but whose broadcast hasn't propagated yet. That path skips the confirm-check entirely — it's the one gap in the "guaranteed zero collisions" claim, and it deserves a real test and a visible demo control rather than a footnote. See **[amended]** items in Phase 3 and Phase 5 below.

**Not doing (and why):**
- Real sockets/networking for the confirm-check — simulated tick delay only; the project is about the coordination pattern, not distributed-systems plumbing.
- Multi-coordinator / sharded ring buffer — single coordinator is correct at any fleet size this demo will actually animate (up to ~150 robots).
- Porting a Ribbon filter from the earlier PRP project — no reusable codebase exists; write a small standalone implementation (or use an existing crate) instead of a phantom port.
- Rigorous benchmarking of the filter's memory/FP-rate against academic baselines — the resume claims need one credible, honestly-measured number per metric, not a research-grade sweep.

**Key assumptions to validate while building:**
- That a visibly modeled staleness window (a live lag control, not just prose) is what makes this read as engineering judgment to a reviewer — this is the whole bet behind the Phase 5 amendment.
- That the staleness gap either (a) never produces a real collision at realistic refresh-interval/occupation-time ratios, which the Phase 3 amendment's adversarial test should demonstrate, or (b) does, in which case that's a finding to report honestly (and possibly mitigate) rather than paper over.

## 4. Tech stack

- **Filter core:** Rust — Ribbon filter and ring buffer as a small crate, exposed to Python via PyO3. Rust's manual memory control fits a bit-packed structure like Ribbon; avoids the pointer-bug class C++ is prone to in a banded solver.
- **Simulation/orchestration:** Python (grid simulation, robot movement, scenario configs), calling the Rust core through PyO3.
- **Visualization:** web frontend (HTML canvas + vanilla JS, or a small React app) driven by a Python backend (FastAPI or a WebSocket server) streaming simulation state.
- **No real network:** the confirm-check's "network call" is an artificial delay, not real sockets.

## 5. Repo structure

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

## 6. Build phases

### Phase 1 — Grid world and ground-truth collision detection

Goal: a correct, filter-free simulation baseline.

1. `grid_world.py`: NxN grid (default 30) as a 2D array of cell states (`free` / `occupied(robot_id)`). Robots have `(x, y)`, `(target_x, target_y)`, `robot_id`. Each tick, greedy movement toward target (reduce larger of dx/dy first) — no A* or obstacle avoidance needed.
2. **Tick loop:** resolve moves within a tick in a fixed, deterministic order (e.g. by `robot_id`) so results are reproducible for a given seed — needed for Phase 4's benchmark and Phase 5's split-screen mode.
3. **Ground-truth collision check:** before finalizing any tick, verify no two robots claimed the same cell (build a set of final positions, assert size == robot count). Any failure here is a bug to fix in Phase 1, not later.
4. **Target reassignment:** on reaching target, assign a new random target so the sim runs indefinitely.
5. **Config surface:** expose grid size, robot count, random seed as constructor params from the start — reused by `scenarios.py` and the frontend controls.
6. `robot.py` / `coordinator.py` stubs only — no checking before moving yet.

**Exit criteria:** `test_grid_world.py` passes: fixed-seed reproducibility, boundary test (no off-grid movement), collision-assertion test; 50 robots × 10,000 ticks → zero double-occupied cells.

### Phase 2 — Ring buffer and coordinator

1. `ring_buffer.rs` (in `core-rs`, exposed to Python via PyO3 — see §4/§5; this is where the PyO3/maturin toolchain gets stood up, ahead of Phase 3's Ribbon filter): fixed-capacity buffer of `(cell, robot_id, expiry_tick)`. Size ≥ 4x max expected simultaneous claims. Expose `claim(cell, robot_id, ttl_ticks)`, `is_claimed(cell) -> bool`, `release(cell)`. Evict expired entries once per tick, not per lookup.
2. `coordinator.py`: holds the ring buffer, exposes `confirm_check(cell) -> bool`. Simulate round-trip as a randomized 1–3 tick delay (not fixed) — gives the benchmark a realistic backlog curve.
3. **Concurrent requests:** model the coordinator as processing a queue; track queue depth per tick (becomes the Phase 5 dashboard's "coordinator load" metric and the CPU-load claim in Phase 4).
4. Route every move through `confirm_check` — no Ribbon filter yet. This is the always-ask baseline Warden gets compared against.
5. **Logging:** per tick — confirm-check calls received, current queue depth, average response delay.

**Exit criteria:** still zero collisions; 50 robots × 10,000 ticks produces logged confirm-check-per-tick and queue-depth series — the naive baseline for Phases 3–4.

### Phase 3 — Ribbon filter integration

1. **Fingerprint:** each claimed cell's `(x, y)` hashed into a single key — no type dimension needed, unlike perception-dedup work.
2. Each `robot.py` holds its own local Ribbon filter (from `core-rs` via PyO3), rebuilt periodically from a broadcast of the coordinator's ring-buffer contents. Default refresh interval: every 5 ticks — tune in Phase 4.
3. **Target false-positive rate:** modest default (e.g. 1%), tuned in Phase 4 against memory footprint.
4. Before a move: filter says "definitely free" → move immediately, log as **instant move**, no coordinator call. Filter says "maybe claimed" → `coordinator.confirm_check(cell)`, log as **confirmed check**, proceed or wait based on result.
5. **Staleness handling:** because local filters refresh periodically, a robot can get "definitely free" from a filter that's a few ticks stale. This is an accepted trade-off, and the ring buffer's TTL and the filter's refresh interval both need to be shorter than the minimum realistic cell-occupation time.
6. **[amended] Staleness is a real correctness path, not just a config tuning concern.** The confirm-check always hits the exact ring buffer, so it can never be wrong — but staleness only matters on the *"definitely free"* path, which never calls confirm at all. That means a robot can move into a cell another robot claimed moments ago, purely because the claiming robot's broadcast hasn't propagated yet. Add a dedicated test that deliberately sets `refresh_interval ≥ typical cell-occupation time` (an adversarial setting) and runs enough moves to see whether this actually produces a ground-truth collision. Two acceptable outcomes: (a) it doesn't, because occupation time is structurally bounded below by something else in the sim (state and prove what, e.g. minimum move duration) — document that invariant explicitly; or (b) it does, in which case report it plainly (this is a genuine, interesting finding — the filter's guarantee is conditional, not absolute) and decide whether to mitigate (e.g. a robot always confirms its own immediate next cell if its filter is more than N ticks stale) or ship it as a documented, demonstrated limitation.
7. Track and log instant vs. confirmed move mix; confirm the Phase 1 ground-truth check still never fails **under realistic (non-adversarial) settings** — run for at least 100,000 total simulated moves across varied densities.

**Exit criteria:** zero collisions across ≥100,000 simulated moves at realistic settings; the adversarial staleness test from step 6 run and its outcome documented either way; moderate-density run shows large majority of moves as "instant," confirmed-check volume far below the Phase 2 baseline — this gap is the traffic-reduction figure.

### Phase 4 — Benchmark harness

1. `scenarios.py`: density presets — sparse (10), moderate (50), crowded (120+) — fixed grid size, plus a `naive_mode` flag bypassing the filter (routes through Phase 2's always-confirm path).
2. **Statistical rigor:** ≥5 random seeds per preset, report mean ± range. Total simulated moves across all runs should exceed 100,000.
3. **Metrics per run:** instant-move %, confirmed-check %, network traffic reduction (Warden confirm-checks vs. naive, as a %), average/peak coordinator queue depth, total wall-clock time.
4. **Deriving headline figures:** traffic-reduction % = `1 - (Warden confirm-checks / Naive confirm-checks)` at a representative density — measure it, don't estimate it. CPU claim needs an actual profiling pass (`cProfile` or wall-clock-vs-idle) at the 120-robot density, not queue depth as a proxy — note if they diverge.
5. Output a results table/CSV (naive vs. Warden per density) plus a matplotlib PNG chart of coordinator load vs. robot count for both modes — this is the source for the Phase 5 dashboard's chart design and a static fallback if the live demo isn't available.

**Exit criteria:** confirm-check volume stays low and roughly flat for Warden from 10 → 120+ robots while naive mode's volume and backlog grow sharply; the specific figures are captured, not assumed.

### Phase 5 — Live visualization

1. **Floor renderer** (`grid_view.js`, canvas): NxN grid, adjustable size (10×10–40×40, re-initializes the sim). Robots as small colored circles with a heading indicator; optionally shade contested cells faintly.
2. **Robot count slider** (1–150): live-adjusts robot count without restarting the sim; debounce (~150ms).
3. **Mode toggle — Naive vs. Warden:** swaps coordination logic against the identical `grid_world` and movement logic, so the comparison is apples-to-apples.
4. **Split-screen mode:** Naive and Warden run simultaneously on two floor views fed identical spawn positions and movement seed. This is the core visual pitch.
5. **Live dashboard** (Chart.js via CDN, fed by the WebSocket tick stream): stacked/grouped bar of instant moves / confirmed checks / conflicts avoided; rolling line chart of coordinator load (one line per mode in split-screen); running totals as large numbers above the charts.
6. **Density/stress control:** push robot count toward grid capacity and watch Naive's load line climb while Warden's stays flat, matching the Phase 4 headline result.
7. **[amended] Broadcast-lag control:** a slider (or discrete presets: normal / degraded / adversarial) controlling the local filter's refresh interval, live-adjustable like the robot count slider. At normal settings, behaves like the rest of the demo — mostly green, occasional yellow. Pushed to the adversarial end (matching the setting proven or disproven in the Phase 3 amendment), it either visibly stays safe because of a demonstrated structural invariant, or produces a rare, clearly-flagged near-miss — either way, this is the control that shows the guarantee is understood, not just asserted. Log near-misses distinctly from confirmed conflicts in the dashboard so a viewer can see the difference between "the system caught it" (yellow→red) and "the filter didn't know yet" (a flagged near-miss).
8. **Visual design:** flat, minimal — no gradients/heavy shadows, small consistent color set (green = instant, yellow = confirmed check, red = conflict avoided; pick a distinct marker, not red, for a near-miss so it isn't confused with a caught conflict), sentence-case labels, generous whitespace. Use the `frontend-design` skill guidance when building this UI.

**Exit criteria:** a person can open the page, adjust robot count, switch Naive/Warden/split-screen, adjust the broadcast-lag control, and watch the dashboard diverge under load and (at adversarial lag) show the staleness boundary directly — no setup beyond running the server.

## 7. Notes for the agent

- Correctness first: the Phase 1 ground-truth collision check must never fail under realistic settings in later phases. A false negative causing a real collision is a P0 bug, not a tuning issue — except at the deliberately adversarial staleness setting from the Phase 3 amendment, where the outcome is a documented finding, not a bug to silently fix away.
- Keep confirm-check "network delay" simulated and configurable (tick-count delay) — no real networking.
- Write the Ribbon filter and ring buffer once, in `core-rs`, called from Python via PyO3 — don't reimplement either in Python.
- No Ribbon filter code exists to port from a prior project — write it standalone or use an existing crate; don't assume shared code that isn't there.
