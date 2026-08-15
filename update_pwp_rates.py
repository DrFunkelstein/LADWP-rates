import json
import re
from datetime import datetime
import requests
from bs4 import BeautifulSoup

URL = "https://pwp.cityofpasadena.net/water-and-electric-rates/#powres"

def scrape_pwp():
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    response = requests.get(URL, headers=headers)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text()
    
    # 1. Parse Electric
    cust_match = re.search(r"Customer Charges\s+\$([\d\.]+)", text)
    grid_match = re.search(r"Grid Access Charge\s+\$([\d\.]+)", text)
    energy_match = re.search(r"Energy Charge\s+([\d\.]+)¢", text)
    trans_match = re.search(r"Transmission Charge\s+([\d\.]+)¢", text)
    t1_dist = re.search(r"First 350 kWh per month\s+([\d\.]+)¢", text)
    t2_dist = re.search(r"Next 400 kWh per month\s+([\d\.]+)¢", text)
    t3_dist = re.search(r"All additional kWh per month\s+([\d\.]+)¢", text)
    
    # 2. Parse Water
    water_meter = re.search(r"¾\s*\"\s+\$([\d\.]+)", text)
    sfr_t1 = re.search(r"Tier 1\s+0-7\s+\$([\d\.]+)", text)
    sfr_t2 = re.search(r"Tier 2\s+7-29\s+\$([\d\.]+)", text)
    sfr_t3 = re.search(r"Tier 3\s+Over 29\s+\$([\d\.]+)", text)
    
    data = {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "utility": "PWP",
        "electric": {
            "fixed": {
                "customerCharge": float(cust_match.group(1)) if cust_match else 11.00,
                "gridAccessCharge": float(grid_match.group(1)) if grid_match else 6.50,
                "taxRate": 0.07872
            },
            "energyCharge": float(energy_match.group(1)) / 100.0 if energy_match else 0.100825,
            "transmissionCharge": float(trans_match.group(1)) / 100.0 if trans_match else 0.016090,
            "distribution": {
                "tier1": float(t1_dist.group(1)) / 100.0 if t1_dist else 0.035050,
                "tier2": float(t2_dist.group(1)) / 100.0 if t2_dist else 0.140180,
                "tier3": float(t3_dist.group(1)) / 100.0 if t3_dist else 0.252330
            },
            "limits": {
                "tier1": 350.0,
                "tier2": 750.0
            }
        },
        "water": {
            "monthlyMeterCharge": float(water_meter.group(1)) if water_meter else 47.15,
            "limits": {
                "tier1": 7.0,
                "tier2": 29.0
            },
            "rates": {
                "tier1": float(sfr_t1.group(1)) if sfr_t1 else 2.74458,
                "tier2": float(sfr_t2.group(1)) if sfr_t2 else 7.23544,
                "tier3": float(sfr_t3.group(1)) if sfr_t3 else 7.86867
            }
        }
    }
    
    with open("pwp_rates.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Successfully updated pwp_rates.json")

if __name__ == "__main__":
    scrape_pwp()
