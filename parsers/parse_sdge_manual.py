import os
import re
import json
import sys
import pdfplumber
from datetime import datetime

# --- RESOLVE NESTED DIRECTORY PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
UPLOAD_DIR = os.path.normpath(os.path.join(ROOT_DIR, "upload_folders", "sdge_uploads"))
JSON_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "sdge_rates.json"))

def extract_decimal(text):
    if not text: return 0.0
    clean = text.replace('$', '').replace(',', '').strip()
    if '(' in clean and ')' in clean:
        clean = "-" + clean.replace('(', '').replace(')', '')
    match = re.search(r"(-?\d+\.\d{3,6})", clean)
    return float(match.group(1)) if match else 0.0

def parse_sdge_pdf(pdf_path):
    print(f"\n[Analyzing PDF] {os.path.basename(pdf_path)}")
    
    results = {
        "plan_id": None,
        "is_tiered": False,
        "summer": {"on": None, "mid": None, "off": None},
        "winter": {"on": None, "mid": None, "off": None},
        "baseline_credit": None,
        "service_charge": None,
        "service_charge_reduced": None,
        "nsc_rate": None,
        "sbp_export_rate": None,
        "sbp_delivery_export": None,
        "sbp_generation_export": None
    }

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            full_text = page.extract_text() or ""
            if not full_text: continue

            # 1. Identify Plan ID (first page)
            if page_idx == 0:
                plan_match = re.search(r"Schedule\s+([A-Z0-9-]+)", full_text)
                if plan_match:
                    results["plan_id"] = plan_match.group(1)
                    if results["plan_id"] == "DR": results["is_tiered"] = True
                    print(f"  > Target Plan: {results['plan_id']}")

            lines = full_text.split('\n')
            current_season = None

            for line in lines:
                line_clean = line.strip()
                if not line_clean: continue
                line_upper = line_clean.upper()

                # Track Season context
                if "Summer" in line_clean: current_season = "summer"
                elif "Winter" in line_clean: current_season = "winter"

                # 2. Solar SBP & NSC Detection
                if any(k in line_upper for k in ["NET SURPLUS COMPENSATION", "NSC RATE", "SURPLUS COMPENSATION"]):
                    decimals = re.findall(r"\d+\.\d{4,5}", line_clean)
                    if decimals:
                        results["nsc_rate"] = float(decimals[-1])
                        print(f"    [Extracted] Net Surplus Compensation: ${results['nsc_rate']:.5f}/kWh")

                if "DELIVERY EXPORT" in line_upper:
                    decimals = re.findall(r"\d+\.\d{4,5}", line_clean)
                    if decimals:
                        results["sbp_delivery_export"] = float(decimals[-1])
                        print(f"    [Extracted] SBP Delivery Export Rate: ${results['sbp_delivery_export']:.5f}/kWh")

                if "GENERATION EXPORT" in line_upper:
                    decimals = re.findall(r"\d+\.\d{4,5}", line_clean)
                    if decimals:
                        results["sbp_generation_export"] = float(decimals[-1])
                        print(f"    [Extracted] SBP Generation Export Rate: ${results['sbp_generation_export']:.5f}/kWh")

                if current_season:
                    decimals = re.findall(r"\d+\.\d{5}", line_clean)
                    if decimals:
                        # Standard DR (Tiered)
                        if results["is_tiered"]:
                            if "Tier 1" in line_clean or "Up to" in line_clean:
                                results[current_season]["on"] = float(decimals[-1])
                                print(f"    [Extracted] {current_season} Tier 1: {decimals[-1]}")
                            elif "Tier 2" in line_clean or "Above" in line_clean:
                                val = float(decimals[-1])
                                results[current_season]["mid"] = val
                                results[current_season]["off"] = val
                                print(f"    [Extracted] {current_season} Tier 2: {val}")
                        
                        # TOU 3-bin block
                        elif "On-Peak" in line_clean and "Super Off-Peak" in line_clean:
                            if len(decimals) >= 3:
                                results[current_season]["on"] = float(decimals[-3])
                                results[current_season]["mid"] = float(decimals[-2])
                                results[current_season]["off"] = float(decimals[-1])
                                print(f"    [Extracted] {current_season} Block: On:{decimals[-3]} Mid:{decimals[-2]} Off:{decimals[-1]}")
                        
                        # TOU Individual lines
                        elif "On-Peak" in line_clean:
                            results[current_season]["on"] = float(decimals[-1])
                            print(f"    [Extracted] {current_season} On-Peak: {decimals[-1]}")
                        elif "Super Off-Peak" in line_clean:
                            results[current_season]["off"] = float(decimals[-1])
                            print(f"    [Extracted] {current_season} Super Off-Peak: {decimals[-1]}")
                        elif "Off-Peak" in line_clean:
                            results[current_season]["mid"] = float(decimals[-1])
                            print(f"    [Extracted] {current_season} Off-Peak: {decimals[-1]}")

                # 3. Base Services / Fixed Charges
                if "Base Services Charge" in line_clean:
                    decimals = re.findall(r"\d+\.\d{5}", line_clean)
                    if decimals:
                        val = float(decimals[-1])
                        if "DRAH" in line_clean or "FERA" in line_clean:
                            results["service_charge_reduced"] = val
                            print(f"    [Extracted] Reduced Svc Charge: {val}")
                        else:
                            results["service_charge"] = val
                            print(f"    [Extracted] Standard Svc Charge: {val}")

                # 4. Baseline Adjustment Credit
                if "Baseline Adjustment Credit" in line_clean:
                    credit_match = re.findall(r"\(?\d+\.\d{5}\)?", line_clean)
                    if credit_match:
                        results["baseline_credit"] = abs(extract_decimal(credit_match[-1]))
                        print(f"    [Extracted] Baseline Credit: {results['baseline_credit']}")

    return results

