"""Open play as possessions.

Each team builds attacks from a few templates (build-up, wing play ending in a cross, through ball, switch of play, long
ball, counter-attack) weighted by a style. Scripted moments are never touched: shortly before each one the ball is steered
to where the scripted action starts, and after it play resumes from where the action ended.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

try:
    from .movement import with_ball, without_ball
except ImportError:  # run as a script
    from movement import with_ball, without_ball

MARGIN_MS = 1500  # open play stops this long before the first and after the last event of a scripted moment
STEP_MS = 2400  # one steering pass or carry, used to decide when to start steering towards a scripted moment


@dataclass(frozen=True)
class Style:
    templates: dict[str, float]  # how often each attack template is chosen
    pass_ok: float  # completion chance of an ordinary pass
    long_ok: float  # completion chance of a pass of 30 m or more
    carry_ok: float
    tempo: float  # below 1 plays faster
    press: float  # 0..1: how readily the team closes the ball down (also read by the movement model)
    line: float  # metres the defensive line sits above its base position (movement model)
    shoot_range: float  # distance from goal at which the team is happy to shoot
    finish: float  # how often a chance created is turned into a shot


STYLES = {
    "possession": Style({"build_up": 6, "recycle": 4, "through_ball": 1.6, "wing_play": 1.8, "switch_play": 1.2, "long_ball": .3, "counter": 1}, .93, .66, .88, 1.0, .45, 6.0, 20, 1.0),
    "counter": Style({"build_up": 3, "recycle": 2.5, "through_ball": 1.4, "wing_play": 1.8, "switch_play": .8, "long_ball": 1.4, "counter": 4}, .91, .60, .85, .88, .30, -7.0, 22, .75),
    "direct": Style({"build_up": 3, "recycle": 2, "through_ball": 1, "wing_play": 2.2, "switch_play": .8, "long_ball": 3, "counter": 2}, .90, .60, .85, .92, .60, 3.0, 24, .65),
}
TEAM_STYLE = {"t_rvr": "possession", "t_hrb": "counter", "t_ash": "direct"}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def slot_of(player_id: str) -> int:
    return int(player_id.rsplit("_", 1)[1]) - 1


def moment_windows(scripted: list[dict], home_id: str, neutral_types: set[str]) -> list[dict]:
    """One window per scripted moment: when open play must stop, where the ball has to be, and how play restarts."""
    side_of = lambda team_id: 0 if team_id == home_id else 1  # noqa: E731
    groups: dict[str, list[dict]] = {}
    for event in scripted:
        groups.setdefault(event["_moment"], []).append(event)
    windows = []
    clusters: list[tuple[str, list[dict]]] = []
    for moment_id, moment_rows in groups.items():  # a moment with a long gap (a second yellow) is several windows
        moment_rows.sort(key=lambda e: e["match_ms"])
        split: list[list[dict]] = [[moment_rows[0]]]
        for event in moment_rows[1:]:
            if event["match_ms"] - split[-1][-1]["match_ms"] > 15_000:
                split.append([event])
            else:
                split[-1].append(event)
        clusters += [(moment_id if len(split) == 1 else f"{moment_id}#{n}", rows) for n, rows in enumerate(split)]
    for moment_id, rows in clusters:
        ball_rows = [e for e in rows if e["type"] not in neutral_types]
        fouls = [e for e in rows if e["type"] == "foul"]
        window = {"id": moment_id, "low": rows[0]["match_ms"] - MARGIN_MS, "high": rows[-1]["match_ms"] + MARGIN_MS, "target": None, "after": None}
        if ball_rows:
            first, last = ball_rows[0], ball_rows[-1]
            window["target"] = {"spot": (first["start"]["x"], first["start"]["y"]), "side": side_of(first["team_id"]), "actor": slot_of(first["player_id"]) if first["player_id"] else None}
            acted, other = side_of(last["team_id"]), 1 - side_of(last["team_id"])
            end = last.get("end") or last["start"]
            if last["type"] == "goal":
                window["after"] = {"spot": (52.5, 34.0), "side": other, "keeper": False}
            elif last["type"] == "save":
                window["after"] = {"spot": (5.5 if last["start"]["x"] < 52.5 else 99.5, 34.0), "side": acted, "keeper": True}
            elif last["type"] == "shot":
                window["after"] = {"spot": (6.0 if end["x"] < 52.5 else 99.0, 34.0), "side": other, "keeper": True}
            elif last["type"] in {"offside", "goal_disallowed"}:
                window["after"] = {"spot": (last["start"]["x"], last["start"]["y"]), "side": other, "keeper": False}
            else:
                window["after"] = {"spot": (end["x"], end["y"]), "side": acted, "keeper": False}
        elif fouls:
            spot = fouls[0]["start"]
            fouled = 1 - side_of(fouls[0]["team_id"])
            window["target"] = {"spot": (spot["x"], spot["y"]), "side": fouled, "actor": None}
            window["after"] = {"spot": (spot["x"], spot["y"]), "side": fouled, "keeper": False}
        windows.append(window)
    return sorted(windows, key=lambda w: w["low"])


class OpenPlay:
    def __init__(self, rng: random.Random, scenario: dict, teams: tuple[dict, dict], windows: list[dict], duration: int, make_event) -> None:
        self.rng, self.teams, self.windows, self.duration, self.make = rng, teams, windows, duration, make_event
        self.first_half = scenario["period_lengths_ms"][0]
        self.styles = [STYLES[TEAM_STYLE[team["team_id"]]] for team in teams]
        self.events: list[dict] = []
        self.window_possession: dict[str, int] = {}
        self.t, self.ball, self.side, self.holder = 0, (52.5, 34.0), 0, None
        self.possession, self.poss_events = 1, 0
        self.transition_until = -1
        self.cursor = 0  # index of the first window not yet played through

    # ----- geometry -------------------------------------------------------------------------------------------------

    @property
    def period(self) -> int:
        return 1 if self.t <= self.first_half else 2

    def frame(self, side: int | None = None) -> tuple[int, float]:
        """(direction of attack along x, x of the team's own goal line)."""
        side = self.side if side is None else side
        ltr = (side == 0) == (self.period == 1)
        return (1, 0.0) if ltr else (-1, 105.0)

    def uv(self, point: tuple[float, float], side: int | None = None) -> tuple[float, float]:
        fwd, own = self.frame(side)
        return (point[0] - own) * fwd, point[1]

    def xy(self, u: float, v: float, side: int | None = None) -> tuple[float, float]:
        fwd, own = self.frame(side)
        return own + fwd * u, v

    def shape_point(self, side: int, slot: int, ball: tuple[float, float]) -> tuple[float, float]:
        """Where a player normally stands with the ball at the given spot: the positioning the tracking simulation also uses,
        so the people the events name are really there."""
        bu, bv = self.uv(ball, side)
        style = self.styles[side]
        if side == self.side:
            tu, tv, _ = with_ball(slot, bu, bv, style.line, False)
        else:
            tu, tv, _ = without_ball(slot, bu, bv, style.line, style.press)
        return self.xy(tu, tv, side)

    def closest(self, side: int, spot: tuple[float, float], count: int, exclude: frozenset[int] = frozenset()) -> list[int]:
        low = 0 if self.uv(spot, side)[0] < 14 else 1
        return sorted((i for i in range(low, 11) if i not in exclude), key=lambda i: math.dist(self.shape_point(side, i, spot), spot))[:count]

    def nearest_slot(self, side: int, spot: tuple[float, float], exclude: int | frozenset[int] | None = None) -> int:
        skip = frozenset([exclude]) if isinstance(exclude, int) else (exclude or frozenset())
        ranked = self.closest(side, spot, 2, skip)
        return ranked[0] if self.rng.random() < .72 else ranked[-1]

    def receiver(self, desired: tuple[float, float]) -> int:
        """The teammate who is best placed to take a pass played to the desired spot."""
        ranked = sorted((i for i in range(1, 11) if i != self.holder), key=lambda i: math.dist(self.shape_point(self.side, i, self.ball), desired))
        return ranked[0] if self.rng.random() < .7 else ranked[1]

    # ----- timing ---------------------------------------------------------------------------------------------------

    def hold(self, low: float = 1.0, high: float = 2.2, fast: bool = False) -> int:
        return int(self.rng.uniform(low, high) * 1000 * self.styles[self.side].tempo * (.75 if fast else 1.0))

    def needs_steering(self, window: dict) -> bool:
        target = window["target"]
        if target is None:
            return False
        steps = math.ceil(math.dist(self.ball, target["spot"]) / 16) + 1 + (1 if self.side != target["side"] else 0)
        return window["low"] - self.t <= steps * STEP_MS + 2500

    def interrupted(self, span: int = 0) -> bool:
        """True when play has to stop: the match is ending, or an action lasting span ms would run into a scripted moment."""
        if self.t + span >= self.duration - 2000:
            return True
        window = self.windows[self.cursor] if self.cursor < len(self.windows) else None
        return bool(window and (self.t + span >= window["low"] or self.needs_steering(window)))

    # ----- events ---------------------------------------------------------------------------------------------------

    def emit(self, kind: str, side: int, slot: int, start: tuple[float, float], end: tuple[float, float] | None = None, *,
             target: int | None = None, outcome: str | None = None, quals: tuple[str, ...] = (), body: str | None = None,
             related: tuple[str, ...] = (), at: int | None = None) -> str:
        key = f"filler_{len(self.events):05d}"
        team = self.teams[side]
        extra = {"period": self.period, "target_player_id": team["squad"][target]["player_id"] if target is not None else None,
                 "end": {"x": round(end[0], 2), "y": round(end[1], 2)} if end else None, "outcome": outcome,
                 "possession_id": self.possession, "qualifiers": list(quals), "body_part": body, "related_event_ids": list(related)}
        self.events.append(self.make(key, self.t if at is None else at, kind, team["team_id"], team["squad"][slot]["player_id"], {"x": round(start[0], 2), "y": round(start[1], 2)}, **extra))
        self.poss_events += 1
        return key

    def turnover(self, spot: tuple[float, float], kind: str, winner: int | None = None) -> None:
        """The other team wins the ball at spot (an interception, or a tackle after a lost carry)."""
        winner = winner if winner is not None else self.nearest_slot(1 - self.side, spot)
        self.side, self.possession, self.poss_events = 1 - self.side, self.possession + 1, 0
        self.emit(kind, self.side, winner, spot, outcome="won")
        self.holder, self.ball = winner, spot
        self.transition_until = self.t + 7000
        self.t += self.hold(.8, 1.5)

    def pressure(self, u: float) -> float:
        """Sometimes a defender closes the ball down first; the action that follows is then a little harder."""
        defender = self.styles[1 - self.side]
        if u < 12 or self.rng.random() > defender.press * .22:
            return 0.0
        slot = self.nearest_slot(1 - self.side, self.ball, exclude=0)
        self.emit("pressure", 1 - self.side, slot, self.ball)
        self.t += int(self.rng.uniform(300, 600))
        return .06

    # ----- actions --------------------------------------------------------------------------------------------------

    def pass_to(self, u: float, v: float, *, quals: tuple[str, ...] = (), ok: float | None = None, force: bool = False,
                receiver: int | None = None, fast: bool = False, clear_on_fail: bool = False) -> bool:
        """Play the ball to (u, v) in the attacking frame. True when it arrived; False when lost or when play has to stop."""
        if self.holder is None:
            return False
        style = self.styles[self.side]
        start = self.ball
        end = self.xy(clamp(u, 3, 102), clamp(v, 2, 66))
        distance = math.dist(start, end)
        if distance < 4:
            fwd, _ = self.frame()
            end = (clamp(start[0] + 4 * fwd, 2, 103), start[1])
            distance = math.dist(start, end)
        if self.interrupted(int(clamp(distance / 19, .4, 2.7) * 1000) + (3000 if clear_on_fail else 1200)):
            return False
        target = receiver if receiver is not None else self.receiver(end)
        if receiver is None:  # the ball goes to where the receiver is, or a little ahead of him when it is played into space
            shape = self.shape_point(self.side, target, start)
            gap = math.dist(shape, end)
            ahead = bool(set(quals) & {"through_ball", "cross", "long_ball", "switch_of_play", "counter"})
            reach = min(gap, 10.0) if ahead else 0.0
            end = (shape[0] + (end[0] - shape[0]) * reach / gap, shape[1] + (end[1] - shape[1]) * reach / gap) if gap else shape
            end = (clamp(end[0], 2, 103), clamp(end[1], 2, 66))
            distance = math.dist(start, end)
        base = ok if ok is not None else (style.long_ok if distance >= 30 else style.pass_ok)
        base -= self.pressure(self.uv(start)[0]) + .16 * max(0.0, (self.uv(end)[0] - 70) / 35)
        success = force or (self.poss_events <= 45 and self.rng.random() < clamp(base, .2, .97))
        winner = None
        if not success and not clear_on_fail:  # a lost pass is cut out by a defender who is really there
            chasing = frozenset(self.closest(1 - self.side, start, 2)) | {0}  # the players closing the ball carrier down cannot also cut out the pass
            winner = self.nearest_slot(1 - self.side, end, chasing)
            end = self.shape_point(1 - self.side, winner, end)
        self.emit("pass", self.side, self.holder, start, end, target=target, outcome="complete" if success else None, quals=quals)
        flight = int(clamp(math.dist(start, end) / 19, .4, 2.7) * 1000)
        if success:
            self.t += flight + self.hold(1.0, 2.2, fast)
            self.holder, self.ball = target, end
            return True
        self.t += flight
        if clear_on_fail:
            self.clear(end)
        else:
            self.turnover(end, "interception", winner)
        return False

    def carry(self, du: float, dv: float = 0.0, *, quals: tuple[str, ...] = (), fast: bool = False) -> bool:
        if self.holder is None:
            return False
        style = self.styles[self.side]
        start = self.ball
        u, v = self.uv(start)
        end = self.xy(clamp(u + du, 3, 102), clamp(v + dv, 2, 66))
        distance = math.dist(start, end)
        if self.interrupted(int(distance / 4.2 * 1000) + 1800):
            return False
        base = style.carry_ok - self.pressure(u) - .08 * max(0.0, (u - 75) / 30)
        success = self.rng.random() < clamp(base, .3, .95)
        self.emit("carry", self.side, self.holder, start, end, outcome="complete" if success else None, quals=quals)
        self.t += int((distance / self.rng.uniform(5.0, 6.6) + .3) * 1000 * (.7 if fast else 1.0))
        if success:
            self.ball = end
            return True
        self.turnover(end, "tackle", self.closest(1 - self.side, start, 1)[0])
        return False

    def shot(self, quals: tuple[str, ...] = (), body: str | None = None, bonus: float = 0.0) -> None:
        """Shoot. Open-play shots never score: the scoreline belongs to the scripted goals."""
        if self.holder is None or self.interrupted(7000):
            return
        style = self.styles[self.side]
        u, v = self.uv(self.ball)
        distance = math.hypot(105 - u, v - 34)
        if distance > style.shoot_range + 8 and self.rng.random() < .65:
            self.carry(self.rng.uniform(6, 11), (34 - v) * .3 + self.rng.uniform(-4, 4))
            return
        on_target = self.rng.random() < clamp(.52 - .012 * max(0.0, distance - 8) + bonus, .16, .6)
        goal_x = self.xy(105, 34)[0]
        y = 34 + (self.rng.uniform(-3.2, 3.2) if on_target else self.rng.choice([-1, 1]) * self.rng.uniform(4.5, 9))
        start = self.ball
        shot_key = self.emit("shot", self.side, self.holder, start, (goal_x, y), outcome="on_target" if on_target else "off_target",
                             quals=quals, body=body or self.rng.choice(["right_foot", "right_foot", "left_foot"]))
        self.t += int(clamp(distance / 24, .4, 1.5) * 1000)
        shooting, defending = self.side, 1 - self.side
        self.side, self.possession, self.poss_events = defending, self.possession + 1, 0
        if on_target:
            self.emit("save", defending, 0, (goal_x, y), outcome="saved", related=(shot_key,))
            self.t += self.hold(1.8, 3.0)
            self.holder, self.ball = 0, self.xy(5.5, 34, defending)
        elif self.rng.random() < .24:
            self.side, self.possession = shooting, self.possession - 1
            self.t += self.hold(2.5, 4.0)
            self.corner()
        else:
            self.t += self.hold(1.8, 3.2)
            self.holder, self.ball = 0, self.xy(6.0, 34, defending)

    def finish(self, chance: float, **shot_args) -> None:
        if self.rng.random() < chance * self.styles[self.side].finish:
            self.shot(**shot_args)

    def clear(self, spot: tuple[float, float]) -> None:
        """A defender clears the ball; it either goes out for a corner or the defending team gets it back."""
        defending = 1 - self.side
        slot = self.nearest_slot(defending, spot, exclude=0)
        du, dv = self.uv(spot, defending)
        out_v = clamp(dv + self.rng.choice([-1, 1]) * self.rng.uniform(14, 26), 3, 65)
        end = self.xy(clamp(du + self.rng.uniform(16, 26), 8, 100), out_v, defending)
        self.emit("clearance", defending, slot, spot, end, outcome="cleared")
        self.t += int(clamp(math.dist(spot, end) / 19, .6, 2.0) * 1000)
        if self.rng.random() < .3:
            self.t += self.hold(2.0, 3.5)
            self.ball = end
            self.corner()
            return
        self.side, self.possession, self.poss_events = defending, self.possession + 1, 0
        self.holder, self.ball = self.nearest_slot(defending, end), end
        self.t += self.hold(.6, 1.2)

    def corner(self) -> None:
        """The attacking team (self.side) takes a corner from the flag nearest the last action."""
        if self.interrupted(6000):
            return
        u, v = self.uv(self.ball)
        flag = self.xy(105, 1.0 if v < 34 else 67.0)
        taker = (7 if v < 34 else 5) if self.rng.random() < .5 else (10 if v < 34 else 8)
        end = self.xy(self.rng.uniform(95, 99), 34 + self.rng.uniform(-7, 7))
        target = self.receiver(end)
        self.holder, self.ball = taker, flag
        success = self.rng.random() < .55
        self.emit("pass", self.side, taker, flag, end, target=target, outcome="complete" if success else None, quals=("corner", "set_piece"))
        self.t += int(clamp(math.dist(flag, end) / 19, .8, 2.0) * 1000)
        if success:
            self.holder, self.ball = target, end
            self.t += self.hold(.4, .9)
            self.finish(.4, quals=("header",), body="head", bonus=.08)
        else:
            self.clear(end)

    # ----- attack templates -----------------------------------------------------------------------------------------

    def cross(self, flank: float) -> bool:
        style = self.styles[self.side]
        if not self.pass_to(self.rng.uniform(92, 98), 34 + self.rng.uniform(-7, 7), quals=("cross",), ok=style.pass_ok - .30, clear_on_fail=True):
            return False
        self.t += self.hold(.2, .5)
        self.finish(.3, quals=("header",), body="head", bonus=.05)
        return True

    def t_build_up(self) -> None:
        for _ in range(self.rng.randint(3, 6)):
            u, v = self.uv(self.ball)
            if u > 18 and self.rng.random() < .4:
                moved = self.carry(self.rng.uniform(8, 16), self.rng.uniform(-5, 5))
            else:
                moved = self.pass_to(u + (self.rng.uniform(-4, 11) if u > 18 else self.rng.uniform(4, 12)), v + self.rng.uniform(-16, 16))
            if not moved:
                return

    def t_recycle(self) -> None:
        """Keep the ball: short passes, mostly sideways or back."""
        for _ in range(self.rng.randint(2, 4)):
            u, v = self.uv(self.ball)
            if not self.pass_to(u + self.rng.uniform(-9, 3), v + self.rng.uniform(-18, 18)):
                return

    def t_wing_play(self) -> None:
        u, v = self.uv(self.ball)
        flank = 7.0 if (v < 34) == (self.rng.random() < .75) else 61.0
        if u < 58 and not self.pass_to(min(u + self.rng.uniform(12, 22), 74), flank + self.rng.uniform(-2, 3)):
            return
        for _ in range(self.rng.randint(1, 2)):
            u, v = self.uv(self.ball)
            if u < 88 and not self.carry(self.rng.uniform(7, 14), (flank - v) * .4):
                return
        if self.rng.random() < .5:
            self.cross(flank)

    def t_through_ball(self) -> None:
        style = self.styles[self.side]
        u, v = self.uv(self.ball)
        while u < 52:
            if not self.pass_to(u + self.rng.uniform(10, 18), clamp(v + self.rng.uniform(-12, 12), 14, 54)):
                return
            u, v = self.uv(self.ball)
        if self.rng.random() < .5 and not self.pass_to(u + self.rng.uniform(2, 8), 34 + self.rng.uniform(-9, 9)):
            return
        u, v = self.uv(self.ball)
        if not self.pass_to(min(u + self.rng.uniform(14, 24), 94), 34 + self.rng.uniform(-13, 13), quals=("through_ball",), ok=style.pass_ok - .18):
            return
        if self.rng.random() < .35 and not self.carry(self.rng.uniform(3, 8), self.rng.uniform(-3, 3)):
            return
        self.finish(.3, bonus=.04)

    def t_switch_play(self) -> None:
        style = self.styles[self.side]
        u, v = self.uv(self.ball)
        if not self.pass_to(u + self.rng.uniform(2, 9), (62.0 if v < 34 else 6.0) + self.rng.uniform(-3, 3), quals=("switch_of_play",), ok=style.long_ok + .12):
            return
        self.t_wing_play()

    def t_long_ball(self) -> None:
        style = self.styles[self.side]
        u, v = self.uv(self.ball)
        if not self.pass_to(min(u + self.rng.uniform(32, 46), 90), 34 + self.rng.uniform(-18, 18), quals=("long_ball",), ok=style.long_ok - .08):
            return
        if self.rng.random() < .2 * style.finish:
            self.shot()
        else:
            u, v = self.uv(self.ball)
            self.pass_to(u - self.rng.uniform(3, 8), v + self.rng.uniform(-10, 10))

    def t_counter(self) -> None:
        style = self.styles[self.side]
        self.transition_until = -1
        u, v = self.uv(self.ball)
        if not self.pass_to(u + self.rng.uniform(18, 30), 34 + self.rng.uniform(-22, 22), quals=("counter",), ok=style.pass_ok - .12, fast=True):
            return
        if self.rng.random() < .6 and not self.carry(self.rng.uniform(8, 15), self.rng.uniform(-6, 6), quals=("counter",), fast=True):
            return
        u, v = self.uv(self.ball)
        if u < 82 and self.rng.random() < .6 and not self.pass_to(u + self.rng.uniform(8, 16), 34 + self.rng.uniform(-14, 14), quals=("counter",), fast=True):
            return
        self.finish(.35, quals=("counter",), bonus=.08)

    def choose(self) -> str:
        style = self.styles[self.side]
        u, _ = self.uv(self.ball)
        weights = dict(style.templates)
        if u < 30:
            weights["build_up"] *= 2
        elif u >= 62:
            weights.update(build_up=0, long_ball=0, recycle=weights["recycle"] * 1.5, switch_play=weights["switch_play"] * .5, wing_play=weights["wing_play"] * 1.1, through_ball=weights["through_ball"] * 1.1)
        if self.t < self.transition_until and u < 70:
            weights["counter"] *= 4
            weights["build_up"] *= .3
        else:
            weights["counter"] *= .1
        names = [name for name, weight in weights.items() if weight > 0]
        return self.rng.choices(names, weights=[weights[name] for name in names])[0]

    # ----- the loop -------------------------------------------------------------------------------------------------

    def steer(self, window: dict) -> None:
        """Move the ball to where the scripted action starts: completed passes and carries, no turnovers, no shots."""
        target = window["target"]
        if self.side != target["side"]:
            self.turnover(self.ball, "tackle" if self.rng.random() < .5 else "interception")
            return
        actor = target["actor"]
        if self.holder is None:
            self.holder = self.nearest_slot(self.side, self.ball)
        remaining = math.dist(self.ball, target["spot"])
        if remaining <= 2.5 and (actor is None or self.holder == actor):
            self.t = max(self.t, window["low"])
            return
        step = min(remaining, self.rng.uniform(12, 20))
        if remaining <= 20:
            end, receiver = target["spot"], actor
        else:
            ratio = step / remaining
            end = (self.ball[0] + (target["spot"][0] - self.ball[0]) * ratio, self.ball[1] + (target["spot"][1] - self.ball[1]) * ratio + self.rng.uniform(-3, 3))
            receiver = None
        end = (clamp(end[0], 0, 105), clamp(end[1], 0, 68))
        before = self.t
        if remaining > 20 and self.rng.random() < .3:
            self.emit("carry", self.side, self.holder, self.ball, end, outcome="complete")
            self.t += int((math.dist(self.ball, end) / 5.0 + .3) * 1000)
            self.ball = end
        else:
            if receiver is None or receiver == self.holder:
                receiver = self.receiver(end)
            self.emit("pass", self.side, self.holder, self.ball, end, target=receiver, outcome="complete")
            self.t += int(clamp(math.dist(self.ball, end) / 19, .4, 2.7) * 1000) + self.hold(.4, 1.0, True)
            self.holder, self.ball = receiver, end
        if self.t == before:
            self.t += 300

    def enter(self, window: dict) -> None:
        """Skip over a scripted moment and restart open play from where it ended."""
        self.window_possession[window["id"]] = self.possession
        self.t = window["high"] + 100
        after = window["after"]
        if after:
            self.side, self.ball, self.holder = after["side"], after["spot"], None
            self.possession, self.poss_events = self.possession + 1, 0
            if after["keeper"]:
                self.holder = 0
            self.transition_until = -1
        if window["id"].startswith("half_time"):
            self.side, self.ball, self.holder = 1, (52.5, 34.0), None
            self.possession, self.poss_events, self.transition_until = self.possession + 1, 0, -1
        self.cursor += 1

    def run(self) -> tuple[list[dict], dict[str, int]]:
        while self.t < self.duration - 2500:
            window = self.windows[self.cursor] if self.cursor < len(self.windows) else None
            if window and self.t >= window["low"]:
                self.enter(window)
            elif window and self.needs_steering(window):
                self.steer(window)
            else:
                before = self.t
                if self.holder is None:
                    self.holder = self.nearest_slot(self.side, self.ball)
                getattr(self, "t_" + self.choose())()
                if self.t == before:
                    self.t += 300
        return self.events, self.window_possession
