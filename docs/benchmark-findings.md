# Benchmark findings (Task 7 — build spec §6 Phase 4)

Full run: `python -m sim.scenarios` — 5 seeds x 3 density presets (sparse/moderate/crowded
= 10/50/120 robots) x 2 modes, 1,500 ticks each, fixed 30x30 grid. 1,683,190 total
simulated moves (well over the 100,000 floor). Raw output:
`results/benchmark_results.csv`, `results/coordinator_load.png`.

## Traffic reduction — measured, and it is not flat

| Density | Robots | Warden instant % | Reduction vs. naive |
| --- | --- | --- | --- |
| sparse | 10 | 94.1% | **84.0%** |
| moderate | 50 | 71.2% | 46.9% |
| crowded | 120 | 19.1% | 17.1% |

The build spec's Phase 4 exit criteria describes confirm-check volume staying "low and
roughly flat for Warden as density scales from 10 to 120+ robots." That's not what this
measures. The reduction is real and positive at every density tested, but it shrinks
substantially under heavy contention — this matches the Task 5 finding that at high
density, most "maybe claimed" verdicts are *true* positives (genuine contention), not
filter noise, so the filter correctly stops being able to say "free." A flat reduction
line would only be honest if contention itself stayed flat with density, and it
doesn't — it can't, on a fixed grid.

**Queue depth (average coordinator backlog) tells the same story, not flat either, but
consistently lower for Warden at every density:**

| Density | Naive avg queue depth | Warden avg queue depth |
| --- | --- | --- |
| sparse | 3.4 | 0.55 |
| moderate | 19.6 | 10.4 |
| crowded | 76.9 | 63.8 |

(See `results/coordinator_load.png` — both lines climb with density; Warden's stays
below Naive's at every point measured, by a shrinking margin.)

**Recommendation:** if this demo's numbers get quoted anywhere (resume, portfolio), use
the sparse/moderate figures with the density stated explicitly — e.g. "up to 84%
reduction in coordinator confirm-check traffic at low-to-moderate robot density (10–50
robots on a 30x30 grid), narrowing under heavy contention" — rather than a single
flat 85%-across-10-to-120+-robots claim, which this benchmark does not support.

## CPU claim — does not transfer to a single-process demo, measured and dropped

The "<2% CPU" claim assumes a coordinator running as a separate, lightweight service
with huge spare capacity relative to one warehouse's traffic. That framing doesn't
apply here: this is a single Python process where the "coordinator" isn't a separate
service being lightly polled — it's most of what the per-tick loop actually does
(ring-buffer syncs, PyO3 filter/ring-buffer calls, confirm-check queue bookkeeping).

Measured via a policy=None baseline vs. the full run (same seed), attributing the
difference to coordinator + policy overhead — not queue depth used as a proxy, an
actual CPU-time measurement (`profile_coordinator_cpu_share` in `sim/scenarios.py`):

| Density | Naive coordinator CPU share | Warden coordinator CPU share |
| --- | --- | --- |
| sparse | 100.0% | 83.3% |
| crowded | 78.9% | 85.7% |

Nowhere near 2%. This isn't a bug or a tuning miss — it's the "<2%" claim describing a
different system than the one this demo actually is. **Recommendation: drop the CPU
claim entirely for this project**, or rephrase it as what's actually true: Warden
reduces the *coordinator's own request volume* (the queue-depth table above), which is
the honest analogue of "less coordinator load," without asserting a CPU percentage this
single-process model can't measure meaningfully.
