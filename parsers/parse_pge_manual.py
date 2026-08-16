import os
import re
import json
import argparse
import sys
import pdfplumber
from datetime import datetime

# --- RESOLVE NESTED DIRECTORY PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
UPLOAD_DIR = os.path.normpath(os.path.join(ROOT_DIR, "upload_folders", "pge_uploads"))
JSON_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "pge_rates.json"))

def clean_val(val):
    if not val: return 0.0
    s = str(val).replace('$', '').replace(',', '').strip()
    if '(' in s and ')' in s:
        s = "-" + s.replace('(', '').replace(')', '')
    try:
        return float(s)
    except:
        return 0.0

def extract_pge_tariff_data(pdf_path):
    """
    Parses official PG&E Residential Tariff Sheets, AB 920 NSC tables,
    and Base Services / dedicated meter charge schedules.
    """
    results = {
        "plan_id": None,
        "rates": {},
        "nbc_total": 0.0,
        "baseline_credit": None,
        "nsc_rate": None,
        "sbp_export_rate": None,
        "fixed": {}
    }
    
    print(f"\n[Scanning PDF] {os.path.basename(pdf_path)}")
    
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
        
        # 1. Identify Schedule or Document Type
        plan_match = re.search(r"SCHEDULE\s+(E-[A-Z0-9-]+|EV2?-A|EV-B|E-1)", full_text, re.I)
        if plan_match:
            results["plan_id"] = plan_match.group(1).upper().strip()
            if results["plan_id"] == "E-1": results["plan_id"] = "E-1 tiered"
            print(f"  > Detected Schedule: {results['plan_id']}")

        # 2. NSC & SBP Solar Table Detection (e.g. AB920 or NBT Schedule)
        if any(x in full_text.upper() for x in ["NET SURPLUS COMPENSATION", "AB 920", "AB920"]):
            matches = re.findall(r"\$?0\.0\d{3,5}", full_text)
            if matches:
                cleaned = [float(m.replace("$", "")) for m in matches if float(m.replace("$", "")) > 0.005]
                if cleaned:
                    results["nsc_rate"] = cleaned[-1]
                    print(f"  > Detected PG&E NSC Rate: ${results['nsc_rate']:.5f}/kWh")

        if any(x in full_text.upper() for x in ["AVOIDED COST", "ENERGY EXPORT CREDIT", "SBP EXPORT"]):
            matches = re.findall(r"\$?0\.0\d{3,5}", full_text)
            if matches:
                cleaned = [float(m.replace("$", "")) for m in matches if float(m.replace("$", "")) > 0.01]
                if cleaned:
                    results["sbp_export_rate"] = cleaned[-1]
                    print(f"  > Detected SBP Export Rate: ${results['sbp_export_rate']:.5f}/kWh")

        # 3. NBC Components
        nbc_patterns = {
            "PPP": r"Public Purpose Programs.*?(\d+\.\d{5})",
            "Nuclear": r"Nuclear Decommissioning.*?(-?\d+\.\d{5})",
            "Wildfire": r"Wildfire Fund.*?(\d+\.\d{5})",
            "CTC": r"Competition Transition.*?(\d+\.\d{5})",
            "Recovery": r"Recovery Bond Charge.*?(\d+\.\d{5})"
        }
        nbc_sum = 0.0
        for name, pattern in nbc_patterns.items():
            match = re.search(pattern, full_text, re.I)
            if match:
                val = abs(float(match.group(1))) 
                nbc_sum += val
        results["nbc_total"] = nbc_sum

        # 4. Extract Rates & Fixed Charges Line-by-Line
        lines = full_text.split('\n')
        total_usage_count = 0
        
        for line in lines:
            line_clean = line.strip()
            line_upper = line_clean.upper()

            # --- Base Services Charges ---
            if "BASE SERVICE CHARGE" in line_upper or "BASE SERVICES CHARGE" in line_upper:
                decimals = re.findall(r"\d+\.\d{2,5}", line_clean)
                for d_str in decimals:
                    val = float(d_str)
                    if 20.0 <= val <= 30.0:
                        results["fixed"]["baseServiceStandard"] = round(val / 30.0, 5)
                        print(f"    [Captured] Base Services Standard: ${val:.2f}/mo (${results['fixed']['baseServiceStandard']:.5f}/day)")
                    elif 10.0 <= val <= 15.0:
                        results["fixed"]["baseServiceFERA"] = round(val / 30.0, 5)
                        print(f"    [Captured] Base Services FERA: ${val:.2f}/mo (${results['fixed']['baseServiceFERA']:.5f}/day)")
                    elif 4.0 <= val <= 8.0:
                        results["fixed"]["baseServiceCARE"] = round(val / 30.0, 5)
                        print(f"    [Captured] Base Services CARE: ${val:.2f}/mo (${results['fixed']['baseServiceCARE']:.5f}/day)")
                    elif 0.60 <= val <= 1.00:
                        results["fixed"]["baseServiceStandard"] = val

            # --- EV-B Dedicated Meter Charge ---
            if ("EV, RATE B" in line_upper or "EV-B" in line_upper) and ("METER CHARGE" in line_upper or "CUSTOMER CHARGE" in line_upper):
                decimals = re.findall(r"\d+\.\d{3,5}", line_clean)
                for d_str in decimals:
                    val = float(d_str)
                    if 0.10 <= val <= 1.50:
                        results["fixed"]["evbMeterCharge"] = val
                        print(f"    [Captured] EV-B Dedicated Meter Charge: ${val:.5f}/day")
                        break

            # --- Baseline Credit ---
            if "Baseline Credit" in line_clean or "Baseline Adjustment" in line_clean:
                credit_match = re.findall(r"\(?\d+\.\d{5}\)?", line_clean)
                if credit_match:
                    results["baseline_credit"] = abs(clean_val(credit_match[-1]))
                    print(f"    [Captured] Baseline Credit: ${results['baseline_credit']:.5f}")

            # --- Volumetric Rates ---
            # PATTERN 1: 'Total Usage'
            if line_clean.startswith("Total Usage"):
                decimals = re.findall(r"(\d+\.\d{5})", line_clean)
                if len(decimals) >= 2:
                    total_usage_count += 1
                    season = "summer" if total_usage_count == 1 else "winter"
                    results["rates"][f"{season}_on"] = float(decimals[0])
                    results["rates"][f"{season}_off"] = float(decimals[1])
                    print(f"    [Captured Total] {season.upper()}: Peak=${decimals[0]}, Off-Peak=${decimals[1]}")
            
            # PATTERN 2: 'Tiered' (E-1 specific)
            elif "Tier 1" in line_clean or "Tier 2" in line_clean:
                if any(x in line_clean for x in ["Adjustment", "Income", "Credit", "Limiter", "Component"]):
                    continue
                
                decimals = re.findall(r"(\d+\.\d{5})", line_clean)
                if decimals:
                    rate_val = float(decimals[-1])
                    if rate_val < 0.20: continue
                        
                    if "Tier 1" in line_clean:
                        current = results["rates"].get("summer_off", 0)
                        if rate_val > current:
                            results["rates"]["summer_off"] = rate_val
                            results["rates"]["winter_off"] = rate_val
                            print(f"    [Captured Tier] E-1 TIER 1: {rate_val:.5f}")
                    elif "Tier 2" in line_clean:
                        current = results["rates"].get("summer_on", 0)
                        if rate_val > current:
                            results["rates"]["summer_on"] = rate_val
                            results["rates"]["winter_on"] = rate_val
                            print(f"    [Captured Tier] E-1 TIER 2: {rate_val:.5f}")

    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run: print("\n!!! PDF DRY RUN MODE: No files will be modified !!!")

    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
        print(f"Created upload directory: {UPLOAD_DIR}")

    if not os.path.exists(JSON_FILE):
        print(f"[Error] {JSON_FILE} not found.")
        return

    with open(JSON_FILE, 'r') as f:
        data = json.load(f)

    updated = False
    files = [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
    if not files:
        print(f"No PDF files found in {UPLOAD_DIR}.")
        sys.exit(0)
    
    for filename in files:
        pdf_results = extract_pge_tariff_data(os.path.join(UPLOAD_DIR, filename))
        
        # 1. Update Global Fixed Fees
        if "fixed" not in data:
            data["fixed"] = {}
        for k, v in pdf_results["fixed"].items():
            curr_v = data["fixed"].get(k, 0.0)
            if abs(v - curr_v) > 0.0001:
                print(f"  [UPDATE] Fixed Charge {k}: {curr_v} -> {v}")
                if not args.dry_run:
                    data["fixed"][k] = v
                updated = True

        # 2. Update Global Solar Rates
        if pdf_results["nsc_rate"]:
            curr_nsc = data.get("nscRate", 0.0)
            if abs(pdf_results["nsc_rate"] - curr_nsc) > 0.0001:
                print(f"  [UPDATE] Global NSC Rate: {curr_nsc} -> {pdf_results['nsc_rate']}")
                if not args.dry_run:
                    data["nscRate"] = pdf_results["nsc_rate"]
                updated = True

        if pdf_results["sbp_export_rate"]:
            curr_sbp = data.get("sbpExportRate", 0.0)
            if abs(pdf_results["sbp_export_rate"] - curr_sbp) > 0.0001:
                print(f"  [UPDATE] Global SBP Export Rate: {curr_sbp} -> {pdf_results['sbp_export_rate']}")
                if not args.dry_run:
                    data["sbpExportRate"] = pdf_results["sbp_export_rate"]
                updated = True

        if pdf_results["baseline_credit"]:
            curr_bc = data.get("baselineCredit", 0.0)
            if abs(pdf_results["baseline_credit"] - curr_bc) > 0.0001:
                print(f"  [UPDATE] Global Baseline Credit: {curr_bc} -> {pdf_results['baseline_credit']}")
                if not args.dry_run:
                    data["baselineCredit"] = pdf_results["baseline_credit"]
                updated = True

        # 3. Update NBC
        if pdf_results["nbc_total"] > 0:
            old_nbc = data.get("nbcRate", 0)
            diff_nbc = abs(pdf_results["nbc_total"] - old_nbc)
            if diff_nbc > 0.00001:
                print(f"  [UPDATE] Global NBC: {old_nbc:.5f} -> {pdf_results['nbc_total']:.5f}")
                if not args.dry_run:
                    data["nbcRate"] = pdf_results["nbc_total"]
                updated = True

        # 4. Update Bin Rates
        target_id = pdf_results["plan_id"]
        if target_id and target_id in data["plans"]:
            print(f"\n[Comparison Ledger: {target_id}]")
            for key, val in pdf_results["rates"].items():
                season, bin_type = key.split('_')
                json_bin = "onPeak" if bin_type == "on" else "offPeak"
                
                is_3_bin = any(x in target_id for x in ["EV", "ELEC"])
                if bin_type == "off" and is_3_bin:
                    json_bin = "superOffPeak"
                
                if bin_type == "mid" and not is_3_bin:
                    continue

                old_val = data["plans"][target_id][season].get(json_bin, 0)
                diff = abs(val - old_val)
                status = "[MATCH]" if diff < 0.00001 else "[CHANGE DETECTED]"
                print(f"  {status} {season} {json_bin}: JSON=${old_val:.5f} | PDF=${val:.5f}")
                
                if diff > 0.00001 and not args.dry_run:
                    data["plans"][target_id][season][json_bin] = val
                    updated = True

    if updated:
        data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not args.dry_run:
            with open(JSON_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            print("\n>>> Success: JSON updated with PDF data.")
        else:
            print("\n>>> Dry Run Complete: Changes detected but not saved.")
    else:
        print("\n>>> Result: No significant changes detected in PDF folder.")

if __name__ == "__main__":
    main()
