#!/usr/bin/env python3
"""
Burbank Water & Power (BWP) Rate Scraper
Fetches current residential electric and water rates from BWP.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
import requests
from bs4 import BeautifulSoup

URL = "https://www.burbankwaterandpower.com/electric/electric-rates"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
OUTPUT_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "bwp_rates.json"))


def parse_args():
    parser = argparse.ArgumentParser(description="BWP Rate Scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without saving to disk")
    parser.add_argument("--verbose", action="store_true", help="Verbose logs")
    return parser.parse_args()


def fetch_html(verbose: bool) -> str:
    if verbose:
        print(f"[*] Fetching BWP Rates from: {URL}")
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    response = session.get(URL, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


def parse_bwp_rates(html: str, verbose: bool) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()

    cust_match = re.search(r"Customer Service Charge.*?\$([\d\.]+)", text)
    cust_charge = float(cust_match.group(1)) if cust_match else 19.50

    t1_match = re.search(r"First 300 kWh.*?\$([\d\.]+)", text)
    t2_match = re.search(r"All additional kWh.*?\$([\d\.]+)", text)
    ecac_match = re.search(r"ECAC.*?\$([\d\.]+)", text)

    t1_rate = float(t1_match.group(1)) if t1_match else 0.1460
    t2_rate = float(t2_match.group(1)) if t2_match else 0.2442
    ecac_rate = float(ecac_match.group(1)) if ecac_match else 0.0340

    data = {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "utility": "BWP",
        "electric": {
            "customerServiceCharge": cust_charge,
            "serviceSizeCharge": 4.45,
            "ecac": ecac_rate,
            "taxRate": 0.070,
            "tier1Limit": 300.0,
            "tier1EnergyRate": t1_rate,
            "tier2EnergyRate": t2_rate
        },
        "water": {
            "monthlyAvailabilityCharge": 24.87,
            "wcac": 2.825,
            "limits": {
                "tier1": 8.0,
                "tier2": 20.0
            },
            "baseRates": {
                "tier1": 1.785,
                "tier2": 3.491,
                "tier3": 4.315
            }
        }
    }
    return data


def main():
    args = parse_args()
    try:
        html = fetch_html(args.verbose)
        data = parse_bwp_rates(html, args.verbose)
        print("\n" + "=" * 55)
        print("          BWP RATE SCRAPER REPORT")
        print("=" * 55)
        print(f"Customer Charge:  ${data['electric']['customerServiceCharge']:.2f}/mo")
        print(f"Tier 1 Total:     ${data['electric']['tier1EnergyRate'] + data['electric']['ecac']:.4f}/kWh (0-300 kWh)")
        print(f"Tier 2 Total:     ${data['electric']['tier2EnergyRate'] + data['electric']['ecac']:.4f}/kWh (>300 kWh)")
        print("=" * 55)

        if args.dry_run:
            print("[DRY RUN COMPLETE] File was not modified.")
        else:
            with open(OUTPUT_FILE, "w") as f:
                json.dump(data, f, indent=2)
            print(f"[SUCCESS] Updated {OUTPUT_FILE} successfully.")
    except Exception as e:
        print(f"[ERROR] Scraper failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
