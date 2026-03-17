"""Check NBA official injury page HTML for data structure."""
import requests, re

r = requests.get(
    "https://official.nba.com/nba-injury-report-2025-26-season/",
    timeout=20,
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
)
html = r.text

# Check for table tags
table_count = html.count("<table")
tr_count = html.count("<tr")
print(f"<table> tags: {table_count}")
print(f"<tr> tags: {tr_count}")

# Check for known injury keywords
for kw in ["OUT", "Questionable", "Doubtful", "injury-report", "InjuryReport", "player_name", "game_date"]:
    idx = html.find(kw)
    if idx != -1:
        print(f"\nFound '{kw}' at pos {idx}:")
        print(html[max(0, idx-100):idx+200])

# Check for embedded JSON
json_matches = re.findall(r'window\.__[A-Z_]+\s*=\s*(\{.*?\});', html[:50000])
print(f"\nEmbedded JSON vars: {len(json_matches)}")

# Check for PDF link
pdf = re.findall(r'https?://[^\s"\']+\.pdf', html)
print(f"\nPDF links: {pdf[:5]}")

# Check for iframe or external data source
iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html)
print(f"iframes: {iframes[:5]}")

# Print chunk around 'injury' in HTML
for m in re.finditer(r'(?i)injury', html):
    start = max(0, m.start()-50)
    end = min(len(html), m.end()+200)
    snippet = html[start:end].replace('\n', ' ')
    if '<' not in snippet[50:100]:  # Skip pure JS/CSS
        print(f"\ntext context: {snippet}")
    break
