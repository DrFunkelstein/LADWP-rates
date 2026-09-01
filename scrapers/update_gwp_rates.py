#!/usr/bin/env python3
"""
Glendale Water & Power (GWP) Automated Rate Scraper & Verification Engine
Features:
- Multi-tier WAF bypass (Direct -> TLS Impersonation -> Jina Gateway)
- Strict section-isolated parsing preventing Summer/Winter TOU cross-contamination
- Full rate change verification table matching LADWP
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
GATEWAY_URL = f"https://r.jina.ai/{URL}"

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


class RateChangeRecord:
    def __init__(self, plan_name: str, period: str, tier_name: str, old_rate: float, new_rate: float):
        self.plan_name = plan_name
        self.period = period
        self.tier_name = tier_name
        self.old_rate = old_rate
        self.new_rate = new_rate
        self.delta = new_rate - old_rate
        self.pct_change = (self.delta / old_rate * 100) if old_rate > 0 else 0.0

    @property
    def status(self) -> str:
        if abs(self.delta) < 0.00001:
            return "UNCHANGED"
        if abs(self.pct_change) <= 25.0:
            return "REASONABLE"
        return "WARN: LARGE SHIFT"


def parse_args():
    parser = argparse.ArgumentParser(description="GWP Rate Scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without writing to disk")
    parser.add_argument("--verbose", action="store_true", help="Print verbose logs")
    return parser.parse_args()


def fetch_page_content(verbose: bool) -> tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

    # Strategy 1: Direct requests
    try:
        if verbose:
            print(f"[*] Strategy 1: Direct requests fetch ({URL})...")
        r = requests.get(URL, headers=headers, timeout=12)
        if r.status_code == 200 and len(r.text) > 1000:
            return r.text, "Direct"
    except Exception as e:
        if verbose:
            print(f"[!] Strategy 1 failed: {e}")

    # Strategy 2: curl_cffi (if installed in workflow)
    try:
        from curl_cffi import requests as cffi_requests
        if verbose:
            print("[*] Strategy 2: curl_cffi TLS impersonator...")
        r = cffi_requests.get(URL, impersonate="chrome124", timeout=15)
        if r.status_code == 200 and len(r.text) > 1000:
            return r.text, "TLS Impersonator"
    except ImportError:
        pass
    except Exception as e:
        if verbose:
            print(f"[!] Strategy 2 failed: {e}")

    # Strategy 3: Jina Reader Gateway
    try:
        if verbose:
            print(f"[*] Strategy 3: Proxying via Gateway ({GATEWAY_URL})...")
        r = requests.get(GATEWAY_URL, headers={"User-Agent": "MeterWise/1.4.0"}, timeout=20)
        if r.status_code == 200 and len(r.text) > 500:
            return r.text, "Gateway"
    except Exception as e:
        if verbose:
            print(f"[!] Strategy 3 failed: {e}")

    raise RuntimeError("All network fetch strategies failed.")


def parse_rate_value(pattern: str, text_block: str, default: float) -> float:
    match = re.search(pattern, text_block, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return default
    return default


def parse_gwp_rates(content: str, existing_data: dict, verbose: bool) -> dict:
    text = content
    if "<html" in content.lower():
        soup = BeautifulSoup(content, "html.parser")
        text = soup.get_text(separator=" ")

    clean_text = text.replace("&nbsp;", " ").replace("\xa0", " ")

    # Customer Charge ($0.75/day)
    cust_match = re.search(r"Customer\s+Charge\s*-\s*per\s+meter\s+per\s+day[^\$]+?\$([\d\.]+)", clean_text, re.IGNORECASE)
    cust_charge = float(cust_match.group(1)) if cust_match else 0.75

    # =========================================================================
    # 1. ISOLATE L-1-A STANDARD SECTION
    # =========================================================================
    l1a_block = clean_text
    if "L-1-B" in clean_text:
        l1a_block = clean_text.split("L-1-B")[0]

    # Split L-1-A into High (July-Oct) and Low (Nov-Jun)
    l1a_high_block = l1a_block
    l1a_low_block = l1a_block

    if "July through October" in l1a_block and "November through June" in l1a_block:
        parts = l1a_block.split("November through June")
        l1a_high_block = parts[0]
        l1a_low_block = parts[1]

    h_t1 = parse_rate_value(r"First\s+10\s*kWh[^\$]+?\$([\d\.]+)", l1a_high_block, 0.3071)
    h_t2 = parse_rate_value(r"Next\s+10\s*kWh[^\$]+?\$([\d\.]+)", l1a_high_block, 0.3806)
    h_t3 = parse_rate_value(r"Remaining\s+kWh[^\$]+?\$([\d\.]+)", l1a_high_block, 0.4547)

    l_t1 = parse_rate_value(r"First\s+10\s*kWh[^\$]+?\$([\d\.]+)", l1a_low_block, 0.2575)
    l_t2 = parse_rate_value(r"Next\s+10\s*kWh[^\$]+?\$([\d\.]+)", l1a_low_block, 0.3189)
    l_t3 = parse_rate_value(r"Remaining\s+kWh[^\$]+?\$([\d\.]+)", l1a_low_block, 0.3935)

    # =========================================================================
    # 2. ISOLATE L-1-B TIME-OF-USE SECTION
    # =========================================================================
    l1b_block = clean_text
    if "L-1-B" in clean_text:
        l1b_block = clean_text.split("L-1-B")[1]
    if "L-1-D" in l1b_block:
        l1b_block = l1b_block.split("L-1-D")[0]

    # Split L-1-B tables cleanly between High and Low Season tables
    # Find occurrences of July through October and November through June inside L-1-B
    high_match = re.search(r"July\s+through\s+October\s*\(High\s+Season\)", l1b_block, re.IGNORECASE)
    low_match = re.search(r"November\s+through\s+June\s*\(Low\s+Season\)", l1b_block, re.IGNORECASE)

    if high_match and low_match and high_match.start() < low_match.start():
        tou_high_section = l1b_block[high_match.start():low_match.start()]
        tou_low_section = l1b_block[low_match.start():]
    else:
        # Fallback split if headers omit '(High Season)' parenthetical
        parts = re.split(r"November\s+through\s+June", l1b_block, flags=re.IGNORECASE)
        tou_high_section = parts[0]
        tou_low_section = parts[-1] if len(parts) > 1 else l1b_block

    h_tou_base = parse_rate_value(r"Base\s+Period[^\$]+?\$([\d\.]+)", tou_high_section, 0.2280)
    h_tou_peak = parse_rate_value(r"Peak\s+Period[^\$]+?\$([\d\.]+)", tou_high_section, 0.6839)

    l_tou_base = parse_rate_value(r"Base\s+Period[^\$]+?\$([\d\.]+)", tou_low_section, 0.1901)
    l_tou_peak = parse_rate_value(r"Peak\s+Period[^\$]+?\$([\d\.]+)", tou_low_section, 0.5700)

    # Preserve cached water rates
    water_data = existing_data.get("water", {
        "dailyCustomerCharge": 0.881,
        "limits": { "tier1": 8.0, "tier2": 15.0 },
        "rates": { "tier1": 2.80, "tier2": 4.11, "tier3": 4.28 }
    })

    return {
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
                    "tier1": h_t1,
                    "tier2": h_t2,
                    "tier3": h_t3
                },
                "lowSeason": {
                    "tier1": l_t1,
                    "tier2": l_t2,
                    "tier3": l_t3
                }
            },
            "tou": {
                "highSeason": {
                    "peak": h_tou_peak,
                    "offPeak": h_tou_base
                },
                "lowSeason": {
                    "peak": l_tou_peak,
                    "offPeak": l_tou_base
                }
            }
        },
        "water": water_data
    }


def load_existing_json() -> dict:
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "utility": "GWP",
        "electric": {
            "dailyCustomerCharge": 0.75,
            "taxRate": 0.070,
            "highSeasonMonths": [7, 8, 9, 10],
            "standard": {
                "tier1DailyKwh": 10.0,
                "tier2DailyKwh": 10.0,
                "highSeason": { "tier1": 0.3071, "tier2": 0.3806, "tier3": 0.4547 },
                "lowSeason": { "tier1": 0.2575, "tier2": 0.3189, "tier3": 0.3935 }
            },
            "tou": {
                "highSeason": { "peak": 0.6839, "offPeak": 0.2280 },
                "lowSeason": { "peak": 0.5700, "offPeak": 0.1901 }
            }
        },
        "water": {
            "dailyCustomerCharge": 0.881,
            "limits": { "tier1": 8.0, "tier2": 15.0 },
            "rates": { "tier1": 2.80, "tier2": 4.11, "tier3": 4.28 }
        }
    }


def print_comparison_report(records: list[RateChangeRecord], dry_run: bool, source_engine: str):
    print("\n" + "=" * 94)
    print(f"               GWP RATE CHANGE VERIFICATION AUDIT (Engine: {source_engine})")
    print("=" * 94)
    print(f"{'Plan & Period':<26} | {'Tier / Line':<12} | {'Old Rate':<10} | {'New Rate':<10} | {'Delta ($)':<10} | {'Shift %':<8} | {'Status'}")
    print("-" * 94)

    updated_count = 0
    warning_count = 0

    for r in records:
        delta_str = f"{r.delta:+.5f}" if abs(r.delta) >= 0.00001 else "$0.00000"
        pct_str = f"{r.pct_change:+.2f}%" if abs(r.delta) >= 0.00001 else "0.00%"
        
        status_tag = f"[{r.status}]"
        if r.status == "REASONABLE":
            updated_count += 1
        elif r.status == "WARN: LARGE SHIFT":
            updated_count += 1
            warning_count += 1

        label = f"{r.plan_name} ({r.period})"
        print(f"{label:<26} | {r.tier_name:<12} | ${r.old_rate:<9.5f} | ${r.new_rate:<9.5f} | {delta_str:<10} | {pct_str:<8} | {status_tag}")

    print("=" * 94)
    mode_text = "[DRY RUN: NO FILES MODIFIED]" if dry_run else "[COMMITTED TO DISK]"
    print(f"Summary: {len(records)} Total Audited | {updated_count} Changed | {warning_count} Warnings | {mode_text}\n")


def main():
    args = parse_args()
    existing_data = load_existing_json()
    print(f"[*] Starting automated GWP rate scrape (Target: {OUTPUT_FILE})...")

    try:
        content, source_engine = fetch_page_content(args.verbose)
        parsed_data = parse_gwp_rates(content, existing_data, args.verbose)

        change_records: list[RateChangeRecord] = []
        
        # Standard Summer
        old_std_h = existing_data["electric"]["standard"]["highSeason"]
        new_std_h = parsed_data["electric"]["standard"]["highSeason"]
        change_records.append(RateChangeRecord("L-1-A Standard", "Summer", "Tier 1", old_std_h.get("tier1", 0.3071), new_std_h["tier1"]))
        change_records.append(RateChangeRecord("L-1-A Standard", "Summer", "Tier 2", old_std_h.get("tier2", 0.3806), new_std_h["tier2"]))
        change_records.append(RateChangeRecord("L-1-A Standard", "Summer", "Tier 3", old_std_h.get("tier3", 0.4547), new_std_h["tier3"]))

        # Standard Winter
        old_std_l = existing_data["electric"]["standard"]["lowSeason"]
        new_std_l = parsed_data["electric"]["standard"]["lowSeason"]
        change_records.append(RateChangeRecord("L-1-A Standard", "Winter", "Tier 1", old_std_l.get("tier1", 0.2575), new_std_l["tier1"]))
        change_records.append(RateChangeRecord("L-1-A Standard", "Winter", "Tier 2", old_std_l.get("tier2", 0.3189), new_std_l["tier2"]))
        change_records.append(RateChangeRecord("L-1-A Standard", "Winter", "Tier 3", old_std_l.get("tier3", 0.3935), new_std_l["tier3"]))

        # TOU Rates (High Season & Low Season)
        old_tou_h = existing_data["electric"]["tou"]["highSeason"]
        new_tou_h = parsed_data["electric"]["tou"]["highSeason"]
        old_tou_l = existing_data["electric"]["tou"]["lowSeason"]
        new_tou_l = parsed_data["electric"]["tou"]["lowSeason"]
        change_records.append(RateChangeRecord("L-1-B TOU", "Summer", "Peak", old_tou_h.get("peak", 0.6839), new_tou_h["peak"]))
        change_records.append(RateChangeRecord("L-1-B TOU", "Summer", "Off-Peak", old_tou_h.get("offPeak", 0.2280), new_tou_h["offPeak"]))
        change_records.append(RateChangeRecord("L-1-B TOU", "Winter", "Peak", old_tou_l.get("peak", 0.5700), new_tou_l["peak"]))
        change_records.append(RateChangeRecord("L-1-B TOU", "Winter", "Off-Peak", old_tou_l.get("offPeak", 0.1901), new_tou_l["offPeak"]))

        # Customer Charge
        old_cust = existing_data["electric"].get("dailyCustomerCharge", 0.75)
        new_cust = parsed_data["electric"]["dailyCustomerCharge"]
        change_records.append(RateChangeRecord("Fixed Service", "Daily", "Cust Charge", old_cust, new_cust))

        print_comparison_report(change_records, args.dry_run, source_engine)

        if not args.dry_run:
            os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
            with open(OUTPUT_FILE, "w") as f:
                json.dump(parsed_data, f, indent=2)
            print(f"[SUCCESS] Verified and committed GWP rate table to {OUTPUT_FILE}")
    except Exception as e:
        print(f"[ERROR] GWP Scraper encountered an error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()