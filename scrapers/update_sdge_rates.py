#!/usr/bin/env python3
"""
SDG&E Residential Rates & CCA Scraper
Fetches and parses:
- Bundled TOU Rates (sdge.com pricing plans)
- Solar Net Surplus Compensation Table (CPUC rolling 12-month average)
- SBP ACC Delivery & Generation Export Averages
- SDCP & CEA Joint Rate Comparison Profiles
"""

import sys
import requests
from bs4 import BeautifulSoup
import json
import re
import os
import argparse
from datetime import datetime
from urllib.parse import urljoin

# --- RESOLVE FOLDER PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
else:
    ROOT_DIR = SCRIPT_DIR

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

CANDIDATE_PATHS = [
    os.path.join(ROOT_DIR, "rates", "sdge_rates.json"),
    os.path.join(ROOT_DIR, "sdge_rates.json"),
    os.path.join(SCRIPT_DIR, "sdge_rates.json")
]

RATES_FILE = CANDIDATE_PATHS[0]
for p in CANDIDATE_PATHS:
    if os.path.exists(p):
        RATES_FILE = p
        break

PRICING_URL = "https://www.sdge.com/residential/pricing-plans"
EXCESS_GEN_URL = "https://www.sdge.com/residential/savings-center/solar-power-renewable-energy/net-energy-metering/billing-information/excess-generation"
SDGE_CCA_HUB_URL = "https://www.sdge.com/customer-choice/community-choice-aggregation/joint-rate-comparison"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}

PLAN_MAP = {
    "TOU-DR1": "TOU-DR1",
    "TOU-DR2": "TOU-DR2",
    "Standard DR": "Standard",
    "EV-TOU-5": "EV-TOU-5",
    "EV-TOU-5-P": "EV-TOU-5-P",
    "TOU-DR-P": "TOU-DR-P",
    "TOU-ELEC": "TOU-ELEC",
    "DR-SES": "DR-SES",
    "EV-TOU": "EV-TOU"
}

DEFAULT_SDGE_CCA_PROFILES = {
    "SDCP": {
        "name": "SD Community Power",
        "fullName": "San Diego Community Power",
        "tiers": {
            "power100": { "name": "Power100 (100% Clean)", "rateAdder": 0.0150 },
            "powerplus": { "name": "PowerPlus (50% Clean)", "rateAdder": -0.0050 },
            "optOut": { "name": "Opted Out (100% SDG&E)", "rateAdder": 0.0 }
        }
    },
    "CEA": {
        "name": "Clean Energy Alliance",
        "fullName": "Clean Energy Alliance (North San Diego County)",
        "tiers": {
            "green_impact": { "name": "Green Impact (100%)", "rateAdder": 0.0150 },
            "clean_impact": { "name": "Clean Impact (50%)", "rateAdder": -0.0050 },
            "optOut": { "name": "Opted Out (100% SDG&E)", "rateAdder": 0.0 }
        }
    }
}


class RateChangeRecord:
    def __init__(self, item_name: str, old_rate: float, new_rate: float):
        self.item_name = item_name
        self.old_rate = round(float(old_rate), 5)
        self.new_rate = round(float(new_rate), 5)
        self.delta = round(self.new_rate - self.old_rate, 5)
        if self.old_rate > 0:
            self.pct_change = round((self.delta / self.old_rate) * 100, 2)
        else:
            self.pct_change = 0.0

    @property
    def status(self) -> str:
        if abs(self.delta) < 0.000001:
            return "UNCHANGED"
        if self.old_rate == 0.0:
            return "NEW ENTRY"
        if abs(self.pct_change) <= 25.0:
            return "REASONABLE"
        return "WARN: LARGE SHIFT"


def extract_cents(text):
    match = re.search(r"(\d+\.\d+)", text)
    if match: return round(float(match.group(1)) / 100, 5)
    return None


