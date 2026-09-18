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
