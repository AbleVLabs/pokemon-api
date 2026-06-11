# probe_onepiece.py
# ---------------------------------------------------------------------------
# One Piece TCG data-source validation probe.
#
# EtherDex's One Piece adapter will be built on a community API (there is
# no official Bandai one). Before building, this script knocks on each
# candidate endpoint and reports what's actually there: status codes,
# response structure, and field names — without dumping megabytes of JSON.
#
# HOW TO RUN (from the project root, venv active):
#     python probe_onepiece.py
#
# Then paste the WHOLE output back into the chat.
# This file is throwaway — you can delete it afterwards.
# ---------------------------------------------------------------------------

import json

import requests

TIMEOUT = 15
HEADERS = {"User-Agent": "EtherDex/0.1 (one-time data source validation)"}

# Each candidate: (label, url, query params or None)
CANDIDATES = [
    # --- optcgapi.com — One Piece-specific community API ---
    ("optcgapi: all sets", "https://optcgapi.com/api/allSets/", None),
    ("optcgapi: one set's cards", "https://optcgapi.com/api/sets/OP01/", None),
    ("optcgapi: standard sets", "https://optcgapi.com/api/allStandardSets/", None),
    # --- apitcg.com — multi-TCG community API (may require a free key) ---
    (
        "apitcg: card search, no key",
        "https://apitcg.com/api/one-piece/cards",
        {"name": "Luffy"},
    ),
    (
        "apitcg (www variant)",
        "https://www.apitcg.com/api/one-piece/cards",
        {"name": "Luffy"},
    ),
]


def shape(value, depth: int = 0):
    """Describe a JSON value's STRUCTURE without printing all of it."""
    if isinstance(value, dict):
        if depth >= 3:
            return "{...}"
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        described = shape(value[0], depth + 1) if value else "empty"
        return [f"list of {len(value)} items, first looks like:", described]
    text = str(value)
    return text[:70] + ("..." if len(text) > 70 else "")


def main() -> None:
    for label, url, params in CANDIDATES:
        print("=" * 72)
        print(f"PROBE: {label}")
        print(f"  GET {url} {params or ''}")
        try:
            response = requests.get(
                url, params=params, headers=HEADERS, timeout=TIMEOUT
            )
        except requests.RequestException as e:
            print(f"  REQUEST FAILED: {e}")
            continue

        print(
            f"  HTTP {response.status_code}  "
            f"({response.headers.get('content-type', '?')}, "
            f"{len(response.content)} bytes)"
        )

        try:
            data = response.json()
        except ValueError:
            print("  NOT JSON. First 200 chars:")
            print("  " + response.text[:200].replace("\n", " "))
            continue

        print(json.dumps(shape(data), indent=2)[:2000])

    print("=" * 72)
    print("Done — copy EVERYTHING above and paste it back into the chat.")


if __name__ == "__main__":
    main()
