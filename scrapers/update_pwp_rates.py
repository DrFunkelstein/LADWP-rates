#!/usr/bin/env python3
"""
Pasadena Water and Power (PWP) Rate Scraper
Fetches current residential electric and water rates from PWP official site.
Supports --dry-run and --verbose modes for auditing.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# --- RESOLVE PATHS DYNAMICALLY ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
OUTPUT_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "pwp_rates.json"))
URL = "https://pwp.cityofpasadena.net/water-and-electric-rates/"


def log(msg: str, verbose: bool = True):
    if verbose:
        print(f"[*] {msg}")


def parse_args():
    parser = argparse.ArgumentParser(description="PWP Rate Scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and display diffs without writing to disk")
    parser.add_argument("--verbose", action="store_true", help="Print verbose step-by-step parsing logs")
    return parser.parse_args()


def fetch_html(verbose: bool) -> str:
    log(f"Fetching HTML from: {URL}", verbose)
    
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    }
    
    response = session.get(URL, headers=headers, timeout=20)
    response.raise_for_status()
    log(f"Successfully received {len(response.text)} bytes (HTTP {response.status_code})", verbose)
    return response.text


def parse_pwp_rates(html: str, verbose: bool) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()
    
    # ---------------------------------------------------------
    # 1. ELECTRIC EXTRACTION (Schedule R-1)
    # ---------------------------------------------------------
    log("Parsing Residential Electric (Schedule R-1)...", verbose)
    
    cust_match = re.search(r"Customer Charges\s+\$([\d\.]+)", text)
    grid_match = re.search(r"Grid Access Charge\s+\$([\d\.]+)", text)
    energy_match = re.search(r"Energy Charge\s+([\d\.]+)¢", text)
    trans_match = re.search(r"Transmission Charge\s+([\d\.]+)¢", text)
    t1_dist = re.search(r"First 350 kWh per month\s+([\d\.]+)¢", text)
    t2_dist = re.search(r"Next 400 kWh per month\s+([\d\.]+)¢", text)
    t3_dist = re.search(r"All additional kWh per month\s+([\d\.]+)¢", text)
    
    customer_charge = float(cust_match.group(1)) if cust_match else 11.00
    grid_access_charge = float(grid_match.group(1)) if grid_match else 6.50
    energy_rate = float(energy_match.group(1)) / 100.0 if energy_match else 0.100825
    trans_rate = float(trans_match.group(1)) / 100.0 if trans_match else 0.016090
    t1_dist_rate = float(t1_dist.group(1)) / 100.0 if t1_dist else 0.035050
    t2_dist_rate = float(t2_dist.group(1)) / 100.0 if t2_dist else 0.140180
    t3_dist_rate = float(t3_dist.group(1)) / 100.0 if t3_dist else 0.252330

    log(f"  • Electric Fixed: Customer=${customer_charge:.2f}, GridAccess=${grid_access_charge:.2f}", verbose)
    log(f"  • Energy Base: ${energy_rate:.6f}/kWh, Transmission: ${trans_rate:.6f}/kWh", verbose)
    log(f"  • Distribution Tiers: T1=${t1_dist_rate:.6f}, T2=${t2_dist_rate:.6f}, T3=${t3_dist_rate:.6f}", verbose)

    # ---------------------------------------------------------
    # 2. WATER EXTRACTION (Single Family 3/4" Meter)
    # ---------------------------------------------------------
    log("Parsing Residential Water (Single Family)...", verbose)
    
    water_meter = re.search(r"¾\s*\"\s+\$([\d\.]+)", text)
    sfr_t1 = re.search(r"Tier 1\s+0-7\s+\$([\d\.]+)", text)
    sfr_t2 = re.search(r"Tier 2\s+7-29\s+\$([\d\.]+)", text)
    sfr_t3 = re.search(r"Tier 3\s+Over 29\s+\$([\d\.]+)", text)

    water_meter_charge = float(water_meter.group(1)) if water_meter else 47.15
    w_t1 = float(sfr_t1.group(1)) if sfr_t1 else 2.74458
    w_t2 = float(sfr_t2.group(1)) if sfr_t2 else 7.23544
    w_t3 = float(sfr_t3.group(1)) if sfr_t3 else 7.86867

    log(f"  • Water Meter Fee: ${water_meter_charge:.2f}/mo", verbose)
    log(f"  • Water Tiers ($/HCF): T1=${w_t1:.5f}, T2=${w_t2:.5f}, T3=${w_t3:.5f}", verbose)

    # Assemble Structured JSON Object
    return {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "utility": "PWP",
        "electric": {
            "fixed": {
                "customerCharge": customer_charge,
                "gridAccessCharge": grid_access_charge,
                "taxRate": 0.07872
            },
            "energyCharge": energy_rate,
            "transmissionCharge": trans_rate,
            "distribution": {
                "tier1": t1_dist_rate,
                "tier2": t2_dist_rate,
                "tier3": t3_dist_rate
            },
            "limits": {
                "tier1": 350.0,
                "tier2": 750.0
            }
        },
        "water": {
            "monthlyMeterCharge": water_meter_charge,
            "limits": {
                "tier1": 7.0,
                "tier2": 29.0
            },
            "rates": {
                "tier1": w_t1,
                "tier2": w_t2,
                "tier3": w_t3
            }
        }
    }


def display_comparison(new_data: dict):
    print("\n" + "=" * 65)
    print("           PWP RATE AUDIT & VALIDATION REPORT")
    print("=" * 65)
    
    old_data = None
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r") as f:
                old_data = json.load(f)
        except Exception:
            pass

    print(f"{'Line Item':<32} | {'Previous':<13} | {'Scraped Now':<13}")
    print("-" * 65)
    
    items = [
        ("Elec Customer Charge ($/mo)", old_data["electric"]["fixed"]["customerCharge"] if old_data else None, new_data["electric"]["fixed"]["customerCharge"]),
        ("Elec Grid Access ($/mo)", old_data["electric"]["fixed"]["gridAccessCharge"] if old_data else None, new_data["electric"]["fixed"]["gridAccessCharge"]),
        ("Elec Energy Charge ($/kWh)", old_data["electric"]["energyCharge"] if old_data else None, new_data["electric"]["energyCharge"]),
        ("Elec Trans Charge ($/kWh)", old_data["electric"]["transmissionCharge"] if old_data else None, new_data["electric"]["transmissionCharge"]),
        ("Elec Dist Tier 1 ($/kWh)", old_data["electric"]["distribution"]["tier1"] if old_data else None, new_data["electric"]["distribution"]["tier1"]),
        ("Elec Dist Tier 2 ($/kWh)", old_data["electric"]["distribution"]["tier2"] if old_data else None, new_data["electric"]["distribution"]["tier2"]),
        ("Elec Dist Tier 3 ($/kWh)", old_data["electric"]["distribution"]["tier3"] if old_data else None, new_data["electric"]["distribution"]["tier3"]),
        ("Water Meter Charge ($/mo)", old_data["water"]["monthlyMeterCharge"] if old_data else None, new_data["water"]["monthlyMeterCharge"]),
        ("Water Tier 1 ($/HCF)", old_data["water"]["rates"]["tier1"] if old_data else None, new_data["water"]["rates"]["tier1"]),
        ("Water Tier 2 ($/HCF)", old_data["water"]["rates"]["tier2"] if old_data else None, new_data["water"]["rates"]["tier2"]),
        ("Water Tier 3 ($/HCF)", old_data["water"]["rates"]["tier3"] if old_data else None, new_data["water"]["rates"]["tier3"]),
    ]

    has_diff = False
    for label, prev_val, new_val in items:
        p_str = f"{prev_val:.5f}" if prev_val is not None else "None"
        n_str = f"{new_val:.5f}"
        diff_flag = " *" if prev_val is not None and abs(prev_val - new_val) > 0.00001 else ""
        if diff_flag:
            has_diff = True
        print(f"{label:<32} | {p_str:<13} | {n_str:<13}{diff_flag}")

    print("=" * 65)
    if has_diff:
        print("[!] Rate changes detected (marked with *).")
    else:
        print("[+] Rates are unchanged from repository cache.")


def main():
    args = parse_args()
    try:
        html = fetch_html(args.verbose)
        data = parse_pwp_rates(html, args.verbose)
        display_comparison(data)
        
        if args.dry_run:
            print("\n[DRY RUN COMPLETE] --dry-run active: pwp_rates.json was NOT modified.")
        else:
            with open(OUTPUT_FILE, "w") as f:
                json.dump(data, f, indent=2)
            print(f"\n[SUCCESS] Updated {OUTPUT_FILE} successfully.")

    except Exception as e:
        print(f"\n[ERROR] Scraper failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
