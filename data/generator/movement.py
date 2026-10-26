"""Where each player wants to be, for the full-match tracking simulation.

Positions are worked out in the team's attacking frame (depth u = metres from its own goal line, width v = pitch y) and
turned back into pitch coordinates by the caller. A team with the ball stretches and pushes up; without it the team drops
into a compact block, slides towards the ball and sends one or two players to close it down.
"""

from __future__ import annotations

import math

# The 4-3-3 shape in the attacking frame: depth is metres from the team's own goal line, width is the pitch y.
ROLE_U = (5, 22, 22, 22, 22, 43, 43, 43, 65, 68, 65)
ROLE_V = (34, 10, 27, 44, 58, 15, 34, 53, 12, 34, 56)
JOG, RUN, SPRINT = .55, .8, 1.0  # fractions of a player's top speed


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sway(slot: int, at_ms: int) -> tuple[float, float]:
    """A slow sway so players off the ball are never frozen on their spot."""
    seconds = at_ms / 1000
    return math.sin(seconds / (7 + slot % 4 * 1.7) + slot) * 1.1, math.cos(seconds / (9 + slot % 3 * 2.1) + slot * 1.3) * 1.1


def with_ball(slot: int, ball_u: float, ball_v: float, line: float, counter: bool) -> tuple[float, float, float]:
    base_u, base_v = ROLE_U[slot], ROLE_V[slot]
    flank_side = (base_v < 34) == (ball_v < 34)  # the player is on the ball's side of the pitch
    wide_ball = abs(ball_v - 34) > 17
    if slot == 0:
        return 5 + .14 * max(0.0, ball_u - 50), 34 + (ball_v - 34) * .1, JOG
    if counter and slot in (5, 7, 8, 9, 10):
        ahead = 24 if slot >= 8 else 10
        return min(100, ball_u + ahead), 34 + (base_v - 34) * (1.0 if slot >= 8 else .7), SPRINT
    if slot <= 4:
        target_u = base_u + (ball_u - 52.5) * .55 + line * .4
        target_v = base_v + (ball_v - 34) * .2
        if ball_u > 28:
            target_u = min(target_u, ball_u - 7)
        if slot in (1, 4) and ball_u > 55 and flank_side:  # the full-back on the ball side overlaps
            return clamp(ball_u - 3, 30, 92), 6.5 if base_v < 34 else 61.5, RUN
        return target_u, target_v, JOG
    if slot <= 7:
        target_u = base_u + (ball_u - 52.5) * .66 + line * .7
        target_v = base_v + (ball_v - 34) * .26
        if ball_u > 68:  # arrive at the edge of the box
            return clamp(max(target_u, 80), 0, 88), 34 + (base_v - 34) * .6, RUN
        return target_u, target_v, JOG
    if ball_u < 56:
        return base_u + (ball_u - 52.5) * .52 + line * .3, base_v + (ball_v - 34) * .18, JOG
    if slot == 9:  # the striker attacks the near post, or the penalty spot when the ball is central
        return min(99, ball_u + 6) if not wide_ball else 97, 34 + (ball_v - 34) * .15 + (5 if ball_v < 34 else -5), RUN
    if flank_side:  # the winger on the ball side holds the touchline
        return min(98, ball_u + 4), 7 if base_v < 34 else 61, RUN
    return min(97, ball_u + 6), 34 + (-5 if ball_v > 34 else 5), RUN  # the far-side winger arrives at the far post


def without_ball(slot: int, ball_u: float, ball_v: float, line: float, press: float) -> tuple[float, float, float]:
    """ball_u is measured in this team's own frame, so a small value means the ball is close to its goal."""
    base_v = ROLE_V[slot]
    defensive_line = clamp(.58 * ball_u + 2 + line, 15, 52)
    if slot == 0:
        return clamp(4 + .05 * ball_u, 4, 9), 34 + (ball_v - 34) * .25, JOG
    if slot <= 4:
        return defensive_line, 34 + (base_v - 34) * .78 + (ball_v - 34) * .38, JOG
    if slot <= 7:
        return defensive_line + 14, 34 + (base_v - 34) * .72 + (ball_v - 34) * .42, JOG
    return clamp(defensive_line + 26 + press * 6, 20, 82), 34 + (base_v - 34) * .7 + (ball_v - 34) * .35, JOG


def presser_distance(press: float, rank: int) -> float:
    """How far from the ball a defender will still step out to close it down; rank 0 is the closest."""
    if rank == 0:
        return 28 if press > .5 else 20 if press >= .4 else 14
    return 18 if rank == 1 and press >= .45 else 0
