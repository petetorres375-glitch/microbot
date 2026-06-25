"""
fetch_verdicts.py
-----------------
Reads morning_verdicts from the Google Sheet "Verdicts" tab,
writes morning_verdicts.json, and commits + pushes.

Run daily at 8:50 AM ET (after the 8:30 AM CCR analysis completes).
The CCR container cannot push to GitHub, so it writes verdicts to the
Verdicts tab instead. This script is the bridge that gets them into git.

Usage:
    python fetch_verdicts.py          # fetch + commit + push
    python fetch_verdicts.py --dry-run  # print only, no write/commit
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

VERDICTS_FILE = "morning_verdicts.json"


def _sheet():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json"),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.getenv("GSHEET_ID")).worksheet("Verdicts")


def fetch() -> dict | None:
    try:
        ws = _sheet()
        rows = ws.get_all_values()
    except Exception as e:
        print(f"[fetch_verdicts] Sheet read failed: {e}")
        return None

    data: dict = {}
    for row in rows:
        if len(row) >= 2:
            data[row[0]] = row[1]

    sheet_date = data.get("date", "")
    verdicts_json = data.get("verdicts_json", "")

    if not sheet_date or not verdicts_json:
        print("[fetch_verdicts] Verdicts tab is empty or malformed.")
        return None

    if sheet_date != date.today().isoformat():
        print(f"[fetch_verdicts] Sheet verdicts are from {sheet_date}, not today. Skipping.")
        return None

    try:
        verdicts = json.loads(verdicts_json)
    except json.JSONDecodeError as e:
        print(f"[fetch_verdicts] Bad JSON in sheet: {e}")
        return None

    return {"date": sheet_date, "verdicts": verdicts}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    result = fetch()
    if result is None:
        sys.exit(1)

    clean = sum(1 for v in result["verdicts"].values() if v == "CLEAN")
    caution = sum(1 for v in result["verdicts"].values() if v == "CAUTION")
    avoid = sum(1 for v in result["verdicts"].values() if v == "AVOID")
    print(f"[fetch_verdicts] {result['date']}: {clean} CLEAN  {caution} CAUTION  {avoid} AVOID")

    if args.dry_run:
        print("[fetch_verdicts] dry-run — no write or commit.")
        return

    with open(VERDICTS_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[fetch_verdicts] Wrote {VERDICTS_FILE}")

    try:
        subprocess.run(["git", "add", VERDICTS_FILE], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"verdicts: {result['date']}"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
        print("[fetch_verdicts] Committed and pushed.")
    except subprocess.CalledProcessError as e:
        print(f"[fetch_verdicts] git step failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
