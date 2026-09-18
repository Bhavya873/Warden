from sim.simulator import Simulator


def _instant_and_confirmed(sim):
    instant = sum(e["instant_moves"] for e in sim.coordinator.log)
    confirmed = sum(e["confirmed_checks"] for e in sim.coordinator.log)
    return instant, confirmed


def test_zero_collisions_across_100k_moves_varied_densities():
    # >=100,000 total tick x robot attempts across varied densities, at realistic
    # (non-adversarial) filter refresh settings — build spec §6 Phase 3 exit criteria.
    # GridWorld.tick() raises CollisionError internally if this is ever violated.
    scenarios = [
        dict(grid_size=30, robot_count=10, seed=1, ticks=5000),  # 50,000
        dict(grid_size=30, robot_count=20, seed=2, ticks=3000),  # 60,000
    ]
    total_attempts = 0
    for scenario in scenarios:
        ticks = scenario.pop("ticks")
        sim = Simulator(warden_mode=True, **scenario)
        sim.run(ticks)
        total_attempts += ticks * scenario["robot_count"]

        positions = [(r.x, r.y) for r in sim.world.robots]
        assert len(set(positions)) == len(positions)

    assert total_attempts >= 100_000


def test_majority_instant_and_confirmed_far_below_naive():
    # Moderate density: large majority of moves resolved as instant, confirmed-check
    # volume dramatically lower than the naive always-confirm baseline (build spec §6
    # Phase 3 exit criteria). Thresholds below have real margin against the measured
    # values (instant ~89%, confirmed ~29% of naive) so they aren't seed-fragile.
    grid_size, robot_count, seed, ticks = 30, 20, 42, 3000

    naive = Simulator(grid_size=grid_size, robot_count=robot_count, seed=seed, naive_mode=True)
    naive.run(ticks)
    naive_confirmed = sum(e["confirm_checks"] for e in naive.coordinator.log)

    warden = Simulator(grid_size=grid_size, robot_count=robot_count, seed=seed, warden_mode=True)
    warden.run(ticks)
    instant, confirmed = _instant_and_confirmed(warden)

    assert instant / (instant + confirmed) > 0.7, "expected a large majority of instant moves"
    assert confirmed < naive_confirmed * 0.5, "warden confirm-check volume should be far below naive"
