# Warden — Task Plan

Derived from `Warden — Build Spec.md` (authoritative spec). No code exists yet — this breaks the spec's 5 phases into vertically-sliced, dependency-ordered tasks with acceptance criteria and verification steps.

## Dependency graph

```
Task 0 (scaffold)
   ├──> Task 1 (ring_buffer.rs)   ─┐  parallel, no shared state
   ├──> Task 2 (ribbon_filter.rs) ─┘  (merge only in lib.rs)
   └──> Task 3 (grid_world.py, Phase 1)
              │
              ├──> Task 4 (naive coordinator, Phase 2)      [needs Task 1 + Task 3]
              │       │
              │       ├──> Task 5 (Warden mode, Phase 3)    [needs Task 2 + Task 4]
              │       │       │
              │       │       ├──> Task 6 (adversarial staleness test)  [needs Task 5]
              │       │       ├──> Task 7 (benchmark harness, Phase 4)  [needs Task 5]
              │       │       └──> Task 8 (floor renderer, Phase 5.1)   [needs Task 5, W0/W1]
              │       │               │
              │       │               ├──> Task 9  (dashboard, Phase 5.2/5.6) [needs Task 8 + 7]
              │       │               ├──> Task 10 (split-screen, Phase 5.3)  [needs Task 8]
              │       │               └──> Task 11 (broadcast-lag, Phase 5.4) [needs Task 8 + Task 6]
              │       │                       │
              │       │                       └──> Task 12 (visual design pass) [needs Tasks 8-11]
              │       │
              │       └──> Task W1 (naive-mode dashboard skeleton) [needs Task 4 only]
              │
              └──> Task W0 (WS plumbing + bare canvas viewer) [needs Task 3 only]
```

Task 1/2 are independent (build in parallel). Task W0 needs only Task 3 and can run before Phase 2 finishes. Task W1 needs only Task 4 and can run in parallel with Task 5/6/7. Task 11 is blocked on **both** Task 8 and Task 6 — its visual/logging behavior is defined by whatever Task 6 finds, so it cannot start on an assumed outcome.

## Tasks

### Task 0 — Repo & toolchain scaffold
**Delivers:** directories per Build Spec §5, `core-rs/Cargo.toml` with PyO3 + maturin config, empty `lib.rs` exposing one placeholder function, `requirements.txt`, pytest config.
**Acceptance:** `maturin develop` succeeds; `python -c "import warden_core; warden_core.ping()"` returns without error; `pytest` and `cargo test` both run (even with zero real tests) without config errors.
**Verify:** run the build, run the import, run both test runners.

### Task 1 — `ring_buffer.rs` (parallel with Task 2)
**Delivers:** fixed-capacity ring buffer of `(cell, robot_id, expiry_tick)`, `claim`/`is_claimed`/`release`, per-tick expiry eviction, PyO3 binding. (Spec §6 Phase 2 step 1 — corrected to Rust, matching §5/§7.)
**Acceptance:** cargo unit tests cover capacity eviction (oldest evicted when full), TTL expiry, and `is_claimed` correctness after mixed claim/release/expire sequences.
**Verify:** `cargo test -p core-rs`; a Python smoke test importing the binding and calling `claim`/`is_claimed` once.

### Task 2 — `ribbon_filter.rs` (parallel with Task 1)
**Delivers:** Ribbon filter with configurable target FP rate (default 1%, spec §6 Phase 3 step 3), `insert`/`query`, PyO3 binding.
**Acceptance:** cargo property test — every inserted key returns `query(key) == true` (zero false negatives, the one non-negotiable invariant per spec §7); measured FP rate on a random non-inserted key set is near the configured target.
**Verify:** `cargo test -p core-rs`; a standalone script printing measured FP rate at a few fill ratios.

### Task 3 — Grid world + ground-truth collisions (Phase 1)
**Delivers:** `core/grid_world.py` (NxN grid, greedy movement, deterministic per-tick resolution order by `robot_id`, ground-truth collision assertion, target reassignment, config surface for grid size/robot count/seed), `core/robot.py`/`core/coordinator.py` stubs, `sim/simulator.py` as the formal tick-driving loop (built here so Phase 2 extends it rather than rebuilding it).
**Acceptance:** spec's Phase 1 exit criteria exactly — `tests/test_grid_world.py` passes fixed-seed reproducibility, boundary (no off-grid moves), and collision-assertion tests; 50 robots × 10,000 ticks → zero double-occupied cells.
**Verify:** `pytest tests/test_grid_world.py -v`; a stress-run script logging zero collisions over the 50×10k run.

