#!/usr/bin/env python3
"""
Glendale Water & Power (GWP) Rate Scraper
Fetches current residential electric rates from GWP's official website.
Outputs to rates/gwp_rates.json matching MeterWise GWPRatesJSON schema.
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

# --- ROBUST PATH RESOLUTION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
else:
    ROOT_DIR = SCRIPT_DIR

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

CANDIDATE_PATHS = [
    os.path.join(ROOT_DIR, "rates", "gwp_rates.json"),
    os.path.join(ROOT_DIR, "gwp_rates.json"),
    os.path.join(SCRIPT_DIR, "gwp_rates.json")
]

OUTPUT_FILE = CANDIDATE_PATHS[0]
for p in CANDIDATE_PATHS:
    if os.path.exists(p):
        OUTPUT_FILE = p
        break


def parse_args():
    parser = argparse.ArgumentParser(description="GWP Rate Scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without writing to disk")
    parser.add_argument("--verbose", action="store_true", help="Print verbose logs")
    return parser.parse_args()


def fetch_html(verbose: bool) -> str:
    if verbose:
        print(f"[*] Fetching HTML from: {URL}")
    
    session = requests.Session()
    # Comprehensive anti-blocking header set for municipal CMS / Cloudflare / Granicus
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }
    
    response = session.get(URL, headers=headers, timeout=25)
    response.raise_for_status()
    return response.text


def parse_gwp_rates(html: str, verbose: bool) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")

    # 1. Customer Charge ($0.75/day)
    cust_match = re.search(r"Customer\s+Charge\s*-\s*per\s+meter\s+per\s+day\s+\$([\d\.]+)", text, re.IGNORECASE)
    cust_charge = float(cust_match.group(1)) if cust_match else 0.75

    # 2. Standard L-1-A High Season (July through October)
    h_t1 = re.search(r"July\s+through\s+October[^\$]+?First\s+10\s*kWh[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)
    h_t2 = re.search(r"July\s+through\s+October[^\$]+?Next\s+10\s*kWh[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)
    h_t3 = re.search(r"July\s+through\s+October[^\$]+?Remaining\s+kWh[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)

    # 3. Standard L-1-A Low Season (November through June)
    l_t1 = re.search(r"November\s+through\s+June[^\$]+?First\s+10\s*kWh[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)
    l_t2 = re.search(r"November\s+through\s+June[^\$]+?Next\s+10\s*kWh[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)
    l_t3 = re.search(r"November\s+through\s+June[^\$]+?Remaining\s+kWh[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)

    # 4. TOU L-1-B High Season
    h_tou_base = re.search(r"L-1-B[\s\S]*?July\s+through\s+October[\s\S]*?Base\s+Period[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)
    h_tou_peak = re.search(r"L-1-B[\s\S]*?July\s+through\s+October[\s\S]*?Peak\s+Period[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)

    # 5. TOU L-1-B Low Season
    l_tou_base = re.search(r"L-1-B[\s\S]*?November\s+through\s+June[\s\S]*?Base\s+Period[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)
    l_tou_peak = re.search(r"L-1-B[\s\S]*?November\s+through\s+June[\s\S]*?Peak\s+Period[^\$]+?\$([\d\.]+)", text, re.IGNORECASE)

    # Read existing JSON to preserve water structure if present
    existing_water = {
        "dailyCustomerCharge": 0.881,
        "limits": { "tier1": 8.0, "tier2": 15.0 },
        "rates": { "tier1": 2.80, "tier2": 4.11, "tier3": 4.28 }
    }
    
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r') as f:
                old_data = json.load(f)
                if "water" in old_data:
                    existing_water = old_data["water"]
        except Exception:
            pass

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
        "water": existing_water
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
        print(f"Target Output File:    {OUTPUT_FILE}")
        print(f"Customer Charge:       ${data['electric']['dailyCustomerCharge']:.2f}/day")
        print(f"High Season (Jul-Oct): T1=${data['electric']['standard']['highSeason']['tier1']:.4f}, T2=${data['electric']['standard']['highSeason']['tier2']:.4f}, T3=${data['electric']['standard']['highSeason']['tier3']:.4f}")
        print(f"Low Season (Nov-Jun):  T1=${data['electric']['standard']['lowSeason']['tier1']:.4f}, T2=${data['electric']['standard']['lowSeason']['tier2']:.4f}, T3=${data['electric']['standard']['lowSeason']['tier3']:.4f}")
        print(f"TOU Peak High/Low:     ${data['electric']['tou']['highSeason']['peak']:.4f} / ${data['electric']['tou']['lowSeason']['peak']:.4f}")
        print("=" * 55)

        if args.dry_run:
            print("[DRY RUN COMPLETE] File was not modified.")
        else:
            os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
            with open(OUTPUT_FILE, "w") as f:
                json.dump(data, f, indent=2)
            print(f"[SUCCESS] Updated {OUTPUT_FILE} successfully.")
    except Exception as e:
        print(f"[ERROR] Scraper failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()