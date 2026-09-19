"""WebSocket server streaming simulation state to the browser frontend (build spec §6
Phase 5). Drives the tick loop itself; browser clients are read-only observers plus a
small set of live controls (robot count, grid size, broadcast lag).

The server starts directly in "split" mode and the shipped frontend never sends
`set_mode` — there's exactly one view (Naive vs. Warden, side by side), not a choice
between three. Single-board mode ("naive" | "warden") still exists as a server
capability — `set_mode` still works, still covered by tests — for any other client
that wants just one board; the shipped web/ frontend just isn't that client anymore.

Message schema (server -> client), one per tick — this is the contract Task 11 extends,
so keep additions backward-compatible (new keys, not renamed/removed ones):

Single-board mode ("naive" | "warden") — not used by the shipped frontend, still a
valid server capability for any other client:
    {"type": "tick", "grid_size": int, "mode": "naive" | "warden",
     "tick": int, "robot_count": int, "broadcast_lag": "normal"|"degraded"|"adversarial",
     "paused": bool,
     "robots": [{"id": int, "x": int, "y": int, "dx": int, "dy": int, "near_miss": bool,
                 "outcome": "moved" | "waiting" | "conflict" | "idle"}, ...],
     "stats": {"instant_moves": int, "confirmed_checks": int, "conflicts_avoided": int,
               "near_misses": int, "queue_depth": int, "near_miss_count_total": int,
               "server_seconds": float},
     "totals": {"instant_moves": int, "confirmed_checks": int, "conflicts_avoided": int,
                "near_misses": int}}
     (instant_moves is always 0 in naive mode — every move is a confirmed check there,
     and near_miss/near_misses are always 0/false there too, since staleness is a
     Warden-filter concept — see README.md's "The staleness finding" section. "totals" are cumulative
     since the current mode was selected — a mode switch resets them, since it's a
     fresh coordinator; a robot-count change does not. `outcome` is the color-coding
     from the original design doc's demo spec — green="moved" (this tick, either
     instant or after a cleared confirm-check), yellow="waiting" (an in-flight confirm-
     check), red="conflict" (a confirm-check just resolved "claimed" for this robot),
     and "idle" (default/neutral) for a robot sitting at its current target. `outcome`
     is a snapshot of this one tick, not an animation — it doesn't try to represent a
     multi-tick "ping traveling" state beyond "currently waiting". `server_seconds` is
     real wall-clock time (`time.perf_counter()`, measured inside core/coordinator.py's
     own tick()/can_move()) spent on genuine server-side work only — 0.0 right after a
     reset(), before anything's been ticked. Deliberately excludes Warden's local
     Ribbon-filter checks and periodic rebuild, which are robot-side work in the real
     architecture (each robot holds its own filter and checks it locally before ever
     contacting the server) — timing the whole per-tick step instead would wrongly
     attribute that robot-side cost to the server.)

Split mode — two independent worlds, same seed, run in lockstep, each mirroring the
single-mode "board" shape above (tick/robot_count/robots/stats/totals) under its label:
    {"type": "tick_split", "grid_size": int, "mode": "split", "paused": bool,
     "boards": {"naive": {<board>}, "warden": {<board>}}}

Entering split mode always starts a *fresh* pair of worlds (same seed on both sides) —
it's a deliberate side-by-side comparison, not a continuation of whatever the single-mode
world was doing. Leaving split mode resumes the single-mode world exactly where it was
frozen while split was active (build spec §6 Phase 5 step 4).

Control messages (client -> server), one JSON object per WS text frame:

    {"action": "set_mode", "mode": "naive" | "warden" | "split"}
    {"action": "set_robot_count", "count": int}
    {"action": "set_grid_size", "size": int}
    {"action": "set_broadcast_lag", "level": "normal" | "degraded" | "adversarial"}
    {"action": "set_paused", "paused": bool}
    {"action": "reset"}

set_paused stops/resumes ticking without touching sim state — while paused, the server
keeps re-broadcasting the same last state (so a client that connects or reconnects while
paused still gets a real payload instead of nothing) rather than freezing the board on a
half-drawn frame. `state["paused"]` reflects the current value on every broadcast.

reset rebuilds the current board(s) from tick 0 with fresh spawn positions, keeping the
current grid size / robot count / broadcast-lag preference, but with a newly rolled seed
— repeated resets show a different layout each time rather than replaying the same one.

set_broadcast_lag controls the Warden filter's refresh interval — it's a standing
preference, applied to whichever Warden coordinator(s) are currently active and to
any created afterward (mode switch, split entry, grid resize), not just a one-shot
action. It has no effect on naive mode (no filter to go stale). "adversarial"
(1,000,000 ticks — effectively never refreshes) is the exact setting README.md's
staleness finding tested: ~33% near-miss rate, zero collisions, ever, by
construction — not a coincidence of this demo's random seeds.
"""

