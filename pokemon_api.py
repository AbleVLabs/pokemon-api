# pokemon_api.py
# ---------------------------------------------------------------------------
# Pokémon Card Price Checker — backend (single-file, simple architecture).
#
# How it works:
#   1. A search checks the local SQLite database.
#   2. FRESHNESS CHECK: if matching cards exist AND were fetched within the
#      last 7 days, serve them from the DB (fast).
#   3. Otherwise (no data, or stale data) re-fetch from the Pokémon TCG API
#      using a wildcard query, save with a fresh timestamp, and serve.
#
#   This means the DB self-heals — stale data or changed fetch logic
#   refreshes automatically. No manual database deletes needed.
# ---------------------------------------------------------------------------

import os
import csv
import sqlite3
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DATABASE = "pokemon_cards.db"
POKEMON_TCG_API = "https://api.pokemontcg.io/v2/cards"
PAGE_SIZE = 250
MAX_PAGES = 20
REQUEST_TIMEOUT = 20

# How long cached card data stays "fresh" before we re-fetch it.
# 7 days is a reasonable balance: prices don't change wildly day-to-day,
# and repeat searches within a week stay instant.
FRESHNESS_DAYS = 7

API_KEY = os.environ.get("POKEMON_TCG_API_KEY", "")


app = FastAPI(title="Pokemon Card Price API", version="1.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# POKÉMON NAME LIST (for autocomplete)
# ---------------------------------------------------------------------------


def load_pokemon_names() -> list[str]:
    """Read unique Pokémon names from Pokemon.csv (for the autocomplete)."""
    names: list[str] = []
    seen = set()
    try:
        with open("Pokemon.csv", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("Name") or "").strip()
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    names.append(name)
    except FileNotFoundError:
        print("WARNING: Pokemon.csv not found — autocomplete will be empty.")
    print(f"Loaded {len(names)} Pokémon names for autocomplete.")
    return sorted(names)


POKEMON_NAMES = load_pokemon_names()


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------


def init_db() -> None:
    """
    Create the cards table if it doesn't exist.

    The `last_updated` column stores an ISO timestamp of when each card
    was last fetched from the API. This powers the freshness (TTL) check.
    """
    conn = sqlite3.connect(DATABASE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            card_id       TEXT PRIMARY KEY,
            pokemon_name  TEXT,
            set_name      TEXT,
            card_number   TEXT,
            rarity        TEXT,
            market_price  REAL,
            small_image   TEXT,
            large_image   TEXT,
            last_updated  TEXT
        )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_name ON cards(pokemon_name)")
    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC time as an ISO string — used for the last_updated stamp."""
    return datetime.now(timezone.utc).isoformat()


def _api_headers() -> dict:
    """Return request headers, including the API key if one is set."""
    return {"X-Api-Key": API_KEY} if API_KEY else {}


def extract_market_price(card: dict) -> float:
    """Pull a market price out of the TCG API's nested price data."""
    tcgplayer = card.get("tcgplayer") or {}
    prices = tcgplayer.get("prices") or {}
    for price_data in prices.values():
        if isinstance(price_data, dict):
            market = price_data.get("market")
            if isinstance(market, (int, float)) and market > 0:
                return float(market)
    return 0.0


def _row_from_api_card(card: dict) -> dict | None:
    """Convert one raw API card into our database row shape. None if invalid."""
    card_id = card.get("id")
    if not card_id:
        return None
    images = card.get("images") or {}
    card_set = card.get("set") or {}
    return {
        "card_id": card_id,
        "pokemon_name": card.get("name"),
        "set_name": card_set.get("name"),
        "card_number": card.get("number"),
        "rarity": card.get("rarity"),
        "market_price": extract_market_price(card),
        "small_image": images.get("small"),
        "large_image": images.get("large"),
    }


def is_data_fresh(name: str) -> bool:
    """
    Return True if we have matching cards in the DB that were fetched
    within the last FRESHNESS_DAYS days.

    We check the OLDEST matching card's timestamp — if even the oldest
    is still fresh, the whole set is fresh. If there are no matching
    cards at all, it's not fresh (we need to fetch).
    """
    conn = sqlite3.connect(DATABASE)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MIN(last_updated) FROM cards WHERE pokemon_name LIKE ?",
            (f"%{name}%",),
        )
        oldest = cursor.fetchone()[0]
    finally:
        conn.close()

    # No matching cards, or a row with no timestamp → treat as stale.
    if not oldest:
        return False

    try:
        oldest_dt = datetime.fromisoformat(oldest)
    except ValueError:
        return False  # unparseable timestamp → re-fetch to be safe

    cutoff = datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)
    return oldest_dt >= cutoff


