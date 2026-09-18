"""Drives a GridWorld's tick loop. Phase 2 extends this to wire in the coordinator and
per-tick logging; Phase 1 just drives ticks and enforces the collision guarantee.
"""

from core.grid_world import GridWorld


class Simulator:
    def __init__(self, grid_size: int = 30, robot_count: int = 10, seed: int = 0):
        self.world = GridWorld(grid_size=grid_size, robot_count=robot_count, seed=seed)

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.world.tick()
