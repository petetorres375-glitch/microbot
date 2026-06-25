"""
fetch_verdicts.py
-----------------
Reads morning verdicts written by the CCR container and applies them locally.

The CCR container cannot push to GitHub (403 proxy). Instead it writes
morning_verdicts_ccr.json to a Google Drive folder using its Drive MCP tools.
This script reads that file via the service account, writes morning_verdicts.json,
and commits + pushes — acting as the bridge from CCR → git.

Sources tried in order:
  1. Google Drive folder (primary — written by CCR routine via Drive MCP)
  2. Google Sheet "Verdicts" tab (fallback)

Run daily at 8:50 AM ET (after the 8:30 AM CCR analysis completes).

Usage:
    python fetch_verdicts.py            # fetch + commit + push
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
DRIVE_FOLDER_ID = "12_v9m-kyzN4KrUMCXdObQlTEkUBqM7OP"
DRIVE_FILE_NAME = "morning_verdicts_ccr.json"


def _drive_creds():
    import google.oauth2.service_account as sa
    return sa.Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json"),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )


def fetch_from_drive() -> dict | None:
    """Read the most recent verdicts file from the CCR Google Drive folder."""
    try:
        import google.auth.transport.requests
        import requests as http

        creds = _drive_creds()
        creds.refresh(google.auth.transport.requests.Request())
        headers = {"Authorization": f"Bearer {creds.token}"}

        # Find the most recently modified verdicts file in the folder
        resp = http.get(
            "https://www.googleapis.com/drive/v3/files",
            headers=headers,
            params={
                "q": f"name='{DRIVE_FILE_NAME}' and trashed=false",
                "orderBy": "modifiedTime desc",
                "pageSize": 1,
                "fields": "files(id,name,modifiedTime)",
            },
        )
        resp.raise_for_status()
        files = resp.json().get("files", [])
        if not files:
            print("[fetch_verdicts] No Drive verdicts file found yet.")
            return None

        file_id = files[0]["id"]
        content_resp = http.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
            headers=headers,
        )
        content_resp.raise_for_status()
        data = content_resp.json()

        if data.get("date") != date.today().isoformat():
            print(f"[fetch_verdicts] Drive file dated {data.get('date')}, not today.")
            return None

        return data
    except Exception as e:
        print(f"[fetch_verdicts] Drive read failed: {e}")
        return None


def fetch_from_sheet() -> dict | None:
    """Fallback: read verdicts from the Google Sheet Verdicts tab."""
    try:
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
        ws = gc.open_by_key(os.getenv("GSHEET_ID")).worksheet("Verdicts")
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
        print(f"[fetch_verdicts] Sheet verdicts dated {sheet_date}, not today.")
        return None

    try:
        return {"date": sheet_date, "verdicts": json.loads(verdicts_json)}
    except json.JSONDecodeError as e:
        print(f"[fetch_verdicts] Bad JSON in sheet: {e}")
        return None


def fetch() -> dict | None:
    result = fetch_from_drive()
    if result:
        print("[fetch_verdicts] Source: Google Drive")
        return result
    result = fetch_from_sheet()
    if result:
        print("[fetch_verdicts] Source: Google Sheet (fallback)")
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    result = fetch()
    if result is None:
        print("[fetch_verdicts] No fresh verdicts found. Nothing to apply.")
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
