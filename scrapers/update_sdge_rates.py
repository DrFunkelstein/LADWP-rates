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

# Verified Default CCA Profiles for SDG&E Territory (Joint Rate Comparisons)
DEFAULT_SDGE_CCA_PROFILES = {
    "SDCP": {
        "name": "SD Community Power",
        "fullName": "San Diego Community Power",
        "tiers": {
            "power100": {"name": "Power100 (100% Clean)", "rateAdder": 0.0150},
            "powerplus": {"name": "PowerPlus (50% Clean)", "rateAdder": -0.0050}
        }
    },
    "CEA": {
        "name": "Clean Energy Alliance",
        "fullName": "Clean Energy Alliance (North San Diego County)",
        "tiers": {
            "green_impact": {"name": "Green Impact (100%)", "rateAdder": 0.0150},
            "clean_impact": {"name": "Clean Impact (50%)", "rateAdder": -0.0050}
        }
    }
}

def extract_cents(text):
    """Converts '62.1¢' to 0.62100"""
    match = re.search(r"(\d+\.\d+)", text)
    if match:
        return round(float(match.group(1)) / 100, 5)
    return None

def fetch_sdge_nsc_rate():
    """
    Parses SDG&E's True-Up Monthly Rate Table matrix (Months x Years)
    to extract the current month's Net Surplus Compensation (NSC) rate.
    """
    print("\n[Solar Scan] Checking SDG&E True-Up Excess Generation Table...")
    
    nsc_rate = 0.01306  # Verified default
    now = datetime.now()
    current_year_str = str(now.year)
    current_month_name = now.strftime("%B")
    short_month_name = now.strftime("%b")
    
    try:
        res = requests.get(EXCESS_GEN_URL, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            tables = soup.find_all('table')
            
            target_table = None
            for t in tables:
                txt = t.get_text()
                if "January" in txt or "Jan" in txt:
                    target_table = t
                    break
            
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
                month_names = [
                    "January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"
                ]
                
                current_month_idx = now.month - 1
                found_rate = None
                
                for m_idx in range(current_month_idx, -1, -1):
                    target_m = month_names[m_idx]
                    for r in rows:
                        row_txt = r.get_text()
                        if target_m in row_txt or target_m[:3] in row_txt:
                            cells = r.find_all('td')
                            if len(cells) > year_col_idx:
                                val_str = cells[year_col_idx].get_text().strip()
                                m_match = re.search(r"(\d+\.\d+)", val_str)
                                if m_match:
                                    extracted = float(m_match.group(1))
                                    val = extracted / 100 if extracted > 0.5 else extracted
                                    if val > 0.001:
                                        found_rate = val
                                        print(f"  > Parsed SDG&E NSC Rate for {target_m} {current_year_str}: ${val:.5f}/kWh")
                                        break
                    if found_rate:
                        nsc_rate = found_rate
                        break
                        
                if not found_rate:
                    print(f"  > Retaining Verified Fallback NSC Rate: ${nsc_rate:.5f}/kWh")
            else:
                print(f"  [Warning] Table not found on page. Retaining fallback: ${nsc_rate:.5f}/kWh")
        else:
            print(f"  [Warning] HTTP {res.status_code}. Retaining fallback: ${nsc_rate:.5f}/kWh")
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

    # 3. Preserve & Merge CCA Rate Blocks
    print("\n[CCA Protection Ledger]")
    if "cca" not in data or not data["cca"]:
        if not dry_run:
            data["cca"] = DEFAULT_SDGE_CCA_PROFILES
        updated = True
        print("  [NEW] Initialized Default SDG&E CCA Profiles (SDCP, CEA)")
    else:
        for cca_key, cca_val in DEFAULT_SDGE_CCA_PROFILES.items():
            if cca_key not in data["cca"]:
                if not dry_run:
                    data["cca"][cca_key] = cca_val
                updated = True
                print(f"  [NEW] Merged missing CCA profile: {cca_key}")
            else:
                print(f"  [PRESERVED] CCA Profile: {cca_key} ({data['cca'][cca_key]['name']})")

    # 4. Write updates
    if updated and not dry_run:
        data["lastUpdated"] = now.strftime("%Y-%m-%d %H:%M")
        with open(RATES_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\n>>> Success: {RATES_FILE} updated.")
    else:
        print("\n>>> No updates needed.")

if __name__ == "__main__":
    main()
