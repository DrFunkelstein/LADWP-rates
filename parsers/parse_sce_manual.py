import pdfplumber
import json
import re
import os
import sys
import argparse
from datetime import datetime

UPLOAD_FOLDER = "sce_uploads"
JSON_FILE = "sce_rates.json"

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
    "PRIME": {
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
        "PRIME": {"summer": {}, "winter": {}},
        "Domestic": {"summer": {}, "winter": {}}
    }
    
    fixed_values = {}
    lines = text.split('\n')
    current_plan, current_season = None, None
    domestic_tier_context = None 
    locked_bins, locked_fixed = set(), set()
    
    plan_targets = {
        "TOU-D-4": "OPTION4-9PM", "TOU-D-5": "OPTION5-8PM",
        "PRIME": "OPTIONPRIME", "Domestic": "DOMESTICSERVICE" # Switched anchor
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

        # 1. FIXED CHARGES
        if "BASESERVICESCHARGE" in norm and "METER" in norm and "DAILY" not in locked_fixed:
            m = re.search(r"(\d+\.\d{3})", clean_line)
            if m: 
                fixed_values["dailyCharge"] = float(m.group(1))
                locked_fixed.add("DAILY")
        if "BASELINECREDIT" in norm and "CREDIT" not in locked_fixed:
            m = re.search(r"(\d+\.\d{5})", clean_line)
            if m: 
                fixed_values["baselineCredit"] = float(m.group(1))
                locked_fixed.add("CREDIT")

        # 2. PLAN DETECTION
        # Logic: If we see a plan marker, lock in the context
        for plan_id, target in plan_targets.items():
            if target in norm:
                # Filter out general sentences
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
            # In Schedule D, these are sub-headers for the next few lines
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
                # Look for rates on the line containing the season name
                # Row looks like: 'Summer 0.18482 (R) 0.11761 (R) 0.00000'
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
    parser = argparse.ArgumentParser(); parser.add_argument('--dry-run', action='store_true'); args = parser.parse_args()
    if not os.path.exists(UPLOAD_FOLDER): os.makedirs(UPLOAD_FOLDER)
    files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith((".pdf", ".txt"))]
    if not files: print("No files found."); sys.exit(0)

    full_matrix, all_fixed = {}, {}
    for filename in files:
        path = os.path.join(UPLOAD_FOLDER, filename)
        content = ""
        if filename.endswith(".pdf"):
            with pdfplumber.open(path) as pdf: content = "\n".join([p.extract_text() or "" for p in pdf.pages])
        else:
            with open(path, 'r', encoding='utf-8') as f: content = f.read()
        
        rates, fixed = extract_from_raw_text(content)
        for plan, seasons in rates.items():
            if plan not in full_matrix: full_matrix[plan] = seasons
            else:
                for season, buckets in seasons.items(): full_matrix[plan][season].update(buckets)
        all_fixed.update(fixed)

    if args.dry_run:
        print("\n--- FINAL AUDITED DRY RUN RESULTS ---")
        print(json.dumps(full_matrix, indent=2)); print("Fixed Charges:", all_fixed); sys.exit(0)

    try:
        with open(JSON_FILE, 'r') as f: data = json.load(f)
        if "dailyCharge" in all_fixed: data["fixed"]["dailyCharge"] = all_fixed["dailyCharge"]
        if "baselineCredit" in all_fixed: data["fixed"]["baselineCredit"] = all_fixed["baselineCredit"]
        for pid, seasons in full_matrix.items():
            if seasons["summer"] or seasons["winter"]: data["plans"][pid] = seasons
        data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(JSON_FILE, 'w') as f: json.dump(data, f, indent=2)
        print(f"\nSUCCESS: Updated {JSON_FILE}")
    except Exception as e: print(f"Error: {e}"); sys.exit(1)

if __name__ == "__main__":
    main()
