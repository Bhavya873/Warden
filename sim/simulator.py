"""Drives a GridWorld's tick loop, optionally routing every move through a Coordinator
(naive mode, Phase 2). Task 5 adds Warden mode (Ribbon-filter-gated) alongside it —
`naive_mode` anticipates the same flag name `scenarios.py` uses in Phase 4.
"""

from core.coordinator import Coordinator
from core.grid_world import GridWorld


class Simulator:
    def __init__(
        self,
        grid_size: int = 30,
        robot_count: int = 10,
        seed: int = 0,
        naive_mode: bool = False,
    ):
        self.world = GridWorld(grid_size=grid_size, robot_count=robot_count, seed=seed)
        self.coordinator = Coordinator(robot_count=robot_count, seed=seed) if naive_mode else None

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()

    def step(self) -> None:
        if self.coordinator is not None:
            next_tick = self.world.tick_count + 1
            self.coordinator.tick(next_tick, self.world.occupied_cells())
        self.world.tick(policy=self.coordinator)
