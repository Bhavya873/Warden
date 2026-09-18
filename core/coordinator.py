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
        ring_buffer_capacity: int | None = None,
    ):
        capacity = ring_buffer_capacity if ring_buffer_capacity is not None else max(1, robot_count * CAPACITY_MULTIPLIER)
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
        self._conflicts_avoided_this_tick = 0
        self.log: list[dict] = []

    def claimed_cells(self) -> list[Cell]:
        """The coordinator's broadcast: currently-claimed cells per the ring buffer, as
        of the last `tick()` sync. Warden mode's robots rebuild their local filter from
        this (build spec §6 Phase 3 step 2)."""
        return self._ring_buffer.claimed_cells()

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
        if claimed:
            self._conflicts_avoided_this_tick += 1  # the confirm-check found a real conflict
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
                "conflicts_avoided": self._conflicts_avoided_this_tick,
                "queue_depth": len(self._pending),
                "avg_delay": (sum(resolved_delays) / len(resolved_delays)) if resolved_delays else None,
            }
        )
        self._checks_this_tick = 0
        self._conflicts_avoided_this_tick = 0


DEFAULT_FILTER_REFRESH_INTERVAL_TICKS = 5
DEFAULT_FILTER_TARGET_FPR = 0.01


class WardenCoordinator:
    """Warden mode (Phase 3): each robot checks its local Ribbon filter first.
    "Definitely free" -> instant move, no coordinator call. "Maybe claimed" -> falls
    back to the exact same confirm-check round trip as naive mode, via a wrapped
    Coordinator instance — reusing Task 4's tested logic rather than duplicating it.

    # ponytail: one shared filter object stands in for every robot's own identical
    # copy, since every robot receives the same broadcast at the same refresh tick in
    # this simulation (no per-robot broadcast latency/loss modeled). Switch to
    # per-robot filter instances if that ever changes.
    """

    def __init__(
        self,
        robot_count: int,
        seed: int = 0,
        min_delay_ticks: int = 1,
        max_delay_ticks: int = 3,
        claim_ttl_ticks: int = 2,
        filter_refresh_interval_ticks: int = DEFAULT_FILTER_REFRESH_INTERVAL_TICKS,
        filter_target_fpr: float = DEFAULT_FILTER_TARGET_FPR,
        ring_buffer_capacity: int | None = None,
    ):
        self._coordinator = Coordinator(
            robot_count=robot_count,
            seed=seed,
            min_delay_ticks=min_delay_ticks,
            max_delay_ticks=max_delay_ticks,
            claim_ttl_ticks=claim_ttl_ticks,
            ring_buffer_capacity=ring_buffer_capacity,
        )
        self.filter_refresh_interval_ticks = filter_refresh_interval_ticks
        self.filter_target_fpr = filter_target_fpr

        self._filter = None
        self.instant_moves_this_tick = 0
        self.log: list[dict] = []

    @property
    def confirm_check_log(self) -> list[dict]:
        """The wrapped Coordinator's own per-tick log (tick, confirm_checks,
        queue_depth, avg_delay) — lets callers read Warden's queue-depth series with
        the same shape as naive mode's, for an apples-to-apples benchmark comparison."""
        return self._coordinator.log

    def can_move(self, robot, next_cell: Cell) -> bool:
        if not self._filter.contains(next_cell):
            self.instant_moves_this_tick += 1
            return True  # definitely free — no coordinator call
        return self._coordinator.can_move(robot, next_cell)  # maybe claimed — confirm

    def tick(self, current_tick: int, occupied_cells: dict[Cell, int]) -> None:
        self._coordinator.tick(current_tick, occupied_cells)

        due_for_refresh = self._filter is None or current_tick % self.filter_refresh_interval_ticks == 0
        if due_for_refresh:
            self._filter = warden_core.RibbonFilter(self._coordinator.claimed_cells(), self.filter_target_fpr)

        self.log.append(
            {
                "tick": current_tick,
                "instant_moves": self.instant_moves_this_tick,
                "confirmed_checks": self._coordinator.log[-1]["confirm_checks"],
                "conflicts_avoided": self._coordinator.log[-1]["conflicts_avoided"],
                "filter_refreshed": due_for_refresh,
            }
        )
        self.instant_moves_this_tick = 0
