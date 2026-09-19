import asyncio
import contextlib
import json

import websockets

from server.ws_server import MAX_ROBOT_COUNT, SimulationServer


def test_step_and_serialize_shape():
    # Single-board mode is no longer the server's default (the shipped frontend only
    # ever shows split view) but it's still a valid server capability — verify its
    # message shape by asking for it explicitly.
    server = SimulationServer()
    server.set_mode("warden")
    state = server.step_and_serialize()

    assert state["type"] == "tick"
    assert state["tick"] == 1
    assert state["grid_size"] == server.grid_size
    assert state["mode"] in ("naive", "warden")
    assert len(state["robots"]) == state["robot_count"]
    for robot in state["robots"]:
        assert set(robot) == {"id", "x", "y", "dx", "dy", "near_miss", "outcome"}
        assert robot["outcome"] in ("moved", "waiting", "conflict", "idle")
    assert set(state["stats"]) == {
        "queue_depth",
        "step_seconds",
        "instant_moves",
        "confirmed_checks",
        "conflicts_avoided",
        "near_misses",
        "near_miss_count_total",
    }
    assert set(state["totals"]) == {"instant_moves", "confirmed_checks", "conflicts_avoided", "near_misses"}
    assert state["broadcast_lag"] == "normal"


def test_robot_outcome_matches_coordinator_state():
    server = SimulationServer()
    server.set_mode("naive")  # every move goes through confirm-check — easy to exercise

    saw_moved = saw_waiting = saw_conflict = False
    for _ in range(300):
        state = server.step_and_serialize()
        claimed_ids = set(server.sim.coordinator.claimed_robot_ids_this_tick)
        outstanding_ids = set(server.sim.coordinator.outstanding_robot_ids)
        for robot in state["robots"]:
            if robot["id"] in claimed_ids:
                assert robot["outcome"] == "conflict"
                saw_conflict = True
            elif robot["outcome"] == "moved":
                saw_moved = True
            elif robot["id"] in outstanding_ids:
                assert robot["outcome"] == "waiting"
                saw_waiting = True

    assert saw_moved and saw_waiting and saw_conflict, "expected to observe all three outcomes over 300 ticks"


def test_step_and_serialize_runs_cleanly_over_many_ticks():
    server = SimulationServer()
    for _ in range(500):
        server.step_and_serialize()  # raises CollisionError internally if ever violated


def test_set_mode_preserves_robot_positions_and_tick_count():
    server = SimulationServer()
    for _ in range(10):
        server.step_and_serialize()

    positions_before = {r.robot_id: (r.x, r.y) for r in server.sim.world.robots}
    targets_before = {r.robot_id: (r.target_x, r.target_y) for r in server.sim.world.robots}
    tick_before = server.sim.world.tick_count

    server.set_mode("naive" if server.mode == "warden" else "warden")

    positions_after = {r.robot_id: (r.x, r.y) for r in server.sim.world.robots}
    targets_after = {r.robot_id: (r.target_x, r.target_y) for r in server.sim.world.robots}
    assert positions_after == positions_before
    assert targets_after == targets_before
    assert server.sim.world.tick_count == tick_before


def test_totals_accumulate_and_reset_on_mode_switch():
    server = SimulationServer()
    server.set_mode("warden")
    for _ in range(20):
        state = server.step_and_serialize()
    assert state["totals"]["instant_moves"] + state["totals"]["confirmed_checks"] > 0
    totals_before_switch = dict(state["totals"])
    assert totals_before_switch == server.totals

    server.set_mode("naive" if server.mode == "warden" else "warden")
    assert server.totals == {"instant_moves": 0, "confirmed_checks": 0, "conflicts_avoided": 0, "near_misses": 0}

    state = server.step_and_serialize()
    # totals resumed accumulating from zero in the new mode: after exactly one tick,
    # they should equal that tick's own stats, not carry over the old mode's numbers
    assert state["totals"]["instant_moves"] == state["stats"]["instant_moves"]
    assert state["totals"]["confirmed_checks"] == state["stats"]["confirmed_checks"]
    assert state["totals"]["conflicts_avoided"] == state["stats"]["conflicts_avoided"]
    assert state["totals"]["near_misses"] == state["stats"]["near_misses"]


