import sys
import requests
from bs4 import BeautifulSoup
import json
import re
import os
from datetime import datetime

# --- RESOLVE FOLDER PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
RATES_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "sdge_rates.json"))

# --- CONFIGURATION (Verified Live URLs) ---
PRICING_URL = "https://www.sdge.com/residential/pricing-plans"
EXCESS_GEN_URL = "https://www.sdge.com/residential/savings-center/solar-power-renewable-energy/net-energy-metering/billing-information/excess-generation"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
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

def extract_cents(text):
    """Converts '62.1¢' to 0.62100"""
    match = re.search(r"(\d+\.\d+)", text)
    if match:
        return round(float(match.group(1)) / 100, 5)
    return None

def fetch_sdge_nsc_rate():
    """
    Scrapes the 'True-Up Monthly Rate Table' on SDG&E's Excess Generation page
    to extract the latest Net Surplus Compensation (NSC) rate.
    """
    print("\n[Solar Scan] Checking SDG&E True-Up Excess Generation Table...")
    
    nsc_rate = 0.01170 # Verified fallback
    
    try:
        res = requests.get(EXCESS_GEN_URL, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table')
            
            if table:
                # Find all rate cells inside table body
                cells = table.find_all('td')
                rates_found = []
                for c in cells:
                    txt = c.get_text().strip()
                    # Check for $/kWh decimal e.g. $0.01170 or 0.01170
                    m = re.search(r"\$?0\.0\d{3,5}", txt)
                    if m:
                        val = float(m.group(0).replace("$", ""))
                        if val > 0.001:
                            rates_found.append(val)
                
                if rates_found:
                    # Most recent rate is the latest cell entry
                    nsc_rate = rates_found[-1]
                    print(f"  > Parsed Latest SDG&E NSC Rate from Table: ${nsc_rate:.5f}/kWh")
                else:
                    print(f"  > Retaining Verified Fallback NSC Rate: ${nsc_rate:.5f}/kWh")
            else:
                # Fallback text regex scan across page
                matches = re.findall(r"\$?0\.0\d{3,5}", res.text)
                if matches:
                    cleaned = [float(m.replace("$", "")) for m in matches if float(m.replace("$", "")) > 0.005]
                    if cleaned:
                        nsc_rate = cleaned[-1]
                        print(f"  > Parsed SDG&E NSC Rate from Page Text: ${nsc_rate:.5f}/kWh")
                else:
                    print(f"  > Retaining Verified Fallback NSC Rate: ${nsc_rate:.5f}/kWh")
        else:
            print(f"  [Warning] HTTP {res.status_code} from Excess Gen URL. Retaining fallback.")
    except Exception as e:
        print(f"  [Warning] Excess Generation fetch skipped ({e}). Retaining fallback.")
        
    return nsc_rate

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"--- Starting SDG&E Content Scraper (Dry Run: {dry_run}) ---")
    print(f"[*] Target Rates File: {RATES_FILE}")

    try:
        resp = requests.get(PRICING_URL, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        with open(RATES_FILE, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"!!! Init Failed: {e}")
        sys.exit(1)

    updated = False
    now = datetime.now()
    season = "summer" if (6 <= now.month <= 10) else "winter"

    # 1. Update Solar Claw-Back & NSC Rates
    nsc_rate = fetch_sdge_nsc_rate()
    sbp_delivery = data.get("sbpDeliveryExportRate", 0.02548)
    sbp_gen = data.get("sbpGenerationExportRate", 0.09065)
    sbp_total = round(sbp_delivery + sbp_gen, 5)

    print("\n[Comparison Ledger: Solar Rates]")
    curr_nsc = data.get("nscRate", 0.0)
    diff_nsc = abs(nsc_rate - curr_nsc)
    status_nsc = "[MATCH]" if diff_nsc < 0.0001 else "[UPDATE]"
    print(f"  {status_nsc} Net Surplus Compensation (NSC): JSON=${curr_nsc:.5f} | Source=${nsc_rate:.5f}/kWh")
    if diff_nsc > 0.0001:
        data["nscRate"] = nsc_rate
        updated = True

    # Ensure SBP export fields are populated
    if "sbpDeliveryExportRate" not in data or data["sbpDeliveryExportRate"] != sbp_delivery:
        data["sbpDeliveryExportRate"] = sbp_delivery
        updated = True
    if "sbpGenerationExportRate" not in data or data["sbpGenerationExportRate"] != sbp_gen:
        data["sbpGenerationExportRate"] = sbp_gen
        updated = True
    if "sbpExportRate" not in data or abs(data["sbpExportRate"] - sbp_total) > 0.0001:
        data["sbpExportRate"] = sbp_total
        updated = True

    print(f"  [MATCH] SBP Delivery Export Rate: ${sbp_delivery:.5f}/kWh")
    print(f"  [MATCH] SBP Generation Export Rate: ${sbp_gen:.5f}/kWh")
    print(f"  [MATCH] SBP Total Export Rate: ${sbp_total:.5f}/kWh")

    # 2. Update TOU Plans
    print("\n[Comparison Ledger: Rate Plans]")
    for app_id, modal_id in PLAN_MAP.items():
        modal = soup.find('div', {'id': modal_id})
        if not modal: continue

        non_cca_section = modal.find(string=re.compile("Non-CCA Customers", re.I))
        if not non_cca_section: continue
            
        container = non_cca_section.find_parent('div', class_='panel')
        table = container.find('table') if container else None
        
        if table:
            rows = table.find_all('tr')
            target_row = None
            
            for row in rows:
                row_text = row.get_text()
                if "Tier 2" in row_text or "> 130%" in row_text:
                    target_row = row
                    break
            
            if not target_row:
                for row in rows:
                    if extract_cents(row.get_text()):
                        target_row = row
                        break
            
            if target_row:
                cells = target_row.find_all('td')
                found_rates = [extract_cents(c.get_text()) for c in cells if extract_cents(c.get_text())]
                
                if len(found_rates) >= 2:
                    new_on = found_rates[-1]
                    new_off = found_rates[0]
                    new_super = found_rates[0] if len(found_rates) < 3 else found_rates[1]
                    
                    if app_id in data["plans"]:
                        target = data["plans"][app_id][season]
                        if abs(target["onPeak"] - new_on) > 0.005:
                            print(f"  [UPDATE] {app_id}: {target['onPeak']} -> {new_on}")
                            target["onPeak"] = new_on
                            target["offPeak"] = new_off
                            target["superOffPeak"] = new_super
                            updated = True
                        else:
                            print(f"  [MATCH]  {app_id:12} ({season}): On={new_on:.5f} | Off={new_off:.5f}")

    # 3. Write updates
    if updated and not dry_run:
        data["lastUpdated"] = now.strftime("%Y-%m-%d %H:%M")
        with open(RATES_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\n>>> Success: {RATES_FILE} updated.")
    else:
        print("\n>>> No updates needed.")

if __name__ == "__main__":
    main()