import asyncio
import json
import os
import random

import websockets

from core.coordinator import CAPACITY_MULTIPLIER, Coordinator, WardenCoordinator
from sim.simulator import Simulator

HOST = os.environ.get("WARDEN_HOST", "localhost")
PORT = int(os.environ.get("PORT", 8765))
TICK_INTERVAL_SECONDS = 0.1

MIN_ROBOT_COUNT = 1
MAX_ROBOT_COUNT = 1000
MIN_GRID_SIZE = 10
MAX_GRID_SIZE = 150

DEFAULT_GRID_SIZE = 30
DEFAULT_ROBOT_COUNT = 20
DEFAULT_SEED = 1

# Sized for the slider's max, not the current robot count, so add_robot()/remove_robot()
# never needs to resize the (fixed-capacity) ring buffer mid-run.
RING_BUFFER_CAPACITY = MAX_ROBOT_COUNT * CAPACITY_MULTIPLIER

# Filter refresh interval (ticks) per broadcast-lag preset. "normal" matches
# WardenCoordinator's own default. "adversarial" is the exact value README.md's
# staleness finding used — see that section for the measured near-miss rate this
# reproduces.
BROADCAST_LAG_PRESETS = {"normal": 5, "degraded": 30, "adversarial": 1_000_000}
DEFAULT_BROADCAST_LAG = "normal"


def _zero_totals() -> dict:
    return {"instant_moves": 0, "confirmed_checks": 0, "conflicts_avoided": 0, "near_misses": 0}


