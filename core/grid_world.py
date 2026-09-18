"""NxN grid, greedy robot movement, and the ground-truth collision guarantee that every
later phase (naive confirm-check, Ribbon filter, staleness) is measured against.

No coordination happens here — robots that want the same cell in the same tick are
resolved by processing order alone (ascending robot_id), which is what makes this phase's
zero-collision result a structural guarantee rather than a probabilistic one.
"""

import random

from core.robot import Robot

Cell = tuple[int, int]


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
        self._spawn_robots()

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

    def _greedy_step(self, robot: Robot) -> Cell:
        """One cell closer to target, reducing whichever of dx/dy is larger first."""
        dx = robot.target_x - robot.x
        dy = robot.target_y - robot.y
        if dx == 0 and dy == 0:
            return (robot.x, robot.y)
        if abs(dx) >= abs(dy):
            return (robot.x + (1 if dx > 0 else -1), robot.y)
        return (robot.x, robot.y + (1 if dy > 0 else -1))

    def tick(self) -> None:
        self.tick_count += 1
        for robot in self.robots:  # fixed order: ascending robot_id (spawn order)
            next_cell = self._greedy_step(robot)
            if next_cell == (robot.x, robot.y) or next_cell in self._occupied:
                continue  # already there, or blocked this tick — try again next tick

            del self._occupied[(robot.x, robot.y)]
            robot.x, robot.y = next_cell
            self._occupied[next_cell] = robot.robot_id

            if (robot.x, robot.y) == (robot.target_x, robot.target_y):
                robot.target_x, robot.target_y = self._random_target(exclude=(robot.x, robot.y))

        self._check_no_collisions()

    def _check_no_collisions(self) -> None:
        positions = [(r.x, r.y) for r in self.robots]
        if len(set(positions)) != len(positions):
            raise CollisionError(f"tick {self.tick_count}: duplicate robot positions {positions}")

    def robot_positions(self) -> dict[int, Cell]:
        return {r.robot_id: (r.x, r.y) for r in self.robots}
