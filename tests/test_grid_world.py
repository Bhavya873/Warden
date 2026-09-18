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
