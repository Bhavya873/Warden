"""WebSocket server streaming simulation state to the browser frontend (build spec §6
Phase 5). Drives the tick loop itself; browser clients are read-only observers plus a
small set of live controls (mode, robot count, grid size).

Message schema (server -> client), one per tick — this is the contract Tasks 9/10/11
extend, so keep additions backward-compatible (new keys, not renamed/removed ones):

    {"type": "tick", "tick": int, "grid_size": int, "mode": "naive" | "warden",
     "robot_count": int,
     "robots": [{"id": int, "x": int, "y": int, "dx": int, "dy": int}, ...],
     "stats": {"instant_moves": int, "confirmed_checks": int, "queue_depth": int,
               "near_miss_count_total": int}}
     (instant_moves is always 0 in naive mode — every move is a confirmed check there)

Control messages (client -> server), one JSON object per WS text frame:

    {"action": "set_mode", "mode": "naive" | "warden"}
    {"action": "set_robot_count", "count": int}
    {"action": "set_grid_size", "size": int}
"""

import asyncio
import json

import websockets

from core.coordinator import CAPACITY_MULTIPLIER, Coordinator, WardenCoordinator
from sim.simulator import Simulator

HOST = "localhost"
PORT = 8765
TICK_INTERVAL_SECONDS = 0.1

MIN_ROBOT_COUNT = 1
MAX_ROBOT_COUNT = 150
MIN_GRID_SIZE = 10
MAX_GRID_SIZE = 40

DEFAULT_GRID_SIZE = 30
DEFAULT_ROBOT_COUNT = 20
DEFAULT_SEED = 1

# Sized for the slider's max, not the current robot count, so add_robot()/remove_robot()
# never needs to resize the (fixed-capacity) ring buffer mid-run.
RING_BUFFER_CAPACITY = MAX_ROBOT_COUNT * CAPACITY_MULTIPLIER


class SimulationServer:
    def __init__(self) -> None:
        self.grid_size = DEFAULT_GRID_SIZE
        self.mode = "warden"
        self.clients: set = set()
        self.sim = self._new_simulator()

    def _new_simulator(self) -> Simulator:
        return Simulator(
            grid_size=self.grid_size,
            robot_count=DEFAULT_ROBOT_COUNT,
            seed=DEFAULT_SEED,
            naive_mode=(self.mode == "naive"),
            warden_mode=(self.mode == "warden"),
            ring_buffer_capacity=RING_BUFFER_CAPACITY,
        )

    def set_mode(self, mode: str) -> None:
        """Swaps only the coordinator — `self.sim.world` (robot positions, targets,
        tick_count) is untouched, so this never causes the visible jump/reset a full
        Simulator rebuild would (build spec §6 Phase 5 step 3)."""
        if mode not in ("naive", "warden") or mode == self.mode:
            return
        self.mode = mode
        robot_count = len(self.sim.world.robots)
        if mode == "naive":
            self.sim.coordinator = Coordinator(
                robot_count=robot_count, seed=DEFAULT_SEED, ring_buffer_capacity=RING_BUFFER_CAPACITY
            )
        else:
            self.sim.coordinator = WardenCoordinator(
                robot_count=robot_count, seed=DEFAULT_SEED, ring_buffer_capacity=RING_BUFFER_CAPACITY
            )

    def set_grid_size(self, size: int) -> None:
        """Re-initializes the sim — grid size can't change under existing robots without
        a reset (build spec §6 Phase 5 step 1)."""
        size = max(MIN_GRID_SIZE, min(MAX_GRID_SIZE, size))
        if size == self.grid_size:
            return
        self.grid_size = size
        self.sim = self._new_simulator()

    def set_robot_count(self, count: int) -> None:
        count = max(MIN_ROBOT_COUNT, min(MAX_ROBOT_COUNT, count))
        while len(self.sim.world.robots) < count:
            self.sim.world.add_robot()
        while len(self.sim.world.robots) > count:
            self.sim.world.remove_robot()

    def step_and_serialize(self) -> dict:
        before = self.sim.world.robot_positions()
        self.sim.step()
        world = self.sim.world

        robots = [
            {
                "id": robot.robot_id,
                "x": robot.x,
                "y": robot.y,
                "dx": robot.x - before.get(robot.robot_id, (robot.x, robot.y))[0],
                "dy": robot.y - before.get(robot.robot_id, (robot.x, robot.y))[1],
            }
            for robot in world.robots
        ]

        stats = {"instant_moves": 0, "confirmed_checks": 0, "queue_depth": 0}
        if self.sim.coordinator is not None:
            entry = self.sim.coordinator.log[-1]
            if self.mode == "naive":
                stats["confirmed_checks"] = entry["confirm_checks"]
                stats["queue_depth"] = entry["queue_depth"]
            else:
                stats["instant_moves"] = entry["instant_moves"]
                stats["confirmed_checks"] = entry["confirmed_checks"]
                stats["queue_depth"] = self.sim.coordinator.confirm_check_log[-1]["queue_depth"]
        stats["near_miss_count_total"] = world.near_miss_count

        return {
            "type": "tick",
            "tick": world.tick_count,
            "grid_size": self.grid_size,
            "mode": self.mode,
            "robot_count": len(world.robots),
            "robots": robots,
            "stats": stats,
        }

    def handle_control_message(self, raw_message: str) -> None:
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            return
        action = message.get("action")
        if action == "set_mode":
            self.set_mode(message.get("mode"))
        elif action == "set_robot_count":
            self.set_robot_count(int(message.get("count", 0)))
        elif action == "set_grid_size":
            self.set_grid_size(int(message.get("size", 0)))

    async def handle_client(self, websocket) -> None:
        self.clients.add(websocket)
        try:
            async for raw_message in websocket:
                self.handle_control_message(raw_message)
        finally:
            self.clients.discard(websocket)

    async def broadcast_loop(self) -> None:
        while True:
            state = self.step_and_serialize()
            if self.clients:
                payload = json.dumps(state)
                await asyncio.gather(*(client.send(payload) for client in self.clients), return_exceptions=True)
            await asyncio.sleep(TICK_INTERVAL_SECONDS)


async def run_server(host: str = HOST, port: int = PORT) -> None:
    server_state = SimulationServer()
    async with websockets.serve(server_state.handle_client, host, port):
        print(f"Warden WS server listening on ws://{host}:{port}")
        print("Open web/index.html directly in a browser to view the demo.")
        await server_state.broadcast_loop()


if __name__ == "__main__":
    asyncio.run(run_server())
