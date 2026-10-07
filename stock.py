"""Scores a stock's fit for long-term, short-term, day, and swing trading.

Usage:
    python stock.py [TICKER]           concise verdict for each trading style
    python stock.py [TICKER] -v        full per-metric reasoning
    python stock.py [TICKER] -i        raw indicator readings only, no verdicts
"""

import argparse
import math
from numbers import Real
from dataclasses import dataclass, field

import yfinance as yf

STRONG_THRESHOLD = 0.8
MODERATE_THRESHOLD = 0.6
MIN_EVALUATED_METRICS = 3
MIN_COVERAGE = 0.6

RATING_LABELS = {
    "strong": "STRONG CANDIDATE",
    "moderate": "MODERATE CANDIDATE",
    "poor": "POOR CANDIDATE",
    "inconclusive": "INCONCLUSIVE",
}

NO_DATA = "n/a"


@dataclass
class Metric:
    """One indicator reading: its raw value, what it means, and how it scored.

    A missing or nonfinite score means the data was unavailable, so it is excluded
    from the final percentage instead of counting as a failure.
    """

    label: str
    value: str
    detail: str
    score: float | None = None

    @property
    def is_evaluated(self) -> bool:
        return self.score is not None and math.isfinite(self.score)


@dataclass
class Analysis:
    """A scored verdict for one trading style.

    `base` holds metrics shared with another style (day and swing trading both
    build on the short-term signals). Shared metrics count toward the score but
    are only printed once, under the style that owns them.
    """

    style: str
    metrics: list[Metric]
    verdicts: dict[str, str]
    base: "Analysis | None" = None
    notes: list[str] = field(default_factory=list)

    @property
    def all_metrics(self) -> list[Metric]:
        return (self.base.all_metrics if self.base else []) + self.metrics

    @property
    def evaluated(self) -> list[Metric]:
        return [m for m in self.all_metrics if m.is_evaluated]

    @property
    def evaluated_count(self) -> int:
        return len(self.evaluated)

    @property
    def total_count(self) -> int:
        return len(self.all_metrics)

    @property
    def coverage(self) -> float:
        return self.evaluated_count / self.total_count if self.total_count else 0.0

    @property
    def score(self) -> float:
        return sum(m.score for m in self.evaluated)

    @property
    def indicator_score(self) -> float | None:
        evaluated = self.evaluated
        return self.score / len(evaluated) if evaluated else None

    @property
    def win_rate(self) -> float | None:
        """Compatibility alias for indicator score, not a measured win rate."""
        return self.indicator_score

    @property
    def rating(self) -> str:
        if self.evaluated_count < MIN_EVALUATED_METRICS or self.coverage < MIN_COVERAGE:
            return "inconclusive"
        if self.indicator_score >= STRONG_THRESHOLD:
            return "strong"
        if self.indicator_score >= MODERATE_THRESHOLD:
            return "moderate"
        return "poor"

    @property
    def verdict(self) -> str:
        if self.rating == "inconclusive":
            return ("Not enough indicator evidence to make a determination; "
                    f"requires at least {MIN_EVALUATED_METRICS} evaluated metrics "
                    f"and {MIN_COVERAGE:.0%} coverage.")
        return self.verdicts[self.rating]


