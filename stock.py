import yfinance as yf

def _consistent_growth_trend(series):
    """
    Returns (consecutive_growth, is_consistent) for a chronologically ordered numeric series:
    consecutive_growth is True if every period strictly increased,
    is_consistent is True if no period declined (flat periods allowed).
    """
    consecutive_growth = True
    is_consistent = True

    for i in range(len(series) - 1):
        if series[i+1] < series[i]:
            consecutive_growth = False
            is_consistent = False
        elif series[i+1] == series[i]:
            consecutive_growth = False

    return consecutive_growth, is_consistent


def analyze_long_term_investment(ticker_obj, trailing_pe, forward_pe, pb, roe, debt_to_equity=None,
                                  profit_margins=None, payout_ratio=None, beta=None):
    """
    Performs long-term investment analysis based on valuation, efficiency, balance sheet health,
    competitive advantage, and dividend stability, and returns a final verdict on whether the
    stock is a good long-term investment.
    """
    print("\n===== Long-term Investment Analysis =====")

    score = 0
    evaluated_metrics = 0

    financials = ticker_obj.financials

    # 1. Historical EPS Consistency and Growth Analysis
    if "Diluted EPS" in financials.index:
        # Extract and sort EPS from oldest to newest
        eps_series = financials.loc["Diluted EPS"].dropna().iloc[::-1]

        if len(eps_series) >= 2:
            evaluated_metrics += 1
            consecutive_growth, is_consistent = _consistent_growth_trend(eps_series.values)

            if consecutive_growth:
                print("Historical EPS Trend: Exceptional! EPS has consistently increased year-over-year.")
                score += 1
            elif is_consistent:
                print("Historical EPS Trend: Consistent performance. EPS is stable or rising, with no down years.")
                score += 1
            else:
                print("Historical EPS Trend: Poor consistency. EPS has experienced year-over-year declines.")
        else:
            print("Historical EPS Trend: Insufficient historical data to establish an EPS trend.")
    else:
        print("Historical EPS Trend: Historical Diluted EPS data is unavailable for this ticker.")

    # 2. Historical Revenue Growth Analysis
    if "Total Revenue" in financials.index:
        revenue_series = financials.loc["Total Revenue"].dropna().iloc[::-1]

        if len(revenue_series) >= 2:
            evaluated_metrics += 1
            consecutive_growth, is_consistent = _consistent_growth_trend(revenue_series.values)

            if consecutive_growth:
                print("Historical Revenue Trend: Exceptional! Revenue has consistently increased year-over-year.")
                score += 1
            elif is_consistent:
                print("Historical Revenue Trend: Consistent performance. Revenue is stable or rising, with no down years.")
                score += 1
            else:
                print("Historical Revenue Trend: Poor consistency. Revenue has experienced year-over-year declines, a sign of speculative or unstable business performance.")
        else:
            print("Historical Revenue Trend: Insufficient historical data to establish a revenue trend.")
    else:
        print("Historical Revenue Trend: Historical Total Revenue data is unavailable for this ticker.")

    # 3. Forward vs Trailing P/E Logic (Growth Outlook)
    if trailing_pe is not None and forward_pe is not None:
        evaluated_metrics += 1
        if forward_pe < trailing_pe:
            print("Growth Outlook: Expected growth over next 12 months (Forward P/E < Trailing P/E).")
            score += 1
        elif forward_pe > trailing_pe:
            print("Growth Outlook: Expected decline over next 12 months (Forward P/E > Trailing P/E).")
        else:
            print("Growth Outlook: EPS to remain flat over next 12 months (Forward P/E = Trailing P/E).")
            score += 0.5  # Partial credit for stability

    # 4. P/E Valuation Ranges
    if trailing_pe is not None:
        evaluated_metrics += 1
        if 15 <= trailing_pe <= 25:
            print("P/E Valuation: Stock is fairly valued (P/E 15-25).")
            score += 1
        elif trailing_pe < 15:
            print("P/E Valuation: Stock may be undervalued (P/E < 15).")
            score += 1
        else:
            print("P/E Valuation: Stock may be overvalued (P/E > 25).")
    else:
        print("P/E Valuation: Insufficient P/E data available.")

    # 5. P/B Evaluation
    if pb is not None:
        evaluated_metrics += 1
        if 1.0 <= pb <= 3.0:
            print("P/B Valuation: Stock is fairly valued (P/B 1.0-3.0).")
            score += 1
        elif pb < 1.0:
            print("P/B Valuation: Stock may be undervalued (P/B < 1.0).")
            score += 1
        else:
            print("P/B Valuation: Stock may be overvalued (P/B > 3.0).")
    else:
        print("P/B Valuation: Insufficient P/B data available.")

    # 6. ROE Evaluation (Management Efficiency)
    if roe is not None:
        evaluated_metrics += 1
        if 0.10 <= roe <= 0.20:
            print("ROE: Healthy capital efficiency (10%-20%).")
            score += 1
        elif roe > 0.20:
            print("ROE: Exceptional capital efficiency (>20%). Strong competitive advantage.")
            score += 1
        else:
            print("ROE: Poor capital efficiency (<10%). Management is struggling to generate returns.")
    else:
        print("ROE: Insufficient ROE data available.")

    # 7. Debt-to-Equity Evaluation (Balance Sheet Strength)
    if debt_to_equity is not None:
        evaluated_metrics += 1
        if debt_to_equity < 100:
            print(f"Debt-to-Equity: Low leverage ({debt_to_equity:.1f}). Solid balance sheet, better positioned for downturns.")
            score += 1
        elif debt_to_equity <= 200:
            print(f"Debt-to-Equity: Moderate leverage ({debt_to_equity:.1f}). Acceptable, but norms vary by sector.")
            score += 0.5
        else:
            print(f"Debt-to-Equity: High leverage ({debt_to_equity:.1f}). Greater risk during economic downturns.")
    else:
        print("Debt-to-Equity: Insufficient debt-to-equity data available.")

    # 8. Profit Margin Evaluation (Competitive Advantage / Economic Moat Proxy)
    if profit_margins is not None:
        evaluated_metrics += 1
        if profit_margins >= 0.20:
            print(f"Profit Margin: Strong ({profit_margins:.2%}). Suggests a durable competitive advantage (moat).")
            score += 1
        elif profit_margins >= 0.10:
            print(f"Profit Margin: Healthy ({profit_margins:.2%}). Reasonable pricing power or cost control.")
            score += 0.5
        else:
            print(f"Profit Margin: Thin ({profit_margins:.2%}). Limited pricing power or weak competitive position.")
    else:
        print("Profit Margin: Insufficient profit margin data available.")

    # 9. Dividend Payout Ratio Evaluation (Income Stability)
    if payout_ratio is not None:
        evaluated_metrics += 1
        if payout_ratio <= 0.60:
            print(f"Dividend Payout Ratio: Sustainable ({payout_ratio:.2%}). Dividend unlikely to be cut.")
            score += 1
        elif payout_ratio <= 0.70:
            print(f"Dividend Payout Ratio: Borderline ({payout_ratio:.2%}). Monitor for sustainability.")
            score += 0.5
        else:
            print(f"Dividend Payout Ratio: High ({payout_ratio:.2%}). Dividend may be at risk if earnings decline.")
    else:
        print("Dividend Payout Ratio: No dividend payout data available (may not pay a dividend).")

    # 10. Speculation Check (Beta as a Volatility Proxy)
    if beta is not None:
        evaluated_metrics += 1
        if beta <= 1.2:
            print(f"Volatility (Beta): {beta:.2f}. Stable price behavior, consistent with a long-term holding.")
            score += 1
        elif beta <= 1.8:
            print(f"Volatility (Beta): {beta:.2f}. Moderately volatile; weigh against growth prospects.")
            score += 0.5
        else:
            print(f"Volatility (Beta): {beta:.2f}. Highly volatile, a trait of speculative stocks.")
    else:
        print("Volatility (Beta): Insufficient beta data available.")

    # 11. Final Decision Logic
    print("\n===== FINAL VERDICT =====")
    if evaluated_metrics == 0:
        print("Result: INCONCLUSIVE. Not enough financial data available to make a determination.")
        return

    win_rate = score / evaluated_metrics
    print(f"Score: {score} out of {evaluated_metrics} evaluated metrics passed ({win_rate:.0%}).")

    if win_rate >= 0.8:
        print("Verdict: STRONG CANDIDATE. The stock exhibits excellent fundamentals, consistent growth, and reasonable valuation for a long-term hold.")
    elif win_rate >= 0.6:
        print("Verdict: MODERATE CANDIDATE. The stock has decent fundamentals but may be slightly overvalued or facing short-term growth headwinds. Consider waiting for a better entry price.")
    else:
        print("Verdict: POOR CANDIDATE. The stock shows weak fundamentals, poor efficiency, or severe overvaluation. Not recommended for a long-term investment right now.")


