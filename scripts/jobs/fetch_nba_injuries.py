"""
fetch_nba_injuries.py
====================
Scrapes the official NBA injury report PDF from:
    https://official.nba.com/nba-injury-report-2025-26-season/

Workflow
--------
1. Fetch the page HTML and extract all PDF links
2. Pick the most recent PDF (by timestamp in the filename)
3. Download and parse the PDF with pdfplumber
4. Write structured records to nba_news.duckdb:
      Table: nba_injury_report  (full raw data)
      View:  vw_nba_injury_status  (deduplicated, pool_filter-compatible)

Usage
-----
    python scripts/fetch_nba_injuries.py               # fetch today
    python scripts/fetch_nba_injuries.py --date 2026-02-24
    python scripts/fetch_nba_injuries.py --dry-run      # print + skip DB write
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_nba_injuries")

# ── Constants ──────────────────────────────────────────────────────────────────
NBA_PAGE_URL  = "https://official.nba.com/nba-injury-report-2025-26-season/"
PDF_BASE_URL  = "https://ak-static.cms.nba.com/referee/injury/"
PDF_PATTERN   = re.compile(
    r"https://ak-static\.cms\.nba\.com/referee/injury/"
    r"(Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf)"
)
STATUSES      = {"Out", "Questionable", "Probable", "Doubtful", "Available"}

# Player name in PDF uses LastName,FirstName format (no internal space before the comma).
# Handles: Irving,Kyrie  |  Gilgeous-Alexander,Shai  |  OubreJr.,Kelly
#          McCullarJr.,Kevin  |  McConnell,T.J.  |  ButlerII,Jimmy
_NAME_FRAG    = r"[A-Z][a-zA-Z''\-\.]+(?:Jr\.|Sr\.|II|III|IV|V)?,"
PLAYER_LINE_RE = re.compile(
    r"(?P<name>" + _NAME_FRAG + r"\S+)"
    r"\s+(?P<status>Out|Questionable|Probable|Doubtful|Available)\b"
    r"(?P<reason_rest>.*)"
)

# A line that contains (at some position) a player name + status.
# Used to detect player rows embedded in longer context lines.
EMBEDDED_PLAYER_RE = re.compile(
    r"(?P<name>" + _NAME_FRAG + r"\S+)"
    r"\s+(?P<status>Out|Questionable|Probable|Doubtful|Available)\b"
    r"(?P<reason_rest>.*)"
)

# ── Line-type regexes (matched against stripped lines) ────────────────────────
# 1) Full context: Date Time(ET) Matchup Team PlayerName,First Status [Reason]
DATE_LINE_RE  = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})"
    r"\s+(?P<time>\d{2}:\d{2})\(ET\)"
    r"\s+(?P<matchup>[A-Z]+@[A-Z]+)"
    r"\s+(?P<team>[A-Z][a-zA-Z0-9]+(?: [A-Z][a-zA-Z0-9]+)*?)"
    r"\s+(?P<rest>" + _NAME_FRAG + r"\S.*)"
)
# 2) Date only / "NOTYETSUBMITTED" row (next-day placeholder) — extract context, skip player
DATE_CTX_RE   = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})"
    r"\s+(?P<time>\d{2}:\d{2})\(ET\)"
    r"\s+(?P<matchup>[A-Z]+@[A-Z]+)"
)
# 3) Time+Matchup+Team+Player on same line (same date, new game)
TIME_LINE_RE  = re.compile(
    r"^(?P<time>\d{2}:\d{2})\(ET\)"
    r"\s+(?P<matchup>[A-Z]+@[A-Z]+)"
    r"\s+(?P<team>[A-Z][a-zA-Z0-9]+(?: [A-Z][a-zA-Z0-9]+)*?)"
    r"\s+(?P<rest>" + _NAME_FRAG + r"\S.*)"
)
# 4) Time+Matchup context only (NOTYETSUBMITTED)
TIME_CTX_RE   = re.compile(
    r"^(?P<time>\d{2}:\d{2})\(ET\)"
    r"\s+(?P<matchup>[A-Z]+@[A-Z]+)"
)
# 5) Matchup+Team+Player (same date/time context, new game — matchup appears without time)
MATCHUP_LINE_RE = re.compile(
    r"^(?P<matchup>[A-Z]{2,4}@[A-Z]{2,4})"
    r"\s+(?P<team>[A-Z][a-zA-Z0-9]+(?: [A-Z][a-zA-Z0-9]+)*?)"
    r"\s+(?P<rest>" + _NAME_FRAG + r"\S.*)"
)
# 6) Matchup context only (NOTYETSUBMITTED)
MATCHUP_CTX_RE  = re.compile(
    r"^(?P<matchup>[A-Z]{2,4}@[A-Z]{2,4})"
    r"\s+[A-Z][a-zA-Z]"
)
# 7) Team+Player (same game, new team)
# Team names in PDF are single camelCase words (TorontoRaptors, OklahomaCityThunder, etc.)
# Use * (not +) so a single-word team name matches.
TEAM_PLAYER_RE = re.compile(
    r"^(?P<team>[A-Z][a-zA-Z0-9]+(?: [A-Z][a-zA-Z0-9]+)*)\s+"
    r"(?P<rest>" + _NAME_FRAG + r"\S.*)"
)

# Lines that should NEVER be appended to a player's reason
_SKIP_CONTINUATION = re.compile(
    r"NOTYETSUBMITTED"
    r"|\d{2}:\d{2}\(ET\)"
    r"|\d{2}/\d{2}/\d{4}"
    r"|^Page\d+of\d+$"
    r"|^GameDate\s+GameTime"
    r"|^Injury Report:"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

DB_PATH = ROOT / "data" / "nba_news.duckdb"


# ── Name helpers ───────────────────────────────────────────────────────────────

# Suffixes that can appear concatenated at the end of a last name in the PDF
_SUFFIX_RE = re.compile(r"^(.*?)(Jr\.|Sr\.|II|III|IV|V)$")


def canonical_name(pdf_name: str) -> str:
    """
    Convert PDF 'LastName,FirstName' → 'FirstName LastName' canonical form.
    Handles:
      OubreJr.,Kelly     → Kelly Oubre Jr.
      McCullarJr.,Kevin  → Kevin McCullar Jr.
      ButlerIII,Jimmy    → Jimmy Butler III
      Gilgeous-Alexander,Shai → Shai Gilgeous-Alexander
      McConnell,T.J.     → T.J. McConnell
    """
    if "," not in pdf_name:
        return pdf_name.strip()
    last, first = pdf_name.split(",", 1)
    last = last.strip()
    first = first.strip()
    # Check for suffix concatenated with the last name (e.g., "OubreJr." → "Oubre Jr.")
    m = _SUFFIX_RE.match(last)
    if m:
        last = f"{m.group(1)} {m.group(2)}"
    return f"{first} {last}"


def player_slug(full_name: str) -> str:
    """'LeBron James' → 'lebron_james'  (matches pool_filter._slug)"""
    return (
        full_name.lower()
        .replace("jr.", "jr")
        .replace("'", "")
        .replace(".", "")
        .replace("-", "_")
        .replace(" ", "_")
    )


# ── Step 1: Discover PDFs ──────────────────────────────────────────────────────

def fetch_pdf_links(target_date: Optional[date] = None) -> list[str]:
    """Scrape NBA page and return sorted list of PDF URLs for target_date."""
    log.info("Fetching NBA injury page: %s", NBA_PAGE_URL)
    r = requests.get(NBA_PAGE_URL, timeout=20, headers=HEADERS)
    r.raise_for_status()

    date_str = (target_date or date.today()).strftime("%Y-%m-%d")
    all_links = re.findall(
        r"https://ak-static\.cms\.nba\.com/referee/injury/"
        r"Injury-Report_" + re.escape(date_str) + r"_[^\"' <>\n]+\.pdf",
        r.text,
    )
    unique = sorted(set(all_links))
    log.info("Found %d PDF links for %s", len(unique), date_str)
    return unique


def _pdf_sort_key(url: str) -> datetime:
    """
    Parse filename timestamp for proper chronological sort.
    'Injury-Report_2026-02-24_12_45PM.pdf' → datetime(2026, 2, 24, 12, 45)
    """
    m = PDF_PATTERN.search(url)
    if not m:
        return datetime.min
    date_part = m.group(2)  # YYYY-MM-DD
    hh = int(m.group(3))
    mm = int(m.group(4))
    ampm = m.group(5)
    if ampm == "PM" and hh != 12:
        hh += 12
    elif ampm == "AM" and hh == 12:
        hh = 0
    dt_str = f"{date_part} {hh:02d}:{mm:02d}"
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M")


def latest_pdf_url(links: list[str]) -> str:
    """Return the URL of the most recent injury report."""
    return max(links, key=_pdf_sort_key)


def get_latest_db_url(db_path: Path) -> Optional[str]:
    """
    Return the report_url of the most recently ingested record in
    nba_injury_report, or None if the table doesn't exist / is empty.
    """
    if not db_path.exists():
        return None
    try:
        # Prefer the shared registry connection (prevents read_only conflict in-process)
        try:
            from analysis.shared.db import get_conn
            con = get_conn(db_path, db_key="nba_news")
        except Exception:
            import duckdb
            con = duckdb.connect(str(db_path))
        row = con.execute(
            "SELECT report_url FROM nba_injury_report "
            "ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def ensure_current(
    db_path: Path | None = None,
    target_date: Optional[date] = None,
    force: bool = False,
    print_report: bool = False,
) -> dict:
    """
    Public API used by the pipeline and CLI.

    1. Fetch the list of PDF links from NBA page (lightweight HEAD-like request).
    2. Compare the latest link against what's already stored in the DB.
    3. If they match and force=False → skip (already current).
    4. Otherwise → download, parse, and write to DB.

    Returns a dict:
        status   : 'current' | 'updated' | 'error'
        pdf_url  : URL of the latest PDF on the NBA page
        db_url   : URL that was in the DB before this call
        records  : number of rows written (0 if skipped)
        message  : human-readable summary
    """
    _db = db_path or DB_PATH
    result = {
        "status": "error",
        "pdf_url": None,
        "db_url": None,
        "records": 0,
        "message": "",
    }

    # Step 1 – discover latest PDF on the NBA page
    try:
        links = fetch_pdf_links(target_date)
    except Exception as exc:
        result["message"] = f"Failed to fetch PDF links: {exc}"
        log.error(result["message"])
        return result

    if not links:
        result["message"] = f"No PDFs found for {target_date or date.today()}"
        log.warning(result["message"])
        return result

    pdf_url = latest_pdf_url(links)
    result["pdf_url"] = pdf_url

    # Step 2 – compare against what's in the DB
    db_url = get_latest_db_url(_db)
    result["db_url"] = db_url

    if not force and db_url == pdf_url:
        result["status"] = "current"
        result["message"] = f"Already up to date: {pdf_url.split('/')[-1]}"
        log.info("Injury data already current — skipping fetch (%s)", pdf_url.split('/')[-1])
        return result

    reason = "forced" if force else f"new PDF available ({pdf_url.split('/')[-1]})"
    log.info("Fetching injury report: %s", reason)

    # Step 3 – download + parse
    try:
        pdf_bytes = download_pdf(pdf_url)
    except Exception as exc:
        result["message"] = f"Failed to download PDF: {exc}"
        log.error(result["message"])
        return result

    records = parse_pdf(pdf_bytes, pdf_url)
    if not records:
        result["message"] = "PDF parsed but no records extracted"
        log.warning(result["message"])
        return result

    if print_report:
        print_injury_report(records)

    # Step 4 – write to DB
    try:
        _db.parent.mkdir(parents=True, exist_ok=True)
        n = write_to_db(records, _db, pdf_url)
        result["status"] = "updated"
        result["records"] = n
        result["message"] = f"Updated: {n} records from {pdf_url.split('/')[-1]}"
        log.info(result["message"])
    except Exception as exc:
        result["message"] = f"DB write failed: {exc}"
        log.error(result["message"])

    return result


# ── Step 2: Download & parse PDF ───────────────────────────────────────────────

def download_pdf(url: str) -> bytes:
    log.info("Downloading PDF: %s", url.split("/")[-1])
    r = requests.get(url, timeout=30, headers=HEADERS)
    r.raise_for_status()
    log.info("PDF size: %s bytes", f"{len(r.content):,}")
    return r.content


def parse_pdf(pdf_bytes: bytes, report_url: str) -> list[dict]:
    """
    Parse all pages of the NBA injury PDF and return a list of dicts,
    one per player record.
    """
    try:
        import pdfplumber
    except ImportError:
        log.error("pdfplumber not installed: pip install pdfplumber")
        return []

    records: list[dict] = []

    # State machine context (carries forward page-to-page)
    ctx: dict = {
        "game_date": "",
        "game_time": "",
        "matchup":   "",
        "team":      "",
    }
    cur: Optional[dict] = None  # player currently being assembled

    def flush(player: dict | None) -> None:
        """Commit the current player record to results."""
        if player:
            player["reason"] = player["reason"].strip()
            records.append(player)

    def new_player(pdf_name: str, status: str, reason: str = "") -> dict:
        full_name = canonical_name(pdf_name)
        return {
            "game_date":       ctx["game_date"],
            "game_time":       ctx["game_time"],
            "matchup":         ctx["matchup"],
            "team":            ctx["team"],
            "player_pdf_name": pdf_name,
            "player_name":     full_name,
            "player_id":       player_slug(full_name),
            "status":          status,
            "reason":          reason.strip(),
            "report_url":      report_url,
            "fetched_at":      datetime.utcnow().isoformat(),
        }

    def try_extract_player(rest: str) -> Optional[dict]:
        """Try to extract a player record from the tail of a context line."""
        pm = EMBEDDED_PLAYER_RE.search(rest)
        if pm:
            return new_player(
                pm.group("name"),
                pm.group("status"),
                pm.group("reason_rest").strip(),
            )
        return None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            lines = text.splitlines()

            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue

                # ── Always skip these regardless of state ───────────────────
                if (
                    line.startswith("GameDate")
                    or line.startswith("Injury Report:")
                    or re.match(r"^Page\d+of\d+$", line)
                ):
                    continue

                # ── Skip NOTYETSUBMITTED placeholders entirely ───────────────
                if "NOTYETSUBMITTED" in line:
                    # Still update ctx for any date/time/matchup embedded here
                    # so we don't mis-assign next player; just don't create a record
                    m_dc = DATE_CTX_RE.match(line)
                    if m_dc:
                        flush(cur); cur = None
                        ctx["game_date"] = m_dc.group("date")
                        ctx["game_time"] = m_dc.group("time")
                        ctx["matchup"]   = m_dc.group("matchup")
                    m_tc = TIME_CTX_RE.match(line)
                    if not m_dc and m_tc:
                        flush(cur); cur = None
                        ctx["game_time"] = m_tc.group("time")
                        ctx["matchup"]   = m_tc.group("matchup")
                    continue

                # ── 1) Full context: Date Time(ET) Matchup Team Player Status ─
                m = DATE_LINE_RE.match(line)
                if m:
                    flush(cur); cur = None
                    ctx["game_date"] = m.group("date")
                    ctx["game_time"] = m.group("time")
                    ctx["matchup"]   = m.group("matchup")
                    ctx["team"]      = m.group("team").strip()
                    cur = try_extract_player(m.group("rest"))
                    continue

                # ── 2) Date context only (no player, e.g. next-day header) ───
                m = DATE_CTX_RE.match(line)
                if m:
                    flush(cur); cur = None
                    ctx["game_date"] = m.group("date")
                    ctx["game_time"] = m.group("time")
                    ctx["matchup"]   = m.group("matchup")
                    continue

                # ── 3) Time + Matchup + Team + Player (same date, new game) ──
                m = TIME_LINE_RE.match(line)
                if m:
                    flush(cur); cur = None
                    ctx["game_time"] = m.group("time")
                    ctx["matchup"]   = m.group("matchup")
                    ctx["team"]      = m.group("team").strip()
                    cur = try_extract_player(m.group("rest"))
                    continue

                # ── 4) Time + Matchup context only ────────────────────────────
                m = TIME_CTX_RE.match(line)
                if m:
                    flush(cur); cur = None
                    ctx["game_time"] = m.group("time")
                    ctx["matchup"]   = m.group("matchup")
                    continue

                # ── 5) Matchup + Team + Player (same date/time, new game) ─────
                m = MATCHUP_LINE_RE.match(line)
                if m:
                    flush(cur); cur = None
                    ctx["matchup"] = m.group("matchup")
                    ctx["team"]    = m.group("team").strip()
                    cur = try_extract_player(m.group("rest"))
                    continue

                # ── 6) Matchup context only ────────────────────────────────────
                m = MATCHUP_CTX_RE.match(line)
                if m:
                    flush(cur); cur = None
                    ctx["matchup"] = m.group("matchup")
                    continue

                # ── 7) Team + Player (same game, new team) ────────────────────
                m = TEAM_PLAYER_RE.match(line)
                if m:
                    rest = m.group("rest")
                    ep = try_extract_player(rest)
                    if ep:
                        flush(cur); cur = None
                        ctx["team"] = m.group("team").strip()
                        cur = ep
                        continue

                # ── 8) Bare player line: LastName,FirstName Status [Reason] ───
                m = PLAYER_LINE_RE.match(line)
                if m:
                    flush(cur); cur = None
                    cur = new_player(
                        m.group("name"),
                        m.group("status"),
                        m.group("reason_rest").strip(),
                    )
                    continue

                # ── 9) Continuation / reason wrap ─────────────────────────────
                # Skip anything that looks like game-scheduling context
                if _SKIP_CONTINUATION.search(line):
                    continue
                if cur is not None:
                    cur["reason"] = (cur["reason"] + " " + line).strip()

        # Flush the last player when the PDF ends
        flush(cur)

    log.info("Parsed %d player records from PDF", len(records))
    return records


# ── Step 3: Write to DuckDB ────────────────────────────────────────────────────

def _ensure_db(db_path: Path) -> None:
    """Create or migrate the nba_injury_report table and vw_nba_injury_status view."""
    try:
        from analysis.shared.db import get_conn
        con = get_conn(db_path, db_key="nba_news")
    except Exception:
        import duckdb
        con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS nba_injury_report (
            game_date         VARCHAR,
            game_time         VARCHAR,
            matchup           VARCHAR,
            team              VARCHAR,
            player_pdf_name   VARCHAR,
            player_name       VARCHAR,
            player_id         VARCHAR,
            status            VARCHAR,
            reason            VARCHAR,
            report_url        VARCHAR,
            fetched_at        TIMESTAMP
        )
    """)

    # Always replace the view so schema changes take effect
    con.execute("""
        CREATE OR REPLACE VIEW vw_nba_injury_status AS
        SELECT
            player_id,
            player_name,
            UPPER(status)  AS status,
            reason         AS detail,
            game_date,
            game_time,
            matchup,
            team,
            1.0            AS confidence
        FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY player_id
                    ORDER BY fetched_at DESC
                ) AS rn
            FROM nba_injury_report
        ) t
        WHERE rn = 1
    """)


