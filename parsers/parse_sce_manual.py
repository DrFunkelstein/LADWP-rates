import pdfplumber
import json
import re
import os
import sys
import argparse
from datetime import datetime

# --- RESOLVE NESTED DIRECTORY PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
UPLOAD_FOLDER = os.path.normpath(os.path.join(ROOT_DIR, "upload_folders", "sce_uploads"))
JSON_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "sce_rates.json"))

# --- SCHEMA VALIDATOR ---
VALID_BUCKETS = {
    "TOU-D-4": {
        "summer": ["onPeak", "midPeak", "offPeak"],
        "winter": ["midPeak", "offPeak", "superOffPeak"]
    },
    "TOU-D-5": {
        "summer": ["onPeak", "midPeak", "offPeak"],
        "winter": ["midPeak", "offPeak", "superOffPeak"]
    },
    "TOU-D-PRIME": {
        "summer": ["onPeak", "midPeak", "offPeak"],
        "winter": ["midPeak", "offPeak", "superOffPeak"]
    },
    "Domestic": {
        "summer": ["tier1", "tier2"],
        "winter": ["tier1", "tier2"]
    }
}

def normalize(text):
    """Removes all whitespace and converts to uppercase for reliable matching."""
    return re.sub(r'\s+', '', text).upper()

