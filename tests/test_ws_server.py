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
        assert set(robot) == {"id", "x", "y", "dx", "dy"}
    assert set(state["stats"]) == {
        "queue_depth",
        "instant_moves",
        "confirmed_checks",
        "near_miss_count_total",
    }


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
