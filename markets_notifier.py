#!/usr/bin/env python3
"""
Markets Update Notifier for GitHub Actions
Sends hourly emails with Treasury yields, SOFR, and stock indices
"""

import os
import json
import requests
from datetime import datetime, timedelta
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ============================================
# CONFIGURATION
# ============================================

# Email configuration from GitHub Secrets
SENDER_EMAIL = os.environ.get('GMAIL_ADDRESS', 'kr@redduckcapital.com')
SENDER_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')
RECIPIENT_EMAIL = os.environ.get('GMAIL_ADDRESS', 'kr@redduckcapital.com')

# Baseline storage file
BASELINE_FILE = 'baseline_rates.json'

# Manual SOFR fallback
MANUAL_SOFR_RATE = "3.67"

# ============================================
# BASELINE MANAGEMENT
# ============================================

def should_update_baseline():
    """
    Determine if we should update the baseline
    Updates at: Friday 4:30 PM ET, and Mon-Fri at 2:00 PM PT
    """
    pt_tz = pytz.timezone('America/Los_Angeles')
    et_tz = pytz.timezone('America/New_York')
    now_pt = datetime.now(pt_tz)
    now_et = datetime.now(et_tz)
    
    # Friday at 4:30 PM ET or later
    if now_et.weekday() == 4 and now_et.hour >= 16 and now_et.minute >= 30:
        return True
    
    # Monday-Friday at 2:00 PM PT (market close for the day)
    if now_pt.weekday() < 5 and now_pt.hour == 14 and now_pt.minute == 0:
        return True
    
    return False


def get_baseline_comparison_time():
    """
    Returns a string describing what time period we're comparing against
    """
    pt_tz = pytz.timezone('America/Los_Angeles')
    now_pt = datetime.now(pt_tz)
    
    # Sunday or Monday morning = compare to Friday close
    if now_pt.weekday() == 6:  # Sunday
        return "vs. Fri 4:30 PM ET"
    elif now_pt.weekday() == 0 and now_pt.hour < 14:  # Monday before 2 PM PT
        return "vs. Fri 4:30 PM ET"
    # Tuesday-Friday morning = compare to previous day 2 PM PT
    elif now_pt.weekday() >= 1 and now_pt.hour < 14:
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
        prev_day = days[now_pt.weekday() - 1]
        return f"vs. {prev_day} 2:00 PM PT"
    # During the day = compare to today's open (6 AM PT)
    else:
        return "vs. Today 6:00 AM PT"


def load_baseline():
    """Load baseline rates from file"""
    try:
        if os.path.exists(BASELINE_FILE):
            with open(BASELINE_FILE, 'r') as f:
                data = json.load(f)
                return data.get('rates', None)
        return None
    except Exception as e:
        print(f"Error loading baseline: {e}")
        return None