def analyze_short_term_investment(ticker_obj, info):
    """
    Performs short-term trading analysis based on technical indicators, catalysts, and liquidity,
    and returns a final verdict on whether the stock is good for short-term trading.
    """
    print("\n===== Short-term Trading Analysis =====")

    score = 0
    evaluated_metrics = 0

    history = ticker_obj.history(period="6mo")
    close = history["Close"].dropna() if not history.empty else None

    # 1. Moving Average Trend
    if close is not None and len(close) >= 50:
        evaluated_metrics += 1
        sma20 = close.rolling(window=20).mean().iloc[-1]
        sma50 = close.rolling(window=50).mean().iloc[-1]
        last_price = close.iloc[-1]

        if last_price > sma20 > sma50:
            print("Moving Average Trend: Strong uptrend (price above SMA20, SMA20 above SMA50).")
            score += 1
        elif last_price > sma20:
            print("Moving Average Trend: Short-term uptrend (price above SMA20).")
            score += 0.5
        elif last_price < sma20 < sma50:
            print("Moving Average Trend: Strong downtrend (price below SMA20, SMA20 below SMA50).")
        else:
            print("Moving Average Trend: Mixed signals, no clear trend.")
    else:
        print("Moving Average Trend: Insufficient price history to compute moving averages.")

    # 2. RSI (Relative Strength Index)
    if close is not None and len(close) >= 15:
        evaluated_metrics += 1
        delta = close.diff().dropna()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean().iloc[-1]
        avg_loss = loss.rolling(window=14).mean().iloc[-1]

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        if rsi < 30:
            print(f"RSI: {rsi:.1f}. Oversold, potential rebound opportunity.")
            score += 1
        elif rsi > 70:
            print(f"RSI: {rsi:.1f}. Overbought, potential pullback risk.")
            score += 0.5
        else:
            print(f"RSI: {rsi:.1f}. Neutral momentum.")
            score += 0.5
    else:
        print("RSI: Insufficient price history to compute RSI.")

    # 3. Bollinger Bands (Volatility and Reversal Points)
    if close is not None and len(close) >= 20:
        evaluated_metrics += 1
        window = close.rolling(window=20)
        mid_band = window.mean().iloc[-1]
        std_dev = window.std().iloc[-1]
        upper_band = mid_band + (2 * std_dev)
        lower_band = mid_band - (2 * std_dev)
        last_price = close.iloc[-1]

        if last_price <= lower_band:
            print("Bollinger Bands: Price at/below lower band. Potential reversal upward.")
            score += 1
        elif last_price >= upper_band:
            print("Bollinger Bands: Price at/above upper band. Potential reversal downward.")
            score += 0.5
        else:
            print("Bollinger Bands: Price trading within normal range.")
            score += 0.5
    else:
        print("Bollinger Bands: Insufficient price history to compute Bollinger Bands.")

    # 4. Catalysts (Upcoming Earnings)
    try:
        calendar = ticker_obj.calendar
        has_earnings_date = bool(calendar) and (
            "Earnings Date" in calendar if isinstance(calendar, dict) else not calendar.empty
        )
    except Exception:
        has_earnings_date = False

    evaluated_metrics += 1
    if has_earnings_date:
        print("Catalysts: Upcoming earnings date found. Watch for volatility around the announcement.")
        score += 1
    else:
        print("Catalysts: No confirmed upcoming earnings date found.")

    # 5. Liquidity (Volume and Bid-Ask Spread)
    avg_volume = info.get("averageVolume")
    bid = info.get("bid")
    ask = info.get("ask")

    if avg_volume is not None:
        evaluated_metrics += 1
        if avg_volume >= 1_000_000:
            print(f"Liquidity: Healthy average volume ({avg_volume:,}). Easy to enter/exit positions.")
            score += 1
        elif avg_volume >= 100_000:
            print(f"Liquidity: Moderate average volume ({avg_volume:,}). Trade with caution.")
            score += 0.5
        else:
            print(f"Liquidity: Low average volume ({avg_volume:,}). Risk of slippage on entry/exit.")
    else:
        print("Liquidity: Insufficient volume data available.")

    if bid is not None and ask is not None and bid > 0:
        evaluated_metrics += 1
        spread_pct = (ask - bid) / bid
        if spread_pct <= 0.001:
            print(f"Bid-Ask Spread: Tight spread ({spread_pct:.2%}). Favorable for quick trades.")
            score += 1
        elif spread_pct <= 0.005:
            print(f"Bid-Ask Spread: Moderate spread ({spread_pct:.2%}).")
            score += 0.5
        else:
            print(f"Bid-Ask Spread: Wide spread ({spread_pct:.2%}). Could eat into profits.")
    else:
        print("Bid-Ask Spread: Insufficient bid/ask data available.")

    # 6. Final Decision Logic
    print("\n===== FINAL VERDICT =====")
    if evaluated_metrics == 0:
        print("Result: INCONCLUSIVE. Not enough data available to make a determination.")
        return

    win_rate = score / evaluated_metrics
    print(f"Score: {score} out of {evaluated_metrics} evaluated metrics passed ({win_rate:.0%}).")

    if win_rate >= 0.8:
        print("Verdict: STRONG CANDIDATE. Favorable trend, momentum, and liquidity for short-term trading.")
    elif win_rate >= 0.6:
        print("Verdict: MODERATE CANDIDATE. Some favorable signals, but watch for risk factors before entry.")
    else:
        print("Verdict: POOR CANDIDATE. Weak technical setup or liquidity. Not recommended for short-term trading right now.")


