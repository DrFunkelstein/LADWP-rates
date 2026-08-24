#!/usr/bin/env python3
"""
SMUD Residential Rates Scraper
Fetches published residential tariff tables from SMUD (Sacramento Municipal Utility District)
"""

import os
import re
import json
import argparse
import datetime
import requests
from bs4 import BeautifulSoup

SMUD_TOD_URL = "https://www.smud.org/Rate-Information/Residential-rates/Time-of-Day-5-8pm-Rate/Rate-details-and-holidays"
SMUD_FIXED_URL = "https://www.smud.org/Rate-Information/Residential-rates/Fixed-Rate"
SMUD_MAIN_URL = "https://www.smud.org/Rate-Information/Residential-rates"
JSON_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "rates", "smud_rates.json")

# Verified Baseline Fallbacks (2026 Tariff)
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

    rates_data = json.loads(json.dumps(DEFAULT_RATES))
    rates_data["lastUpdated"] = datetime.datetime.now().strftime("%Y-%m-%d")

    # 1. Fetch Time-of-Day (R-TOD) Rates
    try:
        res = requests.get(SMUD_TOD_URL, headers=headers, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            text = soup.get_text()

            # Summer Peak ($0.3765)
            summer_peak = re.search(r"Peak\s+5\s*p\.m\.\s*[–-]\s*8\s*p\.m\.\s*\$(\d+\.\d+)", text, re.IGNORECASE)
            if summer_peak and 0.20 <= float(summer_peak.group(1)) <= 0.60:
                rates_data["electric"]["rTod"]["summer"]["peak"] = float(summer_peak.group(1))

            # Summer Mid-Peak ($0.2139)
            summer_mid = re.search(r"Mid-Peak.*?\$(\d+\.\d+)", text, re.IGNORECASE)
            if summer_mid and 0.10 <= float(summer_mid.group(1)) <= 0.35:
                rates_data["electric"]["rTod"]["summer"]["midPeak"] = float(summer_mid.group(1))

            # Summer Off-Peak ($0.1550)
            summer_off = re.search(r"Off-Peak\s+Midnight\s*[–-]\s*noon\s*\$(\d+\.\d+)", text, re.IGNORECASE)
            if summer_off and 0.08 <= float(summer_off.group(1)) <= 0.25:
                rates_data["electric"]["rTod"]["summer"]["offPeak"] = float(summer_off.group(1))

            # Non-Summer Peak ($0.1776)
            winter_peak = re.search(r"Non-summer.*?Peak\s+5\s*p\.m\.\s*[–-]\s*8\s*p\.m\.\s*\$(\d+\.\d+)", text, re.IGNORECASE | re.DOTALL)
            if winter_peak and 0.10 <= float(winter_peak.group(1)) <= 0.30:
                rates_data["electric"]["rTod"]["nonSummer"]["peak"] = float(winter_peak.group(1))

            # Non-Summer Off-Peak ($0.1285)
            winter_off = re.search(r"Non-summer.*?Off-Peak.*?\$(\d+\.\d+)", text, re.IGNORECASE | re.DOTALL)
            if winter_off and 0.05 <= float(winter_off.group(1)) <= 0.20:
                rates_data["electric"]["rTod"]["nonSummer"]["offPeak"] = float(winter_off.group(1))

    except Exception as e:
        print(f"Notice: TOD details parsing error: {e}.")

    # 2. Fetch Fixed Rate (Schedule R Seasonal Rates)
    try:
        res_fixed = requests.get(SMUD_FIXED_URL, headers=headers, timeout=15)
        if res_fixed.status_code == 200:
            soup_fixed = BeautifulSoup(res_fixed.text, "html.parser")
            text_fixed = soup_fixed.get_text()

            # Summer Fixed Rate (All Day: $0.2189 / 21.89¢)
            summer_fixed = re.search(r"Summer.*?All\s*day.*?\$(\d+\.\d+)", text_fixed, re.IGNORECASE | re.DOTALL)
            if summer_fixed and 0.15 <= float(summer_fixed.group(1)) <= 0.35:
                rates_data["electric"]["fixedRate"]["summer"] = float(summer_fixed.group(1))
                print(f"  ✓ Fixed Rate Summer: ${rates_data['electric']['fixedRate']['summer']}/kWh")

            # Non-Summer Fixed Rate (All Day: $0.1371 / 13.71¢)
            winter_fixed = re.search(r"Non-summer.*?All\s*day.*?\$(\d+\.\d+)", text_fixed, re.IGNORECASE | re.DOTALL)
            if winter_fixed and 0.08 <= float(winter_fixed.group(1)) <= 0.25:
                rates_data["electric"]["fixedRate"]["nonSummer"] = float(winter_fixed.group(1))
                print(f"  ✓ Fixed Rate Non-Summer: ${rates_data['electric']['fixedRate']['nonSummer']}/kWh")

    except Exception as e:
        print(f"Notice: Fixed rate parsing error: {e}.")

    # 3. Fetch SIFC and Solar Export Rate from Main Page
    try:
        res_main = requests.get(SMUD_MAIN_URL, headers=headers, timeout=15)
        if res_main.status_code == 200:
            text_main = res_main.text
            sifc = re.search(r"System Infrastructure Fixed Charge.*?\$(\d+\.\d{2})", text_main, re.IGNORECASE)
            if sifc and 15.0 <= float(sifc.group(1)) <= 40.0:
                rates_data["electric"]["sifcMonthly"] = float(sifc.group(1))

            ssr = re.search(r"Solar.*?Storage.*?(\d+\.\d+)¢", text_main, re.IGNORECASE)
            if ssr and 5.0 <= float(ssr.group(1)) <= 20.0:
                rates_data["electric"]["ssrSolarExportRate"] = round(float(ssr.group(1)) / 100.0, 4)

    except Exception as e:
        print(f"Notice: Main page SIFC/SSR parsing error: {e}.")

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
