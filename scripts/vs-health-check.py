#!/usr/bin/env python3
"""Vector Store nightly health check script.

Usage (PowerShell):
  python scripts/vs-health-check.py <base_url> <workspace_id>

Example:
  python scripts/vs-health-check.py http://127.0.0.1:8000 ws_123

Returns non-zero exit code if the health endpoint fails or dangling counts are high.
"""

import sys
import json
import urllib.request
import urllib.error


def main():
    if len(sys.argv) < 3:
        print("Usage: vs-health-check.py <base_url> <workspace_id>")
        return 2
    base_url, ws = sys.argv[1], sys.argv[2]
    url = f"{base_url.rstrip('/')}/responses/vector-store/health/summary?workspace_id={ws}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            if resp.status != 200:
                print(f"Error: HTTP {resp.status}")
                return 2
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"Request failed: {e}")
        return 2

    print(json.dumps(data, indent=2))
    # Simple gating: non-zero dangling counts -> exit 1
    dang = data.get("dangling_counts", {})
    if (dang.get("vs_without_db_mapping", 0) > 0) or (dang.get("db_ingested_missing_in_vs", 0) > 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
