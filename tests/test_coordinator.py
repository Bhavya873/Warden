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
        assert entry["conflicts_avoided"] >= 0
        assert entry["queue_depth"] >= 0
        assert entry["avg_delay"] is None or entry["avg_delay"] >= 0

    total_checks = sum(entry["confirm_checks"] for entry in log)
    total_conflicts_avoided = sum(entry["conflicts_avoided"] for entry in log)
    # every move requires a confirm-check in naive mode — with 15 robots moving on a
    # 20x20 grid there should be plenty of confirm-check traffic over 200 ticks
    assert total_checks > 0
    # and on a fairly small grid with 15 robots, some of those checks should find a
    # genuine conflict at least once over 200 ticks
    assert total_conflicts_avoided > 0
    # a resolved response is always exactly one request's outcome (conflict or not),
    # so cumulative conflicts-avoided can never exceed cumulative confirm-checks —
    # this only holds summed across the whole run, not tick-by-tick, since a response
    # can resolve several ticks after the request that produced it
    assert total_conflicts_avoided <= total_checks


def test_per_robot_tracking_for_task_12_markers():
    # outstanding_robot_ids and claimed_robot_ids_this_tick back the per-robot
    # yellow/red markers (Task 12) — a count alone can't say *which* robot to flash.
    sim = Simulator(grid_size=20, robot_count=15, seed=1, naive_mode=True)

    saw_outstanding = False
    saw_claimed = False
    for _ in range(200):
        sim.step()
        outstanding = sim.coordinator.outstanding_robot_ids
        claimed = sim.coordinator.claimed_robot_ids_this_tick
        assert len(outstanding) == len(set(outstanding))  # no duplicate robot ids
        assert len(claimed) == len(set(claimed))
        assert all(0 <= rid < 15 for rid in outstanding)
        assert all(0 <= rid < 15 for rid in claimed)
        saw_outstanding = saw_outstanding or bool(outstanding)
        saw_claimed = saw_claimed or bool(claimed)

    assert saw_outstanding, "expected at least one tick with an outstanding request"
    assert saw_claimed, "expected at least one tick with a claimed (conflict) resolution"


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