def fetch_from_api(name: str) -> int:
    """
    Fetch ALL matching cards from the Pokémon TCG API and save them.

    Wildcard query name:"*term*" catches every variant (Mewtwo, Mewtwo ex,
    Mewtwo VSTAR, etc.). Each saved card gets a fresh `last_updated` stamp.

    Returns the number of cards fetched.
    """
    timestamp = _now_iso()
    query_string = f'name:"*{name}*"'
    total = 0

    conn = sqlite3.connect(DATABASE)
    try:
        for page in range(1, MAX_PAGES + 1):
            try:
                response = requests.get(
                    POKEMON_TCG_API,
                    params={
                        "q": query_string,
                        "pageSize": PAGE_SIZE,
                        "page": page,
                    },
                    headers=_api_headers(),
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                print(f"Pokemon TCG API request failed on page {page}: {e}")
                break
            except ValueError as e:
                print(f"Pokemon TCG API returned invalid JSON on page {page}: {e}")
                break

            page_cards = data.get("data") or []
            if not page_cards:
                break

            print(f"  fetched page {page}: {len(page_cards)} cards")

            for card in page_cards:
                row = _row_from_api_card(card)
                if row is None:
                    continue

                conn.execute(
                    """
                    INSERT OR REPLACE INTO cards
                    (card_id, pokemon_name, set_name, card_number,
                     rarity, market_price, small_image, large_image,
                     last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["card_id"],
                        row["pokemon_name"],
                        row["set_name"],
                        row["card_number"],
                        row["rarity"],
                        row["market_price"],
                        row["small_image"],
                        row["large_image"],
                        timestamp,
                    ),
                )
                total += 1

            if len(page_cards) < PAGE_SIZE:
                break

        conn.commit()
    finally:
        conn.close()

    print(f"Total cards fetched for '{name}': {total}")
    return total


def query_local_db(
    name: str,
    rarity: str,
    sort: str,
    min_price: float,
    max_price: float,
) -> list[dict]:
    """Search the local SQLite database with filters and sorting applied."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT card_id, pokemon_name, set_name, card_number,
                   rarity, market_price, small_image, large_image
            FROM cards
            WHERE pokemon_name LIKE ?
            AND market_price >= ?
            AND market_price <= ?
        """
        params: list = [f"%{name}%", min_price, max_price]

        if rarity:
            query += " AND rarity LIKE ? "
            params.append(f"%{rarity}%")

        if sort == "price_desc":
            query += " ORDER BY market_price DESC "
        elif sort == "price_asc":
            query += " ORDER BY market_price ASC "
        elif sort == "name_asc":
            query += " ORDER BY pokemon_name ASC "
        elif sort == "name_desc":
            query += " ORDER BY pokemon_name DESC "

        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return {"message": "Pokemon API is running!"}


@app.get("/health")
def health():
    """Simple health check."""
    return {
        "status": "ok",
        "api_key_set": bool(API_KEY),
        "freshness_days": FRESHNESS_DAYS,
    }


@app.get("/pokemon-names")
def pokemon_names(q: str = ""):
    """Return Pokémon names for the autocomplete dropdown (max 8)."""
    query = q.strip().lower()
    if not query:
        return {"names": []}
    matches = [n for n in POKEMON_NAMES if n.lower().startswith(query)]
    return {"names": matches[:8]}


@app.get("/search")
def search_cards(
    name: str = "",
    rarity: str = "",
    sort: str = "",
    min_price: float = 0,
    max_price: float = 999999,
):
    """
    Search for Pokémon cards.

    Freshness logic:
      - If we have matching cards fetched within the last FRESHNESS_DAYS,
        serve them straight from the DB (fast).
      - Otherwise, re-fetch from the API, save with a fresh timestamp,
        then serve. This makes the DB self-healing — no manual deletes.
    """
    name = name.strip()
    if not name:
        return {"cards": []}

    if is_data_fresh(name):
        print(f"'{name}' — serving fresh data from DB.")
    else:
        print(f"'{name}' — data missing or stale, fetching from API...")
        fetch_from_api(name)

    results = query_local_db(name, rarity, sort, min_price, max_price)
    return {"cards": results, "count": len(results)}
