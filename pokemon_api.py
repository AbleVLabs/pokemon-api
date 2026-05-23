# pokemon_api.py
# ---------------------------------------------------------------------------
# Pokémon Card Price Checker — backend.
#
# Includes:
#   - Card search (Pokémon TCG API + local DB cache, with freshness TTL)
#   - Autocomplete name suggestions
#   - Per-user WATCHLIST, stored in the database, protected by Clerk auth
#   - Per-card CONDITION (Near Mint, Lightly Played, etc.)
# ---------------------------------------------------------------------------

import os
import csv
import json
import sqlite3
import requests
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
import httpx

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DATABASE = "pokemon_cards.db"
POKEMON_TCG_API = "https://api.pokemontcg.io/v2/cards"
PAGE_SIZE = 250
MAX_PAGES = 20
REQUEST_TIMEOUT = 20
FRESHNESS_DAYS = 7

API_KEY = os.environ.get("POKEMON_TCG_API_KEY", "")
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")

if not CLERK_SECRET_KEY:
    print("WARNING: CLERK_SECRET_KEY not set — watchlist endpoints will fail.")

# The valid card conditions. The backend rejects anything not in this set.
ALLOWED_CONDITIONS = {
    "Mint",
    "Near Mint",
    "Lightly Played",
    "Moderately Played",
    "Heavily Played",
    "Damaged",
}

clerk_client = Clerk(bearer_auth=CLERK_SECRET_KEY)

app = FastAPI(title="Pokemon Card Price API", version="2.1")

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
    """Create the cards table and the watchlist table if they don't exist."""
    conn = sqlite3.connect(DATABASE)

    # Cards cache table.
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

    # Watchlist table — one row per (user, card).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id    TEXT NOT NULL,
            card_id    TEXT NOT NULL,
            card_json  TEXT NOT NULL,
            added_at   TEXT NOT NULL,
            PRIMARY KEY (user_id, card_id)
        )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")

    # Add the 'condition' column if it isn't there yet. This is a safe
    # migration — existing watchlist rows are kept, they just get the
    # default 'Near Mint'. If the column already exists, SQLite raises an
    # error, which we simply ignore.
    try:
        conn.execute(
            "ALTER TABLE watchlist ADD COLUMN condition TEXT NOT NULL "
            "DEFAULT 'Near Mint'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists — nothing to do

    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------------
# CLERK AUTH HELPER
# ---------------------------------------------------------------------------


def get_user_id(authorization: str | None) -> str:
    """
    Verify the Clerk token from the Authorization header and return the
    user's Clerk ID. Raises 401 if the token is missing or invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not signed in.")

    token = authorization.split(" ", 1)[1]

    try:
        fake_request = httpx.Request(
            method="GET",
            url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        )
        state = clerk_client.authenticate_request(
            fake_request,
            AuthenticateRequestOptions(),
        )
    except Exception as e:
        print(f"Clerk token verification error: {e}")
        raise HTTPException(status_code=401, detail="Invalid session.")

    if not state.is_signed_in:
        raise HTTPException(status_code=401, detail="Invalid session.")

    user_id = (state.payload or {}).get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Could not identify user.")

    return user_id


# ---------------------------------------------------------------------------
# CARD SEARCH HELPERS
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_headers() -> dict:
    return {"X-Api-Key": API_KEY} if API_KEY else {}


def extract_market_price(card: dict) -> float:
    tcgplayer = card.get("tcgplayer") or {}
    prices = tcgplayer.get("prices") or {}
    for price_data in prices.values():
        if isinstance(price_data, dict):
            market = price_data.get("market")
            if isinstance(market, (int, float)) and market > 0:
                return float(market)
    return 0.0


def _row_from_api_card(card: dict) -> dict | None:
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

    if not oldest:
        return False
    try:
        oldest_dt = datetime.fromisoformat(oldest)
    except ValueError:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)
    return oldest_dt >= cutoff


def fetch_from_api(name: str) -> int:
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
# REQUEST MODELS
# ---------------------------------------------------------------------------


class WatchCardIn(BaseModel):
    """The card data the frontend sends when adding to the watchlist."""

    card_id: str
    pokemon_name: str
    set_name: str | None = ""
    rarity: str | None = ""
    market_price: float | None = 0
    small_image: str | None = ""
    large_image: str | None = ""


class ConditionUpdateIn(BaseModel):
    """The frontend sends this when the user changes a card's condition."""

    card_id: str
    condition: str


# ---------------------------------------------------------------------------
# ROUTES — search
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return {"message": "Pokemon API is running!"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "api_key_set": bool(API_KEY),
        "clerk_key_set": bool(CLERK_SECRET_KEY),
        "freshness_days": FRESHNESS_DAYS,
    }


@app.get("/pokemon-names")
def pokemon_names(q: str = ""):
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


# ---------------------------------------------------------------------------
# ROUTES — watchlist (require a signed-in user)
# ---------------------------------------------------------------------------


@app.get("/watchlist")
def get_watchlist(authorization: str | None = Header(default=None)):
    """Return the signed-in user's watchlist, each card with its condition."""
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT card_json, condition FROM watchlist "
            "WHERE user_id = ? ORDER BY added_at",
            (user_id,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    cards = []
    for row in rows:
        card = json.loads(row["card_json"])
        card["condition"] = row["condition"]
        cards.append(card)

    return {"cards": cards, "count": len(cards)}


@app.post("/watchlist/add")
def add_to_watchlist(
    card: WatchCardIn,
    authorization: str | None = Header(default=None),
):
    """Add a card to the signed-in user's watchlist."""
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO watchlist
            (user_id, card_id, card_json, added_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, card.card_id, card.model_dump_json(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "added", "card_id": card.card_id}


@app.post("/watchlist/condition")
def update_condition(
    payload: ConditionUpdateIn,
    authorization: str | None = Header(default=None),
):
    """Update the condition of one card in the signed-in user's watchlist."""
    user_id = get_user_id(authorization)

    if payload.condition not in ALLOWED_CONDITIONS:
        raise HTTPException(status_code=400, detail="Invalid condition.")

    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            "UPDATE watchlist SET condition = ? " "WHERE user_id = ? AND card_id = ?",
            (payload.condition, user_id, payload.card_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "updated",
        "card_id": payload.card_id,
        "condition": payload.condition,
    }


@app.delete("/watchlist/remove/{card_id}")
def remove_from_watchlist(
    card_id: str,
    authorization: str | None = Header(default=None),
):
    """Remove a card from the signed-in user's watchlist."""
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND card_id = ?",
            (user_id, card_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "removed", "card_id": card_id}
