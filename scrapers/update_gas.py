import requests
import pdfplumber
import json
import re
import sys
import io
from datetime import datetime

PROCUREMENT_URL = "https://www.socalgas.com/business/energy-market-services/gas-prices"
PDF_URL = "https://www.socalgas.com/regulatory/documents/TariffBookUpdate.pdf"

def scrape_pdf_fees():
    print("Downloading and parsing Tariff PDF...")
    try:
        response = requests.get(PDF_URL, timeout=30)
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            fees = {}
            # Search specifically for the Residential and PPPS schedules
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue

                # 1. Look for Schedule No. GR (Transportation & Customer Charge)
                if "Schedule No. GR" in text and "RESIDENTIAL SERVICE" in text:
                    print("Found Schedule GR Page")
                    # Extract values in Cents and convert to Dollars
                    c_charge = re.search(r"Customer Charge.*?(\d+\.\d+)¢", text)
                    t1_trans = re.search(r"Baseline.*?Transmission Charge.*?(\d+\.\d+)¢", text, re.DOTALL)
                    t2_trans = re.search(r"Non-Baseline.*?Transmission Charge.*?(\d+\.\d+)¢", text, re.DOTALL)
                    
                    if c_charge: fees['cust'] = round(float(c_charge.group(1)) / 100, 5)
                    if t1_trans: fees['t1'] = round(float(t1_trans.group(1)) / 100, 5)
                    if t2_trans: fees['t2'] = round(float(t2_trans.group(1)) / 100, 5)

                # 2. Look for Schedule G-PPPS (Surcharge)
                if "Schedule No. G-PPPS" in text and "Residential" in text:
                    # Look for the Non-CARE Residential rate
                    ppps_match = re.search(r"Residential\s+[\d\.]+\s+(\d+\.\d+)", text)
                    if ppps_match:
                        fees['ppps'] = round(float(ppps_match.group(1)) / 100, 5)
                        print(f"Found PPPS: {fees['ppps']}")

            return fees
    except Exception as e:
        print(f"PDF Scrape Error: {e}")
        return None

def main():
    try:
        with open('socalgas_rates.json', 'r') as f:
            data = json.load(f)

        # --- 1. PROCUREMENT (Monthly HTML) ---
        resp = requests.get(PROCUREMENT_URL, timeout=15)
        html_text = resp.text
        month_year = datetime.now().strftime("%B %Y")
        proc_match = re.search(rf"{month_year}.*?(\d+\.\d{{3,5}})", html_text, re.DOTALL)
        
        if proc_match:
            data["procurement"] = round(float(proc_match.group(1)) / 100, 5)
            print(f"Updated Procurement: {data['procurement']}")

        # --- 2. FEES (Yearly PDF) ---
        # We only try to update these if the PDF is accessible
        new_fees = scrape_pdf_fees()
        if new_fees:
            if 't1' in new_fees: data["transportation"]["base"] = new_fees['t1']
            if 't2' in new_fees: data["transportation"]["over"] = new_fees['t2']
            if 'cust' in new_fees: data["fixed"]["customerCharge"] = new_fees['cust']
            if 'ppps' in new_fees: data["fixed"]["ppps"] = new_fees['ppps']
            print("Successfully updated fees from PDF.")

        # --- 3. SAFETY CHECK ---
        # If we have no procurement and no PDF data, it's a substantial failure
        if not proc_match and not new_fees:
            print("SUBSTANTIAL FAILURE: HTML and PDF both failed.")
            sys.exit(1)

        data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open('socalgas_rates.json', 'w') as f:
            json.dump(data, f, indent=2)
        
        sys.exit(0)

    except Exception as e:
        print(f"Main Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
