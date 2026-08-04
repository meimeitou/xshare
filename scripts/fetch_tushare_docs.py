#!/usr/bin/env python3
"""Fetch Tushare API documentation from https://tushare.pro/wctapi/documents/xxx.md"""

import time
import httpx
from pathlib import Path

BASE_URL = "https://tushare.pro/wctapi/documents/{}.md"
OUTPUT_DIR = Path(__file__).parent.parent / "tushare-api"
START_ID = 3
END_ID = 423
DELAY = 0.3  # seconds between requests to avoid rate limiting


def fetch_doc(doc_id: int, client: httpx.Client) -> str | None:
    url = BASE_URL.format(doc_id)
    try:
        resp = client.get(url, timeout=15)
        if resp.status_code == 200:
            return resp.text
        elif resp.status_code == 404:
            return None
        else:
            print(f"  [{doc_id}] HTTP {resp.status_code}, skipping")
            return None
    except httpx.RequestError as e:
        print(f"  [{doc_id}] Request error: {e}")
        return None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total = END_ID - START_ID + 1
    fetched = 0
    skipped = 0

    with httpx.Client(follow_redirects=True) as client:
        for doc_id in range(START_ID, END_ID + 1):
            out_file = OUTPUT_DIR / f"{doc_id}.md"

            if out_file.exists():
                print(f"[{doc_id}/{END_ID}] Already exists, skipping")
                skipped += 1
                continue

            print(f"[{doc_id}/{END_ID}] Fetching...", end=" ", flush=True)
            content = fetch_doc(doc_id, client)

            if content:
                out_file.write_text(content, encoding="utf-8")
                print(f"saved ({len(content)} bytes)")
                fetched += 1
            else:
                print("not found")
                skipped += 1

            time.sleep(DELAY)

    print(f"\nDone. Fetched: {fetched}, Skipped/Not found: {skipped}, Total: {total}")


if __name__ == "__main__":
    main()