def test_set_robot_count_add_and_remove():
    server = SimulationServer()
    server.set_mode("warden")
    original_positions = {r.robot_id: (r.x, r.y) for r in server.sim.world.robots}

    server.set_robot_count(35)
    assert len(server.sim.world.robots) == 35
    # existing robots weren't touched, only new ones appended
    current_positions = {r.robot_id: (r.x, r.y) for r in server.sim.world.robots}
    for robot_id, pos in original_positions.items():
        assert current_positions[robot_id] == pos

    server.set_robot_count(5)
    assert len(server.sim.world.robots) == 5

    server.set_robot_count(MAX_ROBOT_COUNT + 50)  # clamps to the max
    assert len(server.sim.world.robots) == MAX_ROBOT_COUNT


def test_set_grid_size_reinitializes():
    server = SimulationServer()
    server.set_mode("warden")
    server.step_and_serialize()

    server.set_grid_size(15)
    assert server.grid_size == 15
    assert server.sim.world.grid_size == 15
    assert server.sim.world.tick_count == 0  # fresh world, as documented


def test_set_robot_count_past_grid_capacity_does_not_hang():
    # Regression: this exact sequence (drag robots to the max, then the grid to its
    # min — 150 robots > 10x10 = 100 cells) hung the live server's event loop, because
    # add_robot() looped forever looking for a free cell that no longer existed. A
    # test that completes at all is the proof this no longer hangs.
    server = SimulationServer()
    server.set_mode("warden")
    server.set_grid_size(10)
    server.set_robot_count(MAX_ROBOT_COUNT)  # 150 requested, only 100 cells exist

    assert len(server.sim.world.robots) == 100
    state = server.step_and_serialize()  # must not raise or hang
    assert state["robot_count"] == 100


def test_set_grid_size_shrink_below_robot_count_does_not_hang_in_split_mode():
    server = SimulationServer()
    server.set_mode("split")
    server.set_robot_count(MAX_ROBOT_COUNT)
    server.set_grid_size(10)  # shrinking below the current robot count, both boards

    assert len(server.split_sims["naive"].world.robots) == 100
    assert len(server.split_sims["warden"].world.robots) == 100
    state = server.step_and_serialize()  # must not raise or hang
    assert state["boards"]["naive"]["robot_count"] == 100


def test_split_mode_shape():
    server = SimulationServer()
    server.set_mode("split")
    state = server.step_and_serialize()

    assert state["type"] == "tick_split"
    assert state["mode"] == "split"
    assert set(state["boards"]) == {"naive", "warden"}
    assert state["broadcast_lag"] == "normal"
    for label, board in state["boards"].items():
        assert set(board) == {"tick", "robot_count", "robots", "stats", "totals"}
        assert len(board["robots"]) == board["robot_count"]
        assert set(state["boards"][label]["stats"]) == {
            "queue_depth",
            "step_seconds",
            "instant_moves",
            "confirmed_checks",
            "conflicts_avoided",
            "near_misses",
            "near_miss_count_total",
        }


def test_split_mode_starts_identical_then_can_diverge():
    server = SimulationServer()
    server.set_mode("split")

    naive_positions_initial = {r.robot_id: (r.x, r.y) for r in server.split_sims["naive"].world.robots}
    warden_positions_initial = {r.robot_id: (r.x, r.y) for r in server.split_sims["warden"].world.robots}
    naive_targets_initial = {r.robot_id: (r.target_x, r.target_y) for r in server.split_sims["naive"].world.robots}
    warden_targets_initial = {r.robot_id: (r.target_x, r.target_y) for r in server.split_sims["warden"].world.robots}
    # same seed, same spawn — both boards start from literally identical conditions
    assert naive_positions_initial == warden_positions_initial
    assert naive_targets_initial == warden_targets_initial

    for _ in range(400):
        server.step_and_serialize()

    naive_positions_later = {r.robot_id: (r.x, r.y) for r in server.split_sims["naive"].world.robots}
    warden_positions_later = {r.robot_id: (r.x, r.y) for r in server.split_sims["warden"].world.robots}
    # different coordination strategies produce different move timing — the boards
    # should have diverged by now, not stayed in lockstep forever
    assert naive_positions_later != warden_positions_later