def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run: print("!!! DRY RUN MODE ACTIVE !!!")

    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
        print(f"Created upload directory: {UPLOAD_DIR}")

    if not os.path.exists(JSON_FILE):
        print(f"[Error] {JSON_FILE} not found.")
        sys.exit(1)

    try:
        with open(JSON_FILE, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"[Error] Failed to load JSON: {e}")
        sys.exit(1)

    overall_updated = False
    
    files = [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
    if not files:
        print(f"No PDF files found in {UPLOAD_DIR}.")
        sys.exit(0)

    for filename in files:
        pdf_data = parse_sdge_pdf(os.path.join(UPLOAD_DIR, filename))
        
        # 1. Update Global Solar Rates if captured in PDF
        def update_global_val(key, current_val, new_val):
            nonlocal overall_updated
            if new_val is not None and new_val > 0.0 and abs(new_val - (current_val or 0.0)) > 0.00001:
                print(f"    [CHANGE] Global {key}: {current_val} -> {new_val}")
                overall_updated = True
                return new_val
            return current_val

        data["nscRate"] = update_global_val("nscRate", data.get("nscRate", 0.01306), pdf_data["nsc_rate"])
        data["sbpDeliveryExportRate"] = update_global_val("sbpDeliveryExportRate", data.get("sbpDeliveryExportRate", 0.02548), pdf_data["sbp_delivery_export"])
        data["sbpGenerationExportRate"] = update_global_val("sbpGenerationExportRate", data.get("sbpGenerationExportRate", 0.09065), pdf_data["sbp_generation_export"])
        
        # Auto-compute total SBP Export Rate
        tot_sbp = round(data.get("sbpDeliveryExportRate", 0.02548) + data.get("sbpGenerationExportRate", 0.09065), 5)
        if abs(data.get("sbpExportRate", 0.0) - tot_sbp) > 0.00001:
            data["sbpExportRate"] = tot_sbp
            overall_updated = True

        # 2. Update Plan Rates
        raw_id = pdf_data["plan_id"]
        if not raw_id: continue
        plan_key = "Standard DR" if raw_id == "DR" else raw_id
        if plan_key not in data["plans"]: continue

        p = data["plans"][plan_key]
        
        def update_val(category, bin_name, current_val, new_val):
            nonlocal overall_updated
            if new_val is not None and new_val > 0.0 and abs(new_val - (current_val or 0)) > 0.00001:
                print(f"    [CHANGE] {plan_key} {category} {bin_name}: {current_val} -> {new_val}")
                overall_updated = True
                return new_val
            return current_val

        p["dailyServiceCharge"] = update_val("Fixed", "Std Svc Charge", p.get("dailyServiceCharge"), pdf_data["service_charge"])
        p["dailyServiceChargeLowIncome"] = update_val("Fixed", "Reduced Svc Charge", p.get("dailyServiceChargeLowIncome"), pdf_data["service_charge_reduced"])

        for s in ["summer", "winter"]:
            p[s]["onPeak"] = update_val(s.capitalize(), "On/T1", p[s].get("onPeak"), pdf_data[s]["on"])
            p[s]["offPeak"] = update_val(s.capitalize(), "Off/T2", p[s].get("offPeak"), pdf_data[s]["mid"])
            p[s]["superOffPeak"] = update_val(s.capitalize(), "SuperOff/T2", p[s].get("superOffPeak"), pdf_data[s]["off"])

        if pdf_data["baseline_credit"]:
            data["baselineCredit"] = update_val("Global", "Baseline Credit", data.get("baselineCredit"), pdf_data["baseline_credit"])

    if overall_updated:
        if not dry_run:
            data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(JSON_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"\n>>> Success: {RATES_FILE if 'RATES_FILE' in locals() else JSON_FILE} updated.")
        else:
            print("\n>>> Dry Run Complete: Changes detected but not saved.")
    else:
        print("\n>>> No changes detected between PDF and JSON.")

if __name__ == "__main__":
    main()
