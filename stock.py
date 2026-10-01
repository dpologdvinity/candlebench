import yfinance as yf

def analyze_long_term_investment(ticker_obj, trailing_pe, forward_pe, pb, roe):
    """
    Performs long-term investment analysis based on valuation, efficiency, and historical EPS trends,
    and returns a final verdict on whether the stock is a good long-term investment.
    """
    print("\n===== Long-term Investment Analysis =====")
    
    score = 0
    evaluated_metrics = 0

    # 1. Historical EPS Consistency and Growth Analysis
    financials = ticker_obj.financials
    if "Diluted EPS" in financials.index:
        # Extract and sort EPS from oldest to newest
        eps_series = financials.loc["Diluted EPS"].dropna().iloc[::-1]
        
        if len(eps_series) >= 2:
            evaluated_metrics += 1
            eps_values = eps_series.values
            consecutive_growth = True
            is_consistent = True
            
            for i in range(len(eps_values) - 1):
                if eps_values[i+1] < eps_values[i]:
                    consecutive_growth = False
                    is_consistent = False
                elif eps_values[i+1] == eps_values[i]:
                    consecutive_growth = False

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

    # 2. Forward vs Trailing P/E Logic (Growth Outlook)
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

    # 3. P/E Valuation Ranges
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

    # 4. P/B Evaluation
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

    # 5. ROE Evaluation (Management Efficiency)
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

    # 6. Final Decision Logic
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

    print(f"\nStock: {symbol}")
    print(f"Trailing P/E: {trailing_pe}")
    print(f"Forward P/E: {forward_pe}")
    print(f"P/B ratio: {pb}")
    print(f"Return on Equity (ROE): {roe if roe is None else f'{roe:.2%}'}")
    
    # Call the function
    analyze_long_term_investment(ticker, trailing_pe, forward_pe, pb, roe)


if __name__ == "__main__":
    main()