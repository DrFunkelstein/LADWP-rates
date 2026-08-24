#!/usr/bin/env python3
"""
SMUD Residential Rates Scraper
Fetches published residential tariff tables from SMUD (Sacramento Municipal Utility District)
URL: https://www.smud.org/Rate-Information/Residential-rates
"""

import os
import re
import json
import argparse
import datetime
import requests
from bs4 import BeautifulSoup

SMUD_RATES_URL = "https://www.smud.org/Rate-Information/Residential-rates"
JSON_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "rates", "smud_rates.json")

# Verified Baseline Fallbacks (June 1, 2026 Tariff Update)
DEFAULT_RATES = {
    "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d"),
    "utility": "SMUD",
    "electric": {
        "sifcMonthly": 27.00,
        "sifcLowUseMonthly": 17.00,
        "medicalDiscountMonthly": 15.00,
        "ssrSolarExportRate": 0.0960,
        "rTod": {
            "summer": {
                "peak": 0.3765,
                "midPeak": 0.2139,
                "offPeak": 0.1550
            },
            "nonSummer": {
                "peak": 0.1776,
                "offPeak": 0.1285
            }
        },
        "fixedRate": {
            "summer": 0.2189,
            "nonSummer": 0.1371
        },
        "lowUseTod": {
            "summer": {
                "peak": 0.4154,
                "midPeak": 0.2514,
                "offPeak": 0.1920
            },
            "nonSummer": {
                "peak": 0.2148,
                "offPeak": 0.1654
            }
        }
    }
}


def parse_smud_rates():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    rates_data = DEFAULT_RATES.copy()
    rates_data["lastUpdated"] = datetime.datetime.now().strftime("%Y-%m-%d")

    try:
        print(f"Fetching SMUD rates from: {SMUD_RATES_URL}")
        response = requests.get(SMUD_RATES_URL, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text()

        # 1. Parse System Infrastructure Fixed Charge (SIFC)
        # Look for standard SIFC (e.g. "$27.00" or "$27.00/month")
        sifc_match = re.search(r"System Infrastructure Fixed Charge.*?\$(\d+\.\d{2})", page_text, re.IGNORECASE)
        if sifc_match:
            rates_data["electric"]["sifcMonthly"] = float(sifc_match.group(1))
            print(f"  ✓ SIFC Monthly: ${rates_data['electric']['sifcMonthly']}")

        # 2. Parse Time-of-Day (R-TOD) Rates
        # Scans for Summer Peak (5-8 PM) (e.g., 37.65¢ or $0.3765)
        summer_peak_match = re.search(r"Summer.*?Peak.*?(\d+\.\d+)¢", page_text, re.IGNORECASE | re.DOTALL)
        if summer_peak_match:
            rates_data["electric"]["rTod"]["summer"]["peak"] = round(float(summer_peak_match.group(1)) / 100.0, 4)
            print(f"  ✓ R-TOD Summer Peak: ${rates_data['electric']['rTod']['summer']['peak']}/kWh")

        # 3. Parse Solar Net Billing (Schedule SSR)
        ssr_match = re.search(r"Solar.*?Storage.*?(\d+\.\d+)¢", page_text, re.IGNORECASE)
        if ssr_match:
            rates_data["electric"]["ssrSolarExportRate"] = round(float(ssr_match.group(1)) / 100.0, 4)
            print(f"  ✓ SSR Solar Export Rate: ${rates_data['electric']['ssrSolarExportRate']}/kWh")

    except Exception as e:
        print(f"Warning: Scraper parsing encountered error: {e}. Using verified default rate schema.")

    return rates_data


def main():
    parser = argparse.ArgumentParser(description="Update SMUD rates JSON")
    parser.add_argument("--dry-run", action="store_true", help="Print updated JSON without writing to disk")
    args = parser.parse_args()

    rates_json = parse_smud_rates()
    formatted_output = json.dumps(rates_json, indent=2)

    if args.dry_run:
        print("\n--- DRY RUN OUTPUT ---")
        print(formatted_output)
    else:
        os.makedirs(os.path.dirname(JSON_OUTPUT_PATH), exist_ok=True)
        with open(JSON_OUTPUT_PATH, "w") as f:
            f.write(formatted_output + "\n")
        print(f"\nSuccessfully wrote SMUD rates to {JSON_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
