import asyncio
import contextlib
import json

import websockets

from server.ws_server import MAX_ROBOT_COUNT, SimulationServer


def test_step_and_serialize_shape():
    server = SimulationServer()
    state = server.step_and_serialize()

    assert state["type"] == "tick"
    assert state["tick"] == 1
    assert state["grid_size"] == server.grid_size
    assert state["mode"] in ("naive", "warden")
    assert len(state["robots"]) == state["robot_count"]
    for robot in state["robots"]:
        assert set(robot) == {"id", "x", "y", "dx", "dy", "near_miss"}
    assert set(state["stats"]) == {
        "queue_depth",
        "instant_moves",
        "confirmed_checks",
        "conflicts_avoided",
        "near_misses",
        "near_miss_count_total",
    }
    assert set(state["totals"]) == {"instant_moves", "confirmed_checks", "conflicts_avoided", "near_misses"}
    assert state["broadcast_lag"] == "normal"


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
    server.step_and_serialize()

    server.set_grid_size(15)
    assert server.grid_size == 15
    assert server.sim.world.grid_size == 15
    assert server.sim.world.tick_count == 0  # fresh world, as documented


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
    for _ in range(15):
        server.step_and_serialize()
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


def test_set_broadcast_lag_is_live_and_has_no_effect_in_naive_mode():
    server = SimulationServer()  # starts in warden mode
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
                    assert state["type"] == "tick"
                    initial_robot_count = state["robot_count"]

                    await client.send(json.dumps({"action": "set_robot_count", "count": initial_robot_count + 5}))

                    state = {}
                    for _ in range(30):
                        raw = await asyncio.wait_for(client.recv(), timeout=2)
                        state = json.loads(raw)
                        if state["robot_count"] == initial_robot_count + 5:
                            break
                    assert state["robot_count"] == initial_robot_count + 5
            finally:
                loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await loop_task

    asyncio.run(body())
