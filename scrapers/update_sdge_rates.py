import sys
import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime

# --- CONFIGURATION ---
PRICING_URL = "https://www.sdge.com/residential/pricing-plans"

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

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"--- Starting SDG&E Content Scraper (Dry Run: {dry_run}) ---")

    try:
        resp = requests.get(PRICING_URL, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        with open('sdge_rates.json', 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"!!! Init Failed: {e}")
        sys.exit(1)

    updated = False
    now = datetime.now()
    season = "summer" if (6 <= now.month <= 10) else "winter"

    for app_id, modal_id in PLAN_MAP.items():
        modal = soup.find('div', {'id': modal_id})
        if not modal: continue

        # Target the Non-CCA section
        non_cca_section = modal.find(string=re.compile("Non-CCA Customers", re.I))
        if not non_cca_section: continue
            
        container = non_cca_section.find_parent('div', class_='panel')
        table = container.find('table') if container else None
        
        if table:
            rows = table.find_all('tr')
            target_row = None
            
            # STRATEGY: Look for Tier 2 first (the 'over-baseline' rate).
            # If the plan has no tiers (EV-5, etc), we just take the first row with data.
            for row in rows:
                row_text = row.get_text()
                if "Tier 2" in row_text or "> 130%" in row_text:
                    target_row = row
                    break
            
            # Fallback for non-tiered plans
            if not target_row:
                for row in rows:
                    if extract_cents(row.get_text()):
                        target_row = row
                        break
            
            if target_row:
                cells = target_row.find_all('td')
                found_rates = [extract_cents(c.get_text()) for c in cells if extract_cents(c.get_text())]
                
                if len(found_rates) >= 2:
                    new_on = found_rates[-1] # Peak is last column
                    new_off = found_rates[0]
                    new_super = found_rates[0] if len(found_rates) < 3 else found_rates[1]
                    
                    target = data["plans"][app_id][season]
                    # Buffer check: site 62.1 aligns with JSON 0.62127
                    if abs(target["onPeak"] - new_on) > 0.005:
                        print(f"  [UPDATE] {app_id}: {target['onPeak']} -> {new_on}")
                        target["onPeak"] = new_on
                        target["offPeak"] = new_off
                        target["superOffPeak"] = new_super
                        updated = True
                    else:
                        print(f"  [MATCH] {app_id} aligns with site ({new_on})")

    if updated and not dry_run:
        data["lastUpdated"] = now.strftime("%Y-%m-%d %H:%M")
        with open('sdge_rates.json', 'w') as f:
            json.dump(data, f, indent=2)
        print("\n>>> Success: sdge_rates.json updated.")
    else:
        print("\n>>> No updates saved.")

if __name__ == "__main__":
    main()
