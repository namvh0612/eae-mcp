"""Situation-awareness style guide used by the HMI generators (see knowledge/hmi-design.md).

Static graphics are gray only. Color appears at runtime, set by generated code, and only for abnormal
conditions: alarm priorities (color + shape + number) and values outside their alarm limits. A site style
guide can replace these values; keep the grays neutral and the alarm colors exclusive.
"""

from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]

CANVAS = (230, 230, 230)  # EAE CanvasBackColor of the DefaultLight theme
PANEL = (214, 214, 214)  # symbol card
BORDER = (150, 150, 150)
ENTRY = (255, 255, 255)  # operator entry fields (editable)
TEXT = (32, 32, 32)  # titles and values
TEXT_2 = (80, 80, 80)  # labels, units
TRACK = (242, 242, 242)  # analog indicator span
NORMAL_BAND = (186, 186, 186)  # normal operating range on the span
LIMIT = (90, 90, 90)  # alarm limit ticks
POINTER = (40, 40, 40)  # moving pointer in the normal state
INDICATOR_IDLE = PANEL  # alarm indicator when no alarm (invisible on the card)

FONT = "Microsoft Sans Serif"
SIZE_TITLE = 12
SIZE_TEXT = 10


@dataclass(frozen=True)
class Priority:
    level: int
    name: str
    color: RGB
    text_color: RGB
    shape: str  # triangle | diamond | square | circle


# ISA-18.2 style: few priorities, each with its own color, shape and number.
PRIORITIES = {
    1: Priority(1, "critical", (214, 0, 0), (255, 255, 255), "triangle"),
    2: Priority(2, "high", (255, 140, 0), (0, 0, 0), "diamond"),
    3: Priority(3, "medium", (250, 220, 0), (0, 0, 0), "square"),
    4: Priority(4, "low", (0, 170, 220), (0, 0, 0), "circle"),
}


def priority(level: int) -> Priority:
    if level not in PRIORITIES:
        raise ValueError(f"Alarm priority must be 1..{len(PRIORITIES)}, got {level}.")
    return PRIORITIES[level]
