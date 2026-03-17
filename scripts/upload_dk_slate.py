"""Upload DKSalaries.csv to the local slates API."""
import urllib.request

csv_path = "/tmp/DKSalaries.csv"
url = "http://localhost:8000/api/slates/upload?platform=draftkings&sport=nba&slate_date=2026-02-25"
boundary = "FormBoundaryDKUpload"

with open(csv_path, "rb") as f:
    csv_bytes = f.read()

body = (
    b"--" + boundary.encode() + b"\r\n"
    b'Content-Disposition: form-data; name="file"; filename="DKSalaries.csv"\r\n'
    b"Content-Type: text/csv\r\n\r\n"
    + csv_bytes
    + b"\r\n--" + boundary.encode() + b"--\r\n"
)

req = urllib.request.Request(url, data=body, method="POST")
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

with urllib.request.urlopen(req) as r:
    print(r.read().decode())