class SimulationServer:
    def __init__(self) -> None:
        self.grid_size = DEFAULT_GRID_SIZE
        self.mode = "warden"  # "naive" | "warden" | "split" — see note below on the default
        self.broadcast_lag_level = DEFAULT_BROADCAST_LAG
        self.clients: set = set()
        self.paused = False
        self._last_state: dict | None = None
        self._seed = DEFAULT_SEED  # reset() rerolls this for a fresh layout each time

        self.sim = self._new_single_simulator()
        self.totals = _zero_totals()

        self.split_sims: dict[str, Simulator] | None = None
        self.split_totals: dict[str, dict] | None = None

        # The shipped frontend only ever shows the Naive-vs-Warden comparison now (no
        # mode selector) — start there directly instead of requiring a set_mode("split")
        # round trip after connecting. set_mode() can still switch to a single board
        # (still exercised by tests, available to any other client); self.sim built
        # above is what a switch back to "naive"/"warden" resumes.
        self.mode = "split"
        self.split_sims = self._new_split_simulators(DEFAULT_ROBOT_COUNT)
        self.split_totals = {"naive": _zero_totals(), "warden": _zero_totals()}

    def _apply_broadcast_lag(self, sim: Simulator) -> None:
        """No-op for naive mode's Coordinator — the broadcast-lag control only means
        anything where there's a filter to go stale."""
        if isinstance(sim.coordinator, WardenCoordinator):
            sim.coordinator.filter_refresh_interval_ticks = BROADCAST_LAG_PRESETS[self.broadcast_lag_level]

    def _new_single_simulator(self) -> Simulator:
        sim = Simulator(
            grid_size=self.grid_size,
            robot_count=DEFAULT_ROBOT_COUNT,
            seed=self._seed,
            naive_mode=(self.mode == "naive"),
            warden_mode=(self.mode != "naive"),  # covers "warden" and the initial "split"-free default
            ring_buffer_capacity=RING_BUFFER_CAPACITY,
        )
        self._apply_broadcast_lag(sim)
        return sim

    def _new_split_simulators(self, robot_count: int) -> dict[str, Simulator]:
        """Two worlds, identical seed — visually identical until coordination-driven
        divergence (build spec §6 Phase 5 step 4)."""
        naive_sim = Simulator(
            grid_size=self.grid_size,
            robot_count=robot_count,
            seed=self._seed,
            naive_mode=True,
            ring_buffer_capacity=RING_BUFFER_CAPACITY,
        )
        warden_sim = Simulator(
            grid_size=self.grid_size,
            robot_count=robot_count,
            seed=self._seed,
            warden_mode=True,
            ring_buffer_capacity=RING_BUFFER_CAPACITY,
        )
        self._apply_broadcast_lag(warden_sim)
        return {"naive": naive_sim, "warden": warden_sim}

    def set_mode(self, mode: str) -> None:
        """Swapping between "naive" and "warden" only swaps `self.sim`'s coordinator —
        its world (positions, targets, tick_count) is untouched, so it never causes the
        visible jump/reset a full rebuild would (build spec §6 Phase 5 step 3). Entering
        or leaving "split" is a different kind of transition — see module docstring."""
        if mode not in ("naive", "warden", "split") or mode == self.mode:
            return
        self.mode = mode

        if mode == "split":
            robot_count = len(self.sim.world.robots)
            self.split_sims = self._new_split_simulators(robot_count)
            self.split_totals = {"naive": _zero_totals(), "warden": _zero_totals()}
            return

        self.split_sims = None
        self.split_totals = None
        self.totals = _zero_totals()
        robot_count = len(self.sim.world.robots)
        if mode == "naive":
            self.sim.coordinator = Coordinator(
                robot_count=robot_count, seed=self._seed, ring_buffer_capacity=RING_BUFFER_CAPACITY
            )
        else:
            self.sim.coordinator = WardenCoordinator(
                robot_count=robot_count, seed=self._seed, ring_buffer_capacity=RING_BUFFER_CAPACITY
            )
            self._apply_broadcast_lag(self.sim)
        self._refresh_last_state()

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)

    def _refresh_last_state(self) -> None:
        """Rebuilds `_last_state` from current sim state right now, without stepping —
        for any control message that changes visible state (grid size, robot count,
        mode, reset) so a paused client sees the change immediately instead of it
        silently applying server-side but staying invisible until Play is pressed
        (broadcast_loop only regenerates `_last_state` on non-paused ticks)."""
        if self.mode == "split":
            befores = {label: sim.world.robot_positions() for label, sim in self.split_sims.items()}
            self._last_state = self._snapshot_split(befores)
        else:
            self._last_state = self._snapshot_single(self.sim.world.robot_positions())

    def reset(self) -> None:
        """Rebuilds the current board(s) from tick 0 with a freshly rolled seed —
        same grid size / robot count / broadcast-lag preference, different layout each
        time rather than replaying the same one."""
        self._seed = random.randint(0, 2**31 - 1)
        if self.mode == "split":
            robot_count = len(self.split_sims["naive"].world.robots)
            self.split_sims = self._new_split_simulators(robot_count)
            self.split_totals = {"naive": _zero_totals(), "warden": _zero_totals()}
        else:
            self.sim = self._new_single_simulator()
            self.totals = _zero_totals()
        self._refresh_last_state()

    def set_broadcast_lag(self, level: str) -> None:
        """Live-adjusts the Warden filter's refresh interval on whichever coordinator(s)
        are currently active, and becomes the standing preference for any built
        afterward. `filter_refresh_interval_ticks` is a plain mutable attribute read
        fresh every tick, so this takes effect on the very next tick — no rebuild."""
        if level not in BROADCAST_LAG_PRESETS or level == self.broadcast_lag_level:
            return
        self.broadcast_lag_level = level
        if self.mode == "split":
            self._apply_broadcast_lag(self.split_sims["warden"])
        else:
            self._apply_broadcast_lag(self.sim)
        self._refresh_last_state()

    def set_grid_size(self, size: int) -> None:
        """Re-initializes the sim(s) — grid size can't change under existing robots
        without a reset (build spec §6 Phase 5 step 1)."""
        size = max(MIN_GRID_SIZE, min(MAX_GRID_SIZE, size))
        if size == self.grid_size:
            return
        self.grid_size = size
        if self.mode == "split":
            robot_count = len(self.split_sims["naive"].world.robots)
            robot_count = min(robot_count, size * size)  # can't spawn more robots than cells
            self.split_sims = self._new_split_simulators(robot_count)
            self.split_totals = {"naive": _zero_totals(), "warden": _zero_totals()}
        else:
            self.sim = self._new_single_simulator()
            self.totals = _zero_totals()
        self._refresh_last_state()

    def set_robot_count(self, count: int) -> None:
        count = max(MIN_ROBOT_COUNT, min(MAX_ROBOT_COUNT, count))
        worlds = [sim.world for sim in self.split_sims.values()] if self.mode == "split" else [self.sim.world]
        for world in worlds:
            while len(world.robots) < count:
                if world.add_robot() is None:
                    break  # grid is full — fewer robots than requested, not a hang
            while len(world.robots) > count:
                world.remove_robot()
        self._refresh_last_state()

    def _serialize_board(self, sim: Simulator, board_mode: str, before: dict, totals: dict) -> dict:
        world = sim.world
        near_miss_ids = set(world.near_miss_robot_ids)
        claimed_ids = set(sim.coordinator.claimed_robot_ids_this_tick) if sim.coordinator is not None else set()
        outstanding_ids = set(sim.coordinator.outstanding_robot_ids) if sim.coordinator is not None else set()

        def _outcome(robot_id: int, moved: bool) -> str:
            # Priority matters: a robot whose request just resolved "claimed" this tick
            # is "conflict" even though it also isn't moving; a robot that moved is
            # "moved" even if it still shows up in `outstanding` from a stale read.
            if robot_id in claimed_ids:
                return "conflict"  # red
            if moved:
                return "moved"  # green
            if robot_id in outstanding_ids:
                return "waiting"  # yellow
            return "idle"

        robots = [
            {
                "id": robot.robot_id,
                "x": robot.x,
                "y": robot.y,
                "dx": robot.x - before.get(robot.robot_id, (robot.x, robot.y))[0],
                "dy": robot.y - before.get(robot.robot_id, (robot.x, robot.y))[1],
                "near_miss": robot.robot_id in near_miss_ids,
                "outcome": _outcome(
                    robot.robot_id,
                    moved=(robot.x, robot.y) != before.get(robot.robot_id, (robot.x, robot.y)),
                ),
            }
            for robot in world.robots
        ]

        stats = {"instant_moves": 0, "confirmed_checks": 0, "conflicts_avoided": 0, "queue_depth": 0, "server_seconds": 0.0}
        # A freshly built coordinator (reset(), or a snapshot taken before any tick())
        # has an empty log — nothing has happened yet, so the zeros above are correct.
        if sim.coordinator is not None and sim.coordinator.log:
            entry = sim.coordinator.log[-1]
            if board_mode == "naive":
                stats["confirmed_checks"] = entry["confirm_checks"]
                stats["conflicts_avoided"] = entry["conflicts_avoided"]
                stats["queue_depth"] = entry["queue_depth"]
            else:
                stats["instant_moves"] = entry["instant_moves"]
                stats["confirmed_checks"] = entry["confirmed_checks"]
                stats["conflicts_avoided"] = entry["conflicts_avoided"]
                stats["queue_depth"] = sim.coordinator.confirm_check_log[-1]["queue_depth"]
            # Real wall-clock time (time.perf_counter(), measured inside core/coordinator.py
            # itself) spent in the coordinator's own tick()/can_move() logic — genuine
            # server-side cost only. Deliberately excludes Warden's local Ribbon-filter
            # checks and periodic rebuild, which are robot-side work in the real
            # architecture (each robot holds its own filter) — timing the whole
            # Simulator.step() instead would wrongly charge the server for that.
            stats["server_seconds"] = entry["server_seconds"]
        stats["near_misses"] = len(near_miss_ids)
        stats["near_miss_count_total"] = world.near_miss_count

        totals["instant_moves"] += stats["instant_moves"]
        totals["confirmed_checks"] += stats["confirmed_checks"]
        totals["conflicts_avoided"] += stats["conflicts_avoided"]
        totals["near_misses"] += stats["near_misses"]

        return {
            "tick": world.tick_count,
            "robot_count": len(world.robots),
            "robots": robots,
            "stats": stats,
            "totals": dict(totals),
        }

    def step_and_serialize(self) -> dict:
        if self.mode == "split":
            return self._step_and_serialize_split()
        return self._step_and_serialize_single()

    def _step_and_serialize_single(self) -> dict:
        before = self.sim.world.robot_positions()
        self.sim.step()
        return self._snapshot_single(before)

    def _snapshot_single(self, before: dict) -> dict:
        """Serializes `self.sim`'s current state against `before` — the caller decides
        whether that's a pre-step snapshot (normal ticking) or the same-as-current
        positions (a `reset()` refresh, where nothing has moved yet so dx/dy is 0)."""
        board = self._serialize_board(self.sim, self.mode, before, self.totals)
        return {
            "type": "tick",
            "grid_size": self.grid_size,
            "mode": self.mode,
            "broadcast_lag": self.broadcast_lag_level,
            "paused": self.paused,
            **board,
        }

    def _step_and_serialize_split(self) -> dict:
        befores = {label: sim.world.robot_positions() for label, sim in self.split_sims.items()}
        for sim in self.split_sims.values():
            sim.step()
        return self._snapshot_split(befores)

    def _snapshot_split(self, befores: dict[str, dict]) -> dict:
        boards = {
            label: self._serialize_board(sim, label, befores[label], self.split_totals[label])
            for label, sim in self.split_sims.items()
        }
        return {
            "type": "tick_split",
            "grid_size": self.grid_size,
            "mode": "split",
            "broadcast_lag": self.broadcast_lag_level,
            "paused": self.paused,
            "boards": boards,
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
        elif action == "set_broadcast_lag":
            self.set_broadcast_lag(message.get("level"))
        elif action == "set_paused":
            self.set_paused(bool(message.get("paused")))
        elif action == "reset":
            self.reset()

    async def handle_client(self, websocket) -> None:
        self.clients.add(websocket)
        try:
            async for raw_message in websocket:
                self.handle_control_message(raw_message)
        finally:
            self.clients.discard(websocket)

    async def broadcast_loop(self) -> None:
        while True:
            if not self.paused:
                self._last_state = self.step_and_serialize()
            if self._last_state is not None:
                # Keep this fresh every tick, even while paused and not re-stepping —
                # otherwise a client sees a cached "paused: false" for one tick after
                # actually pausing, since step_and_serialize() is what normally sets it.
                self._last_state["paused"] = self.paused
            if self.clients and self._last_state is not None:
                payload = json.dumps(self._last_state)
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
