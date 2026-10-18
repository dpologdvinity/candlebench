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

try:
    from candlebench import _enginecore
except ImportError:  # built without a C++ compiler; the Python walk below is used
    _enginecore = None

ExitReason = Literal["stop", "target", "timeout", "session_end"]
# Indexed by the reason codes `_enginecore` returns.
REASONS: tuple[ExitReason, ...] = ("stop", "target", "timeout", "session_end")

# The order trades were actually traded in. Trials are drawn in random session
# order, so the order trades accumulate in is a shuffle of the real sequence, and
# anything cumulative measured over a shuffle is an artefact of the draw. Trades
# from different symbols on the same session interleave by bar index, which
# treats the set as one portfolio traded in parallel.
#
# Named here, beside `Trade`, because two separate code paths order trades —
# `metrics._max_drawdown_r` over a `TradeTable` and `trades.chronological` over a frame
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


@dataclass(frozen=True, slots=True)
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


# How `TradeTable` stores each `Trade` field. Labels are object arrays whose
# elements share one string per distinct value, so a label costs a pointer.
_TABLE_DTYPES = {
    "pattern": object, "interval": object, "symbol": object,
    "session": np.int64,  # `date.toordinal()`
    "trial_index": np.int64, "direction": np.int64,
    "entry_index": np.int64, "exit_index": np.int64,
    "entry_price": np.float64, "exit_price": np.float64, "stop_price": np.float64,
    "target_price": np.float64, "risk_per_share": np.float64,
    "exit_reason": object,
    "gross_r": np.float64, "net_r": np.float64, "return_pct": np.float64,
    "window": np.int64,
    "entry_minute": np.float64,  # NaN where a Trade holds None
    "sample": object,
    "cost_bps": np.float64,  # NaN where a Trade holds None
    "matched": np.bool_,
}


@dataclass(frozen=True, eq=False)
class TradeTable:
    """Trades as columns: one numpy array per `Trade` field, in field order.

    A run keeps every trade it made, and as frozen dataclasses those cost
    about 620 bytes each: a 1,600-trial run held 843k of them in 520 MB, and
    building the trade file from them took a second copy that peaked the run
    at 1.26 GB. As columns a trade is about 180 bytes, and the file is written
    from the arrays directly.

    Iterating or indexing yields `Trade` objects, which is for tests and small
    selections; everything that reads whole runs works on the columns. Two
    columns differ from the dataclass: `session` holds date ordinals, and a
    missing `entry_minute` or `cost_bps` is NaN rather than None.
    """

    pattern: np.ndarray
    interval: np.ndarray
    symbol: np.ndarray
    session: np.ndarray
    trial_index: np.ndarray
    direction: np.ndarray
    entry_index: np.ndarray
    exit_index: np.ndarray
    entry_price: np.ndarray
    exit_price: np.ndarray
    stop_price: np.ndarray
    target_price: np.ndarray
    risk_per_share: np.ndarray
    exit_reason: np.ndarray
    gross_r: np.ndarray
    net_r: np.ndarray
    return_pct: np.ndarray
    window: np.ndarray
    entry_minute: np.ndarray
    sample: np.ndarray
    cost_bps: np.ndarray
    matched: np.ndarray

    def __len__(self) -> int:
        return len(self.net_r)

    def __iter__(self):
        return iter(self.rows())

    def __getitem__(self, index: int) -> Trade:
        return self.take(np.array([range(len(self))[index]])).rows()[0]

    @property
    def bars_held(self) -> np.ndarray:
        return self.exit_index - self.entry_index

    def columns(self) -> dict[str, np.ndarray]:
        return {name: getattr(self, name) for name in _TABLE_DTYPES}

    def take(self, selection) -> TradeTable:
        """The rows picked by an index array or a boolean mask, in that order."""
        return TradeTable(**{name: column[selection] for name, column in self.columns().items()})

    def sessions(self) -> list[date]:
        """The distinct sessions traded, in date order."""
        return [date.fromordinal(day) for day in np.unique(self.session).tolist()]

    def chronological_order(self) -> np.ndarray:
        """Row order under `CHRONOLOGICAL`, the same order `chronological_key`
        gives: an unknown minute sorts last and ties keep their row order.
        Labels sort by their codes in `np.unique`, which orders strings as
        Python does."""
        minute = np.where(np.isnan(self.entry_minute), np.inf, self.entry_minute)
        symbol = np.unique(self.symbol, return_inverse=True)[1].reshape(-1)
        interval = np.unique(self.interval, return_inverse=True)[1].reshape(-1)
        return np.lexsort((self.entry_index, interval, symbol, minute, self.session))

    def rows(self) -> list[Trade]:
        days = {day: date.fromordinal(day) for day in np.unique(self.session).tolist()}
        columns = {name: column.tolist() for name, column in self.columns().items()}
        columns["session"] = [days[day] for day in columns["session"]]
        for name in ("entry_minute", "cost_bps"):
            convert = int if name == "entry_minute" else float
            columns[name] = [None if math.isnan(v) else convert(v) for v in columns[name]]
        names = list(columns)
        return [Trade(**dict(zip(names, values))) for values in zip(*columns.values())]

    @classmethod
    def empty(cls) -> TradeTable:
        return cls(**{name: np.empty(0, dtype) for name, dtype in _TABLE_DTYPES.items()})

    @classmethod
    def from_trades(cls, trades) -> TradeTable:
        trades = list(trades)
        def column(name):
            values = [getattr(t, name) for t in trades]
            if name == "session":
                values = [day.toordinal() for day in values]
            elif name in ("entry_minute", "cost_bps"):
                values = [math.nan if v is None else v for v in values]
            return np.array(values, dtype=_TABLE_DTYPES[name]).reshape(len(trades))
        return cls(**{name: column(name) for name in _TABLE_DTYPES})

    @classmethod
    def of(cls, trades) -> TradeTable:
        """`trades` as a table, converting a sequence of `Trade` objects."""
        return trades if isinstance(trades, cls) else cls.from_trades(trades)

    @classmethod
    def concat(cls, tables) -> TradeTable:
        tables = [cls.of(t) for t in tables if len(t)]
        if not tables:
            return cls.empty()
        if len(tables) == 1:
            return tables[0]
        return cls(**{name: np.concatenate([getattr(t, name) for t in tables])
                      for name in _TABLE_DTYPES})


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


