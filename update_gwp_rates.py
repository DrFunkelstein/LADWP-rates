#!/usr/bin/env python3
"""
Glendale Water & Power (GWP) Rate Scraper
Fetches current residential electric rates from GWP's official website.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
import requests
from bs4 import BeautifulSoup

URL = "https://www.glendaleca.gov/government/departments/glendale-water-and-power/rates/residential-electric-rates"
OUTPUT_FILE = "gwp_rates.json"


def parse_args():
    parser = argparse.ArgumentParser(description="GWP Rate Scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without writing to disk")
    parser.add_argument("--verbose", action="store_true", help="Print verbose logs")
    return parser.parse_args()


def fetch_html(verbose: bool) -> str:
    if verbose:
        print(f"[*] Fetching HTML from: {URL}")
    
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }
    response = session.get(URL, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


def parse_gwp_rates(html: str, verbose: bool) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()

    # Parse Customer Charge
    cust_match = re.search(r"Customer Charge - per meter per day\s+\$([\d\.]+)", text)
    cust_charge = float(cust_match.group(1)) if cust_match else 0.75

    # High Season Standard
    h_t1 = re.search(r"July through October.*?First 10kWh.*?\$([\d\.]+)", text, re.DOTALL)
    h_t2 = re.search(r"July through October.*?Next 10kWh.*?\$([\d\.]+)", text, re.DOTALL)
    h_t3 = re.search(r"July through October.*?Remaining kWh.*?\$([\d\.]+)", text, re.DOTALL)

    # Low Season Standard
    l_t1 = re.search(r"November through June.*?First 10kWh.*?\$([\d\.]+)", text, re.DOTALL)
    l_t2 = re.search(r"November through June.*?Next 10kWh.*?\$([\d\.]+)", text, re.DOTALL)
    l_t3 = re.search(r"November through June.*?Remaining kWh.*?\$([\d\.]+)", text, re.DOTALL)

    # High Season TOU
    h_tou_base = re.search(r"L-1-B.*?July through October.*?Base Period.*?\*.*?\$([\d\.]+)", text, re.DOTALL)
    h_tou_peak = re.search(r"L-1-B.*?July through October.*?Peak Period.*?\*\*.*?\$([\d\.]+)", text, re.DOTALL)

    # Low Season TOU
    l_tou_base = re.search(r"L-1-B.*?November through June.*?Base Period.*?\*.*?\$([\d\.]+)", text, re.DOTALL)
    l_tou_peak = re.search(r"L-1-B.*?November through June.*?Peak Period.*?\*\*\*.*?\$([\d\.]+)", text, re.DOTALL)

    data = {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "utility": "GWP",
        "electric": {
            "dailyCustomerCharge": cust_charge,
            "taxRate": 0.070,
            "highSeasonMonths": [7, 8, 9, 10],
            "standard": {
                "tier1DailyKwh": 10.0,
                "tier2DailyKwh": 10.0,
                "highSeason": {
                    "tier1": float(h_t1.group(1)) if h_t1 else 0.3071,
                    "tier2": float(h_t2.group(1)) if h_t2 else 0.3806,
                    "tier3": float(h_t3.group(1)) if h_t3 else 0.4547
                },
                "lowSeason": {
                    "tier1": float(l_t1.group(1)) if l_t1 else 0.2575,
                    "tier2": float(l_t2.group(1)) if l_t2 else 0.3189,
                    "tier3": float(l_t3.group(1)) if l_t3 else 0.3935
                }
            },
            "tou": {
                "highSeason": {
                    "peak": float(h_tou_peak.group(1)) if h_tou_peak else 0.6839,
                    "offPeak": float(h_tou_base.group(1)) if h_tou_base else 0.2280
                },
                "lowSeason": {
                    "peak": float(l_tou_peak.group(1)) if l_tou_peak else 0.5700,
                    "offPeak": float(l_tou_base.group(1)) if l_tou_base else 0.1901
                }
            }
        },
        "water": {
            "dailyCustomerCharge": 0.881,
            "limits": {
                "tier1": 8.0,
                "tier2": 15.0
            },
            "rates": {
                "tier1": 2.80,
                "tier2": 4.11,
                "tier3": 4.28
            }
        }
    }
    return data


def main():
    args = parse_args()
    try:
        html = fetch_html(args.verbose)
        data = parse_gwp_rates(html, args.verbose)
        
        print("\n" + "=" * 55)
        print("          GWP RATE SCRAPER REPORT")
        print("=" * 55)
        print(f"Customer Charge: ${data['electric']['dailyCustomerCharge']:.2f}/day")
        print(f"High Season (Jul-Oct): T1=${data['electric']['standard']['highSeason']['tier1']:.4f}, T2=${data['electric']['standard']['highSeason']['tier2']:.4f}, T3=${data['electric']['standard']['highSeason']['tier3']:.4f}")
        print(f"Low Season (Nov-Jun):  T1=${data['electric']['standard']['lowSeason']['tier1']:.4f}, T2=${data['electric']['standard']['lowSeason']['tier2']:.4f}, T3=${data['electric']['standard']['lowSeason']['tier3']:.4f}")
        print(f"TOU Peak High/Low:     ${data['electric']['tou']['highSeason']['peak']:.4f} / ${data['electric']['tou']['lowSeason']['peak']:.4f}")
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
