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
CHRONOLOGICAL = ("session", "entry_index", "symbol")


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

    # The stop wins a tie, because the bar cannot say which level came first.
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

    if one_way_cost is None:
        per_bar = None
        flat = cost_cfg.slippage_bps / 10_000.0
    elif np.ndim(one_way_cost) == 0:
        per_bar = None
        flat = float(one_way_cost)
    else:
        per_bar = np.asarray(one_way_cost, dtype=np.float64)
        flat = 0.0

    def slip_at(index: int) -> float:
        if per_bar is None:
            return flat
        return float(per_bar[min(index, len(per_bar) - 1)])
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

        target = entry + direction * risk * trade_cfg.reward_multiple

        start = i + 1
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
        return_pct = direction * (fill_exit - fill_entry) / fill_entry

        trades.append(
            Trade(
                pattern=spec.name,
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
                entry_minute=(
                    None if bar_minutes is None else int(bar_minutes[start])
                ),
            )
        )
        next_allowed = exit_index + 1

    return trades
