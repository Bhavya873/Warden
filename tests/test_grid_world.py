import pytest

from core.grid_world import GridWorld


def _positions(world):
    return [(r.x, r.y) for r in world.robots]


def _targets(world):
    return [(r.target_x, r.target_y) for r in world.robots]


def test_fixed_seed_reproducibility():
    world_a = GridWorld(grid_size=20, robot_count=15, seed=42)
    world_b = GridWorld(grid_size=20, robot_count=15, seed=42)
    assert _positions(world_a) == _positions(world_b)
    assert _targets(world_a) == _targets(world_b)

    for _ in range(200):
        world_a.tick()
        world_b.tick()
        assert _positions(world_a) == _positions(world_b)
        assert _targets(world_a) == _targets(world_b)


def test_robots_never_leave_grid_bounds():
    grid_size = 15
    world = GridWorld(grid_size=grid_size, robot_count=20, seed=7)

    for _ in range(500):
        world.tick()
        for robot in world.robots:
            assert 0 <= robot.x < grid_size
            assert 0 <= robot.y < grid_size


def test_no_collisions_over_many_ticks():
    # 50 robots x 10,000 ticks — the exact stress figure from the build spec's Phase 1
    # exit criteria. GridWorld.tick() raises CollisionError internally if this is ever
    # violated, so a clean run is itself the proof.
    world = GridWorld(grid_size=30, robot_count=50, seed=123)
    for _ in range(10_000):
        world.tick()

    positions = _positions(world)
    assert len(set(positions)) == len(positions)


def test_no_permanent_gridlock():
    # Regression test: plain greedy single-axis movement (no anti-deadlock fallback)
    # settles into a permanent, whole-fleet circular-wait deadlock on this exact
    # scenario — 10 robots on a 30x30 grid were fully frozen (0 moves/tick) by tick
    # ~700 and never recovered. The axis-flip / random-escape fallback in
    # GridWorld._preferred_step exists specifically to prevent this.
    world = GridWorld(grid_size=30, robot_count=10, seed=42)
    for _ in range(3000):
        world.tick()

    moves_in_final_window = 0
    for _ in range(200):
        before = _positions(world)
        world.tick()
        after = _positions(world)
        moves_in_final_window += sum(1 for a, b in zip(before, after) if a != b)

    assert moves_in_final_window > 0, "fleet is permanently gridlocked"


def test_bounded_local_targets_reduce_center_clustering():
    # Regression for the "robots huddle in the middle" behavior: point-to-point
    # trips between fully uniform random targets concentrate near the grid's center
    # (a geometric-probability effect — the same reason the midpoint of two uniform
    # random points clusters toward the center of a region). Bounding new targets to
    # a local radius around the robot's current position, reflected (not clamped) at
    # the grid boundary so edge-adjacent robots aren't systematically pulled back
    # inward, reduces that.
    #
    # The margin here is smaller than it used to be (was ">4x lower", now just
    # "meaningfully lower") because _random_target also picks the least-crowded of
    # several candidate targets (see TARGET_CANDIDATE_COUNT), which independently
    # pulls robots toward open space and away from the center under BOTH divisor
    # settings — so most of the old ratio's gap is now attributable to that spacing
    # heuristic rather than to locality alone. Measured ratio (bounded/fully_random)
    # across several seeds: ~0.47-0.59; 0.7 leaves comfortable margin without being
    # so loose it'd miss a real regression.
    import core.grid_world as gw
    from core.coordinator import WardenCoordinator

    def center_fraction(divisor):
        original = gw.TARGET_LOCALITY_DIVISOR
        gw.TARGET_LOCALITY_DIVISOR = divisor
        try:
            grid_size, robot_count, seed, ticks = 30, 60, 7, 1500
            world = GridWorld(grid_size=grid_size, robot_count=robot_count, seed=seed)
            wc = WardenCoordinator(robot_count=robot_count, seed=seed)
            center = grid_size / 2
            center_radius = grid_size * 0.2
            samples = in_center = 0
            for t in range(1, ticks + 1):
                wc.tick(t, world.occupied_cells())
                world.tick(policy=wc)
                if t % 10 == 0:
                    for r in world.robots:
                        samples += 1
                        d = ((r.x - center) ** 2 + (r.y - center) ** 2) ** 0.5
                        if d < center_radius:
                            in_center += 1
            return in_center / samples
        finally:
            gw.TARGET_LOCALITY_DIVISOR = original

    fully_random = center_fraction(divisor=1)  # radius == grid_size, covers the whole grid
    bounded = center_fraction(divisor=5)
    assert bounded < fully_random * 0.7, (
        f"expected bounded-target locality to meaningfully reduce center clustering, "
        f"got bounded={bounded:.3f} vs fully_random={fully_random:.3f}"
    )