def _cost_per_bar(one_way_cost, cost_cfg) -> np.ndarray:
    """The one-way cost of each bar as a fraction of price; the last value
    covers every later bar, so a flat cost is a single value."""
    if one_way_cost is None:
        return np.array([cost_cfg.slippage_bps / 10_000.0])
    return np.atleast_1d(np.asarray(one_way_cost, dtype=np.float64))


def _slippage(one_way_cost, cost_cfg):
    """The one-way cost, as a fraction of price, of filling at a given bar."""
    per_bar = _cost_per_bar(one_way_cost, cost_cfg)
    return lambda index: float(per_bar[min(index, len(per_bar) - 1)])


_REASON_LABELS = np.array(REASONS, dtype=object)


def _native_hold(trade_cfg, n: int) -> int:
    """`max_hold_bars` capped at the session length, for `_enginecore`.

    Every hold longer than the session exits the same way, at the last bar
    with `session_end`, so capping changes no trade. It keeps a browser-set
    value from overflowing int64, which pybind11 refuses outright.
    """
    return min(trade_cfg.max_hold_bars, n + 1)


def _table_from_columns(columns: dict, labels: dict, *, sample: str, matched: bool,
                        bar_minutes) -> TradeTable:
    """A table from `_enginecore`'s columns.

    `labels` holds the pattern, interval, symbol, session ordinal, trial index
    and window, each either one value for every row or one per row.
    """
    n = len(columns["entry_index"])
    if not n:
        return TradeTable.empty()

    def fill(value, dtype):
        if isinstance(value, np.ndarray):
            return value
        out = np.empty(n, dtype)
        out.fill(value)
        return out

    if bar_minutes is None:
        entry_minute = fill(math.nan, np.float64)
    else:
        minutes = np.asarray(bar_minutes)[columns["entry_index"]]
        if minutes.dtype.kind == "f" and np.isnan(minutes).any():
            # What `int()` raises on the Python path; the cast would store garbage.
            raise ValueError("cannot convert float NaN to integer")
        entry_minute = minutes.astype(np.int64).astype(np.float64)
    return TradeTable(
        pattern=fill(labels["pattern"], object),
        interval=fill(labels["interval"], object),
        symbol=fill(labels["symbol"], object),
        session=fill(labels["session"], np.int64),
        trial_index=fill(labels["trial_index"], np.int64),
        direction=columns["direction"],
        entry_index=columns["entry_index"],
        exit_index=columns["exit_index"],
        entry_price=columns["entry_price"],
        exit_price=columns["exit_price"],
        stop_price=columns["stop_price"],
        target_price=columns["target_price"],
        risk_per_share=columns["risk_per_share"],
        exit_reason=_REASON_LABELS[columns["reason"]],
        gross_r=columns["gross_r"],
        net_r=columns["net_r"],
        return_pct=columns["return_pct"],
        window=fill(labels["window"], np.int64),
        entry_minute=entry_minute,
        sample=fill(sample, object),
        cost_bps=columns["cost_bps"],
        matched=fill(matched, np.bool_),
    )


