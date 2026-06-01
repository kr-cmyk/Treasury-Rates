#!/usr/bin/env python3
"""
Markets Update Notifier — SendBlue iMessage Edition
Sends hourly iMessages with Treasury yields, SOFR, stocks, and WTI
during market hours (7 AM - 2 PM PT, Mon-Fri) via GitHub Actions.

Usage:
  python markets_notifier.py              # Normal scheduled run (updates baseline if needed)
  python markets_notifier.py --on-demand  # Immediate update, no baseline changes
"""

import os
import sys
import json
import re
import argparse
import requests
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    import pytz
except ImportError:
    print("Missing dependency: pip install pytz")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env loading optional

# ============================================
# CONFIGURATION
# ============================================

SENDBLUE_API_KEY = os.environ.get("SENDBLUE_API_KEY", "")
SENDBLUE_API_SECRET = os.environ.get("SENDBLUE_API_SECRET", "")
SENDBLUE_FROM_NUMBER = os.environ.get("SENDBLUE_FROM_NUMBER", "")
RECIPIENT_NUMBER = os.environ.get("RECIPIENT_NUMBER", "+12016008025")

SENDBLUE_API_URL = "https://api.sendblue.co/api/send-message"

BASELINE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline_rates.json")

MANUAL_SOFR_RATE = "3.67"

# ============================================
# SENDBLUE MESSAGING
# ============================================

def get_sendblue_headers():
    return {
        "sb-api-key-id": SENDBLUE_API_KEY,
        "sb-api-secret-key": SENDBLUE_API_SECRET,
        "Content-Type": "application/json",
    }


def send_message(content):
    """Send iMessage via SendBlue to recipient."""
    if not SENDBLUE_API_KEY:
        print(f"[DRY RUN] No SendBlue credentials.\n{content}")
        return False

    try:
        resp = requests.post(
            SENDBLUE_API_URL,
            headers=get_sendblue_headers(),
            json={
                "number": RECIPIENT_NUMBER,
                "from_number": SENDBLUE_FROM_NUMBER,
                "content": content,
            },
        )
        resp.raise_for_status()
        print(f"SendBlue message sent: {resp.status_code}")
        return True
    except Exception as e:
        print(f"SendBlue send failed: {e}")
        return False


# ============================================
# BASELINE MANAGEMENT
# ============================================

def should_update_baseline():
    """
    Determine if we should update the baseline.
    Updates at the 2 PM PT run (last run of each trading day).
    GitHub Actions cron can be delayed, so check hour >= 14.
    """
    pt_tz = pytz.timezone("America/Los_Angeles")
    now_pt = datetime.now(pt_tz)

    # Weekday at 2 PM PT or later (the last hourly run of the day)
    if now_pt.weekday() < 5 and now_pt.hour >= 14:
        return True

    return False


def get_baseline_comparison_time():
    """Returns a string describing what time period we're comparing against."""
    pt_tz = pytz.timezone("America/Los_Angeles")
    now_pt = datetime.now(pt_tz)

    if now_pt.weekday() == 6:  # Sunday
        return "vs. Fri 4:30 PM ET"
    elif now_pt.weekday() == 0 and now_pt.hour < 14:  # Monday before 2 PM PT
        return "vs. Fri 4:30 PM ET"
    elif now_pt.weekday() >= 1 and now_pt.hour < 14:
        days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        prev_day = days[now_pt.weekday() - 1]
        return f"vs. {prev_day} 2:00 PM PT"
    else:
        return "vs. Today 6:00 AM PT"


def load_baseline():
    """Load all baseline data (daily, mtd, ytd) from file."""
    try:
        if os.path.exists(BASELINE_FILE):
            with open(BASELINE_FILE, "r") as f:
                data = json.load(f)
                return {
                    "daily": data.get("rates", {}),
                    "mtd": data.get("mtd_rates", {}),
                    "ytd": data.get("ytd_rates", {}),
                }
        return None
    except Exception as e:
        print(f"Error loading baseline: {e}")
        return None


