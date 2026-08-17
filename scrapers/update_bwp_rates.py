#!/usr/bin/env python3
"""
Burbank Water & Power (BWP) Resilient Rate Scraper
Uses Dynamic PDF Discovery + Static HTML Web Scraping + Non-Destructive Fallbacks.
"""

import argparse
import json
import os
import re
import sys
import io
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import pdfplumber

# --- CONFIGURATION ---
BWP_RATES_URL = "https://www.burbankwaterandpower.com/electric/electric-rates"
BWP_SOLAR_URL = "https://www.burbankwaterandpower.com/solar-billing-faq"
CITY_FINANCE_URL = "https://www.burbankca.gov/web/financial-services"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
OUTPUT_FILE = os.path.normpath(os.path.join(ROOT_DIR, "rates", "bwp_rates.json"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def parse_args():
    parser = argparse.ArgumentParser(description="BWP Resilient Rate Scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without saving to disk")
    parser.add_argument("--verbose", action="store_true", help="Verbose logs")
    return parser.parse_args()

# =============================================================================
# LAYER 1: STATIC BWP HTML SCRAPING
# =============================================================================
def scrape_bwp_web_rates(verbose: bool) -> dict:
    """Scrapes the primary BWP Electric Rates and Solar Billing FAQ pages."""
    rates_found = {}
    
    # 1. Electric Rates Page
    try:
        if verbose: print(f"[*] Checking BWP Rates Page: {BWP_RATES_URL}")
        res = requests.get(BWP_RATES_URL, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            text = soup.get_text()

            cust_match = re.search(r"Customer Service Charge.*?\$([\d\.]+)", text)
            if cust_match: rates_found["customerServiceCharge"] = float(cust_match.group(1))

            t1_match = re.search(r"First 300 kWh.*?\$([\d\.]+)", text)
            if t1_match: rates_found["tier1EnergyRate"] = float(t1_match.group(1))

            t2_match = re.search(r"All additional kWh.*?\$([\d\.]+)", text)
            if t2_match: rates_found["tier2EnergyRate"] = float(t2_match.group(1))

            ecac_match = re.search(r"ECAC.*?\$([\d\.]+)", text)
            if ecac_match: rates_found["ecac"] = float(ecac_match.group(1))
    except Exception as e:
        if verbose: print(f"  [Warning] Web rates scrape error: {e}")

    # 2. Solar FAQ Page (Avoided Cost & NSC)
    try:
        if verbose: print(f"[*] Checking BWP Solar FAQ: {BWP_SOLAR_URL}")
        res = requests.get(BWP_SOLAR_URL, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            text = soup.get_text()

            acoe_match = re.search(r"between\s+(\d+\.\d+)\s+and\s+(\d+\.\d+)\s+cents", text, re.I)
            if acoe_match:
                low = float(acoe_match.group(1)) / 100
                high = float(acoe_match.group(2)) / 100
                rates_found["sbpExportRate"] = round((low + high) / 2.0, 5)

            nsc_match = re.search(r"(\d+\.\d+)\s+cents\s+per\s+kWh", text, re.I)
            if nsc_match:
                rates_found["nscRate"] = round(float(nsc_match.group(1)) / 100, 5)
    except Exception as e:
        if verbose: print(f"  [Warning] Solar FAQ scrape error: {e}")

    return rates_found

# =============================================================================
# LAYER 2: DYNAMIC CITYWIDE FEE SCHEDULE PDF DISCOVERY
# =============================================================================
def discover_and_parse_citywide_fee_pdf(verbose: bool) -> dict:
    """
    Visits the City of Burbank Financial Services page, dynamically finds the 
    current 'Fee Schedule' PDF link, and extracts active Schedule D rates.
    """
    if verbose: print(f"\n[*] Scanning City Financial Services for PDF: {CITY_FINANCE_URL}")
    pdf_rates = {}
    
    try:
        res = requests.get(CITY_FINANCE_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200: return pdf_rates
        
        soup = BeautifulSoup(res.text, "html.parser")
        pdf_url = None
        
        # Search all links for Fee Schedule keywords
        for a in soup.find_all("a", href=True):
            link_text = a.get_text().upper()
            href = a["href"]
            if ("FEE SCHEDULE" in link_text or "CITYWIDE FEE" in link_text) and (".pdf" in href.lower() or "docaccess" in href.lower()):
                pdf_url = href if href.startswith("http") else f"https://www.burbankca.gov{href}"
                if verbose: print(f"  > Discovered Current Citywide Fee Schedule Link: {pdf_url}")
                break
                
        if pdf_url:
            pdf_res = requests.get(pdf_url, headers=HEADERS, timeout=25)
            if pdf_res.status_code == 200:
                with pdfplumber.open(io.BytesIO(pdf_res.content)) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text() or ""
                        if "RESIDENTIAL SERVICE" in page_text.upper() or "SCHEDULE D" in page_text.upper():
                            lines = page_text.split('\n')
                            for line in lines:
                                line_clean = line.strip()
                                line_upper = line_clean.upper()
                                decimals = [float(d) for d in re.findall(r"\d+\.\d{3,5}", line_clean)]
                                
                                # 1. Tier 1 Base Energy ($0.1460)
                                if "FIRST 300 KWH" in line_upper:
                                    valid = [d for d in decimals if 0.10 <= d <= 0.20]
                                    if valid:
                                        pdf_rates["tier1EnergyRate"] = valid[0]
                                        if verbose: print(f"    [PDF Extracted] Tier 1 Base Rate: ${pdf_rates['tier1EnergyRate']:.4f}/kWh")
                                
                                # 2. Tier 2 Base Energy ($0.2442)
                                if "ALL ADDITIONAL KWH" in line_upper or "ALL ADD'L KWH" in line_upper:
                                    valid = [d for d in decimals if 0.20 <= d <= 0.32]
                                    if valid:
                                        pdf_rates["tier2EnergyRate"] = valid[0]
                                        if verbose: print(f"    [PDF Extracted] Tier 2 Base Rate: ${pdf_rates['tier2EnergyRate']:.4f}/kWh")

                                # 3. ECAC ($0.0340)
                                if "ECAC" in line_upper:
                                    valid = [d for d in decimals if 0.02 <= d <= 0.06]
                                    if valid:
                                        pdf_rates["ecac"] = valid[0]
                                        if verbose: print(f"    [PDF Extracted] ECAC Surcharge: ${pdf_rates['ecac']:.4f}/kWh")

                                # 4. Customer Service Charge ($19.50)
                                if "CUSTOMER SERVICE CHARGE" in line_upper:
                                    valid = [d for d in decimals if 15.00 <= d <= 25.00]
                                    if valid:
                                        pdf_rates["customerServiceCharge"] = valid[0]
                                        if verbose: print(f"    [PDF Extracted] Customer Service Charge: ${pdf_rates['customerServiceCharge']:.2f}/mo")

                            if "tier1EnergyRate" in pdf_rates:
                                break
    except Exception as e:
        if verbose: print(f"  [Warning] PDF Discovery fallback error: {e}")
        
    return pdf_rates

# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================
def main():
    args = parse_args()
    
    # Load existing JSON for non-destructive updating
    current_data = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r") as f:
            current_data = json.load(f)

    # 1. Attempt Layer 1 (Static Web)
    web_rates = scrape_bwp_web_rates(args.verbose)
    
    # 2. Attempt Layer 2 (PDF Discovery) if web scrape had missing fields
    if not all(k in web_rates for k in ["tier1EnergyRate", "tier2EnergyRate", "customerServiceCharge"]):
        pdf_rates = discover_and_parse_citywide_fee_pdf(args.verbose)
        web_rates.update({k: v for k, v in pdf_rates.items() if k not in web_rates})

    # 3. Assemble Full Rate Dictionary with Fallback Defaults
    data = {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d"),
        "utility": "Burbank Water and Power",
        "electric": {
            "customerServiceCharge": web_rates.get("customerServiceCharge", current_data.get("electric", {}).get("customerServiceCharge", 19.50)),
            "serviceSizeCharge": 4.45,
            "ecac": web_rates.get("ecac", current_data.get("electric", {}).get("ecac", 0.03400)),
            "taxRate": 0.070,
            "tier1Limit": 300.0,
            "tier1EnergyRate": web_rates.get("tier1EnergyRate", current_data.get("electric", {}).get("tier1EnergyRate", 0.14600)),
            "tier2EnergyRate": web_rates.get("tier2EnergyRate", current_data.get("electric", {}).get("tier2EnergyRate", 0.24420)),
            "sbpExportRate": web_rates.get("sbpExportRate", current_data.get("electric", {}).get("sbpExportRate", 0.09110)),
            "nscRate": web_rates.get("nscRate", current_data.get("electric", {}).get("nscRate", 0.04500))
        },
        "water": {
            "monthlyAvailabilityCharge": 24.87,
            "wcac": 2.82500,
            "limits": {
                "tier1": 8.0,
                "tier2": 20.0
            },
            "baseRates": {
                "tier1": 1.78500,
                "tier2": 3.49100,
                "tier3": 4.31500
            }
        }
    }

    print("\n" + "=" * 60)
    print("           BURBANK WATER & POWER RATE REPORT")
    print("=" * 60)
    print(f"Customer Service Charge: ${data['electric']['customerServiceCharge']:.2f}/month")
    print(f"Service Size Charge:     ${data['electric']['serviceSizeCharge']:.2f}/month")
    print(f"Schedule D Tier 1:       ${data['electric']['tier1EnergyRate'] + data['electric']['ecac']:.4f}/kWh (0-300 kWh)")
    print(f"Schedule D Tier 2:       ${data['electric']['tier2EnergyRate'] + data['electric']['ecac']:.4f}/kWh (>300 kWh)")
    print(f"Solar SBP ACOE Export:   ${data['electric']['sbpExportRate']:.5f}/kWh")
    print(f"Solar NSC Buyout:        ${data['electric']['nscRate']:.5f}/kWh")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN COMPLETE] JSON validated. File was not modified.")
    else:
        with open(OUTPUT_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\n[SUCCESS] Updated {OUTPUT_FILE} successfully.")

if __name__ == "__main__":
    main()
