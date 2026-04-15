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
from datetime import datetime
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
    Updates at: Friday 4:30 PM ET, and Mon-Fri at 2:00 PM PT.
    """
    pt_tz = pytz.timezone("America/Los_Angeles")
    et_tz = pytz.timezone("America/New_York")
    now_pt = datetime.now(pt_tz)
    now_et = datetime.now(et_tz)

    if now_et.weekday() == 4 and now_et.hour >= 16 and now_et.minute >= 30:
        return True

    if now_pt.weekday() < 5 and now_pt.hour == 14 and now_pt.minute == 0:
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
    """Save daily baseline rates to file, preserving mtd/ytd."""
    try:
        data = {}
        if os.path.exists(BASELINE_FILE):
            with open(BASELINE_FILE, "r") as f:
                data = json.load(f)

        data["rates"] = rates
        data["timestamp"] = datetime.now(pytz.timezone("America/Los_Angeles")).isoformat()
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
    """Fetch Treasury yields from Treasury.gov XML feed."""
    try:
        pt_tz = pytz.timezone("America/Los_Angeles")
        yr = datetime.now(pt_tz).year
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/pages/xml?data=daily_treasury_yield_curve"
            f"&field_tdr_date_value={yr}"
        )
        response = requests.get(url, timeout=30)
        yields = {}
        if response.status_code == 200:
            field_map = {
                "1Y": "BC_1YEAR", "2Y": "BC_2YEAR", "3Y": "BC_3YEAR",
                "5Y": "BC_5YEAR", "7Y": "BC_7YEAR", "10Y": "BC_10YEAR",
            }
            entries = response.text.split("<entry>")
            if len(entries) > 1:
                last_entry = entries[-1]
                for tenor, tag in field_map.items():
                    m = re.search(rf"d:{tag}[^>]*>(\d+\.?\d*)</d:{tag}", last_entry)
                    if m:
                        yields[tenor] = f"{float(m.group(1)):.2f}"
        print(f"Treasury yields: {yields}")
        return yields if yields else {t: "N/A" for t in ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]}

    except Exception as e:
        print(f"Treasury fetch error: {e}")
        return {t: "N/A" for t in ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]}


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
    stocks = get_stock_indices()
    wti = get_wti_price()

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

    msg += f"STOCKS:\n{hdr}\n"
    msg += pct_block("S&P:", "SPX", stocks.get("SPX", "N/A"), whole=True) + "\n"
    msg += pct_block("Nasdaq:", "NASDAQ", stocks.get("NASDAQ", "N/A"), whole=True) + "\n"
    msg += pct_block("Dow:", "DOW", stocks.get("DOW", "N/A"), whole=True) + "\n\n"

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
    args = parser.parse_args()
    run_update(on_demand=args.on_demand)