def analyze_day_trading(ticker_obj, info):
    """
    Performs day-trading analysis based on intraday volatility, volume, and real-time catalysts,
    on top of the general short-term trading checks, and returns a final verdict.
    """
    analyze_short_term_investment(ticker_obj, info)

    print("\n===== Day-Trading Analysis =====")

    score = 0
    evaluated_metrics = 0

    history = ticker_obj.history(period="1mo")

    # 1. Intraday Volatility (Average Daily Range)
    if not history.empty and len(history) >= 10:
        evaluated_metrics += 1
        daily_range_pct = (history["High"] - history["Low"]) / history["Close"]
        avg_range_pct = daily_range_pct.tail(10).mean()

        if avg_range_pct >= 0.04:
            print(f"Intraday Volatility: High (avg daily range {avg_range_pct:.2%}). Strong profit potential, but higher risk.")
            score += 1
        elif avg_range_pct >= 0.02:
            print(f"Intraday Volatility: Moderate (avg daily range {avg_range_pct:.2%}).")
            score += 0.5
        else:
            print(f"Intraday Volatility: Low (avg daily range {avg_range_pct:.2%}). Limited intraday profit opportunity.")
    else:
        print("Intraday Volatility: Insufficient price history to compute daily range.")

    # 2. Beta (Volatility Relative to Market)
    beta = info.get("beta")
    if beta is not None:
        evaluated_metrics += 1
        if beta >= 1.5:
            print(f"Beta: {beta:.2f}. Highly volatile relative to the market.")
            score += 1
        elif beta >= 1.0:
            print(f"Beta: {beta:.2f}. Moderately volatile relative to the market.")
            score += 0.5
        else:
            print(f"Beta: {beta:.2f}. Low volatility relative to the market.")
    else:
        print("Beta: Insufficient beta data available.")

    # 3. High-Volume Threshold (Stricter than general short-term trading)
    avg_volume = info.get("averageVolume")
    if avg_volume is not None:
        evaluated_metrics += 1
        if avg_volume >= 5_000_000:
            print(f"Day-Trading Volume: Excellent liquidity ({avg_volume:,}). Minimal slippage expected.")
            score += 1
        elif avg_volume >= 1_000_000:
            print(f"Day-Trading Volume: Acceptable liquidity ({avg_volume:,}).")
            score += 0.5
        else:
            print(f"Day-Trading Volume: Low liquidity ({avg_volume:,}). High risk of slippage for intraday trades.")
    else:
        print("Day-Trading Volume: Insufficient volume data available.")

    # 4. Real-Time Catalysts (Today's Price Action as a Proxy)
    change_pct = info.get("regularMarketChangePercent")
    if change_pct is not None:
        evaluated_metrics += 1
        if abs(change_pct) >= 3:
            print(f"Real-Time Catalyst: Large move today ({change_pct:.2f}%). Check news feeds for the trigger.")
            score += 1
        else:
            print(f"Real-Time Catalyst: Modest move today ({change_pct:.2f}%). No major catalyst detected.")
    else:
        print("Real-Time Catalyst: Insufficient real-time price change data available.")
    print("Reminder: Review pre-market/post-market data and live news feeds before trading; these signals update faster than daily data.")

    # 5. Final Decision Logic
    print("\n===== DAY-TRADING VERDICT =====")
    if evaluated_metrics == 0:
        print("Result: INCONCLUSIVE. Not enough data available to make a determination.")
        return

    win_rate = score / evaluated_metrics
    print(f"Score: {score} out of {evaluated_metrics} evaluated metrics passed ({win_rate:.0%}).")

    if win_rate >= 0.8:
        print("Verdict: STRONG CANDIDATE. High volatility and liquidity make this attractive for day trading.")
    elif win_rate >= 0.6:
        print("Verdict: MODERATE CANDIDATE. Some volatility or liquidity, but confirm with live data before trading.")
    else:
        print("Verdict: POOR CANDIDATE. Insufficient volatility or liquidity for day trading right now.")


