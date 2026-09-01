#!/usr/bin/env python3
"""
LADWP Rate Scraper
Fetches current residential electric and water rate tables from LADWP.com.
Outputs to rates/ladwp_rates.json matching MeterWise LADWPRatesJSON schema.
"""

import sys
import os
import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime

# --- ROBUST PATH RESOLUTION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Check root directory whether script is in root or scripts/ folder
if os.path.basename(SCRIPT_DIR) == "scripts":
    ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
else:
    ROOT_DIR = SCRIPT_DIR

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Locate JSON target file
CANDIDATE_PATHS = [
    os.path.join(ROOT_DIR, "rates", "ladwp_rates.json"),
    os.path.join(ROOT_DIR, "ladwp_rates.json"),
    os.path.join(SCRIPT_DIR, "ladwp_rates.json")
]

OUTPUT_FILE = CANDIDATE_PATHS[0]
for p in CANDIDATE_PATHS:
    if os.path.exists(p):
        OUTPUT_FILE = p
        break

# --- CONFIGURATION & URLS ---
ELECTRIC_URL = "https://www.ladwp.com/account/customer-service/electric-rates/residential-rates"
WATER_URL = "https://www.ladwp.com/account/customer-service/water-rates/schedule-residential"

E_PERIOD_MAP = {
    r"January\s*-\s*March": ["janMar"],
    r"April\s*-\s*May": ["aprMay"],
    r"January\s*-\s*May": ["janMar", "aprMay"],
    r"June": ["june"],
    r"July\s*-\s*September": ["julSep"],
    r"June\s*-\s*September": ["june", "julSep"],
    r"October\s*-\s*December": ["octDec"]
}

W_PERIOD_MAP = {
    r"January\s*-\s*June": ["janMar", "aprMay", "june"],
    r"July\s*-\s*December": ["julSep", "octDec"]
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none'
}


def extract_rates(row, expected_count):
    """Extracts rates as floats, filtering for utility-sized decimals."""
    cells = row.find_all(['td', 'th'])
    found = []
    for cell in cells:
        text = cell.get_text(strip=True).replace('$', '').replace(',', '')
        match = re.search(r"(\d+\.\d+)", text)
        if match:
            val = float(match.group(1))
            if 0.01 < val < 2.0 or 5.0 < val < 40.0:
                found.append(val)
    return found[:expected_count]


def scrape_section(soup, occurrence, search_text, year_target, pattern_map, is_water=False):
    """Finds target year rates in the nth table containing search_text."""
    count = 0
    results = {}
    
    for table in soup.find_all('table'):
        if search_text.lower() in table.get_text().lower():
            count += 1
            if count == occurrence:
                in_year_block = False
                for row in table.find_all('tr'):
                    row_text = row.get_text(separator=' ', strip=True)
                    
                    if str(year_target) in row_text:
                        in_year_block = True
                        continue
                    elif any(str(prev) in row_text for prev in range(2020, int(year_target))) and str(year_target) not in row_text:
                        in_year_block = False
                    
                    if in_year_block:
                        for pattern, json_keys in pattern_map.items():
                            if re.search(pattern, row_text, re.IGNORECASE):
                                expected = 4 if is_water else 3
                                nums = extract_rates(row, expected)
                                if len(nums) >= 3:
                                    for key in json_keys:
                                        results[key] = nums
                                    print(f"  [Found] {year_target} {pattern} -> {json_keys}: {nums}")
                break
    return results


def scrape_base_rates(soup):
    """Locates and scrapes the R-1A Standard Base Rates table."""
    results = {}
    for table in soup.find_all('table'):
        table_text = table.get_text().lower()
        if "energy charge" in table_text and "base rate" in table_text:
            print("Scraping LADWP R-1A Base Rates Table...")
            for row in table.find_all('tr'):
                row_text = row.get_text(separator=' ', strip=True)
                for pattern, json_keys in E_PERIOD_MAP.items():
                    if re.search(pattern, row_text, re.IGNORECASE):
                        nums = extract_rates(row, 3)
                        if len(nums) >= 3:
                            for key in json_keys:
                                results[key] = nums
                            print(f"  [Found Base Rate] {pattern} -> {json_keys}: {nums}")
            break
    return results