def extract_from_raw_text(text):
    found_data = {
        "TOU-D-4": {"summer": {}, "winter": {}},
        "TOU-D-5": {"summer": {}, "winter": {}},
        "TOU-D-PRIME": {"summer": {}, "winter": {}},
        "Domestic": {"summer": {}, "winter": {}}
    }
    
    fixed_values = {
        "nscRate": 0.03500,       # Verified fallback
        "sbpExportRate": 0.06500  # CPUC Avoided Cost fallback
    }
    lines = text.split('\n')
    current_plan, current_season = None, None
    domestic_tier_context = None 
    locked_bins, locked_fixed = set(), set()
    
    plan_targets = {
        "TOU-D-4": "OPTION4-9PM",
        "TOU-D-5": "OPTION5-8PM",
        "TOU-D-PRIME": "OPTIONPRIME",
        "Domestic": "DOMESTICSERVICE"
    }

    bucket_order = [
        ("SUPER-OFF-PEAK", "superOffPeak"),
        ("ON-PEAK", "onPeak"),
        ("MID-PEAK", "midPeak"),
        ("OFF-PEAK", "offPeak")
    ]

    for line in lines:
        clean_line = line.strip()
        if not clean_line: continue
        norm = normalize(clean_line)

        # 1. FIXED & SOLAR CHARGES
        if "BASESERVICESCHARGE" in norm and "METER" in norm and "DAILY" not in locked_fixed:
            m = re.search(r"(\d+\.\d{3})", clean_line)
            if m: 
                fixed_values["dailyCharge"] = float(m.group(1))
                locked_fixed.add("DAILY")
                print(f"   [Captured] Daily Service Charge: ${fixed_values['dailyCharge']:.5f}/day")

        if "BASELINECREDIT" in norm and "CREDIT" not in locked_fixed:
            m = re.search(r"(\d+\.\d{5})", clean_line)
            if m: 
                fixed_values["baselineCredit"] = float(m.group(1))
                locked_fixed.add("CREDIT")
                print(f"   [Captured] Baseline Credit: ${fixed_values['baselineCredit']:.5f}/kWh")

        # NSC (Net Surplus Compensation) Detection
        if any(x in norm for x in ["NETSURPLUSCOMPENSATION", "NSCRATE", "SURPLUSCOMPENSATION"]) and "NSC" not in locked_fixed:
            m = re.search(r"(\d+\.\d{4,5})", clean_line)
            if m:
                val = float(m.group(1))
                if 0.005 <= val <= 0.20:
                    fixed_values["nscRate"] = val
                    locked_fixed.add("NSC")
                    print(f"   [Captured] NSC Rate: ${val:.5f}/kWh")

        # SBP / ACC Export Credit Detection
        if any(x in norm for x in ["ENERGYEXPORTCREDIT", "SBPEXPORT", "AVOIDEDCOST"]) and "SBP" not in locked_fixed:
            m = re.search(r"(\d+\.\d{4,5})", clean_line)
            if m:
                val = float(m.group(1))
                if 0.01 <= val <= 0.30:
                    fixed_values["sbpExportRate"] = val
                    locked_fixed.add("SBP")
                    print(f"   [Captured] SBP Export Rate: ${val:.5f}/kWh")

        # 2. PLAN DETECTION
        for plan_id, target in plan_targets.items():
            if target in norm:
                if any(x in norm for x in ["AVAILABLE", "ELIGIB", "PURSUANT", "CANCELLING"]): continue
                
                current_plan = plan_id
                current_season = None 
                domestic_tier_context = None
                print(f"DEBUG: >>> Entering {current_plan} Section")

        if not current_plan: continue

        # 3. SEASON & TIER DETECTION
        if "SUMMER" in norm: current_season = "summer"
        elif "WINTER" in norm: current_season = "winter"

        if current_plan == "Domestic":
            if "BASELINESERVICE" in norm and "OVER" not in norm:
                domestic_tier_context = "tier1"
                print("   [Context] Found Tier 1 Header")
            elif "OVERBASELINESERVICE" in norm:
                domestic_tier_context = "tier2"
                print("   [Context] Found Tier 2 Header")

        # 4. RATE EXTRACTION
        if current_plan == "Domestic" and domestic_tier_context and current_season:
            bin_key = f"DOM_{current_season}_{domestic_tier_context}"
            if bin_key not in locked_bins:
                rates = re.findall(r"(\d+\.\d{5})", clean_line)
                if len(rates) >= 2:
                    total = round(float(rates[0]) + float(rates[1]), 5)
                    found_data["Domestic"][current_season][domestic_tier_context] = total
                    locked_bins.add(bin_key)
                    print(f"   >> MATCH: Domestic {current_season} {domestic_tier_context} -> ${total}")

        elif current_plan != "Domestic":
            for label, json_key in bucket_order:
                if label in norm and current_season:
                    if json_key not in VALID_BUCKETS[current_plan][current_season]:
                        continue
                    lock_key = f"{current_plan}_{current_season}_{json_key}"
                    if lock_key not in locked_bins:
                        rates = re.findall(r"(\d+\.\d{5})", clean_line)
                        if len(rates) >= 2:
                            total = round(float(rates[0]) + float(rates[1]), 5)
                            found_data[current_plan][current_season][json_key] = total
                            locked_bins.add(lock_key)
                            print(f"   >> MATCH: {current_plan} {current_season} {json_key} -> ${total}")
                            break 

    return found_data, fixed_values

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
        print(f"Created upload directory: {UPLOAD_FOLDER}")

    files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith((".pdf", ".txt"))]
    if not files:
        print(f"No files found in {UPLOAD_FOLDER}.")
        sys.exit(0)

    full_matrix, all_fixed = {}, {}
    for filename in files:
        path = os.path.join(UPLOAD_FOLDER, filename)
        print(f"\n[Processing File] {filename}")
        content = ""
        if filename.endswith(".pdf"):
            with pdfplumber.open(path) as pdf:
                content = "\n".join([p.extract_text() or "" for p in pdf.pages])
        else:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        
        rates, fixed = extract_from_raw_text(content)
        for plan, seasons in rates.items():
            if plan not in full_matrix:
                full_matrix[plan] = seasons
            else:
                for season, buckets in seasons.items():
                    full_matrix[plan][season].update(buckets)
        all_fixed.update(fixed)

    if args.dry_run:
        print("\n--- FINAL AUDITED DRY RUN RESULTS ---")
        print(json.dumps(full_matrix, indent=2))
        print("Fixed Charges:", all_fixed)
        sys.exit(0)

    try:
        with open(JSON_FILE, 'r') as f:
            data = json.load(f)
        
        if "fixed" not in data:
            data["fixed"] = {}

        if "dailyCharge" in all_fixed:
            data["fixed"]["dailyCharge"] = all_fixed["dailyCharge"]
        if "baselineCredit" in all_fixed:
            data["fixed"]["baselineCredit"] = all_fixed["baselineCredit"]
        if "nscRate" in all_fixed:
            data["fixed"]["nscRate"] = all_fixed["nscRate"]
        if "sbpExportRate" in all_fixed:
            data["fixed"]["sbpExportRate"] = all_fixed["sbpExportRate"]

        for pid, seasons in full_matrix.items():
            if seasons.get("summer") or seasons.get("winter"):
                data["plans"][pid] = seasons

        data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        with open(JSON_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\nSUCCESS: Updated {JSON_FILE}")
    except Exception as e:
        print(f"Error updating JSON: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
