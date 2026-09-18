from sim.simulator import Simulator


def test_naive_mode_zero_collisions_over_many_ticks():
    # 50 robots x 10,000 ticks with the coordinator wired in — same stress figure as
    # Phase 1, now routed through confirm_check on every move. GridWorld.tick() still
    # raises CollisionError internally if this is ever violated.
    sim = Simulator(grid_size=30, robot_count=50, seed=123, naive_mode=True)
    sim.run(10_000)

    positions = [(r.x, r.y) for r in sim.world.robots]
    assert len(set(positions)) == len(positions)


def test_confirm_check_log_shape():
    ticks = 200
    sim = Simulator(grid_size=20, robot_count=15, seed=1, naive_mode=True)
    sim.run(ticks)

    log = sim.coordinator.log
    assert len(log) == ticks
    for entry in log:
        assert entry["confirm_checks"] >= 0
        assert entry["queue_depth"] >= 0
        assert entry["avg_delay"] is None or entry["avg_delay"] >= 0

    # every move requires a confirm-check in naive mode — with 15 robots moving on a
    # 20x20 grid there should be plenty of confirm-check traffic over 200 ticks
    assert sum(entry["confirm_checks"] for entry in log) > 0


def test_queue_depth_rises_under_density():
    ticks = 500
    grid_size = 30

    sparse = Simulator(grid_size=grid_size, robot_count=5, seed=7, naive_mode=True)
    sparse.run(ticks)
    sparse_avg_queue_depth = sum(e["queue_depth"] for e in sparse.coordinator.log) / ticks

    crowded = Simulator(grid_size=grid_size, robot_count=60, seed=7, naive_mode=True)
    crowded.run(ticks)
    crowded_avg_queue_depth = sum(e["queue_depth"] for e in crowded.coordinator.log) / ticks

    assert crowded_avg_queue_depth > sparse_avg_queue_depth