def test_target_sampling_near_a_boundary_is_not_pulled_inward():
    # A plain clamped sampling window (the first version of the locality fix) can't
    # extend past the grid edge, so a robot sitting at x=0 got a mean target x of
    # ~3.0 instead of the unbiased ~0 (measured directly, see grid_world.py's
    # _reflect_into_range docstring) — a small but systematic inward drift repeated
    # every hop for every edge-adjacent robot. Reflecting instead of clamping only
    # improves this *single-hop* number modestly (~3.0 -> ~2.8: a discrete boundary
    # can't be made perfectly unbiased for a robot sitting exactly on it — some
    # asymmetry is unavoidable there). The dramatic effect is at the population level
    # over many hops (test_bounded_local_targets_reduce_center_clustering, ~8x lower
    # than fully-random) — this test only locks in that the single-hop drift itself
    # got smaller, not that it vanished.
    world = GridWorld(grid_size=30, robot_count=1, seed=1)
    robot = world.robots[0]
    robot.x, robot.y = 0, 15

    targets_x = [world._random_target(exclude=(robot.x, robot.y))[0] for _ in range(3000)]
    mean_x = sum(targets_x) / len(targets_x)
    assert mean_x < 2.95, f"expected reflection to reduce the ~3.0 clamped-window drift at least somewhat, got mean {mean_x:.2f}"


def _median_nearest_neighbor_distance(world):
    coords = _positions(world)
    distances = [
        min(abs(x1 - x2) + abs(y1 - y2) for j, (x2, y2) in enumerate(coords) if i != j)
        for i, (x1, y1) in enumerate(coords)
    ]
    distances.sort()
    mid = len(distances) // 2
    return distances[mid] if len(distances) % 2 else (distances[mid - 1] + distances[mid]) / 2


def test_least_crowded_target_candidate_prevents_persistent_clumping():
    # Regression for a second, subtler form of the "robots clump together" complaint,
    # found after the center-clustering fix above already landed: a plain local random
    # walk (pick one target within a radius, no regard for other robots) has nothing
    # pushing two robots that happen to drift near each other apart again, so a chance
    # local density pocket can persist or even worsen for thousands of ticks instead of
    # dispersing (measured on the pre-fix code: median nearest-neighbor distance across
    # a 20-robot/30x30 run shrank from a spawn-time ~5 to ~2 over 5000 ticks on more
    # than one seed). Sampling TARGET_CANDIDATE_COUNT candidates and keeping the one
    # farthest from another robot fixes this — median spacing should hold steady or
    # improve over a long run, not collapse.
    world = GridWorld(grid_size=30, robot_count=20, seed=1)
    spawn_median = _median_nearest_neighbor_distance(world)

    for _ in range(5000):
        world.tick()

    late_median = _median_nearest_neighbor_distance(world)
    assert late_median >= spawn_median * 0.6, (
        f"expected robot spacing to hold roughly steady over a long run, "
        f"got spawn median={spawn_median} vs late median={late_median}"
    )


def test_add_robot_on_a_full_grid_fails_fast_instead_of_hanging():
    # Regression: _random_free_cell() used to spin forever once the grid had no free
    # cells left (every server WS control message that could overfill a grid — e.g.
    # dragging robot count above grid_size**2, or shrinking the grid below the current
    # robot count in split mode — hung the whole asyncio event loop, since a blocking
    # synchronous infinite loop never yields back to accept new connections). It must
    # now fail fast (None from add_robot, RuntimeError from _random_free_cell directly)
    # instead of looping.
    grid_size = 3
    world = GridWorld(grid_size=grid_size, robot_count=grid_size * grid_size, seed=1)
    assert len(world.robots) == grid_size * grid_size

    assert world.add_robot() is None  # grid is full — no free cell exists

    with pytest.raises(RuntimeError):
        world._random_free_cell()


def test_target_reassignment_keeps_robots_moving_toward_new_targets():
    world = GridWorld(grid_size=10, robot_count=1, seed=1)
    robot = world.robots[0]
    initial_target = (robot.target_x, robot.target_y)

    reached_once = False
    for _ in range(200):
        world.tick()
        if (robot.x, robot.y) == initial_target:
            reached_once = True
            break

    assert reached_once, "robot never reached its initial target"
    # a new target was assigned immediately on arrival — it shouldn't just sit there
    assert (robot.target_x, robot.target_y) != initial_target