def save_baseline(rates):
    """Save daily baseline; auto-promote prior daily to MTD/YTD on month/year transitions.

    The 2 PM PT save on the last trading day of a month captures that month's
    closing rates. The next save, after a month transition, sees a previous
    timestamp from the prior month — at that moment the still-stored daily
    baseline is exactly what next-month's MTD should compare against.
    """
    try:
        data = {}
        prev_rates = None
        prev_ts = None
        if os.path.exists(BASELINE_FILE):
            with open(BASELINE_FILE, "r") as f:
                data = json.load(f)
            prev_rates = data.get("rates")
            ts_str = data.get("timestamp")
            if ts_str:
                try:
                    prev_ts = datetime.fromisoformat(ts_str)
                except Exception:
                    prev_ts = None

        now_pt = datetime.now(pytz.timezone("America/Los_Angeles"))

        if prev_rates and prev_ts and (prev_ts.year, prev_ts.month) != (now_pt.year, now_pt.month):
            data["mtd_rates"] = prev_rates
            data["mtd_baseline_date"] = prev_ts.date().isoformat()
            print(f"Month boundary: promoted previous daily ({prev_ts.date()}) to MTD baseline")

        if prev_rates and prev_ts and prev_ts.year != now_pt.year:
            data["ytd_rates"] = prev_rates
            data["ytd_baseline_date"] = prev_ts.date().isoformat()
            print(f"Year boundary: promoted previous daily ({prev_ts.date()}) to YTD baseline")

        data["rates"] = rates
        data["timestamp"] = now_pt.isoformat()
        data["note"] = "Baseline for market comparisons"

        with open(BASELINE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Baseline saved at {data['timestamp']}")
    except Exception as e:
        print(f"Error saving baseline: {e}")


# ============================================
# DATA FETCHING FUNCTIONS
# ============================================

def get_treasury_yields():
    """Fetch live Treasury yields from CNBC, fallback to Treasury.gov XML."""
    yields = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    # Primary: CNBC (live intraday rates)
    patterns = [
        r'"last":"(\d+\.\d+)"',
        r'data-symbol-last[^>]*>(\d+\.\d+)',
        r'class="QuoteStrip-lastPrice">(\d+\.\d+)',
    ]
    try:
        for tenor in ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y"):
            try:
                url = f"https://www.cnbc.com/quotes/US{tenor}"
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    for pattern in patterns:
                        match = re.search(pattern, response.text)
                        if match:
                            rate_float = float(match.group(1))
                            if 0 < rate_float < 10:
                                yields[tenor] = f"{rate_float:.2f}"
                            elif 10 < rate_float < 100:
                                yields[tenor] = f"{rate_float / 10:.2f}"
                            break
            except Exception as e:
                print(f"CNBC {tenor}: {e}")
    except Exception as e:
        print(f"CNBC fetch error: {e}")

    if yields:
        print(f"CNBC treasury yields: {yields}")

    # Fallback: Treasury.gov XML (daily close) for any missing tenors
    missing = [t for t in ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y") if t not in yields]
    if missing:
        print(f"Fetching {missing} from Treasury.gov XML fallback...")
        try:
            pt_tz = pytz.timezone("America/Los_Angeles")
            yr = datetime.now(pt_tz).year
            treas_url = (
                "https://home.treasury.gov/resource-center/data-chart-center/"
                "interest-rates/pages/xml?data=daily_treasury_yield_curve"
                f"&field_tdr_date_value={yr}"
            )
            response = requests.get(treas_url, timeout=30)
            if response.status_code == 200:
                field_map = {
                    "1Y": "BC_1YEAR", "2Y": "BC_2YEAR", "3Y": "BC_3YEAR",
                    "5Y": "BC_5YEAR", "7Y": "BC_7YEAR", "10Y": "BC_10YEAR",
                }
                entries = response.text.split("<entry>")
                if len(entries) > 1:
                    last_entry = entries[-1]
                    for tenor in missing:
                        tag = field_map[tenor]
                        m = re.search(rf"d:{tag}[^>]*>(\d+\.?\d*)</d:{tag}", last_entry)
                        if m:
                            yields[tenor] = f"{float(m.group(1)):.2f}"
        except Exception as e:
            print(f"Treasury.gov fallback error: {e}")

    for t in ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y"):
        yields.setdefault(t, "N/A")
    print(f"Treasury yields: {yields}")
    return yields


def get_sofr_rate():
    """Fetch SOFR rate from FRED."""
    try:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR30DAYAVG"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            lines = response.text.strip().split("\n")
            if len(lines) >= 2:
                last_line = lines[-1].split(",")
                if len(last_line) >= 2 and last_line[1] not in [".", ""]:
                    return f"{float(last_line[1]):.2f}"

        return MANUAL_SOFR_RATE
    except Exception as e:
        print(f"SOFR fetch error: {e}")
        return MANUAL_SOFR_RATE


def get_stock_indices():
    """Fetch stock indices from Yahoo Finance."""
    try:
        import yfinance as yf

        indices = {"SPX": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI"}

        results = {}
        for name, ticker in indices.items():
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                if "regularMarketPrice" in info:
                    results[name] = f"{info['regularMarketPrice']:.2f}"
                else:
                    hist = stock.history(period="1d")
                    if not hist.empty:
                        results[name] = f"{hist['Close'].iloc[-1]:.2f}"
                    else:
                        results[name] = "N/A"
            except Exception:
                results[name] = "N/A"

        return results
    except Exception as e:
        print(f"Stocks fetch error: {e}")
        return {"SPX": "N/A", "NASDAQ": "N/A", "DOW": "N/A"}


def get_wti_price():
    """Fetch WTI crude oil price from Yahoo Finance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("CL=F")
        info = ticker.info
        if "regularMarketPrice" in info:
            return f"{info['regularMarketPrice']:.2f}"
        hist = ticker.history(period="1d")
        if not hist.empty:
            return f"{hist['Close'].iloc[-1]:.2f}"
        return "N/A"
    except Exception as e:
        print(f"WTI fetch error: {e}")
        return "N/A"


def get_stock_futures():
    """Scrape CNBC front-month E-mini index futures.

    Used outside cash equity hours (Sunday night, weekday pre/post market)
    so the Daily column reflects the futures-implied move rather than
    Friday's stale 4 PM ET cash close. Mirrors the CNBC scraping pattern
    from get_treasury_yields().
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    # CNBC front-month e-mini futures symbols.
    symbols = {"SPX": "@SP.1", "NASDAQ": "@NQ.1", "DOW": "@DJ.1"}
    # Patterns ordered most-specific first; numbers may include thousands commas.
    patterns = [
        r'class="QuoteStrip-lastPrice">([\d,]+\.\d+)',
        r'data-symbol-last[^>]*>([\d,]+\.\d+)',
        r'"last":"([\d,]+\.\d+)"',
    ]
    results = {}
    for name, symbol in symbols.items():
        try:
            url = f"https://www.cnbc.com/quotes/{symbol}"
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                for pattern in patterns:
                    match = re.search(pattern, response.text)
                    if match:
                        raw = match.group(1).replace(",", "")
                        try:
                            val = float(raw)
                        except ValueError:
                            continue
                        # Index futures sit between ~1000 and ~100000; anything
                        # outside that range is some other field on the page.
                        if 1000 <= val < 100000:
                            results[name] = f"{val:.2f}"
                            break
        except Exception as e:
            print(f"CNBC futures {symbol}: {e}")
        results.setdefault(name, "N/A")
    print(f"Stock futures (CNBC): {results}")
    return results


def is_cash_equity_market_open(now_pt):
    """True iff US cash equity market is in regular trading hours (9:30 AM - 4 PM ET).

    Holidays not handled; on a holiday this will say "open" and the cash
    indices stay at the prior close — same behavior as before this change.
    """
    if now_pt.weekday() >= 5:
        return False
    et = now_pt.astimezone(pytz.timezone("America/New_York"))
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


# ============================================
# HISTORICAL FETCH (for --reseed-baselines)
# ============================================

def _weekday_on_or_before(d):
    """Walk back to the nearest weekday (Mon-Fri). Holidays handled by data
    fallbacks (yfinance/Treasury return the most recent prior trading day)."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _yf_close_on(ticker, target_date):
    """Closing price for `ticker` on `target_date` (or nearest prior trading day)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        start = (target_date - timedelta(days=10)).isoformat()
        end = (target_date + timedelta(days=1)).isoformat()
        hist = t.history(start=start, end=end)
        if hist is None or hist.empty:
            return None
        return f"{float(hist['Close'].iloc[-1]):.2f}"
    except Exception as e:
        print(f"Historical {ticker} on {target_date}: {e}")
        return None


def _treasury_yields_on(target_date):
    """Treasury yields from Treasury.gov XML for `target_date` (or nearest prior)."""
    yields = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/"
        "interest-rates/pages/xml?data=daily_treasury_yield_curve"
        f"&field_tdr_date_value={target_date.year}"
    )
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            print(f"Treasury.gov returned {response.status_code} for {target_date.year}")
            return yields
        field_map = {
            "1Y": "BC_1YEAR", "2Y": "BC_2YEAR", "3Y": "BC_3YEAR",
            "5Y": "BC_5YEAR", "7Y": "BC_7YEAR", "10Y": "BC_10YEAR",
        }
        best_entry = None
        best_date = None
        for entry in response.text.split("<entry>")[1:]:
            m = re.search(r"d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})", entry)
            if not m:
                continue
            entry_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            if entry_date <= target_date and (best_date is None or entry_date > best_date):
                best_date = entry_date
                best_entry = entry
        if best_entry is None:
            print(f"No Treasury entry on/before {target_date}")
            return yields
        print(f"Treasury yields from {best_date} (target {target_date})")
        for tenor, tag in field_map.items():
            m = re.search(rf"d:{tag}[^>]*>(\d+\.?\d*)</d:{tag}", best_entry)
            if m:
                yields[tenor] = f"{float(m.group(1)):.2f}"
    except Exception as e:
        print(f"Treasury historical fetch error: {e}")
    return yields


def _sofr_on(target_date):
    """SOFR from FRED on `target_date` (or nearest prior published value)."""
    try:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR30DAYAVG"
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            return None
        best = None
        for line in response.text.strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) < 2 or parts[1] in (".", ""):
                continue
            try:
                d = datetime.strptime(parts[0], "%Y-%m-%d").date()
            except ValueError:
                continue
            if d <= target_date and (best is None or d > best[0]):
                best = (d, parts[1])
        if best is None:
            return None
        print(f"SOFR from {best[0]} (target {target_date})")
        return f"{float(best[1]):.2f}"
    except Exception as e:
        print(f"SOFR historical fetch error: {e}")
        return None


def fetch_historical_baseline(target_date, label=""):
    """Full baseline snapshot for `target_date`, used by reseed_baselines()."""
    print(f"Fetching {label} baseline for {target_date}...")
    yields = _treasury_yields_on(target_date)
    sofr = _sofr_on(target_date) or MANUAL_SOFR_RATE
    spx = _yf_close_on("^GSPC", target_date) or "N/A"
    nasdaq = _yf_close_on("^IXIC", target_date) or "N/A"
    dow = _yf_close_on("^DJI", target_date) or "N/A"
    wti = _yf_close_on("CL=F", target_date) or "N/A"
    return {
        "1Y": yields.get("1Y", "N/A"),
        "2Y": yields.get("2Y", "N/A"),
        "3Y": yields.get("3Y", "N/A"),
        "5Y": yields.get("5Y", "N/A"),
        "7Y": yields.get("7Y", "N/A"),
        "10Y": yields.get("10Y", "N/A"),
        "SOFR": sofr,
        "SPX": spx,
        "NASDAQ": nasdaq,
        "DOW": dow,
        "WTI": wti,
    }


def reseed_baselines():
    """Backfill mtd_rates and ytd_rates in baseline_rates.json from historical data.

    MTD anchor: last weekday of the previous calendar month.
    YTD anchor: last weekday of the previous calendar year.
    """
    pt_tz = pytz.timezone("America/Los_Angeles")
    today = datetime.now(pt_tz).date()

    end_of_prev_month = _weekday_on_or_before(today.replace(day=1) - timedelta(days=1))
    end_of_prev_year = _weekday_on_or_before(date(today.year - 1, 12, 31))

    mtd = fetch_historical_baseline(end_of_prev_month, label=f"MTD ({end_of_prev_month})")
    ytd = fetch_historical_baseline(end_of_prev_year, label=f"YTD ({end_of_prev_year})")

    data = {}
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, "r") as f:
            data = json.load(f)

    data["mtd_rates"] = mtd
    data["mtd_baseline_date"] = end_of_prev_month.isoformat()
    data["ytd_rates"] = ytd
    data["ytd_baseline_date"] = end_of_prev_year.isoformat()

    with open(BASELINE_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nReseeded MTD ({end_of_prev_month}): {mtd}")
    print(f"Reseeded YTD ({end_of_prev_year}): {ytd}")


# ============================================
# CALCULATION FUNCTIONS
# ============================================

def bps_change(current, previous):
    """Calculate basis point change as compact string like (+5) or (-3)."""
    try:
        if current == "N/A" or previous in (None, "N/A"):
            return "--"
        diff = round((float(current) - float(previous)) * 100)
        if diff > 0:
            return f"(+{diff})"
        elif diff < 0:
            return f"({diff})"
        else:
            return "(0)"
    except Exception:
        return "--"


def pct_change(current, previous):
    """Calculate percentage change as compact string like (+1.2%) or (-0.5%)."""
    try:
        if current == "N/A" or previous in (None, "N/A"):
            return "--"
        c, p = float(current), float(previous)
        if p == 0:
            return "--"
        diff = ((c - p) / p) * 100
        if diff > 0:
            return f"(+{diff:.1f}%)"
        elif diff < 0:
            return f"({diff:.1f}%)"
        else:
            return "(0%)"
    except Exception:
        return "--"


# ============================================
# MAIN FUNCTION
# ============================================

def run_update(on_demand=False):
    """Fetch market data and send update via SendBlue."""
    pt_tz = pytz.timezone("America/Los_Angeles")
    now_pt = datetime.now(pt_tz)

    print(f"Current PT time: {now_pt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Day: {now_pt.strftime('%A')}")
    print(f"Mode: {'on-demand' if on_demand else 'scheduled'}")

    # Fetch current data
    print("Fetching market data...")
    yields = get_treasury_yields()
    sofr = get_sofr_rate()
    stocks = get_stock_indices()  # cash; canonical for baseline saves
    wti = get_wti_price()

    # Outside cash equity hours, swap in CNBC futures for the displayed
    # stock values so the Daily column shows the overnight/weekend move.
    # `stocks` (cash) is still what gets written to the baseline at 2 PM PT.
    stocks_display = stocks
    stocks_label = "STOCKS:"
    if not is_cash_equity_market_open(now_pt):
        print("Cash equity market closed - fetching CNBC futures for display")
        futures = get_stock_futures()
        stocks_display = {}
        for k in ("SPX", "NASDAQ", "DOW"):
            fv = futures.get(k)
            cv = stocks.get(k)
            chosen = fv if fv and fv != "N/A" else cv
            # Defense in depth: if CNBC handed us something more than 5% away
            # from the cash close, the scraper grabbed the wrong field (or the
            # symbol is for a dead contract). Fall back to cash rather than
            # display an obviously bogus number.
            try:
                if chosen and chosen != "N/A" and cv and cv != "N/A":
                    if abs(float(chosen) - float(cv)) / float(cv) > 0.05:
                        print(f"Futures {k}={chosen} >5% from cash {cv}; using cash")
                        chosen = cv
            except Exception:
                pass
            stocks_display[k] = chosen if chosen else "N/A"
        stocks_label = "STOCKS (futures):"

    # Load baselines
    baselines = load_baseline()

    if baselines is None:
        print("No baseline found - creating initial baseline...")
        initial = {
            "1Y": yields.get("1Y", "N/A"),
            "2Y": yields.get("2Y", "N/A"),
            "3Y": yields.get("3Y", "N/A"),
            "5Y": yields.get("5Y", "N/A"),
            "7Y": yields.get("7Y", "N/A"),
            "10Y": yields.get("10Y", "N/A"),
            "SOFR": sofr,
            "SPX": stocks.get("SPX", "N/A"),
            "NASDAQ": stocks.get("NASDAQ", "N/A"),
            "DOW": stocks.get("DOW", "N/A"),
            "WTI": wti,
        }
        save_baseline(initial)
        baselines = {"daily": initial, "mtd": initial, "ytd": initial}

    daily = baselines.get("daily", {})
    mtd = baselines.get("mtd", {})
    ytd = baselines.get("ytd", {})

    def fmt_price(val, decimals=2):
        """Format price with commas."""
        try:
            if decimals == 0:
                return f"{float(val):,.0f}"
            return f"{float(val):,.2f}"
        except Exception:
            return val

    # Helper to build a bps row: "TENOR: X.XX%  (D) (M) (Y)"
    def bps_row(label, key, current):
        d = bps_change(current, daily.get(key))
        m = bps_change(current, mtd.get(key))
        y = bps_change(current, ytd.get(key))
        return f"{label} {current}%  {d} {m} {y}"

    def pct_block(label, key, current, whole=False):
        """Two-line format: label on first line, price + changes on second."""
        d = pct_change(current, daily.get(key))
        m = pct_change(current, mtd.get(key))
        y = pct_change(current, ytd.get(key))
        price = fmt_price(current, decimals=0) if whole else fmt_price(current)
        return f"{label}\n{price}  {d} {m} {y}"

    # Build message
    hdr = "         Daily  MTD  YTD"

    msg = f"TREASURIES:\n{hdr}\n"
    msg += bps_row("1Y: ", "1Y", yields.get("1Y", "N/A")) + "\n"
    msg += bps_row("2Y: ", "2Y", yields.get("2Y", "N/A")) + "\n"
    msg += bps_row("3Y: ", "3Y", yields.get("3Y", "N/A")) + "\n"
    msg += bps_row("5Y: ", "5Y", yields.get("5Y", "N/A")) + "\n"
    msg += bps_row("7Y: ", "7Y", yields.get("7Y", "N/A")) + "\n"
    msg += bps_row("10Y:", "10Y", yields.get("10Y", "N/A")) + "\n\n"

    msg += f"SOFR:\n{hdr}\n"
    msg += bps_row("1M: ", "SOFR", sofr) + "\n\n"

    msg += f"{stocks_label}\n{hdr}\n"
    msg += pct_block("S&P:", "SPX", stocks_display.get("SPX", "N/A"), whole=True) + "\n"
    msg += pct_block("Nasdaq:", "NASDAQ", stocks_display.get("NASDAQ", "N/A"), whole=True) + "\n"
    msg += pct_block("Dow:", "DOW", stocks_display.get("DOW", "N/A"), whole=True) + "\n\n"

    msg += f"COMMODITIES:\n{hdr}\n"
    msg += pct_block("WTI:", "WTI", wti) + "\n\n"

    msg += now_pt.strftime("%I:%M %p PT - %b %d, %Y")

    # Send via SendBlue
    send_message(msg)

    # Update daily baseline if needed (skip for on-demand)
    if not on_demand and should_update_baseline():
        print("Updating baseline...")
        current_rates = {
            "1Y": yields.get("1Y", "N/A"),
            "2Y": yields.get("2Y", "N/A"),
            "3Y": yields.get("3Y", "N/A"),
            "5Y": yields.get("5Y", "N/A"),
            "7Y": yields.get("7Y", "N/A"),
            "10Y": yields.get("10Y", "N/A"),
            "SOFR": sofr,
            "SPX": stocks.get("SPX", "N/A"),
            "NASDAQ": stocks.get("NASDAQ", "N/A"),
            "DOW": stocks.get("DOW", "N/A"),
            "WTI": wti,
        }
        save_baseline(current_rates)

    print("Complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Markets Update Notifier")
    parser.add_argument("--on-demand", action="store_true", help="Send immediate update without baseline changes")
    parser.add_argument("--reseed-baselines", action="store_true", help="Backfill mtd_rates/ytd_rates from historical data and exit")
    args = parser.parse_args()
    if args.reseed_baselines:
        reseed_baselines()
    else:
        run_update(on_demand=args.on_demand)
