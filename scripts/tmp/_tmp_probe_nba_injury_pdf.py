"""Probe the NBA official injury PDF to understand its structure."""
import requests
import pdfplumber
import io
import re
from datetime import date
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Step 1: Scrape the page for PDF links
print("Fetching NBA injury page...")
r = requests.get(
    "https://official.nba.com/nba-injury-report-2025-26-season/",
    timeout=20, headers=HEADERS
)
html = r.text

# Find all PDF links matching injury report pattern
pdf_links = re.findall(
    r'https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report[^\s"\'<>]+\.pdf',
    html
)
print(f"\nPDF links found: {len(pdf_links)}")
for lnk in sorted(set(pdf_links)):
    print(f"  {lnk}")

# Step 2: Download the most recent PDF (sort by name descending = newest last)
if not pdf_links:
    print("No PDF links found!")
    raise SystemExit(1)

# Sort and take the latest (most recent time/date in name)
latest_pdf_url = sorted(set(pdf_links))[-1]
print(f"\nDownloading: {latest_pdf_url}")
pdf_resp = requests.get(latest_pdf_url, timeout=30, headers=HEADERS)
print(f"PDF downloaded: {len(pdf_resp.content):,} bytes")

# Step 3: Parse the PDF with pdfplumber
pdf_bytes = io.BytesIO(pdf_resp.content)
with pdfplumber.open(pdf_bytes) as pdf:
    print(f"PDF pages: {len(pdf.pages)}")
    for i, page in enumerate(pdf.pages[:3]):
        print(f"\n=== Page {i+1} text preview ===")
        text = page.extract_text()
        if text:
            print(text[:1000])
        print(f"\n=== Page {i+1} tables ===")
        tables = page.extract_tables()
        print(f"Tables found: {len(tables)}")
        for j, tbl in enumerate(tables[:2]):
            print(f"  Table {j+1}: {len(tbl)} rows x {len(tbl[0]) if tbl else 0} cols")
            for row in tbl[:5]:
                print(f"    {row}")
