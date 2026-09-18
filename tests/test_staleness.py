"""Adversarial staleness test — build spec §6 Phase 3 step 6 (the amendment). Sets the
Warden filter's refresh interval far past any realistic cell-occupation time (it never
refreshes after the initial build) and confirms two things: the risky path is genuinely
exercised (near-misses actually happen, a lot), and it never produces a real collision.

See tasks/staleness-finding.md for the full written finding this test backs.
"""

from core.coordinator import WardenCoordinator
from core.grid_world import GridWorld

NEVER_REFRESH = 1_000_000


def test_no_collisions_under_extreme_filter_staleness():
    grid_size, robot_count, seed, ticks = 30, 50, 7, 5000
    world = GridWorld(grid_size=grid_size, robot_count=robot_count, seed=seed)
    wc = WardenCoordinator(
        robot_count=robot_count, seed=seed, filter_refresh_interval_ticks=NEVER_REFRESH
    )

    for t in range(1, ticks + 1):
        wc.tick(t, world.occupied_cells())
        world.tick(policy=wc)  # raises CollisionError internally if this is ever violated

    positions = [(r.x, r.y) for r in world.robots]
    assert len(set(positions)) == len(positions)


def test_staleness_actually_produces_near_misses():
    # A passing test above is only meaningful if the adversarial setting actually put
    # the risky "filter says free, but it's wrong" path under real load — otherwise a
    # clean run would prove nothing. Assert near-misses are frequent, not incidental.
    grid_size, robot_count, seed, ticks = 30, 50, 7, 5000
    world = GridWorld(grid_size=grid_size, robot_count=robot_count, seed=seed)
    wc = WardenCoordinator(
        robot_count=robot_count, seed=seed, filter_refresh_interval_ticks=NEVER_REFRESH
    )

    for t in range(1, ticks + 1):
        wc.tick(t, world.occupied_cells())
        world.tick(policy=wc)

    total_attempts = sum(e["instant_moves"] + e["confirmed_checks"] for e in wc.log)
    assert world.near_miss_count > total_attempts * 0.1, (
        f"expected heavy near-miss traffic under a never-refreshing filter, "
        f"got {world.near_miss_count} near-misses out of {total_attempts} attempts"
    )