def save_baseline(rates):
    """Save baseline rates to file"""
    try:
        data = {
            'rates': rates,
            'timestamp': datetime.now(pytz.timezone('America/Los_Angeles')).isoformat(),
            'note': 'Baseline for market comparisons'
        }
        with open(BASELINE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Baseline saved at {data['timestamp']}")
    except Exception as e:
        print(f"Error saving baseline: {e}")


# ============================================
# DATA FETCHING FUNCTIONS
# ============================================

def get_treasury_yields():
    """Fetch Treasury yields from CNBC"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        yields = {}
        cnbc_urls = {
            '1Y': 'https://www.cnbc.com/quotes/US1Y',
            '2Y': 'https://www.cnbc.com/quotes/US2Y',
            '3Y': 'https://www.cnbc.com/quotes/US3Y',
            '5Y': 'https://www.cnbc.com/quotes/US5Y',
            '7Y': 'https://www.cnbc.com/quotes/US7Y',
            '10Y': 'https://www.cnbc.com/quotes/US10Y',
        }
        
        import re
        
        for tenor, url in cnbc_urls.items():
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    text = response.text
                    
                    patterns = [
                        r'"last":"(\d+\.\d+)"',
                        r'data-symbol-last[^>]*>(\d+\.\d+)',
                        r'class="QuoteStrip-lastPrice">(\d+\.\d+)',
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, text)
                        if match:
                            rate = match.group(1)
                            rate_float = float(rate)
                            if 0 < rate_float < 10:
                                yields[tenor] = f"{rate_float:.2f}"
                                break
                            elif 10 < rate_float < 100:
                                yields[tenor] = f"{rate_float / 10:.2f}"
                                break
            except Exception as e:
                print(f"Error fetching {tenor}: {e}")
                yields[tenor] = 'N/A'
        
        return yields if yields else {'1Y': 'N/A', '2Y': 'N/A', '3Y': 'N/A', '5Y': 'N/A', '7Y': 'N/A', '10Y': 'N/A'}
    
    except Exception as e:
        print(f"Treasury fetch error: {e}")
        return {'1Y': 'N/A', '2Y': 'N/A', '3Y': 'N/A', '5Y': 'N/A', '7Y': 'N/A', '10Y': 'N/A'}


def get_sofr_rate():
    """Fetch SOFR rate from FRED"""
    try:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR30DAYAVG"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            if len(lines) >= 2:
                last_line = lines[-1].split(',')
                if len(last_line) >= 2 and last_line[1] not in ['.', '']:
                    return last_line[1]
        
        return MANUAL_SOFR_RATE
    except Exception as e:
        print(f"SOFR fetch error: {e}")
        return MANUAL_SOFR_RATE


def get_stock_indices():
    """Fetch stock indices from Yahoo Finance"""
    try:
        import yfinance as yf
        
        indices = {
            'SPX': '^GSPC',
            'NASDAQ': '^IXIC',
            'DOW': '^DJI'
        }
        
        results = {}
        for name, ticker in indices.items():
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                if 'regularMarketPrice' in info:
                    # Format as integer with commas, no decimals
                    results[name] = f"{int(info['regularMarketPrice']):,}"
                else:
                    hist = stock.history(period='1d')
                    if not hist.empty:
                        # Format as integer with commas, no decimals
                        results[name] = f"{int(hist['Close'].iloc[-1]):,}"
                    else:
                        results[name] = 'N/A'
            except:
                results[name] = 'N/A'
        
        return results
    except Exception as e:
        print(f"Stocks fetch error: {e}")
        return {'SPX': 'N/A', 'NASDAQ': 'N/A', 'DOW': 'N/A'}


def get_wti_oil():
    """Fetch WTI Crude Oil price from Yahoo Finance"""
    try:
        import yfinance as yf
        
        # WTI Crude Oil futures ticker
        oil = yf.Ticker('CL=F')
        
        # Try to get current price
        info = oil.info
        if 'regularMarketPrice' in info:
            price = info['regularMarketPrice']
            return f"{price:.2f}"
        else:
            # Fallback to history
            hist = oil.history(period='1d')
            if not hist.empty:
                price = hist['Close'].iloc[-1]
                return f"{price:.2f}"
        
        return 'N/A'
    except Exception as e:
        print(f"WTI Oil fetch error: {e}")
        return 'N/A'


# ============================================
# CALCULATION FUNCTIONS
# ============================================

def calculate_bps_change(current, previous):
    """Calculate basis point change"""
    try:
        if current == 'N/A' or previous == 'N/A':
            return ""
        current_float = float(current)
        previous_float = float(previous)
        bps_change = round((current_float - previous_float) * 100)
        
        if bps_change > 0:
            return f" (+{bps_change} bps)"
        elif bps_change < 0:
            return f" ({bps_change} bps)"
        else:
            return " (unch)"
    except:
        return ""


def calculate_pct_change(current, previous):
    """Calculate percentage change"""
    try:
        if current == 'N/A' or previous == 'N/A':
            return ""
        current_float = float(current)
        previous_float = float(previous)
        pct_change = ((current_float - previous_float) / previous_float) * 100
        
        if pct_change > 0:
            return f" (+{pct_change:.2f}%)"
        elif pct_change < 0:
            return f" ({pct_change:.2f}%)"
        else:
            return " (unch)"
    except:
        return ""


# ============================================
# EMAIL FUNCTION
# ============================================

def send_email(subject, body):
    """Send email via Gmail"""
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Email sent to {RECIPIENT_EMAIL}")
        return True
    except Exception as e:
        print(f"❌ Email error: {e}")
        return False


# ============================================
# MAIN FUNCTION
# ============================================

def main():
    """Main execution"""
    pt_tz = pytz.timezone('America/Los_Angeles')
    now_pt = datetime.now(pt_tz)
    
    print(f"Current PT time: {now_pt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Day: {now_pt.strftime('%A')}")
    
    # Fetch current data
    print("Fetching market data...")
    yields = get_treasury_yields()
    sofr = get_sofr_rate()
    stocks = get_stock_indices()
    wti_oil = get_wti_oil()
    
    # Load baseline for comparison
    baseline = load_baseline()
    
    # If no baseline exists, create one now
    if baseline is None:
        print("No baseline found - creating initial baseline...")
        baseline = {
            '1Y': yields.get('1Y', 'N/A'),
            '2Y': yields.get('2Y', 'N/A'),
            '3Y': yields.get('3Y', 'N/A'),
            '5Y': yields.get('5Y', 'N/A'),
            '7Y': yields.get('7Y', 'N/A'),
            '10Y': yields.get('10Y', 'N/A'),
            'SOFR': sofr,
            'SPX': stocks.get('SPX', 'N/A'),
            'NASDAQ': stocks.get('NASDAQ', 'N/A'),
            'DOW': stocks.get('DOW', 'N/A'),
            'WTI': wti_oil
        }
        save_baseline(baseline)
        print("Initial baseline created!")
    
    comparison_note = get_baseline_comparison_time()
    
    # Calculate changes
    bps_1y = calculate_bps_change(yields.get('1Y'), baseline.get('1Y') if baseline else None)
    bps_2y = calculate_bps_change(yields.get('2Y'), baseline.get('2Y') if baseline else None)
    bps_3y = calculate_bps_change(yields.get('3Y'), baseline.get('3Y') if baseline else None)
    bps_5y = calculate_bps_change(yields.get('5Y'), baseline.get('5Y') if baseline else None)
    bps_7y = calculate_bps_change(yields.get('7Y'), baseline.get('7Y') if baseline else None)
    bps_10y = calculate_bps_change(yields.get('10Y'), baseline.get('10Y') if baseline else None)
    bps_sofr = calculate_bps_change(sofr, baseline.get('SOFR') if baseline else None)
    
    pct_spx = calculate_pct_change(stocks.get('SPX'), baseline.get('SPX') if baseline else None)
    pct_nasdaq = calculate_pct_change(stocks.get('NASDAQ'), baseline.get('NASDAQ') if baseline else None)
    pct_dow = calculate_pct_change(stocks.get('DOW'), baseline.get('DOW') if baseline else None)
    pct_wti = calculate_pct_change(wti_oil, baseline.get('WTI') if baseline else None)
    
    # Build email
    subject = f"Markets Update - {now_pt.strftime('%I:%M %p PT')}"
    
    body = f"TREASURIES:\n"
    body += f"1Y:  {yields.get('1Y', 'N/A')}%{bps_1y}\n"
    body += f"2Y:  {yields.get('2Y', 'N/A')}%{bps_2y}\n"
    body += f"3Y:  {yields.get('3Y', 'N/A')}%{bps_3y}\n"
    body += f"5Y:  {yields.get('5Y', 'N/A')}%{bps_5y}\n"
    body += f"7Y:  {yields.get('7Y', 'N/A')}%{bps_7y}\n"
    body += f"10Y: {yields.get('10Y', 'N/A')}%{bps_10y}\n\n"
    
    body += f"SOFR:\n"
    body += f"1M: {sofr}%{bps_sofr}\n\n"
    
    body += f"STOCKS:\n"
    body += f"S&P:    {stocks.get('SPX', 'N/A')}{pct_spx}\n"
    body += f"Nasdaq: {stocks.get('NASDAQ', 'N/A')}{pct_nasdaq}\n"
    body += f"Dow:    {stocks.get('DOW', 'N/A')}{pct_dow}\n\n"
    
    body += f"COMMODITIES:\n"
    body += f"WTI Oil: ${wti_oil}{pct_wti}\n\n"
    
    body += f"{now_pt.strftime('%I:%M %p PT - %b %d, %Y')}\n"
    if baseline:
        body += f"{comparison_note}"
    
    # Send email
    send_email(subject, body)
    
    # Update baseline if needed
    if should_update_baseline():
        print("Updating baseline...")
        current_rates = {
            '1Y': yields.get('1Y', 'N/A'),
            '2Y': yields.get('2Y', 'N/A'),
            '3Y': yields.get('3Y', 'N/A'),
            '5Y': yields.get('5Y', 'N/A'),
            '7Y': yields.get('7Y', 'N/A'),
            '10Y': yields.get('10Y', 'N/A'),
            'SOFR': sofr,
            'SPX': stocks.get('SPX', 'N/A'),
            'NASDAQ': stocks.get('NASDAQ', 'N/A'),
            'DOW': stocks.get('DOW', 'N/A'),
            'WTI': wti_oil
        }
        save_baseline(current_rates)
    
    print("✅ Complete!")


if __name__ == "__main__":
    main()
