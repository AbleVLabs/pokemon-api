# probe2_onepiece.py
# ---------------------------------------------------------------------------
# One Piece probe, round 2 — now with the CORRECT set-id format ("OP-01")
# that round 1 taught us. Goal: see the card schema (field names, images,
# and whether prices exist), and find the starter-deck endpoints.
#
# HOW TO RUN (from the project root, venv active):
#     python probe2_onepiece.py
#
# Then paste the WHOLE output back into the chat. Throwaway file.
# ---------------------------------------------------------------------------

import json

import requests

TIMEOUT = 15
HEADERS = {"User-Agent": "EtherDex/0.1 (one-time data source validation)"}

CANDIDATES = [
    # The big one: a real set's cards, hyphenated id this time.
    ("cards in set OP-01", "https://optcgapi.com/api/sets/OP-01/", None),
    # Starter decks — likely where ST-xx sets live.
    ("all starter decks", "https://optcgapi.com/api/allDecks/", None),
    ("cards in deck ST-01", "https://optcgapi.com/api/decks/ST-01/", None),
    # Single-card lookup — two id-format guesses.
    ("one card OP01-001", "https://optcgapi.com/api/cards/OP01-001/", None),
    ("one card OP-01-001", "https://optcgapi.com/api/cards/OP-01-001/", None),
]


def shape(value, depth: int = 0):
    """Describe a JSON value's STRUCTURE without printing all of it."""
    if isinstance(value, dict):
        if depth >= 3:
            return "{...}"
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        described = shape(value[0], depth + 1) if value else "empty"
        return [f"list of {len(value)} items, first looks like:", described]
    text = str(value)
    return text[:80] + ("..." if len(text) > 80 else "")


def main() -> None:
    for label, url, params in CANDIDATES:
        print("=" * 72)
        print(f"PROBE: {label}")
        print(f"  GET {url}")
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
            print("  NOT JSON. First 150 chars:")
            print("  " + response.text[:150].replace("\n", " "))
            continue

        print(json.dumps(shape(data), indent=2)[:2400])

    print("=" * 72)
    print("Done — copy EVERYTHING above and paste it back into the chat.")


if __name__ == "__main__":
    main()