class StockData:
    """Fetches and caches the yfinance data the analyses share."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ticker = yf.Ticker(symbol)
        self.info = self.ticker.info
        self._history: dict[str, object] = {}
        self._financials = None

    @property
    def name(self) -> str:
        return self.info.get("longName") or self.info.get("shortName") or self.symbol

    @property
    def price(self):
        return self.info.get("currentPrice") or self.info.get("regularMarketPrice")

    @property
    def financials(self):
        if self._financials is None:
            self._financials = self.ticker.financials
        return self._financials

    def history(self, period: str):
        if period not in self._history:
            self._history[period] = self.ticker.history(period=period)
        return self._history[period]

    def close(self, period: str):
        """Closing prices for a period, oldest first, or None if unavailable."""
        history = self.history(period)
        return _finite(history["Close"]) if not history.empty else None

    def earnings_date(self) -> str | None:
        """Next scheduled earnings date, or None if yfinance has none."""
        try:
            calendar = self.ticker.calendar
            if isinstance(calendar, dict):
                dates = calendar.get("Earnings Date")
            elif calendar is not None and not calendar.empty:
                dates = calendar.loc["Earnings Date"].tolist()
            else:
                return None
        except Exception:
            return None

        if dates is None or (isinstance(dates, (list, tuple)) and not dates):
            return None
        return str(dates[0] if isinstance(dates, (list, tuple)) else dates)


def _growth_trend(series) -> tuple[bool, bool]:
    """Returns (strictly_rising, no_declines) for a series ordered oldest first."""
    strictly_rising = True
    no_declines = True

    for i in range(len(series) - 1):
        if series[i + 1] < series[i]:
            strictly_rising = False
            no_declines = False
        elif series[i + 1] == series[i]:
            strictly_rising = False

    return strictly_rising, no_declines


def _trend_metric(label, financials, row, noun) -> Metric:
    """Scores the year-over-year trend of a financials row, oldest year first.

    `noun` is lowercase for mid-sentence use; acronyms such as EPS stay as given.
    """
    subject = noun[0].upper() + noun[1:]

    if row not in financials.index:
        return Metric(label, NO_DATA, f"No historical {noun} data for this ticker.")

    series = financials.loc[row].dropna().iloc[::-1]
    if len(series) < 2:
        return Metric(label, NO_DATA, f"Too little history to establish a {noun} trend.")

    strictly_rising, no_declines = _growth_trend(series.values)
    value = f"{len(series)} yrs, {'rising' if no_declines else 'uneven'}"

    if strictly_rising:
        return Metric(label, value, f"{subject} rose every year.", 1)
    if no_declines:
        return Metric(label, value, f"{subject} is stable or rising, with no down years.", 1)
    return Metric(label, value, f"{subject} declined in at least one year.", 0)


def _finite(series):
    """Drop missing and infinite prints so no indicator is computed through them."""
    return series[series.map(lambda v: isinstance(v, Real) and math.isfinite(v))]


def _rsi(close, window: int = 14) -> float | None:
    """Relative Strength Index of the most recent close, or None if unavailable.

    RSI is undefined when the window has neither gains nor losses. A flat window
    is read as neutral (50) rather than the 100 the gain/loss ratio's limit
    would give, since an unchanged price is neither overbought nor oversold.
    """
    if not close.map(lambda v: math.isfinite(v)).all():
        return None
    delta = close.diff().dropna()
    if len(delta) < window:
        return None
    avg_gain = delta.clip(lower=0).rolling(window=window).mean().iloc[-1]
    avg_loss = (-delta.clip(upper=0)).rolling(window=window).mean().iloc[-1]

    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _available_info(info: dict) -> dict:
    """Keep nonfinite provider observations unavailable before scoring."""
    return {key: None if isinstance(value, Real) and not math.isfinite(value) else value
            for key, value in info.items()}


def analyze_long_term_investment(data: StockData) -> Analysis:
    """Scores fundamentals, valuation, balance sheet, moat, and dividend safety."""
    info = _available_info(data.info)
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    pb = info.get("priceToBook")
    roe = info.get("returnOnEquity")
    debt_to_equity = info.get("debtToEquity")
    margins = info.get("profitMargins")
    payout_ratio = info.get("payoutRatio")
    beta = info.get("beta")

    metrics = [
        _trend_metric("EPS Trend", data.financials, "Diluted EPS", "EPS"),
        _trend_metric("Revenue Trend", data.financials, "Total Revenue", "revenue"),
    ]

    # Forward vs trailing P/E signals the market's earnings expectation, but only
    # when both are positive: between losses the comparison has no growth reading.
    if trailing_pe is not None and forward_pe is not None and (trailing_pe <= 0 or forward_pe <= 0):
        metrics.append(Metric("Growth Outlook", f"{forward_pe:.1f} vs {trailing_pe:.1f}",
                              "Not comparable: one or both P/E ratios reflect losses."))
    elif trailing_pe is not None and forward_pe is not None:
        value = f"{forward_pe:.1f} vs {trailing_pe:.1f}"
        if forward_pe < trailing_pe:
            metrics.append(Metric("Growth Outlook", value, "EPS growth expected over the next 12 months.", 1))
        elif forward_pe > trailing_pe:
            metrics.append(Metric("Growth Outlook", value, "EPS decline expected over the next 12 months.", 0))
        else:
            metrics.append(Metric("Growth Outlook", value, "EPS expected to stay flat over the next 12 months.", 0.5))
    else:
        metrics.append(Metric("Growth Outlook", NO_DATA, "Forward or trailing P/E unavailable."))

    if trailing_pe is not None:
        value = f"{trailing_pe:.1f}"
        if trailing_pe <= 0:
            metrics.append(Metric("P/E Valuation", value, "Loss-making; a P/E this low reflects losses, not value.", 0))
        elif 15 <= trailing_pe <= 25:
            metrics.append(Metric("P/E Valuation", value, "Fairly valued (P/E 15-25).", 1))
        elif trailing_pe < 15:
            metrics.append(Metric("P/E Valuation", value, "Possibly undervalued (P/E < 15).", 1))
        else:
            metrics.append(Metric("P/E Valuation", value, "Possibly overvalued (P/E > 25); norms vary by sector.", 0))
    else:
        metrics.append(Metric("P/E Valuation", NO_DATA, "Trailing P/E unavailable."))

    if pb is not None:
        value = f"{pb:.2f}"
        if pb <= 0:
            metrics.append(Metric("P/B Valuation", value, "Negative equity; book value is below zero.", 0))
        elif 1.0 <= pb <= 3.0:
            metrics.append(Metric("P/B Valuation", value, "Fairly valued (P/B 1.0-3.0).", 1))
        elif pb < 1.0:
            metrics.append(Metric("P/B Valuation", value, "Possibly undervalued (P/B < 1.0), or financially unstable.", 1))
        else:
            metrics.append(Metric("P/B Valuation", value, "Possibly overvalued (P/B > 3.0); norms vary by sector.", 0))
    else:
        metrics.append(Metric("P/B Valuation", NO_DATA, "Price-to-book unavailable."))

    if roe is not None:
        value = f"{roe:.2%}"
        if 0.10 <= roe <= 0.20:
            metrics.append(Metric("Return on Equity", value, "Healthy capital efficiency (10-20%).", 1))
        elif roe > 0.20:
            metrics.append(Metric("Return on Equity", value, "Exceptional capital efficiency (>20%); check leverage is not inflating it.", 1))
        else:
            metrics.append(Metric("Return on Equity", value, "Weak capital efficiency (<10%).", 0))
    else:
        metrics.append(Metric("Return on Equity", NO_DATA, "Return on equity unavailable."))

    if debt_to_equity is not None:
        value = f"{debt_to_equity:.1f}"
        if debt_to_equity < 0:
            metrics.append(Metric("Debt-to-Equity", value, "Negative equity; liabilities exceed assets.", 0))
        elif debt_to_equity < 100:
            metrics.append(Metric("Debt-to-Equity", value, "Low leverage; better positioned for downturns.", 1))
        elif debt_to_equity <= 200:
            metrics.append(Metric("Debt-to-Equity", value, "Moderate leverage; norms vary by sector.", 0.5))
        else:
            metrics.append(Metric("Debt-to-Equity", value, "High leverage; greater risk in a downturn.", 0))
    else:
        metrics.append(Metric("Debt-to-Equity", NO_DATA, "Debt-to-equity unavailable."))

    # Sustained margins are a usable proxy for an economic moat.
    if margins is not None:
        value = f"{margins:.2%}"
        if margins >= 0.20:
            metrics.append(Metric("Profit Margin", value, "Strong margin; suggests a durable competitive advantage.", 1))
        elif margins >= 0.10:
            metrics.append(Metric("Profit Margin", value, "Healthy margin; reasonable pricing power.", 0.5))
        else:
            metrics.append(Metric("Profit Margin", value, "Thin margin; limited pricing power.", 0))
    else:
        metrics.append(Metric("Profit Margin", NO_DATA, "Profit margin unavailable."))

    if payout_ratio is not None and payout_ratio < 0:
        metrics.append(Metric("Dividend Payout", f"{payout_ratio:.2%}",
                              "Not applicable: a negative payout means dividends paid out of losses."))
    elif payout_ratio is not None:
        value = f"{payout_ratio:.2%}"
        if payout_ratio <= 0.60:
            metrics.append(Metric("Dividend Payout", value, "Sustainable payout; dividend unlikely to be cut.", 1))
        elif payout_ratio <= 0.70:
            metrics.append(Metric("Dividend Payout", value, "Borderline payout; monitor sustainability.", 0.5))
        else:
            metrics.append(Metric("Dividend Payout", value, "High payout; dividend at risk if earnings fall.", 0))
    else:
        metrics.append(Metric("Dividend Payout", NO_DATA, "No payout data; the stock may not pay a dividend."))

    if beta is not None:
        value = f"{beta:.2f}"
        if beta <= 1.2:
            metrics.append(Metric("Volatility (Beta)", value, "Stable price behavior, suited to a long hold.", 1))
        elif beta <= 1.8:
            metrics.append(Metric("Volatility (Beta)", value, "Moderately volatile; weigh against growth prospects.", 0.5))
        else:
            metrics.append(Metric("Volatility (Beta)", value, "Highly volatile, a trait of speculative stocks.", 0))
    else:
        metrics.append(Metric("Volatility (Beta)", NO_DATA, "Beta unavailable."))

    return Analysis(
        style="Long-term investment",
        metrics=metrics,
        verdicts={
            "strong": "Excellent fundamentals, consistent growth, and reasonable valuation for a long-term hold.",
            "moderate": "Decent fundamentals, but possibly overvalued or facing growth headwinds. Consider waiting for a better entry price.",
            "poor": "Weak fundamentals, poor efficiency, or severe overvaluation. Not recommended for a long-term hold right now.",
        },
    )


def analyze_short_term_trading(data: StockData) -> Analysis:
    """Scores trend, momentum, volatility bands, catalysts, and liquidity."""
    info = _available_info(data.info)
    close = data.close("6mo")
    metrics = []

    if close is not None and len(close) >= 50:
        sma20 = close.rolling(window=20).mean().iloc[-1]
        sma50 = close.rolling(window=50).mean().iloc[-1]
        price = close.iloc[-1]
        value = f"${price:.2f} | SMA20 ${sma20:.2f} | SMA50 ${sma50:.2f}"

        if price > sma20 > sma50:
            metrics.append(Metric("Moving Average Trend", value, "Strong uptrend: price above SMA20, SMA20 above SMA50.", 1))
        elif price > sma20:
            metrics.append(Metric("Moving Average Trend", value, "Short-term uptrend: price above SMA20 only.", 0.5))
        elif price < sma20 < sma50:
            metrics.append(Metric("Moving Average Trend", value, "Strong downtrend: price below SMA20, SMA20 below SMA50.", 0))
        else:
            metrics.append(Metric("Moving Average Trend", value, "Mixed signals, no clear trend.", 0))
    else:
        metrics.append(Metric("Moving Average Trend", NO_DATA, "Too little price history for moving averages."))

    rsi = _rsi(close) if close is not None else None
    if rsi is not None:
        value = f"{rsi:.1f}"
        if rsi < 30:
            metrics.append(Metric("RSI (14)", value, "Oversold; potential rebound.", 1))
        elif rsi > 70:
            metrics.append(Metric("RSI (14)", value, "Overbought; pullback risk.", 0.5))
        else:
            metrics.append(Metric("RSI (14)", value, "Neutral momentum.", 0.5))
    else:
        metrics.append(Metric("RSI (14)", NO_DATA, "Too little price history for RSI."))

    if close is not None and len(close) >= 20:
        window = close.rolling(window=20)
        mid = window.mean().iloc[-1]
        std = window.std().iloc[-1]
        upper, lower = mid + 2 * std, mid - 2 * std
        price = close.iloc[-1]
        value = f"${lower:.2f} - ${upper:.2f} | price ${price:.2f}"

        if price <= lower:
            metrics.append(Metric("Bollinger Bands", value, "At or below the lower band; potential upward reversal.", 1))
        elif price >= upper:
            metrics.append(Metric("Bollinger Bands", value, "At or above the upper band; potential downward reversal.", 0.5))
        else:
            metrics.append(Metric("Bollinger Bands", value, "Trading within its normal range.", 0.5))
    else:
        metrics.append(Metric("Bollinger Bands", NO_DATA, "Too little price history for Bollinger Bands."))

    earnings_date = data.earnings_date()
    if earnings_date:
        metrics.append(Metric("Catalyst (Earnings)", earnings_date, "Earnings ahead; expect volatility around the announcement.", 1))
    else:
        metrics.append(Metric("Catalyst (Earnings)", NO_DATA, "No confirmed upcoming earnings date from the provider."))

    avg_volume = info.get("averageVolume")
    if avg_volume is not None:
        value = f"{avg_volume:,}"
        if avg_volume >= 1_000_000:
            metrics.append(Metric("Average Volume", value, "Healthy volume; easy to enter and exit.", 1))
        elif avg_volume >= 100_000:
            metrics.append(Metric("Average Volume", value, "Moderate volume; trade with caution.", 0.5))
        else:
            metrics.append(Metric("Average Volume", value, "Low volume; risk of slippage.", 0))
    else:
        metrics.append(Metric("Average Volume", NO_DATA, "Average volume unavailable."))

    # A quote where ask <= bid is crossed, meaning the feed is stale rather than tight.
    bid, ask = info.get("bid"), info.get("ask")
    if bid is not None and ask is not None and bid > 0 and ask > bid:
        spread = (ask - bid) / bid
        value = f"{spread:.2%} (bid ${bid:.2f} / ask ${ask:.2f})"
        if spread <= 0.001:
            metrics.append(Metric("Bid-Ask Spread", value, "Tight spread; favorable for quick trades.", 1))
        elif spread <= 0.005:
            metrics.append(Metric("Bid-Ask Spread", value, "Moderate spread.", 0.5))
        else:
            metrics.append(Metric("Bid-Ask Spread", value, "Wide spread; eats into profits.", 0))
    else:
        metrics.append(Metric("Bid-Ask Spread", NO_DATA,
                              "No usable bid/ask quote; markets may be closed or the feed is stale."))

    return Analysis(
        style="Short-term trading",
        metrics=metrics,
        verdicts={
            "strong": "Favorable trend, momentum, and liquidity for short-term trading.",
            "moderate": "Some favorable signals, but watch the risk factors before entry.",
            "poor": "Weak technical setup or liquidity. Not recommended for short-term trading right now.",
        },
    )


def analyze_day_trading(data: StockData, base: Analysis) -> Analysis:
    """Scores intraday volatility, volume depth, and same-day catalysts."""
    info = _available_info(data.info)
    history = data.history("1mo")
    metrics = []

    if not history.empty and len(history) >= 10:
        daily_range = ((history["High"] - history["Low"]) / history["Close"]).tail(10).mean()
        value = f"{daily_range:.2%} avg daily range"
        if daily_range >= 0.04:
            metrics.append(Metric("Intraday Volatility", value, "High swings; strong profit potential and higher risk.", 1))
        elif daily_range >= 0.02:
            metrics.append(Metric("Intraday Volatility", value, "Moderate swings.", 0.5))
        else:
            metrics.append(Metric("Intraday Volatility", value, "Narrow swings; limited intraday opportunity.", 0))
    else:
        metrics.append(Metric("Intraday Volatility", NO_DATA, "Too little price history for a daily range."))

    beta = info.get("beta")
    if beta is not None:
        value = f"{beta:.2f}"
        if beta >= 1.5:
            metrics.append(Metric("Beta", value, "Moves far more than the market; good for intraday range.", 1))
        elif beta >= 1.0:
            metrics.append(Metric("Beta", value, "Moves somewhat more than the market.", 0.5))
        else:
            metrics.append(Metric("Beta", value, "Moves less than the market; little intraday range.", 0))
    else:
        metrics.append(Metric("Beta", NO_DATA, "Beta unavailable."))

    # Day trading needs far more volume than general short-term trading.
    avg_volume = info.get("averageVolume")
    if avg_volume is not None:
        value = f"{avg_volume:,}"
        if avg_volume >= 5_000_000:
            metrics.append(Metric("Volume Depth", value, "Excellent liquidity; minimal slippage expected.", 1))
        elif avg_volume >= 1_000_000:
            metrics.append(Metric("Volume Depth", value, "Acceptable liquidity for intraday size.", 0.5))
        else:
            metrics.append(Metric("Volume Depth", value, "Too thin for intraday trading; high slippage risk.", 0))
    else:
        metrics.append(Metric("Volume Depth", NO_DATA, "Average volume unavailable."))

    change_pct = info.get("regularMarketChangePercent")
    if change_pct is not None:
        value = f"{change_pct:+.2f}% today"
        if abs(change_pct) >= 3:
            metrics.append(Metric("Live Catalyst", value, "Large move today; check news feeds for the trigger.", 1))
        else:
            metrics.append(Metric("Live Catalyst", value, "Modest move today; no major catalyst detected.", 0))
    else:
        metrics.append(Metric("Live Catalyst", NO_DATA, "Real-time price change unavailable."))

    return Analysis(
        style="Day trading",
        metrics=metrics,
        base=base,
        notes=["Daily data lags intraday action. Check pre-market, post-market, and live news before trading."],
        verdicts={
            "strong": "High volatility and deep liquidity make this attractive for day trading.",
            "moderate": "Workable volatility or liquidity, but confirm with live data before trading.",
            "poor": "Too little volatility or liquidity for day trading right now.",
        },
    )


def analyze_swing_trading(data: StockData, base: Analysis) -> Analysis:
    """Scores support/resistance, consolidation, reversal patterns, and timeframe alignment."""
    close = data.close("1y")
    metrics = []

    if close is not None and len(close) >= 60:
        window = close.tail(60)
        support, resistance = window.min(), window.max()
        price = close.iloc[-1]
        value = f"support ${support:.2f} | price ${price:.2f} | resistance ${resistance:.2f}"

        if (price - support) / support <= 0.03:
            metrics.append(Metric("Support / Resistance", value, "Near support; favorable risk-to-reward entry.", 1))
        elif (resistance - price) / resistance <= 0.03:
            metrics.append(Metric("Support / Resistance", value, "Near resistance; take profit or wait for a breakout.", 0.5))
        else:
            metrics.append(Metric("Support / Resistance", value, "Mid-range; no clear reversal level nearby.", 0.5))
    else:
        metrics.append(Metric("Support / Resistance", NO_DATA, "Too little price history for support and resistance."))

    # A narrowing Bollinger Band width marks consolidation before a breakout.
    if close is not None and len(close) >= 80:
        rolling20 = close.rolling(window=20)
        bb_width = (4 * rolling20.std()) / rolling20.mean()
        now, past = bb_width.iloc[-1], bb_width.iloc[-60]
        value = f"{now:.2%} now vs {past:.2%} 60d ago"

        if now < past * 0.7:
            metrics.append(Metric("Consolidation", value, "Range tightened sharply; breakout potential building.", 1))
        elif now < past:
            metrics.append(Metric("Consolidation", value, "Range narrowing moderately.", 0.5))
        else:
            metrics.append(Metric("Consolidation", value, "Range expanding; no consolidation.", 0))
    else:
        metrics.append(Metric("Consolidation", NO_DATA, "Too little price history to assess consolidation."))

    if close is not None and len(close) >= 40:
        recent = close.tail(90)
        peaks = recent[(recent.shift(1) < recent) & (recent.shift(-1) < recent)]
        troughs = recent[(recent.shift(1) > recent) & (recent.shift(-1) > recent)]

        def _is_double(points) -> bool:
            """True if the last two swing points sit within 3% of each other."""
            if len(points) < 2:
                return False
            first, second = points.iloc[-2], points.iloc[-1]
            return abs(second - first) / first <= 0.03

        if _is_double(troughs):
            metrics.append(Metric("Reversal Pattern", "double bottom", "Bullish reversal signal from matched swing lows.", 1))
        elif _is_double(peaks):
            metrics.append(Metric("Reversal Pattern", "double top", "Bearish reversal signal from matched swing highs.", 1))
        else:
            metrics.append(Metric("Reversal Pattern", "none", "No clear double top or bottom recently.", 0.5))
    else:
        metrics.append(Metric("Reversal Pattern", NO_DATA, "Too little price history to detect reversal patterns."))

    weekly = close.resample("W").last().dropna() if close is not None else None
    if weekly is not None and len(close) >= 100 and len(weekly) >= 10:
        daily_up = close.iloc[-1] > close.rolling(window=20).mean().iloc[-1]
        weekly_up = weekly.iloc[-1] > weekly.rolling(window=10).mean().iloc[-1]
        value = f"daily {'up' if daily_up else 'down'} | weekly {'up' if weekly_up else 'down'}"

        if daily_up == weekly_up:
            direction = "upward" if daily_up else "downward"
            metrics.append(Metric("Timeframe Alignment", value, f"Daily and weekly trends both {direction}; strong confirmation.", 1))
        else:
            metrics.append(Metric("Timeframe Alignment", value, "Daily and weekly trends conflict; low-confidence setup.", 0))
    else:
        metrics.append(Metric("Timeframe Alignment", NO_DATA, "Too little price history to compare timeframes."))

    return Analysis(
        style="Swing trading",
        metrics=metrics,
        base=base,
        verdicts={
            "strong": "Favorable levels, pattern, and timeframe alignment for a multi-day swing.",
            "moderate": "Some favorable swing signals, but confirm on the chart before entry.",
            "poor": "Weak setup, conflicting trends, or no clear pattern for swing trading right now.",
        },
    )


def _rule(width: int) -> str:
    return "-" * width


def _dotted(label: str, width: int) -> str:
    return f"{label} ".ljust(width, ".")


def print_header(data: StockData) -> None:
    price = data.price
    title = f"{data.symbol} - {data.name}"
    if price is not None:
        title += f" - ${price:,.2f}"
    print(f"\n{title}")
    print(_rule(len(title)))


def print_concise(analyses: list[Analysis]) -> None:
    """One line per trading style: score percentage and verdict label."""
    width = max(len(a.style) for a in analyses) + 2
    print()
    for analysis in analyses:
        rate = "  --" if analysis.indicator_score is None else f"{analysis.indicator_score:>4.0%}"
        print(f"  {_dotted(analysis.style, width)} Indicator score {rate}   "
              f"{RATING_LABELS[analysis.rating]}   "
              f"{analysis.evaluated_count}/{analysis.total_count} evaluated "
              f"({analysis.coverage:.0%} coverage)")
    print("\n  Run with -v for per-metric reasoning, -i for raw indicators.")


def print_verbose(analyses: list[Analysis]) -> None:
    """Every metric with its reading, interpretation, and score contribution."""
    for analysis in analyses:
        rate = "--" if analysis.indicator_score is None else f"{analysis.indicator_score:.0%}"
        heading = f"{analysis.style}  -  Indicator score {rate}  {RATING_LABELS[analysis.rating]}"
        print(f"\n{heading}")
        print(_rule(len(heading)))

        if analysis.base:
            shared = analysis.base
            print(f"  Includes the {shared.style.lower()} signals above "
                  f"({shared.score:g}/{len(shared.evaluated)} points).")

        for metric in analysis.metrics:
            points = " --" if not metric.is_evaluated else f"{metric.score:g}"
            print(f"  [{points:>3}] {_dotted(metric.label, 24)} {metric.value}")
            print(f"         {metric.detail}")

        print(f"  Indicator score: {analysis.score:g} of {analysis.evaluated_count} points ({rate}).")
        print(f"  Coverage: {analysis.evaluated_count}/{analysis.total_count} evaluated "
              f"({analysis.coverage:.0%}).")
        print(f"  Verdict: {analysis.verdict}")
        for note in analysis.notes:
            print(f"  Note: {note}")


def print_info(analyses: list[Analysis]) -> None:
    """Raw indicator readings only, with no scores or verdicts."""
    for analysis in analyses:
        if analysis.base:
            heading = f"{analysis.style} indicators (plus the {analysis.base.style.lower()} indicators above)"
        else:
            heading = f"{analysis.style} indicators"
        print(f"\n{heading}")
        print(_rule(len(heading)))
        for metric in analysis.metrics:
            print(f"  {_dotted(metric.label, 24)} {metric.value}")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score a stock's fit for long-term, short-term, day, and swing trading.",
    )
    parser.add_argument("ticker", nargs="?", help="stock ticker symbol (prompted for if omitted)")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("-v", "--verbose", action="store_true",
                        help="show every metric with its reading and reasoning")
    output.add_argument("-i", "--info", action="store_true",
                        help="show the raw indicator readings only, with no verdicts")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    symbol = (args.ticker or input("Enter stock ticker symbol: ")).strip().upper()
    if not symbol:
        print("Invalid ticker symbol.")
        return 1

    data = StockData(symbol)
    if not data.info.get("symbol") and data.price is None:
        print(f"No data found for '{symbol}'.")
        return 1

    short_term = analyze_short_term_trading(data)
    analyses = [
        analyze_long_term_investment(data),
        short_term,
        analyze_day_trading(data, base=short_term),
        analyze_swing_trading(data, base=short_term),
    ]

    print_header(data)
    if args.info:
        print_info(analyses)
    elif args.verbose:
        print_verbose(analyses)
    else:
        print_concise(analyses)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
