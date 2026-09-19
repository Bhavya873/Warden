"""Adversarial staleness test. Sets the Warden filter's refresh interval far past any
realistic cell-occupation time (it never refreshes after the initial build) and
confirms two things: the risky path is genuinely exercised (near-misses actually
happen, a lot), and it never produces a real collision.

See README.md's "The staleness finding" section for the full written finding this
test backs.
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


def test_near_miss_robot_ids_track_the_current_tick_only():
    # Task 11's live marker needs to know *which* robots had a near-miss on the tick
    # just completed, not just a cumulative count — this is reset every tick, not
    # accumulated, unlike near_miss_count.
    grid_size, robot_count, seed = 30, 50, 7
    world = GridWorld(grid_size=grid_size, robot_count=robot_count, seed=seed)
    wc = WardenCoordinator(robot_count=robot_count, seed=seed, filter_refresh_interval_ticks=NEVER_REFRESH)

    saw_a_near_miss_tick = False
    for t in range(1, 501):
        wc.tick(t, world.occupied_cells())
        world.tick(policy=wc)
        if world.near_miss_robot_ids:
            saw_a_near_miss_tick = True
            assert all(0 <= rid < robot_count for rid in world.near_miss_robot_ids)
            assert len(world.near_miss_robot_ids) == len(set(world.near_miss_robot_ids))  # no duplicates

    assert saw_a_near_miss_tick, "expected at least one tick with a near-miss over 500 ticks at this setting"