### Task 4 — Naive coordinator end-to-end (Phase 2)
**Delivers:** `core/coordinator.py` wired to Task 1's ring buffer via PyO3, `confirm_check(cell)` with randomized 1–3 tick delay, per-tick queue-depth tracking, every move routed through confirm_check (no filter), per-tick logging (confirm-check count, queue depth, avg delay).
**Acceptance:** spec's Phase 2 exit criteria — still zero collisions; 50×10k run produces the confirm-check-per-tick and queue-depth series used as the Phase 4 naive baseline.
**Verify:** rerun the stress test with the coordinator wired in; inspect the emitted log for sane shape (queue depth rises under density).
**Note:** first genuinely demoable "naive mode end-to-end" slice — Task W1 attaches here.

### Task W0 — WS plumbing + bare canvas viewer (parallel risk-reduction)
**Delivers:** `server/ws_server.py` streaming raw `grid_world` tick snapshots (positions/ids only), `web/index.html` + `web/grid_view.js` rendering moving dots on a canvas.
**Acceptance:** open the page, see robots moving in real time (no coordination coloring yet).
**Verify:** manual browser check; document the WS message shape — this becomes the contract Tasks W1/8/9 extend.
**Depends on:** Task 3 only.

### Task W1 — Naive-mode dashboard wiring (parallel risk-reduction)
**Delivers:** WS messages extended with confirm-check count / queue depth from Task 4; `web/` gets an empty Chart.js skeleton fed by these two series.
**Acceptance:** dashboard lines move under load in naive mode.
**Verify:** manual browser check with robot count pushed up — confirm queue-depth line visibly climbs.
**Depends on:** Task 4.

