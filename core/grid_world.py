"""NxN grid, greedy robot movement, and the ground-truth collision guarantee that every
later phase (naive confirm-check, Ribbon filter, staleness) is measured against.

No coordination happens here — robots that want the same cell in the same tick are
resolved by processing order alone (ascending robot_id), which is what makes this phase's
zero-collision result a structural guarantee rather than a probabilistic one.
"""

import random

from core.robot import Robot

Cell = tuple[int, int]

# Escalating anti-deadlock thresholds. Set comfortably above the naive/Warden
# coordinator's max confirm-check delay (3 ticks) so ordinary network wait never
# triggers escalation — only genuine sustained contention does. Without this, greedy
# single-axis movement settles into a permanent, whole-fleet circular-wait deadlock
# (observed: 10 robots on a 30x30 grid, fully frozen by tick ~700, forever; the axis
# flip alone delays but doesn't prevent it — gridlock still creeps in by tick ~2500).
# This isn't a coordination-layer problem — it reproduces with no policy at all — so
# the fix lives here in ground-truth movement, not in Naive/Warden.
AXIS_FLIP_THRESHOLD_TICKS = 6
RANDOM_ESCAPE_THRESHOLD_TICKS = 15


class CollisionError(RuntimeError):
    """Two robots ended up occupying the same cell. Must never happen — see build spec §7."""


class GridWorld:
    def __init__(self, grid_size: int = 30, robot_count: int = 10, seed: int = 0):
        self.grid_size = grid_size
        self.robot_count = robot_count
        self.tick_count = 0

        self._rng = random.Random(seed)
        self._occupied: dict[Cell, int] = {}
        self.robots: list[Robot] = []
        # A near-miss: `policy` approved a move (instant or confirmed) but the live
        # occupancy check below rejected it anyway, because ground truth had already
        # moved on by the time the move was attempted. Defined and measured in build
        # spec §6 Phase 3 step 6's adversarial staleness test — see tests/test_staleness.py
        # and tasks/staleness-finding.md.
        self.near_miss_count = 0
        self._spawn_robots()
        self._next_robot_id = self.robot_count

    def _random_free_cell(self) -> Cell:
        while True:
            cell = (self._rng.randrange(self.grid_size), self._rng.randrange(self.grid_size))
            if cell not in self._occupied:
                return cell

    def _random_target(self, exclude: Cell) -> Cell:
        while True:
            cell = (self._rng.randrange(self.grid_size), self._rng.randrange(self.grid_size))
            if cell != exclude:
                return cell

    def _spawn_robots(self) -> None:
        for robot_id in range(self.robot_count):
            x, y = self._random_free_cell()
            self._occupied[(x, y)] = robot_id
            target_x, target_y = self._random_target(exclude=(x, y))
            self.robots.append(Robot(robot_id, x, y, target_x, target_y))

    def _preferred_step(self, robot: Robot) -> Cell:
        """One cell closer to target (larger of dx/dy first); once blocked
        AXIS_FLIP_THRESHOLD_TICKS ticks in a row, the *other* axis instead; once blocked
        RANDOM_ESCAPE_THRESHOLD_TICKS ticks in a row, a random adjacent cell regardless
        of target direction — escalating anti-deadlock fallbacks."""
        dx = robot.target_x - robot.x
        dy = robot.target_y - robot.y
        if dx == 0 and dy == 0:
            return (robot.x, robot.y)

        if robot.consecutive_blocked_ticks >= RANDOM_ESCAPE_THRESHOLD_TICKS:
            return self._random_escape_step(robot)

        prefer_x = abs(dx) >= abs(dy)
        if robot.consecutive_blocked_ticks >= AXIS_FLIP_THRESHOLD_TICKS:
            prefer_x = not prefer_x

        if prefer_x and dx != 0:
            return (robot.x + (1 if dx > 0 else -1), robot.y)
        if not prefer_x and dy != 0:
            return (robot.x, robot.y + (1 if dy > 0 else -1))
        # preferred axis has no delta to close — fall back to whichever axis does
        if dx != 0:
            return (robot.x + (1 if dx > 0 else -1), robot.y)
        return (robot.x, robot.y + (1 if dy > 0 else -1))

    def _random_escape_step(self, robot: Robot) -> Cell:
        candidates = [
            (robot.x + dx, robot.y + dy)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if 0 <= robot.x + dx < self.grid_size and 0 <= robot.y + dy < self.grid_size
        ]
        return self._rng.choice(candidates)

    def tick(self, policy=None) -> None:
        """`policy`, if given, gates each move attempt via `policy.can_move(robot,
        next_cell) -> bool` (Phase 2's coordinator, Phase 3's Ribbon filter — both use
        the same hook so Naive and Warden modes run identical movement logic on top of
        it). Regardless of what the policy says, the live occupancy check below is the
        final authority — a policy's answer can be stale by the time a move is attempted.
        """
        self.tick_count += 1
        for robot in self.robots:  # fixed order: ascending robot_id (spawn order)
            next_cell = self._preferred_step(robot)
            if next_cell == (robot.x, robot.y):
                robot.consecutive_blocked_ticks = 0
                continue
            if policy is not None and not policy.can_move(robot, next_cell):
                robot.consecutive_blocked_ticks += 1
                continue  # waiting on the policy (e.g. an in-flight confirm-check)
            if next_cell in self._occupied:
                robot.consecutive_blocked_ticks += 1
                if policy is not None:
                    self.near_miss_count += 1  # policy approved this move; ground truth didn't
                continue  # blocked this tick — try again next tick

            del self._occupied[(robot.x, robot.y)]
            robot.x, robot.y = next_cell
            self._occupied[next_cell] = robot.robot_id
            robot.consecutive_blocked_ticks = 0

            if (robot.x, robot.y) == (robot.target_x, robot.target_y):
                robot.target_x, robot.target_y = self._random_target(exclude=(robot.x, robot.y))

        self._check_no_collisions()

    def _check_no_collisions(self) -> None:
        positions = [(r.x, r.y) for r in self.robots]
        if len(set(positions)) != len(positions):
            raise CollisionError(f"tick {self.tick_count}: duplicate robot positions {positions}")

    def robot_positions(self) -> dict[int, Cell]:
        return {r.robot_id: (r.x, r.y) for r in self.robots}

    def occupied_cells(self) -> dict[Cell, int]:
        return dict(self._occupied)

    def add_robot(self) -> Robot:
        """Spawns one robot at a random free cell, without touching any existing robot's
        state — for the live robot-count slider (build spec §6 Phase 5 step 2)."""
        x, y = self._random_free_cell()
        robot_id = self._next_robot_id
        self._next_robot_id += 1
        self._occupied[(x, y)] = robot_id
        target_x, target_y = self._random_target(exclude=(x, y))
        robot = Robot(robot_id, x, y, target_x, target_y)
        self.robots.append(robot)
        self.robot_count += 1
        return robot

    def remove_robot(self) -> Robot | None:
        """Removes the most recently added robot. Returns None if there are none left."""
        if not self.robots:
            return None
        robot = self.robots.pop()
        del self._occupied[(robot.x, robot.y)]
        self.robot_count -= 1
        return robot
