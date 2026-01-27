"""Standalone Discord webhook test.

Usage:
  python scripts/test_discord_webhook.py --text "hello"

Exit code:
  0 = success, 1 = failure
"""

import argparse
import os
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True, help="text to send")
    parser.add_argument("--timeout", type=int, default=8)
    args = parser.parse_args()

    url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        print("DISCORD_WEBHOOK_URL is not set")
        return 1
    if not (url.startswith("http://") or url.startswith("https://")):
        print("DISCORD_WEBHOOK_URL looks invalid (missing http/https)")
        return 1

    payload = {"content": args.text}
    try:
        resp = requests.post(url, json=payload, timeout=args.timeout)
    except requests.exceptions.RequestException as e:
        print(f"request failed: {e}")
        return 1

    if resp.status_code in (200, 204):
        print("ok")
        return 0

    print(f"failed: status={resp.status_code} body={resp.text[:500]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
