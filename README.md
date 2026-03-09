# Markets Update Notifier - GitHub Actions Setup

Automated market updates via email using GitHub Actions (runs in the cloud, completely free).

## 📊 What This Does

Sends you hourly email updates with:
- **Treasury Yields**: 1Y, 2Y, 3Y, 5Y, 7Y, 10Y (with basis point changes)
- **SOFR Rate**: 1-Month TERM SOFR (with basis point changes)
- **Stock Indices**: S&P 500, Nasdaq, Dow Jones (with percentage changes)

## ⏰ Schedule

- **Sunday**: 3:00 PM PT onwards (hourly)
- **Monday-Friday**: 6:00 AM - 2:00 PM PT (hourly, market hours only)
- **Comparison Logic**:
  - Sunday 3 PM & Monday 6 AM: vs. Friday 4:30 PM ET close
  - Tuesday-Friday 6 AM: vs. previous day 2:00 PM PT close
  - All other hours: vs. same day 6:00 AM PT open

## 🚀 Setup Instructions

### Step 1: Create GitHub Repository

1. Go to: https://github.com/new
2. Repository name: `markets-notifier` (or any name you like)
3. **Make it PRIVATE** (contains your email credentials)
4. Click **"Create repository"**

### Step 2: Upload Files

1. Click **"uploading an existing file"** link
2. Drag and drop these 3 files:
   - `markets_notifier.py`
   - `.github/workflows/markets-update.yml`
   - `README.md`
3. Click **"Commit changes"**

### Step 3: Add GitHub Secrets (Important!)

1. In your repository, click **"Settings"** tab
2. Click **"Secrets and variables"** → **"Actions"**
3. Click **"New repository secret"**

Add these two secrets:

**Secret 1:**
- Name: `GMAIL_ADDRESS`
- Value: `kr@redduckcapital.com`
- Click "Add secret"

**Secret 2:**
- Name: `GMAIL_APP_PASSWORD`
- Value: `zcmaudnvrhwvpgkp`
- Click "Add secret"

### Step 4: Enable GitHub Actions

1. Click **"Actions"** tab
2. Click **"I understand my workflows, go ahead and enable them"**

### Step 5: Test It!

1. Go to **"Actions"** tab
2. Click **"Markets Update Notifier"** workflow
3. Click **"Run workflow"** → **"Run workflow"**
4. Wait 30-60 seconds
5. Check your email at kr@redduckcapital.com!

## ✅ You're Done!

The script will now run automatically:
- Every hour on Sunday from 3 PM PT onwards
- Every hour Monday-Friday from 6 AM - 2 PM PT

Your computer can be **completely off** and you'll still get emails! ☁️

## 📧 Email Format

```
TREASURIES:
1Y:  4.15% (-3 bps)
2Y:  4.02% (+2 bps)
3Y:  3.85% (-1 bps)
5Y:  3.79% (-5 bps)
7Y:  4.01% (+3 bps)
10Y: 4.24% (unch)

SOFR:
1M: 3.67% (+2 bps)

STOCKS:
S&P:    6,024.85 (+1.23%)
Nasdaq: 19,756.78 (-0.45%)
Dow:    44,882.13 (+0.89%)

7:00 AM PT - Feb 10, 2026
vs. Today 6:00 AM PT
```

## 🔧 Troubleshooting

**Not receiving emails?**
- Check GitHub Actions tab for errors
- Verify secrets are set correctly
- Check spam folder

**Want to change schedule?**
- Edit `.github/workflows/markets-update.yml`
- Modify cron times (use UTC timezone)

**Want to change email recipient?**
- Add/update `RECIPIENT_EMAIL` secret in GitHub

## 💰 Cost

**Completely FREE!**
- GitHub Actions: 2,000 minutes/month free tier
- This uses ~20 minutes/month
- No credit card needed

## 🔒 Security

- Email credentials stored as encrypted GitHub Secrets
- Repository is private
- Only you can access it
- 