def write_to_db(records: list[dict], db_path: Path, report_url: str) -> int:
    """
    Insert new records into nba_injury_report.
    Deletes existing rows for the same report_url first (idempotent).
    Returns number of rows inserted.
    """
    import pandas as pd

    _ensure_db(db_path)

    if not records:
        log.warning("No records to write")
        return 0

    df = pd.DataFrame(records)
    df["fetched_at"] = pd.to_datetime(df["fetched_at"])

    try:
        from analysis.shared.db import get_conn
        con = get_conn(db_path, db_key="nba_news")
    except Exception:
        import duckdb
        con = duckdb.connect(str(db_path))
    # Idempotent: delete then re-insert
    con.execute("DELETE FROM nba_injury_report WHERE report_url = ?", [report_url])
    con.execute("INSERT INTO nba_injury_report SELECT * FROM df")
    total = con.execute("SELECT COUNT(*) FROM nba_injury_report WHERE report_url = ?", [report_url]).fetchone()[0]

    log.info("Wrote %d records to nba_injury_report", total)
    return total


# ── Reporting ──────────────────────────────────────────────────────────────────

def print_injury_report(records: list[dict]) -> None:
    """Print a human-readable injury summary to stdout."""
    from collections import defaultdict

    print("\n" + "=" * 70)
    print(f"  NBA INJURY REPORT  ({len(records)} players listed)")
    print("=" * 70)

    by_status: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_status[r["status"]].append(r)

    order = ["Out", "Doubtful", "Questionable", "Probable", "Available"]
    for status in order:
        players = by_status.get(status, [])
        if not players:
            continue
        print(f"\n  {status.upper()} ({len(players)})")
        print("  " + "-" * 50)
        for p in sorted(players, key=lambda x: x["player_name"]):
            reason = p["reason"] or "No reason listed"
            print(f"  {p['player_name']:<28s}  {p['team']:<22s}  {reason}")
    print()


# ── CLI Entry Point ────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch & parse NBA injury report PDF")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Print only, skip DB write")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if DB is already current")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to nba_news.duckdb")
    args = parser.parse_args()

    target_date: Optional[date] = None
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    db_path = Path(args.db)

    if args.dry_run:
        # Dry-run: discover + parse + print, never write
        try:
            links = fetch_pdf_links(target_date)
        except Exception as exc:
            log.error("Failed to fetch PDF links: %s", exc)
            return 1
        if not links:
            log.warning("No PDF links found for %s", target_date or date.today())
            return 1
        pdf_url = latest_pdf_url(links)
        pdf_bytes = download_pdf(pdf_url)
        records = parse_pdf(pdf_bytes, pdf_url)
        if records:
            print_injury_report(records)
            log.info("--dry-run: skipping DB write (%d records)", len(records))
        return 0

    result = ensure_current(
        db_path=db_path,
        target_date=target_date,
        force=args.force,
        print_report=True,
    )

    if result["status"] == "current":
        print(f"  [OK] {result['message']}")
        return 0
    elif result["status"] == "updated":
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