def test_leaving_split_resumes_single_mode_world_where_it_was_frozen():
    server = SimulationServer()
    server.set_mode("warden")  # exercise a single-mode world actually advancing first —
    for _ in range(15):  # the server defaults to split, so without this the "before"
        server.step_and_serialize()  # snapshot below would trivially be the frozen spawn state
    positions_before_split = {r.robot_id: (r.x, r.y) for r in server.sim.world.robots}
    tick_before_split = server.sim.world.tick_count

    server.set_mode("split")
    for _ in range(50):
        server.step_and_serialize()  # self.sim is frozen throughout this

    server.set_mode("warden" if server.mode != "warden" else "naive")
    positions_after_split = {r.robot_id: (r.x, r.y) for r in server.sim.world.robots}
    assert positions_after_split == positions_before_split
    assert server.sim.world.tick_count == tick_before_split


def test_set_robot_count_applies_to_both_boards_in_split_mode():
    server = SimulationServer()
    server.set_mode("split")

    server.set_robot_count(40)
    assert len(server.split_sims["naive"].world.robots) == 40
    assert len(server.split_sims["warden"].world.robots) == 40


def test_set_paused_stops_ticking_in_the_real_broadcast_loop():
    # Exercises the actual broadcast_loop coroutine (not a reimplementation of its
    # logic) so this fails if the real pause-handling code path breaks, not just a
    # test double of it.
    async def body():
        server = SimulationServer()
        loop_task = asyncio.ensure_future(server.broadcast_loop())
        try:
            await asyncio.sleep(0.35)  # a few ticks at TICK_INTERVAL_SECONDS=0.1
            server.set_paused(True)
            await asyncio.sleep(0.05)
            tick_at_pause = server._last_state["boards"]["naive"]["tick"]

            await asyncio.sleep(0.5)  # several tick intervals while paused
            tick_after_wait = server._last_state["boards"]["naive"]["tick"]
            assert tick_after_wait == tick_at_pause, "ticked while paused"
            assert server._last_state["paused"] is True

            server.set_paused(False)
            await asyncio.sleep(0.35)
            tick_after_resume = server._last_state["boards"]["naive"]["tick"]
            assert tick_after_resume > tick_at_pause, "never resumed"
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task

    asyncio.run(body())


def test_reset_restarts_from_tick_zero_with_a_different_layout():
    server = SimulationServer()
    for _ in range(30):
        server.step_and_serialize()
    positions_before = {r.robot_id: (r.x, r.y) for r in server.split_sims["naive"].world.robots}
    assert server.split_sims["naive"].world.tick_count == 30

    server.reset()

    assert server.split_sims["naive"].world.tick_count == 0
    assert server.split_sims["warden"].world.tick_count == 0
    positions_after = {r.robot_id: (r.x, r.y) for r in server.split_sims["naive"].world.robots}
    assert positions_after != positions_before  # freshly rolled seed, not a replay
    # totals reset along with the rebuilt coordinators
    assert server.split_totals["naive"] == {
        "instant_moves": 0,
        "confirmed_checks": 0,
        "conflicts_avoided": 0,
        "near_misses": 0,
    }


def test_step_seconds_is_measured_and_resets_to_zero():
    # Regression for the "server CPU usage" dashboard stat: step_seconds must be a real
    # measurement (nonzero after an actual sim.step()), not a placeholder, and must go
    # back to 0.0 right after reset() since nothing has been stepped yet at that point.
    server = SimulationServer()
    state = server.step_and_serialize()
    for board in state["boards"].values():
        assert board["stats"]["step_seconds"] > 0

    server.reset()
    assert server._last_state is not None
    for board in server._last_state["boards"].values():
        assert board["stats"]["step_seconds"] == 0.0


def test_reset_preserves_robot_count_and_grid_size():
    server = SimulationServer()
    server.set_grid_size(20)
    server.set_robot_count(45)

    server.reset()

    assert server.grid_size == 20
    assert len(server.split_sims["naive"].world.robots) == 45
    assert len(server.split_sims["warden"].world.robots) == 45
    assert server.split_sims["naive"].world.grid_size == 20


