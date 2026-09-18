# Staleness finding (Task 6 — build spec §6 Phase 3 step 6)

## The question

The Warden filter's "definitely free" path skips the confirm-check entirely. If a
robot's local filter is stale — built before another robot claimed a cell — can that
ever let a robot move into a cell someone else actually holds, causing a real
collision?

## Setup

`tests/test_staleness.py` sets the Warden filter's refresh interval to 1,000,000 ticks
(it never refreshes past its initial build) on a 50-robot, 30x30 grid for 5,000 ticks —
`refresh_interval` is pushed to roughly 1,000x any realistic cell-occupation time in
this sim (occupation time is normally 1–15 ticks; see `AXIS_FLIP_THRESHOLD_TICKS` /
`RANDOM_ESCAPE_THRESHOLD_TICKS` in `core/grid_world.py`). The filter is frozen at
tick-1 occupancy while the fleet keeps moving for the entire run — about as adversarial
as this setting gets.

## Result

- **Near-misses: 74,643 out of 228,607 total move attempts (~32.6%).** A near-miss is
  logged whenever the policy (filter or coordinator) approved a move but the live
  occupancy check rejected it anyway. The frozen filter is wrong about freedom roughly
  a third of the time under this setting — the risky path is genuinely, heavily
  exercised, not just theoretically possible.
- **Collisions: zero.** `GridWorld.tick()` raises `CollisionError` internally if two
  robots ever end up on the same cell; across this run (and every other test in
  `tests/test_warden.py`, `tests/test_grid_world.py`) it never does.

## Outcome: (a) — structurally impossible, not just empirically rare

The filter's "definitely free" verdict never gates a move directly into the occupancy
map. `GridWorld.tick()` (`core/grid_world.py`) always re-checks `next_cell in
self._occupied` — the live, synchronously-updated occupancy dict — immediately before
applying any move, regardless of what the policy said:

```python
if policy is not None and not policy.can_move(robot, next_cell):
    ...
    continue
if next_cell in self._occupied:      # <- final authority, independent of the policy
    ...
    continue
```

**The invariant:** in this simulation, ground truth (`self._occupied`) is a single
in-process data structure with no propagation delay of its own — updating it and
reading it are the same synchronous operation. The Ribbon filter and the ring buffer
can each be arbitrarily stale relative to it, but neither of them is *ever* consulted
to decide whether a move actually succeeds — only whether a robot is *allowed to
attempt* one without a network round trip. A stale "yes" just means the attempt gets
made and then rejected at the final check — a near-miss, not a collision. This holds
for *any* refresh interval, not just the adversarial one tested here; the 1,000,000-tick
setting was chosen to make the risky path fire constantly and stress-test the finding,
not because the boundary sits anywhere near that value.

**Caveat — this is a property of the simulation, not of Warden's architecture in
general.** In a real distributed deployment, "ground truth" would itself be spread
across robots and a coordinator, each learning about a claim over the network with its
own delay — there would be no single instantaneous source of truth to fall back on the
way `self._occupied` is one here. This finding says the *simulation's* collision
guarantee is unconditional; it doesn't say a real Warden deployment's guarantee would
be. That's an accurate limitation of a single-process demo, worth stating plainly
rather than implying more than what was actually tested.

No mitigation is needed for collision-safety — there's no gap to close. The near-miss
counter (`GridWorld.near_miss_count`) exists purely as instrumentation: it's the signal
that "the filter was wrong," caught by a safety net a real distributed system wouldn't
have.

## Near-miss, defined (for Task 11)

A **near-miss** is a tick where `policy.can_move(robot, next_cell)` returned `True`
(the filter said free, or a confirm-check resolved to "not claimed") but
`next_cell in self._occupied` was true anyway — ground truth had moved on since the
policy's answer was formed. It is distinct from a **conflict avoided** (the normal
yellow→red case: the filter said maybe, the confirm-check correctly reported the cell
as claimed, and the robot waited) — a near-miss is the filter or coordinator being
*wrong*, not correctly cautious. Task 11's broadcast-lag control should visualize these
with a distinct marker, not red, per the build spec's Phase 5 amendment — conflating
the two would misrepresent "the system caught it" as "the system was right."