def load_existing_json():
    """Safely loads current JSON file or returns base fallback schema."""
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Failed to parse existing JSON: {e}")
            
    # Fallback schema matching LADWPRatesJSON.swift
    return {
        "version": 1,
        "utility": "LADWP",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "periods": {
            "janMar": [1, 2, 3],
            "aprMay": [4, 5],
            "june": [6],
            "julSep": [7, 8, 9],
            "octDec": [10, 11, 12]
        },
        "electric": {
            "standard": {},
            "tou": {}
        },
        "water": {},
        "trash": { "flatRate": 72.80 }
    }


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("!!! DRY RUN MODE ACTIVE: No changes will be saved !!!\n")

    data = load_existing_json()
    current_year = datetime.now().year
    print(f"Scraping LADWP for year {current_year} (Target file: {OUTPUT_FILE})...")

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Fetch Electric Page
    try:
        e_resp = session.get(ELECTRIC_URL, timeout=20)
        e_resp.raise_for_status()
        e_soup = BeautifulSoup(e_resp.text, 'html.parser')
    except Exception as e:
        print(f"[ERROR] Could not fetch LADWP electric page: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Scrape R-1A Standard and Base Rates
    r1a_site_data = scrape_section(e_soup, 1, "Total Consumption Charge", current_year, E_PERIOD_MAP)
    if not r1a_site_data:
        # Fallback to previous year anchor if new calendar year has not posted tables yet
        r1a_site_data = scrape_section(e_soup, 1, "Total Consumption Charge", current_year - 1, E_PERIOD_MAP)

    r1a_base_site_data = scrape_base_rates(e_soup)

    # 3. Scrape R-1B TOU
    r1b_site_data = scrape_section(e_soup, 2, "Total Consumption Charge", current_year, E_PERIOD_MAP)
    if not r1b_site_data:
        r1b_site_data = scrape_section(e_soup, 2, "Total Consumption Charge", current_year - 1, E_PERIOD_MAP)

    # 4. Fetch and Scrape Water Page
    try:
        w_resp = session.get(WATER_URL, timeout=20)
        w_resp.raise_for_status()
        w_soup = BeautifulSoup(w_resp.text, 'html.parser')
        water_site_data = scrape_section(w_soup, 1, "Total Consumption Charge", current_year, W_PERIOD_MAP, is_water=True)
        if not water_site_data:
            water_site_data = scrape_section(w_soup, 1, "Total Consumption Charge", current_year - 1, W_PERIOD_MAP, is_water=True)
    except Exception as e:
        print(f"[WARN] Water rate fetch failed, preserving existing water data: {e}")
        water_site_data = {}

    updated = False

    # Apply Electric R-1A
    for key, rates in r1a_site_data.items():
        existing = data["electric"]["standard"].get(key, {})
        base_rates = r1a_base_site_data.get(key, [
            existing.get("baseTier1", 0.07142),
            existing.get("baseTier2", 0.13001),
            existing.get("baseTier3", 0.13001)
        ])
        
        new_val = {
            "tier1": rates[0], 
            "tier2": rates[1], 
            "tier3": rates[2],
            "baseTier1": base_rates[0],
            "baseTier2": base_rates[1],
            "baseTier3": base_rates[2]
        }
        if data["electric"]["standard"].get(key) != new_val:
            print(f"  [UPDATE] Electric R-1A {key} -> {new_val}")
            data["electric"]["standard"][key] = new_val
            updated = True

    # Apply Electric R-1B
    for key, rates in r1b_site_data.items():
        new_val = {"tier1": rates[0], "tier2": rates[1], "tier3": rates[2]}
        if data["electric"]["tou"].get(key) != new_val:
            print(f"  [UPDATE] Electric R-1B {key} -> {new_val}")
            data["electric"]["tou"][key] = new_val
            updated = True

    # Apply Water
    for key, rates in water_site_data.items():
        if len(rates) >= 4:
            new_val = {"tier1": rates[0], "tier2": rates[1], "tier3": rates[2], "tier4": rates[3]}
            if data["water"].get(key) != new_val:
                print(f"  [UPDATE] Water {key} -> {new_val}")
                data["water"][key] = new_val
                updated = True

    if updated:
        if dry_run:
            print("\n>>> FINISH: Changes detected but not saved (Dry Run).")
        else:
            data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            data["version"] = data.get("version", 1) + 1
            os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
            with open(OUTPUT_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"\n>>> FINISH: Success! Updated {OUTPUT_FILE}")
    else:
        print("\n>>> FINISH: No new rate changes detected. JSON is up to date.")


if __name__ == "__main__":
    main()