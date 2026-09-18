"""Drives a GridWorld's tick loop, optionally routing every move through a Coordinator
(naive mode, Phase 2) or a WardenCoordinator (Warden mode, Phase 3). `naive_mode` and
`warden_mode` anticipate the flag names `scenarios.py` uses in Phase 4.
"""

from core.coordinator import Coordinator, WardenCoordinator
from core.grid_world import GridWorld


class Simulator:
    def __init__(
        self,
        grid_size: int = 30,
        robot_count: int = 10,
        seed: int = 0,
        naive_mode: bool = False,
        warden_mode: bool = False,
        ring_buffer_capacity: int | None = None,
    ):
        if naive_mode and warden_mode:
            raise ValueError("choose one of naive_mode or warden_mode, not both")

        self.world = GridWorld(grid_size=grid_size, robot_count=robot_count, seed=seed)
        if warden_mode:
            self.coordinator = WardenCoordinator(
                robot_count=robot_count, seed=seed, ring_buffer_capacity=ring_buffer_capacity
            )
        elif naive_mode:
            self.coordinator = Coordinator(
                robot_count=robot_count, seed=seed, ring_buffer_capacity=ring_buffer_capacity
            )
        else:
            self.coordinator = None

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()

    def step(self) -> None:
        if self.coordinator is not None:
            next_tick = self.world.tick_count + 1
            self.coordinator.tick(next_tick, self.world.occupied_cells())
        self.world.tick(policy=self.coordinator)