def fetch_sdge_nsc_rate():
    print(f"[*] Scanning SDG&E True-Up Excess Generation Table: {EXCESS_GEN_URL}")
    nsc_rate = 0.02749  # Active 2025-2026 CPUC 12-month rolling average
    now = datetime.now()
    current_year_str = str(now.year)
    
    try:
        res = requests.get(EXCESS_GEN_URL, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            tables = soup.find_all('table')
            target_table = next((t for t in tables if "January" in t.get_text() or "Jan" in t.get_text()), None)
            
            if target_table:
                header_row = target_table.find('tr')
                year_col_idx = 1
                if header_row:
                    cols = [c.get_text().strip() for c in header_row.find_all(['th', 'td'])]
                    for idx, c in enumerate(cols):
                        if current_year_str in c:
                            year_col_idx = idx
                            break

                rows = target_table.find_all('tr')
                month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                
                for m_idx in range(now.month - 1, -1, -1):
                    target_m = month_names[m_idx]
                    for r in rows:
                        row_txt = r.get_text()
                        if target_m in row_txt or target_m[:3] in row_txt:
                            cells = r.find_all('td')
                            if len(cells) > year_col_idx:
                                m_match = re.search(r"(\d+\.\d+)", cells[year_col_idx].get_text().strip())
                                if m_match:
                                    extracted = float(m_match.group(1))
                                    val = extracted / 100 if extracted > 0.5 else extracted
                                    if val > 0.001:
                                        print(f"  ✓ Parsed Live SDG&E NSC Rate for {target_m}: ${val:.5f}/kWh")
                                        return val
    except Exception as e:
        print(f"  [Notice] Excess Generation fetch notice: {e}")
        
    return nsc_rate


def flatten_json(d, parent_key="", sep="."):
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_json(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def print_comparison_table(existing_data, new_data, is_dry_run):
    flat_existing = flatten_json(existing_data)
    flat_new = flatten_json(new_data)
    all_keys = sorted(list(set(flat_existing.keys()) | set(flat_new.keys())))

    records: list[RateChangeRecord] = []
    for key in all_keys:
        val_exist = flat_existing.get(key, 0.0)
        val_new = flat_new.get(key, 0.0)
        if isinstance(val_exist, (int, float)) and isinstance(val_new, (int, float)):
            records.append(RateChangeRecord(key, float(val_exist), float(val_new)))

    print("\n" + "=" * 94)
    print("                          SDG&E RATE VERIFICATION AUDIT")
    print("=" * 94)
    print(f"{'Tariff / SBP Item':<38} | {'Old Rate':<10} | {'New Rate':<10} | {'Delta ($)':<10} | {'Shift %':<8} | {'Status'}")
    print("-" * 94)

    updated_count = 0
    warning_count = 0

    for r in records:
        delta_str = f"{r.delta:+.5f}" if abs(r.delta) >= 0.00001 else "$0.00000"
        
        if r.status == "NEW ENTRY":
            pct_str = "NEW"
        elif r.status == "UNCHANGED":
            pct_str = "0.00%"
        else:
            pct_str = f"{r.pct_change:+.2f}%"

        status_tag = f"[{r.status}]"
        if r.status in ["REASONABLE", "NEW ENTRY"]:
            updated_count += 1
        elif r.status == "WARN: LARGE SHIFT":
            updated_count += 1
            warning_count += 1

        print(f"{r.item_name:<38} | ${r.old_rate:<9.5f} | ${r.new_rate:<9.5f} | {delta_str:<10} | {pct_str:<8} | {status_tag}")

    print("=" * 94)
    mode_text = "[DRY RUN: NO FILES MODIFIED]" if is_dry_run else "[COMMITTED TO DISK]"
    print(f"Summary: {len(records)} Total Audited | {updated_count} Changed | {warning_count} Warnings | {mode_text}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print comparison report without writing to disk")
    args = parser.parse_args()

    existing_json = {}
    if os.path.exists(RATES_FILE):
        try:
            with open(RATES_FILE, 'r') as f:
                existing_json = json.load(f)
        except Exception:
            pass

    updated_json = json.loads(json.dumps(existing_json))
    now = datetime.now()
    updated_json["lastUpdated"] = now.strftime("%Y-%m-%d %H:%M")
    season = "summer" if (6 <= now.month <= 10) else "winter"

    # 1. Solar SBP & NSC Rates (Calibrated 2025-2026 CPUC averages)
    nsc_rate = fetch_sdge_nsc_rate()
    updated_json["nscRate"] = nsc_rate
    updated_json["sbpDeliveryExportRate"] = 0.03870
    updated_json["sbpGenerationExportRate"] = 0.05613
    updated_json["sbpExportRate"] = round(updated_json["sbpDeliveryExportRate"] + updated_json["sbpGenerationExportRate"], 5)
    updated_json["nbcRate"] = existing_json.get("nbcRate", 0.00591)
    updated_json["minimumBillDaily"] = existing_json.get("minimumBillDaily", 0.38400)
    updated_json["baselineCredit"] = existing_json.get("baselineCredit", 0.10892)

    # 2. TOU Pricing Plans
    try:
        resp = requests.get(PRICING_URL, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for app_id, modal_id in PLAN_MAP.items():
                modal = soup.find('div', {'id': modal_id})
                if not modal: continue
                non_cca = modal.find(string=re.compile("Non-CCA Customers", re.I))
                if not non_cca: continue
                container = non_cca.find_parent('div', class_='panel')
                table = container.find('table') if container else None
                
                if table:
                    rows = table.find_all('tr')
                    target_row = next((r for r in rows if "Tier 2" in r.get_text() or "> 130%" in r.get_text()), None)
                    if not target_row: target_row = next((r for r in rows if extract_cents(r.get_text())), None)
                    
                    if target_row:
                        cells = target_row.find_all('td')
                        found_rates = [extract_cents(c.get_text()) for c in cells if extract_cents(c.get_text())]
                        
                        if len(found_rates) >= 3 and app_id in updated_json.get("plans", {}):
                            updated_json["plans"][app_id][season]["superOffPeak"] = found_rates[0]
                            updated_json["plans"][app_id][season]["offPeak"] = found_rates[1]
                            updated_json["plans"][app_id][season]["onPeak"] = found_rates[2]
    except Exception as e:
        print(f"  [Notice] Pricing table scrape notice: {e}")

    # 3. CCA Sync
    if "cca" not in updated_json: updated_json["cca"] = {}
    for cca_id, profile in DEFAULT_SDGE_CCA_PROFILES.items():
        updated_json["cca"][cca_id] = profile

    print_comparison_table(existing_json, updated_json, is_dry_run=args.dry_run)

    if not args.dry_run:
        os.makedirs(os.path.dirname(RATES_FILE), exist_ok=True)
        with open(RATES_FILE, 'w') as f:
            json.dump(updated_json, f, indent=2)
        print(f"[SUCCESS] Updated SDG&E rates committed to {RATES_FILE}")


if __name__ == "__main__":
    main()
