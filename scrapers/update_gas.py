import os
import sys
import requests
import pdfplumber
import json
import re
import io
from datetime import datetime

# --- RESOLVE FOLDER PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
OUTPUT_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "socalgas_rates.json"))

PROCUREMENT_URL = "https://www.socalgas.com/business/energy-market-services/gas-prices"
PDF_URL = "https://www.socalgas.com/regulatory/documents/TariffBookUpdate.pdf"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}

def scrape_pdf_fees():
    print("[*] Downloading and parsing Tariff PDF...")
    try:
        response = requests.get(PDF_URL, headers=HEADERS, timeout=30)
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            fees = {}
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue

                # 1. Schedule No. GR (Transportation & Customer Charge)
                if "Schedule No. GR" in text and "RESIDENTIAL SERVICE" in text:
                    print("  [+] Found Schedule GR Page")
                    c_charge = re.search(r"Customer Charge.*?(\d+\.\d+)¢", text)
                    t1_trans = re.search(r"Baseline.*?Transmission Charge.*?(\d+\.\d+)¢", text, re.DOTALL)
                    t2_trans = re.search(r"Non-Baseline.*?Transmission Charge.*?(\d+\.\d+)¢", text, re.DOTALL)
                    
                    if c_charge: fees['cust'] = round(float(c_charge.group(1)) / 100, 5)
                    if t1_trans: fees['t1'] = round(float(t1_trans.group(1)) / 100, 5)
                    if t2_trans: fees['t2'] = round(float(t2_trans.group(1)) / 100, 5)

                # 2. Schedule G-PPPS (Surcharge)
                if "Schedule No. G-PPPS" in text and "Residential" in text:
                    ppps_match = re.search(r"Residential\s+[\d\.]+\s+(\d+\.\d+)", text)
                    if ppps_match:
                        fees['ppps'] = round(float(ppps_match.group(1)) / 100, 5)
                        print(f"  [+] Found PPPS: {fees['ppps']}")

            return fees
    except Exception as e:
        print(f"  [!] PDF Scrape Warning: {e}")
        return None

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"--- Starting SoCalGas Scraper (Dry Run: {dry_run}) ---")
    print(f"[*] Target Rates File: {OUTPUT_FILE}")

    try:
        with open(OUTPUT_FILE, 'r') as f:
            data = json.load(f)

        # --- 1. PROCUREMENT (Monthly HTML) ---
        resp = requests.get(PROCUREMENT_URL, headers=HEADERS, timeout=15)
        html_text = resp.text
        month_year = datetime.now().strftime("%B %Y")
        proc_match = re.search(rf"{month_year}.*?(\d+\.\d{{3,5}})", html_text, re.DOTALL)
        
        if proc_match:
            data["procurement"] = round(float(proc_match.group(1)) / 100, 5)
            print(f"  [+] Updated Procurement ({month_year}): ${data['procurement']:.5f}/therm")
        else:
            print(f"  [!] No Procurement match found for {month_year}.")

        # --- 2. FEES (Yearly PDF) ---
        new_fees = scrape_pdf_fees()
        if new_fees:
            if 't1' in new_fees: data["transportation"]["base"] = new_fees['t1']
            if 't2' in new_fees: data["transportation"]["over"] = new_fees['t2']
            if 'cust' in new_fees: data["fixed"]["customerCharge"] = new_fees['cust']
            if 'ppps' in new_fees: data["fixed"]["ppps"] = new_fees['ppps']
            print("  [+] Successfully updated fees from PDF.")

        # --- 3. SAFETY CHECK ---
        if not proc_match and not new_fees:
            print("!!! SUBSTANTIAL FAILURE: HTML and PDF both failed.")
            sys.exit(1)

        data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        if not dry_run:
            with open(OUTPUT_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"\n>>> Success: {OUTPUT_FILE} updated.")
        else:
            print(f"\n>>> [DRY RUN] Would write updates to {OUTPUT_FILE}:")
            print(f"    Procurement: ${data.get('procurement', 0):.5f}")
            print(f"    T1 Delivery: ${data['transportation'].get('base', 0):.5f}")
            print(f"    T2 Delivery: ${data['transportation'].get('over', 0):.5f}")

        sys.exit(0)

    except Exception as e:
        print(f"!!! Main Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