def analyze_swing_trading(ticker_obj, info):
    """
    Performs swing-trading analysis based on support/resistance, consolidation, reversal patterns,
    and multi-timeframe trend alignment, on top of the general short-term trading checks,
    and returns a final verdict.
    """
    analyze_short_term_investment(ticker_obj, info)

    print("\n===== Swing-Trading Analysis =====")

    score = 0
    evaluated_metrics = 0

    history = ticker_obj.history(period="1y")
    close = history["Close"].dropna() if not history.empty else None

    # 1. Support and Resistance Proximity
    if close is not None and len(close) >= 60:
        evaluated_metrics += 1
        window = close.tail(60)
        resistance = window.max()
        support = window.min()
        last_price = close.iloc[-1]
        dist_to_support = (last_price - support) / support
        dist_to_resistance = (resistance - last_price) / resistance

        if dist_to_support <= 0.03:
            print(f"Support/Resistance: Price near support (${support:.2f}). Favorable risk-to-reward entry point.")
            score += 1
        elif dist_to_resistance <= 0.03:
            print(f"Support/Resistance: Price near resistance (${resistance:.2f}). Consider taking profit or waiting for a breakout.")
            score += 0.5
        else:
            print(f"Support/Resistance: Price trading mid-range (support ${support:.2f}, resistance ${resistance:.2f}).")
            score += 0.5
    else:
        print("Support/Resistance: Insufficient price history to identify support/resistance levels.")

    # 2. Price Consolidation (Bollinger Band Width Narrowing)
    if close is not None and len(close) >= 80:
        evaluated_metrics += 1
        rolling20 = close.rolling(window=20)
        bb_width = (4 * rolling20.std()) / rolling20.mean()
        current_width = bb_width.iloc[-1]
        past_width = bb_width.iloc[-60]

        if current_width < past_width * 0.7:
            print(f"Price Consolidation: Range has tightened significantly ({current_width:.2%} vs {past_width:.2%}). Breakout potential building.")
            score += 1
        elif current_width < past_width:
            print(f"Price Consolidation: Range is narrowing moderately ({current_width:.2%} vs {past_width:.2%}).")
            score += 0.5
        else:
            print(f"Price Consolidation: Range is expanding ({current_width:.2%} vs {past_width:.2%}). No clear consolidation.")
    else:
        print("Price Consolidation: Insufficient price history to assess consolidation.")

    # 3. Trend Reversal Pattern Detection (Double Top / Double Bottom)
    if close is not None and len(close) >= 40:
        evaluated_metrics += 1
        recent = close.tail(90) if len(close) >= 90 else close
        is_peak = (recent.shift(1) < recent) & (recent.shift(-1) < recent)
        is_trough = (recent.shift(1) > recent) & (recent.shift(-1) > recent)
        peaks = recent[is_peak]
        troughs = recent[is_trough]
        pattern_found = False

        if len(peaks) >= 2:
            last_two_peaks = peaks.iloc[-2:]
            if abs(last_two_peaks.iloc[1] - last_two_peaks.iloc[0]) / last_two_peaks.iloc[0] <= 0.03:
                print("Reversal Pattern: Possible double top detected. Bearish reversal signal.")
                score += 1
                pattern_found = True

        if not pattern_found and len(troughs) >= 2:
            last_two_troughs = troughs.iloc[-2:]
            if abs(last_two_troughs.iloc[1] - last_two_troughs.iloc[0]) / last_two_troughs.iloc[0] <= 0.03:
                print("Reversal Pattern: Possible double bottom detected. Bullish reversal signal.")
                score += 1
                pattern_found = True

        if not pattern_found:
            print("Reversal Pattern: No clear double top/bottom pattern detected recently.")
            score += 0.5
    else:
        print("Reversal Pattern: Insufficient price history to detect reversal patterns.")

    # 4. Timeframe Alignment (Daily vs Weekly Trend)
    if close is not None and len(close) >= 100:
        weekly_close = close.resample("W").last().dropna()
        if len(weekly_close) >= 10:
            evaluated_metrics += 1
            daily_sma20 = close.rolling(window=20).mean().iloc[-1]
            daily_trend_up = close.iloc[-1] > daily_sma20
            weekly_sma10 = weekly_close.rolling(window=10).mean().iloc[-1]
            weekly_trend_up = weekly_close.iloc[-1] > weekly_sma10

            if daily_trend_up == weekly_trend_up:
                direction = "upward" if daily_trend_up else "downward"
                print(f"Timeframe Alignment: Daily and weekly trends both {direction}. Strong confirmation for a swing entry.")
                score += 1
            else:
                print("Timeframe Alignment: Daily and weekly trends conflict. Lower-confidence setup, trade with caution.")
        else:
            print("Timeframe Alignment: Insufficient weekly data to assess trend alignment.")
    else:
        print("Timeframe Alignment: Insufficient price history to assess trend alignment.")

    # 5. Final Decision Logic
    print("\n===== SWING-TRADING VERDICT =====")
    if evaluated_metrics == 0:
        print("Result: INCONCLUSIVE. Not enough data available to make a determination.")
        return

    win_rate = score / evaluated_metrics
    print(f"Score: {score} out of {evaluated_metrics} evaluated metrics passed ({win_rate:.0%}).")

    if win_rate >= 0.8:
        print("Verdict: STRONG CANDIDATE. Favorable support/resistance setup, pattern, and timeframe alignment for swing trading.")
    elif win_rate >= 0.6:
        print("Verdict: MODERATE CANDIDATE. Some favorable swing signals, but confirm with chart patterns before entry.")
    else:
        print("Verdict: POOR CANDIDATE. Weak setup, conflicting trends, or no clear pattern for swing trading right now.")


def main():
    # Prompt user for ticker input
    symbol = input("Enter stock ticker symbol: ").strip().upper()
    if not symbol:
        print("Invalid ticker symbol.")
        return

    ticker = yf.Ticker(symbol)
    info = ticker.info

    # Fetch data safely from the info dictionary
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    pb = info.get("priceToBook")
    roe = info.get("returnOnEquity")
    debt_to_equity = info.get("debtToEquity")
    profit_margins = info.get("profitMargins")
    payout_ratio = info.get("payoutRatio")
    beta = info.get("beta")

    print(f"\nStock: {symbol}")
    print(f"Trailing P/E: {trailing_pe}")
    print(f"Forward P/E: {forward_pe}")
    print(f"P/B ratio: {pb}")
    print(f"Return on Equity (ROE): {roe if roe is None else f'{roe:.2%}'}")

    # Call the functions
    analyze_long_term_investment(ticker, trailing_pe, forward_pe, pb, roe, debt_to_equity,
                                  profit_margins, payout_ratio, beta)
    analyze_day_trading(ticker, info)
    analyze_swing_trading(ticker, info)


if __name__ == "__main__":
    main()