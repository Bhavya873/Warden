"""A robot agent: position, target, and (from Phase 3) its local Ribbon filter."""

from dataclasses import dataclass


@dataclass
class Robot:
    robot_id: int
    x: int
    y: int
    target_x: int
    target_y: int
