#!/usr/bin/env bash
# ============================================================
# DoD Contract Intelligence Agent — Live Run Setup
# Run this once on your machine, then use the commands below.
# ============================================================

set -e

echo ""
echo "=================================================="
echo "  DoD Contract Agent — Installing dependencies"
echo "=================================================="

pip install yfinance requests beautifulsoup4 pyyaml --upgrade -q

echo ""
echo "=================================================="
echo "  Ready. Example commands:"
echo "=================================================="
echo ""
echo "  # Best option: live USAspending contracts + live yfinance financials"
echo "  python main.py --source usaspending --live"
echo ""
echo "  # Last 14 days of contracts"
echo "  python main.py --source usaspending --live --days 14"
echo ""
echo "  # Specialist tier only (mid-cap, high DoD concentration)"  
echo "  python main.py --source usaspending --live --specialist-only"
echo ""
echo "  # Specialist only, top 10, save JSON scores too"
echo "  python main.py --source usaspending --live --specialist-only --top 10 --json"
echo ""
echo "  # Scrape defense.gov directly instead"
echo "  python main.py --source live --live"
echo ""
echo "  # Mock data with live fundamentals (test fundamentals pipeline)"
echo "  python main.py --source mock --live"
echo ""
echo "  Report saved to: reports/report_YYYYMMDD_HHMM.md"
echo ""
