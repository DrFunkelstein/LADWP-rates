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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

def scrape_procurement_rate():
    print(f"[*] Fetching HTML Procurement from: {PROCUREMENT_URL}")
    try:
        session = requests.Session()
        resp = session.get(PROCUREMENT_URL, headers=HEADERS, timeout=20)
        
        if resp.status_code != 200:
            print(f"  [!] HTTP {resp.status_code} received from SoCalGas.")
            return None

        html_text = resp.text
        now = datetime.now()
        month_name = now.strftime("%B") # e.g. "August"
        year = now.strftime("%Y")       # e.g. "2026"

        # Strategy A: Match the summary announcement sentence
        # "Effective August 1, 2026, the procurement component ... to 41.856 ¢/therm"
        sentence_pattern = rf"Effective\s+{month_name}\s+0?\d{{1,2}},?\s+{year}[^\n\r]*?to\s+(\d+\.\d{{3,5}})\s*¢/therm"
        match = re.search(sentence_pattern, html_text, re.IGNORECASE)
        if match:
            val = round(float(match.group(1)) / 100, 5)
            print(f"  [+] Scraped via Summary Sentence: ${val:.5f}/therm")
            return val

        # Strategy B: Match the HTML table row
        # "August 01, 2026" followed by 41.856
        table_pattern = rf"{month_name}\s+0?\d{{1,2}},?\s+{year}[\s\S]{{0,150}}?(\d+\.\d{{3,5}})"
        match = re.search(table_pattern, html_text, re.IGNORECASE)
        if match:
            val = round(float(match.group(1)) / 100, 5)
            print(f"  [+] Scraped via Table Row: ${val:.5f}/therm")
            return val

        print(f"  [!] Regex could not find {month_name} {year} in HTML.")
        return None

    except Exception as e:
        print(f"  [!] Procurement Scrape Error: {e}")
        return None

def scrape_pdf_fees():
    print(f"[*] Fetching Tariff PDF from: {PDF_URL}")
    try:
        session = requests.Session()
        response = session.get(PDF_URL, headers=HEADERS, timeout=30)
        
        if response.status_code != 200:
            print(f"  [!] PDF HTTP Status: {response.status_code} (Skipping PDF fees)")
            return None

        # Verify bytes actually represent a valid PDF
        if not response.content.startswith(b"%PDF"):
            print("  [!] Warning: Firewall returned non-PDF content (Preserving existing PDF fees).")
            return None

        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            fees = {}
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue

                # 1. Schedule No. GR (Transportation & Customer Charge)
                if "Schedule No. GR" in text and "RESIDENTIAL SERVICE" in text:
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

            return fees

    except Exception as e:
        print(f"  [!] PDF Scrape Warning: {e} (Preserving existing fees)")
        return None

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"\n=======================================================")
    print(f"  SoCalGas Rate Scraper (Dry Run: {dry_run})")
    print(f"  Target File: {OUTPUT_FILE}")
    print(f"=======================================================\n")

    try:
        # Load existing JSON payload
        data = {}
        if os.path.exists(OUTPUT_FILE):
            with open(OUTPUT_FILE, 'r') as f:
                data = json.load(f)
        else:
            print(f"  [!] Output file not found. Initializing empty structure.")
            data = {"transportation": {}, "fixed": {}, "allowances": {}}

        # --- 1. PROCUREMENT (Monthly HTML) ---
        new_proc = scrape_procurement_rate()
        if new_proc:
            data["procurement"] = new_proc
            print(f"  [✓] Updated Procurement: ${data['procurement']:.5f}/therm")
        else:
            print(f"  [!] Keeping existing Procurement: ${data.get('procurement', 0):.5f}/therm")

        # --- 2. FEES (Tariff PDF) ---
        new_fees = scrape_pdf_fees()
        if new_fees:
            if 't1' in new_fees: data["transportation"]["base"] = new_fees['t1']
            if 't2' in new_fees: data["transportation"]["over"] = new_fees['t2']
            if 'cust' in new_fees: data["fixed"]["customerCharge"] = new_fees['cust']
            if 'ppps' in new_fees: data["fixed"]["ppps"] = new_fees['ppps']
            print(f"  [✓] Updated fees from Tariff PDF.")
        else:
            print(f"  [!] Preserved existing fixed & transportation fees.")

        # --- 3. SAFETY CHECK ---
        if not new_proc and not new_fees:
            print("\n[!] Warning: No new data scraped this session. Preserving existing JSON.")
            sys.exit(0)

        data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        # --- 4. OUTPUT / DRY-RUN WRITE ---
        if not dry_run:
            with open(OUTPUT_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"\n>>> [SUCCESS] {OUTPUT_FILE} successfully written.")
            print(f"    - Procurement: ${data.get('procurement', 0):.5f}/therm")
            print(f"    - Base Delivery: ${data['transportation'].get('base', 0):.5f}/therm")
        else:
            print(f"\n>>> [DRY RUN COMPLETE] No files were modified.")
            print(json.dumps(data, indent=2))

        sys.exit(0)

    except Exception as e:
        print(f"\n!!! Fatal Main Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