def _execute(
    geom: Geometry, start: int, direction: int, entry: float, stop: float, risk: float,
    trade_cfg, slip_at, commission_r: float, *, pattern: str, interval: str, symbol: str,
    session: date, trial_index: int, window: int, bar_minutes: np.ndarray | None,
    matched: bool = False, sample: str = "discovery",
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
        sample=sample,
        cost_bps=(slip_at(start) + slip_at(exit_index)) / 2 * 10_000,
        matched=matched,
    )


def simulate(*args, **kwargs) -> list[Trade]:
    """`simulate_table` as `Trade` objects."""
    return simulate_table(*args, **kwargs).rows()


def simulate_table(
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
    sample: str = "discovery",
) -> TradeTable:
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
        return TradeTable.empty()

    direction = spec.direction
    # Named for what it is rather than "window", which is the walk-forward
    # window this trade belongs to and arrives as a parameter.
    extreme_window = spec.bars_required
    extremes = (
        rolling_min(geom.low, extreme_window)
        if direction > 0
        else rolling_max(geom.high, extreme_window)
    )

    commission_r = cost_cfg.commission_per_trade / trade_cfg.risk_per_trade_usd

    if _enginecore is not None:
        columns = _enginecore.simulate(
            geom.open, geom.high, geom.low, geom.close, extremes, np.flatnonzero(mask),
            direction, trade_cfg.stop_buffer, trade_cfg.reward_multiple,
            _native_hold(trade_cfg, n), trade_cfg.min_risk_pct,
            trade_cfg.allow_overlapping_trades, _cost_per_bar(one_way_cost, cost_cfg),
            commission_r,
        )
        labels = {"pattern": spec.name, "interval": interval, "symbol": symbol,
                  "session": session.toordinal(), "trial_index": trial_index, "window": window}
        return _table_from_columns(columns, labels, sample=sample, matched=False,
                                   bar_minutes=bar_minutes)

    slip_at = _slippage(one_way_cost, cost_cfg)
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
            trial_index=trial_index, window=window, bar_minutes=bar_minutes, sample=sample,
        )
        trades.append(trade)
        exit_index = trade.exit_index
        next_allowed = exit_index + 1

    return TradeTable.from_trades(trades)


# The first and last bar after its pattern's entry on which a matched control
# may enter. Later windows were measured in docs/experiments/matched-window.md:
# they recover more of an edge that lasts several bars, but miss most one-bar
# edges and raise the detection limit by a fifth to a half.
MATCH_ENTRY_BARS = (1, 5)


def simulate_matched(*args, **kwargs) -> list[Trade]:
    """`simulate_matched_table` as `Trade` objects."""
    return simulate_matched_table(*args, **kwargs).rows()


def simulate_matched_table(
    geom: Geometry,
    template: TradeTable | list[Trade],
    trade_cfg,
    cost_cfg,
    rng: np.random.Generator,
    *,
    bar_minutes: np.ndarray | None = None,
    one_way_cost: float | None = None,
    entry_bars: tuple[int, int] | None = None,
    sample: str = "discovery",
) -> TradeTable:
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
    template = TradeTable.of(template)
    if n < 2 or not template:
        return TradeTable.empty()
    commission_r = cost_cfg.commission_per_trade / trade_cfg.risk_per_trade_usd

    # One draw per template trade that has a bar to enter on, in template
    # order. A single bounded draw over every trade consumes the generator
    # exactly as one `rng.choice(range(low, high + 1))` per trade did, so the
    # controls are unchanged; it is only 20x cheaper.
    entered = template.entry_index
    low = entered + first
    high = np.minimum(n - 1, entered + last)
    drawable = np.flatnonzero(high >= low)
    starts = low[drawable] + rng.integers(0, high[drawable] - low[drawable] + 1)

    if _enginecore is not None:
        columns = _enginecore.simulate_matched(
            geom.open, geom.high, geom.low, geom.close, drawable, starts, entered,
            template.direction, template.stop_price,
            trade_cfg.reward_multiple, _native_hold(trade_cfg, n), trade_cfg.min_risk_pct,
            _cost_per_bar(one_way_cost, cost_cfg), commission_r,
        )
        source = columns["source"]
        labels = {name: getattr(template, name)[source] for name in (
            "pattern", "interval", "symbol", "session", "trial_index", "window")}
        return _table_from_columns(columns, labels, sample=sample, matched=True,
                                   bar_minutes=bar_minutes)

    slip_at = _slippage(one_way_cost, cost_cfg)
    rows = template.rows()
    out: list[Trade] = []
    for index, start in zip(drawable.tolist(), starts.tolist()):
        trade = rows[index]
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
            bar_minutes=bar_minutes, matched=True, sample=sample,
        ))
    return TradeTable.from_trades(out)