def test_reset_while_paused_is_visible_immediately():
    # Regression: reset() rebuilt the sim state correctly but left _last_state (what
    # broadcast_loop actually re-sends every tick while paused) untouched, so hitting
    # Reset while paused silently did nothing until Play was pressed — the cached
    # payload kept showing the pre-reset tick count and positions. reset() must refresh
    # _last_state itself, not rely on the next non-paused step to do it, and it must
    # not crash doing so (the freshly built coordinator has never ticked, so its log
    # is empty going into that first serialization).
    server = SimulationServer()
    for _ in range(25):
        server.step_and_serialize()
    server.set_paused(True)
    server._last_state = server.step_and_serialize()  # simulate broadcast_loop's last cached send
    tick_before_reset = server._last_state["boards"]["naive"]["tick"]
    assert tick_before_reset > 0

    server.reset()

    assert server._last_state is not None
    assert server._last_state["boards"]["naive"]["tick"] == 0
    assert server._last_state["boards"]["warden"]["tick"] == 0
    assert server._last_state["paused"] is True  # reset doesn't implicitly resume


def test_set_broadcast_lag_is_live_and_has_no_effect_in_naive_mode():
    server = SimulationServer()
    server.set_mode("warden")
    assert isinstance(server.sim.coordinator.filter_refresh_interval_ticks, int)
    default_interval = server.sim.coordinator.filter_refresh_interval_ticks

    server.set_broadcast_lag("adversarial")
    assert server.broadcast_lag_level == "adversarial"
    assert server.sim.coordinator.filter_refresh_interval_ticks == 1_000_000
    assert server.sim.coordinator.filter_refresh_interval_ticks != default_interval

    # switching to naive mode: no filter to apply it to, but the preference persists
    server.set_mode("naive")
    assert server.broadcast_lag_level == "adversarial"
    assert not hasattr(server.sim.coordinator, "filter_refresh_interval_ticks")

    # switching back to warden: the standing preference is reapplied to the fresh coordinator
    server.set_mode("warden")
    assert server.sim.coordinator.filter_refresh_interval_ticks == 1_000_000


def test_broadcast_lag_applies_to_warden_board_in_split_mode():
    server = SimulationServer()
    server.set_mode("split")
    server.set_broadcast_lag("degraded")

    assert server.split_sims["warden"].coordinator.filter_refresh_interval_ticks == 30
    assert not hasattr(server.split_sims["naive"].coordinator, "filter_refresh_interval_ticks")


def test_near_miss_rate_rises_with_broadcast_lag():
    # Cross-checks the live server against tasks/staleness-finding.md's offline finding:
    # near-miss frequency should rise as broadcast lag worsens, not stay flat or drop.
    def near_miss_rate(level: str) -> float:
        server = SimulationServer()
        server.set_mode("warden")
        server.set_robot_count(50)
        server.set_broadcast_lag(level)
        total_attempts = 0
        total_near_misses = 0
        for _ in range(1500):
            state = server.step_and_serialize()
            total_attempts += state["stats"]["instant_moves"] + state["stats"]["confirmed_checks"]
            total_near_misses += state["stats"]["near_misses"]
        return total_near_misses / total_attempts

    assert near_miss_rate("normal") < near_miss_rate("adversarial")


def test_server_end_to_end_over_real_websocket():
    async def body():
        server_state = SimulationServer()
        async with websockets.serve(server_state.handle_client, "localhost", 0) as ws_server:
            port = ws_server.sockets[0].getsockname()[1]
            loop_task = asyncio.ensure_future(server_state.broadcast_loop())
            try:
                async with websockets.connect(f"ws://localhost:{port}") as client:
                    raw = await asyncio.wait_for(client.recv(), timeout=2)
                    state = json.loads(raw)
                    # split is the server's real default now — the shipped frontend
                    # never sends set_mode, so this is what a fresh connection actually
                    # gets, not a special case to opt into.
                    assert state["type"] == "tick_split"
                    initial_robot_count = state["boards"]["naive"]["robot_count"]

                    await client.send(json.dumps({"action": "set_robot_count", "count": initial_robot_count + 5}))

                    state = {}
                    for _ in range(30):
                        raw = await asyncio.wait_for(client.recv(), timeout=2)
                        state = json.loads(raw)
                        if state["boards"]["naive"]["robot_count"] == initial_robot_count + 5:
                            break
                    assert state["boards"]["naive"]["robot_count"] == initial_robot_count + 5
                    assert state["boards"]["warden"]["robot_count"] == initial_robot_count + 5
            finally:
                loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await loop_task

    asyncio.run(body())
