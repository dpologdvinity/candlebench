"""The default sampling universe.

The list is hardcoded rather than screened at runtime so that two runs with the
same seed draw the same symbols. A live screener would silently change the
universe between runs and make results impossible to reproduce.
"""

# The first fifty are the original list, in its original order, so a run with
# `sample_size = 50` draws exactly the universe earlier results were measured on.
TOP_100_LIQUID = (
    # index ETFs
    "SPY", "QQQ", "IWM", "DIA",
    # mega-cap technology
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    # semiconductors and enterprise software
    "INTC", "MU", "QCOM", "CRM", "ORCL", "ADBE", "CSCO", "TXN",
    # financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "V", "MA",
    # energy
    "XOM", "CVX", "OXY", "SLB",
    # healthcare
    "UNH", "JNJ", "PFE", "LLY", "MRK", "ABBV",
    # consumer and industrials
    "WMT", "COST", "HD", "DIS", "KO", "PEP", "BA", "CAT", "GE", "F",
    # sector, bond, gold and emerging-market ETFs
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLY", "XLP", "XLU", "SMH", "KRE", "XBI",
    "GLD", "TLT", "HYG", "EEM", "ARKK",
    # technology, semiconductors and software
    "PLTR", "UBER", "PYPL", "SHOP", "SNOW", "COIN", "MRVL", "ARM", "SMCI", "LRCX",
    "AMAT", "KLAC", "ADI", "IBM", "NOW", "PANW", "CRWD", "SOFI",
    # consumer, communications, healthcare and industrials
    "ABNB", "SBUX", "NKE", "MCD", "LOW", "TGT", "T", "VZ", "CMCSA", "TMO", "ABT",
    "BMY", "RTX", "LMT", "UPS", "DE",
)
# Added on request: more of the AI hardware chain, storage, and the other big
# S&P 500 and world index funds. GOOG and GOOGL, and SPY, VOO, IVV and SPYM,
# track the same underlying; their trades are correlated on any one date,
# which the date-clustered bootstrap already treats as one draw. CRWV (listed
# March 2025) and SNDK (spun off February 2025) have less than two years.
REQUESTED = (
    "GOOG", "ANET", "CRWV", "CMI", "DELL", "KEYS", "LITE", "NBIS", "SNDK", "STX",
    "SOUN", "TSM", "WDC", "VOO", "IVV", "VT", "SPYM",
)
UNIVERSE = TOP_100_LIQUID + REQUESTED
TOP_50_LIQUID = TOP_100_LIQUID[:50]


def resolve(symbols: tuple[str, ...], sample_size: int) -> tuple[str, ...]:
    """The symbols trials may draw from.

    An empty `symbols` selects the built-in list. `sample_size` truncates
    whichever list is in use; it never pads, so asking for more symbols than
    exist yields the whole list rather than an error.
    """
    pool = symbols or UNIVERSE
    if sample_size <= 0:
        raise ValueError("universe.sample_size must be positive")
    return tuple(pool[:sample_size])
