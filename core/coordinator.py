"""Holds the ring buffer and gates every move through a simulated confirm-check round
trip. This is the naive baseline (Phase 2): every move waits on a network round trip,
with no local filter short-circuiting it — Task 5 adds that.

Acts as a GridWorld move-policy (`can_move(robot, next_cell) -> bool`): a robot with no
in-flight request for `next_cell` gets one queued and is told to wait; once the delayed
response comes back, it's told to move or wait again based on whether the cell was
claimed as of the last ring-buffer sync.
"""

import random

import warden_core

Cell = tuple[int, int]

# Ring buffer sized to comfortably exceed max expected simultaneous claims (build spec
# §6 Phase 2 step 1).
CAPACITY_MULTIPLIER = 4


class Coordinator:
    def __init__(
        self,
        robot_count: int,
        seed: int = 0,
        min_delay_ticks: int = 1,
        max_delay_ticks: int = 3,
        claim_ttl_ticks: int = 2,
    ):
        capacity = max(1, robot_count * CAPACITY_MULTIPLIER)
        self._ring_buffer = warden_core.RingBuffer(capacity)
        self._rng = random.Random(seed)
        self.min_delay_ticks = min_delay_ticks
        self.max_delay_ticks = max_delay_ticks
        self.claim_ttl_ticks = claim_ttl_ticks

        self._current_tick = 0
        self._next_request_id = 0
        # request_id -> (arrival_tick, cell, requested_tick)
        self._pending: dict[int, tuple[int, Cell, int]] = {}
        self._responses: dict[int, bool] = {}
        # robot_id -> (request_id, cell) — a robot's single outstanding request, if any
        self._outstanding_by_robot: dict[int, tuple[int, Cell]] = {}

        self._checks_this_tick = 0
        self.log: list[dict] = []

    def can_move(self, robot, next_cell: Cell) -> bool:
        outstanding = self._outstanding_by_robot.get(robot.robot_id)

        if outstanding is None or outstanding[1] != next_cell:
            self._request_confirm(robot.robot_id, next_cell)
            return False

        request_id, _ = outstanding
        if request_id not in self._responses:
            return False  # still in flight

        claimed = self._responses.pop(request_id)
        del self._outstanding_by_robot[robot.robot_id]
        return not claimed

    def _request_confirm(self, robot_id: int, cell: Cell) -> None:
        self._checks_this_tick += 1
        delay = self._rng.randint(self.min_delay_ticks, self.max_delay_ticks)
        request_id = self._next_request_id
        self._next_request_id += 1
        self._pending[request_id] = (self._current_tick + delay, cell, self._current_tick)
        self._outstanding_by_robot[robot_id] = (request_id, cell)

    def _sync_occupancy(self, occupied_cells: dict[Cell, int]) -> None:
        """Mirrors ground-truth occupancy into the ring buffer, standing in for robots'
        own periodic broadcasts of the cells they currently hold."""
        for cell, robot_id in occupied_cells.items():
            self._ring_buffer.claim(cell, robot_id, self.claim_ttl_ticks)

    def tick(self, current_tick: int, occupied_cells: dict[Cell, int]) -> None:
        """Advances the coordinator's clock, resolves any requests due this tick, and
        logs the tick's confirm-check volume, queue depth, and average response delay —
        the three numbers Phase 4's benchmark harness reads (build spec §6 Phase 2
        step 5)."""
        self._current_tick = current_tick
        self._ring_buffer.tick(current_tick)
        self._sync_occupancy(occupied_cells)

        resolved_delays = []
        for request_id, (arrival_tick, cell, requested_tick) in list(self._pending.items()):
            if arrival_tick <= current_tick:
                self._responses[request_id] = self._ring_buffer.is_claimed(cell)
                resolved_delays.append(arrival_tick - requested_tick)
                del self._pending[request_id]

        self.log.append(
            {
                "tick": current_tick,
                "confirm_checks": self._checks_this_tick,
                "queue_depth": len(self._pending),
                "avg_delay": (sum(resolved_delays) / len(resolved_delays)) if resolved_delays else None,
            }
        )
        self._checks_this_tick = 0
