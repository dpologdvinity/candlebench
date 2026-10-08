"""Signal to closed trade.

The trade model is fixed and deliberately pessimistic where bar data is
ambiguous. A bar records only its open, high, low and close, so when a bar
touches both the stop and the target there is no way to know which came first.
Assuming the favourable one is the single most common way a backtest flatters
itself, so the stop wins. Gaps fill at the open rather than at the level,
because that is what actually happens to an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import math

import numpy as np

from candlebench.patterns import PatternSpec
from candlebench.patterns.context import Geometry, rolling_max, rolling_min

ExitReason = Literal["stop", "target", "timeout", "session_end"]

# The order trades were actually traded in. Trials are drawn in random session
# order, so the order trades accumulate in is a shuffle of the real sequence, and
# anything cumulative measured over a shuffle is an artefact of the draw. Trades
# from different symbols on the same session interleave by bar index, which
# treats the set as one portfolio traded in parallel.
#
# Named here, beside `Trade`, because two separate code paths order trades —
# `metrics._max_drawdown_r` over objects and `trades.chronological` over a frame
# — and a curve whose worst decline disagreed with the reported drawdown would
# discredit both numbers.
CHRONOLOGICAL = ("session", "entry_minute", "symbol", "interval", "entry_index")


def chronological_key(trade: Trade):
    """`CHRONOLOGICAL` for Trade objects, with an unknown minute sorting last.

    Clock minute, not bar index: `entry_index` counts bars within one
    interval, so pooling 1m and 5m trades by it interleaved them by bar number,
    and the pooled drawdown was measured along an order nobody traded in. An
    unknown minute sorts last, as pandas puts a missing value last, so this and
    `trades.chronological` agree.
    """
    minute = math.inf if trade.entry_minute is None else trade.entry_minute
    return (trade.session, minute, trade.symbol, trade.interval, trade.entry_index)


@dataclass(frozen=True)
class Trade:
    pattern: str
    interval: str
    symbol: str
    session: date
    trial_index: int
    direction: int  # +1 long, -1 short
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    risk_per_share: float
    exit_reason: ExitReason
    gross_r: float
    net_r: float
    return_pct: float
    # Which walk-forward window the trial came from. 0 when the run uses one
    # window, which is the default.
    window: int = 0
    # Minutes from the session open at the entry bar. Bar index cannot stand in
    # for clock time: validation drops malformed bars, so index times interval
    # is not the time of day. None when the caller supplied no bar clock.
    entry_minute: int | None = None
    sample: str = "discovery"
    # Mean one-way cost charged on the two legs, in bps. What this trade
    # actually paid, so a run's cost figure can be averaged over executed legs
    # rather than over bars nobody traded. None for trades stored before it was.
    cost_bps: float | None = None
    # A stop-matched random-entry control for a pattern trade of the same
    # `pattern` name, not a pattern trade itself. Stored beside the pattern's
    # trades so the dashboard draws exactly what the statistics compared with.
    matched: bool = False

    @property
    def bars_held(self) -> int:
        return self.exit_index - self.entry_index


def _resolve_exit(
    direction: int,
    stop: float,
    target: float,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    hit_hold_limit: bool,
) -> tuple[int, float, ExitReason]:
    """Walk one forward window and return (offset, fill price, reason).

    Vectorised over the window rather than looped bar by bar: the first touch of
    each level is an `argmax` over a boolean comparison.
    """
    if direction > 0:
        stop_touch = low <= stop
        target_touch = high >= target
    else:
        stop_touch = high >= stop
        target_touch = low <= target

    k_stop = int(np.argmax(stop_touch)) if stop_touch.any() else None
    k_target = int(np.argmax(target_touch)) if target_touch.any() else None

    # The stop wins a tie, because the bar cannot say which level came first,
    # unless the bar opened beyond the target. The open is the one price whose
    # place in the bar is known, so the target filled there before any later
    # trade could reach the stop.
    if k_stop is not None and k_stop == k_target:
        opened_past = open_[k_stop] >= target if direction > 0 else open_[k_stop] <= target
        if opened_past:
            return k_stop, float(open_[k_stop]), "target"
    if k_stop is not None and (k_target is None or k_stop <= k_target):
        gap = open_[k_stop]
        fill = min(stop, gap) if direction > 0 else max(stop, gap)
        return k_stop, float(fill), "stop"

    if k_target is not None:
        gap = open_[k_target]
        fill = max(target, gap) if direction > 0 else min(target, gap)
        return k_target, float(fill), "target"

    last = len(close) - 1
    return last, float(close[last]), "timeout" if hit_hold_limit else "session_end"


def _slippage(one_way_cost, cost_cfg):
    """The one-way cost, as a fraction of price, of filling at a given bar."""
    if one_way_cost is None:
        flat = cost_cfg.slippage_bps / 10_000.0
        return lambda index: flat
    if np.ndim(one_way_cost) == 0:
        flat = float(one_way_cost)
        return lambda index: flat
    per_bar = np.asarray(one_way_cost, dtype=np.float64)
    return lambda index: float(per_bar[min(index, len(per_bar) - 1)])


def _execute(
    geom: Geometry, start: int, direction: int, entry: float, stop: float, risk: float,
    trade_cfg, slip_at, commission_r: float, *, pattern: str, interval: str, symbol: str,
    session: date, trial_index: int, window: int, bar_minutes: np.ndarray | None,
    matched: bool = False,
) -> Trade:
    """Walk one trade from its entry bar to its exit and charge both legs.

    Shared by pattern trades and their matched controls, so the two can differ
    only in where they enter and never in how they exit or what they pay.
    """
    n = len(geom)
    target = entry + direction * risk * trade_cfg.reward_multiple
    hold_limit = start + trade_cfg.max_hold_bars - 1
    stop_index = min(hold_limit, n - 1)
    hit_hold_limit = hold_limit <= n - 1

    offset, exit_price, reason = _resolve_exit(
        direction,
        stop,
        target,
        geom.open[start : stop_index + 1],
        geom.high[start : stop_index + 1],
        geom.low[start : stop_index + 1],
        geom.close[start : stop_index + 1],
        hit_hold_limit,
    )
    exit_index = start + offset

    fill_entry = entry * (1 + direction * slip_at(start))
    fill_exit = exit_price * (1 - direction * slip_at(exit_index))

    gross_r = direction * (exit_price - entry) / risk
    net_r = direction * (fill_exit - fill_entry) / risk - commission_r
    # Commission is a fixed dollar charge on a position sized to risk
    # `risk_per_trade_usd`, so as a fraction of the entry notional it is
    # commission_r x risk / entry. Charged here as well as in net R so the
    # two describe the same trade.
    return_pct = (
        direction * (fill_exit - fill_entry) / fill_entry - commission_r * risk / fill_entry
    )
    return Trade(
        pattern=pattern,
        interval=interval,
        symbol=symbol,
        session=session,
        trial_index=trial_index,
        direction=direction,
        entry_index=start,
        exit_index=exit_index,
        entry_price=fill_entry,
        exit_price=fill_exit,
        stop_price=stop,
        target_price=target,
        risk_per_share=risk,
        exit_reason=reason,
        gross_r=float(gross_r),
        net_r=float(net_r),
        return_pct=float(return_pct),
        window=window,
        entry_minute=None if bar_minutes is None else int(bar_minutes[start]),
        cost_bps=(slip_at(start) + slip_at(exit_index)) / 2 * 10_000,
        matched=matched,
    )


def simulate(
    geom: Geometry,
    mask: np.ndarray,
    spec: PatternSpec,
    trade_cfg,
    cost_cfg,
    *,
    symbol: str,
    interval: str,
    session: date,
    trial_index: int,
    window: int = 0,
    bar_minutes: np.ndarray | None = None,
    one_way_cost: float | None = None,
) -> list[Trade]:
    """Turn a signal mask into closed trades over one session's bars.

    `bar_minutes` is the clock minute of each bar, counted from the session
    open. It is supplied by the caller rather than derived here because this
    module sees only price arrays, and recording the time of day is what makes a
    time-of-day breakdown possible later.

    `one_way_cost` is the fraction of price each leg pays, overriding
    `cost_cfg.slippage_bps`. The caller supplies it so one session is priced
    once and identically for every pattern that trades it; without that, two
    patterns entering on the same bar could pay different spreads.

    It may be a single number or one value per bar. Per bar matters because the
    quoted spread is not flat within a session: sampled across five symbols and
    three sessions the open runs a median 4.0x midday and as much as 7.7x, so a
    09:35 entry and a 13:00 exit do not pay the same thing. Each leg is charged
    at the bar it actually filled on.
    """
    n = len(geom)
    if n < 2:
        return []

    direction = spec.direction
    # Named for what it is rather than "window", which is the walk-forward
    # window this trade belongs to and arrives as a parameter.
    extreme_window = spec.bars_required
    extremes = (
        rolling_min(geom.low, extreme_window)
        if direction > 0
        else rolling_max(geom.high, extreme_window)
    )

    slip_at = _slippage(one_way_cost, cost_cfg)
    commission_r = cost_cfg.commission_per_trade / trade_cfg.risk_per_trade_usd

    trades: list[Trade] = []
    next_allowed = 0

    for i in np.flatnonzero(mask):
        i = int(i)

        # A signal on the final bar has no next bar to enter on. Skipping it is
        # the only honest choice; entering on the signal bar's own close would
        # use information the signal did not have.
        if i + 1 >= n:
            continue
        if not trade_cfg.allow_overlapping_trades and i + 1 < next_allowed:
            continue

        entry = float(geom.open[i + 1])
        extreme = float(extremes[i])
        if not np.isfinite(entry) or not np.isfinite(extreme) or entry <= 0:
            continue

        if direction > 0:
            stop = extreme * (1 - trade_cfg.stop_buffer)
            risk = entry - stop
        else:
            stop = extreme * (1 + trade_cfg.stop_buffer)
            risk = stop - entry

        # A stop within a tick of the entry produces an R multiple large enough
        # to dominate every statistic downstream, so the signal is discarded.
        if risk <= 0 or risk / entry < trade_cfg.min_risk_pct:
            continue

        trade = _execute(
            geom, i + 1, direction, entry, stop, risk, trade_cfg, slip_at, commission_r,
            pattern=spec.name, interval=interval, symbol=symbol, session=session,
            trial_index=trial_index, window=window, bar_minutes=bar_minutes,
        )
        trades.append(trade)
        exit_index = trade.exit_index
        next_allowed = exit_index + 1

    return trades


# The first and last bar after its pattern's entry on which a matched control
# may enter. Later windows were measured in docs/experiments/matched-window.md:
# they recover more of an edge that lasts several bars, but miss most one-bar
# edges and raise the detection limit by a fifth to a half.
MATCH_ENTRY_BARS = (1, 5)


def simulate_matched(
    geom: Geometry,
    template: list[Trade],
    trade_cfg,
    cost_cfg,
    rng: np.random.Generator,
    *,
    bar_minutes: np.ndarray | None = None,
    one_way_cost: float | None = None,
    entry_bars: tuple[int, int] | None = None,
) -> list[Trade]:
    """One random-entry control trade for each pattern trade in `template`.

    The question a pattern answers is whether entering *on its signal* beats
    entering at some other moment with the same risk. Each control therefore
    copies one pattern trade's direction and its stop distance from the last
    close before entry, enters on a random bar shortly *after* the pattern's
    entry, and exits under exactly the same rules through `_execute`.

    Why after, and why the same distance:

    - The shared random controls set their stops at their own signal bar's
      extreme, which is usually tighter than a multi-bar formation's. Stop
      width is not neutral — the rule that a bar touching both levels is a stop
      penalises tight stops — so a pattern could beat those controls on
      geometry alone.
    - A control entering *before* the pattern trades through bars the pattern
      was selected on: its formation, its trend gate, and the exit of the trade
      before it. On a synthetic random walk, controls entering one to five bars
      early lost 0.10R against the template's 0.01R, and 0.56R five bars before
      a tweezer top, whose trend gate had selected a rising path. Entering one
      to five bars after matched the template to within 0.03R.
    - The stop is anchored to the last close before the control's entry, at the
      pattern's distance from its own last close, so the control's risk
      includes its entry gap exactly as a pattern's does.

    A pattern whose edge lasts several bars partly shares it with its controls,
    so the comparison understates such an edge rather than inventing one.
    Controls may overlap one another, since each answers for one pattern trade.
    """
    first, last = MATCH_ENTRY_BARS if entry_bars is None else entry_bars
    if not 1 <= first <= last:
        # Bar 0 is the pattern's own entry; earlier bars were selected by it.
        raise ValueError(
            f"matched controls enter 1 or more bars after the pattern, not {first}-{last}")
    n = len(geom)
    if n < 2 or not template:
        return []
    slip_at = _slippage(one_way_cost, cost_cfg)
    commission_r = cost_cfg.commission_per_trade / trade_cfg.risk_per_trade_usd
    out: list[Trade] = []
    for trade in template:
        candidates = range(trade.entry_index + first, min(n - 1, trade.entry_index + last) + 1)
        if not candidates:
            continue
        start = int(rng.choice(candidates))
        entry = float(geom.open[start])
        if not np.isfinite(entry) or entry <= 0:
            continue
        reach = trade.direction * (float(geom.close[trade.entry_index - 1]) - trade.stop_price)
        stop = float(geom.close[start - 1]) - trade.direction * reach
        risk = trade.direction * (entry - stop)
        if risk <= 0 or risk / entry < trade_cfg.min_risk_pct:
            continue
        out.append(_execute(
            geom, start, trade.direction, entry, stop, risk, trade_cfg, slip_at, commission_r,
            pattern=trade.pattern, interval=trade.interval, symbol=trade.symbol,
            session=trade.session, trial_index=trade.trial_index, window=trade.window,
            bar_minutes=bar_minutes, matched=True,
        ))
    return out