### Task 5 — Warden mode end-to-end (Phase 3 core)
**Delivers:** `core/robot.py` holding a real Ribbon filter instance (Task 2's binding), periodic rebuild from a new `coordinator.py` broadcast method (default refresh interval 5 ticks, configurable), `(x,y)` fingerprinting, move logic (`definitely free` → instant move, no coordinator call; `maybe claimed` → `confirm_check`), instant-vs-confirmed logging.
**Acceptance:** spec's Phase 3 exit criteria minus the adversarial test (split into Task 6) — zero ground-truth collisions across ≥100,000 simulated moves at realistic settings across varied densities; moderate-density run shows large majority "instant," confirmed-check volume far below Task 4's naive baseline.
**Verify:** run the ≥100k-move harness at realistic settings; diff instant/confirmed counts against Task 4's always-confirm counts.

### Task 6 — Adversarial staleness test (Phase 3 amendment — high value)
**Delivers:** a dedicated test setting `refresh_interval >= typical cell-occupation time`, run long enough to observe whether a real ground-truth collision occurs, and a written finding: either (a) a structural invariant bounding occupation time below the adversarial setting — state and prove it — or (b) a real, reported collision, with a decision on whether to mitigate (e.g. "always confirm own immediate next cell if filter is >N ticks stale") or ship as a documented limitation. Also resolves what counts as a "near-miss" for Task 11's logging.
**Acceptance:** the test runs and produces one of the two documented outcomes — this is a required deliverable regardless of which outcome occurs, not a pass/fail gate.
**Verify:** run the adversarial-settings harness; read the written finding.
**Depends on:** Task 5.

### Task 7 — Benchmark harness (Phase 4)
**Delivers:** `sim/scenarios.py` density presets (sparse 10 / moderate 50 / crowded 120+) + `naive_mode` flag; harness running ≥5 seeds per preset (>100,000 total moves); metrics (instant %, confirmed %, traffic-reduction %, avg/peak queue depth, wall-clock); CPU profiling pass at 120-robot density (cProfile or wall-clock-vs-idle, not queue depth as proxy); output CSV/table + matplotlib PNG.
**Acceptance:** spec's Phase 4 exit criteria — confirm-check volume stays low/flat for Warden from 10→120+ robots while naive's grows sharply; figures captured, not estimated.
**Verify:** run the harness, inspect CSV/PNG, spot-check the traffic-reduction formula (`1 - Warden/Naive confirm-checks`) against raw numbers.
**Depends on:** Task 5 (naive path already satisfied by Task 4).
**May split into 7a (raw metrics harness) / 7b (headline figures + CPU profiling + charts) if it feels heavy for one slice.**

### Task 8 — Floor renderer + robot-count slider + mode toggle (Phase 5.1)
**Delivers:** canvas grid (10×10–40×40, re-initializes sim on resize), robots as circles with heading indicator, robot-count slider (1–150, live-adjust without sim restart — requires `grid_world`/`simulator` to support adding/removing robots mid-run), Naive/Warden mode toggle against identical `grid_world`/movement logic.
**Acceptance:** open the page, adjust robot count live, switch Naive/Warden — both modes behave identically at the movement level, differently at the coordination level.
**Verify:** manual browser check; toggle mid-run and confirm robot positions don't jump/reset.
**Depends on:** Task 5, Task W0/W1.

### Task 9 — Chart.js dashboard + density/stress control (Phase 5.2 + 5.6)
**Delivers:** stacked/grouped bar (instant/confirmed/conflict), rolling coordinator-load line, running totals; density/stress control pushing robot count toward capacity to reproduce Task 7's headline result live.
**Acceptance:** at high density, Naive's load line climbs while Warden's stays flat — visually matches Task 7's captured figures.
**Verify:** manual browser check against Task 7's numbers.
**Depends on:** Task 8, Task 7.

### Task 10 — Split-screen mode (Phase 5.3)
**Delivers:** Naive and Warden running simultaneously on two floor views, fed identical spawn positions and movement seed (requires `simulator.py` to run two coordinator+robot sets off a shared RNG seed).
**Acceptance:** visually identical robot paths on both sides until a coordination-driven divergence (a wait) occurs.
**Verify:** manual browser check with a fixed seed; confirm both sides start identical.
**Depends on:** Task 8.

### Task 11 — Broadcast-lag control (Phase 5 amendment — high value)
**Delivers:** slider/presets (normal/degraded/adversarial) live-adjusting `refresh_interval` on running robots; near-miss detection and logging distinct from confirmed conflicts (design resolved in Task 6); distinct marker (not red) for near-miss in both renderer and dashboard.
**Acceptance:** at normal settings, mostly green with occasional yellow; at adversarial settings, either stays safe (matching Task 6's proven invariant) or shows a rare, clearly-flagged near-miss (matching Task 6's reported finding).
**Verify:** push the slider to adversarial live, watch for the flagged marker; cross-check frequency roughly matches Task 6's offline adversarial test.
**Depends on:** Task 8 and Task 6.

### Task 12 — Visual design pass (Phase 5.8)
**Delivers:** flat/minimal styling, consistent color set (green/yellow/red + distinct near-miss marker), sentence-case labels, whitespace — using the `frontend-design` skill per spec's explicit instruction.
**Acceptance:** visual review against spec §6 Phase 5 item 8's checklist.
**Verify:** manual review; no functional test needed.
**Depends on:** Tasks 8–11.

## Checkpoints

1. **After Task 0 + Tasks 1/2** — review the PyO3 boundary shape before any Python code depends on it. Most expensive thing to redo later.
2. **After Task 3** — Phase 1's ground-truth check is a non-negotiable P0 gate per spec §7; nothing downstream should build on an unproven invariant.
3. **After Task 4** — the logging/metrics schema (confirm-check count, queue depth, avg delay) is consumed by Task 7's harness and W1's dashboard; lock it once.
4. **After Task 6 — most important checkpoint.** Resolves the spec's one open empirical question (does staleness ever cause a real collision) and directly determines Task 11's design. Task 7 and Task 11 should not start on an assumed outcome.
5. **After Task 7** — sanity-check that traffic-reduction % and the CPU figure were measured, not estimated, per spec §6 Phase 4 step 4's explicit warning.
6. **After Task 8** — confirm the WS schema is stable before Tasks 10/11 both extend it; restructuring later touches three downstream tasks at once.
