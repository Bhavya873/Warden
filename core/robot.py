"""A robot agent: position, target, and (from Phase 3) its local Ribbon filter."""

from dataclasses import dataclass


@dataclass
class Robot:
    robot_id: int
    x: int
    y: int
    target_x: int
    target_y: int
    # consecutive ticks with no move — grid_world uses this to break deadlocks (see
    # GridWorld._preferred_step)
    consecutive_blocked_ticks: int = 0
