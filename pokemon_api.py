# pokemon_api.py
# ---------------------------------------------------------------------------
# Pokémon Card Price Checker — backend.
#
# Includes:
#   - Card search (Pokémon TCG API + local DB cache, with freshness TTL)
#   - Autocomplete name suggestions
#   - Per-user WATCHLIST, stored in the database, protected by Clerk auth
#   - Per-card CONDITION (Near Mint, Lightly Played, etc.)
#   - Per-card QUANTITY owned (how many copies the user has)
#   - PRICE SNAPSHOTS — a dated price record every time cards are fetched,
#     so we can show price history over time.
#   - SET DATA — each set's card count, for set-completion tracking.
#   - PRICE ALERTS — an optional target price per watchlist card.
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
SETS_API = "https://api.pokemontcg.io/v2/sets"
PAGE_SIZE = 250
MAX_PAGES = 20
REQUEST_TIMEOUT = 20
FRESHNESS_DAYS = 7
SETS_FRESHNESS_DAYS = 7

API_KEY = os.environ.get("POKEMON_TCG_API_KEY", "")
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")

if not CLERK_SECRET_KEY:
    print("WARNING: CLERK_SECRET_KEY not set — watchlist endpoints will fail.")

ALLOWED_CONDITIONS = {
    "Mint",
    "Near Mint",
    "Lightly Played",
    "Moderately Played",
    "Heavily Played",
    "Damaged",
}

clerk_client = Clerk(bearer_auth=CLERK_SECRET_KEY)

app = FastAPI(title="Pokemon Card Price API", version="2.5")

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
    """Create the cards, watchlist, price_snapshots, and sets tables."""
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

    # Add the 'condition' column to watchlist if it isn't there yet.
    try:
        conn.execute(
            "ALTER TABLE watchlist ADD COLUMN condition TEXT NOT NULL "
            "DEFAULT 'Near Mint'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Add the 'quantity' column to watchlist if it isn't there yet.
    try:
        conn.execute(
            "ALTER TABLE watchlist ADD COLUMN quantity INTEGER NOT NULL " "DEFAULT 1"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Add the 'target_price' column to watchlist if it isn't there yet.
    # NULL means no price alert is set for that card.
    try:
        conn.execute("ALTER TABLE watchlist ADD COLUMN target_price REAL")
    except sqlite3.OperationalError:
        pass  # column already exists

    # price_snapshots — one row per (card, date). Records what a card's
    # market price was on a given day, so we can build price-history charts.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            card_id        TEXT NOT NULL,
            snapshot_date  TEXT NOT NULL,
            market_price   REAL NOT NULL,
            PRIMARY KEY (card_id, snapshot_date)
        )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshot_card " "ON price_snapshots(card_id)"
    )

    # sets — one row per Pokémon set, with its total card count.
    # Powers set-completion tracking on the dashboard.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sets (
            set_id         TEXT PRIMARY KEY,
            set_name       TEXT,
            total          INTEGER,
            printed_total  INTEGER,
            release_date   TEXT,
            last_synced    TEXT
        )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_set_name ON sets(set_name)")

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


def _today_str() -> str:
    """Today's date as YYYY-MM-DD (UTC) — used as the snapshot date."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


def record_snapshot(conn: sqlite3.Connection, card_id: str, price: float) -> None:
    """
    Save today's price for a card into price_snapshots.

    INSERT OR IGNORE means: if a snapshot for this card already exists for
    today, we keep the first one and skip — at most one snapshot per card
    per day. Cards with no real price (0) are skipped.
    """
    if not price or price <= 0:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO price_snapshots
        (card_id, snapshot_date, market_price)
        VALUES (?, ?, ?)
        """,
        (card_id, _today_str(), price),
    )


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
                # Record today's price snapshot for this card.
                record_snapshot(conn, row["card_id"], row["market_price"])
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
                   rarity, market_price, small_image, large_image,
                   last_updated
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
# SET DATA — card counts per set, for set-completion tracking
# ---------------------------------------------------------------------------


def sets_need_sync() -> bool:
    """True if the sets table is empty or hasn't been synced recently."""
    conn = sqlite3.connect(DATABASE)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(last_synced) FROM sets")
        newest = cursor.fetchone()[0]
    finally:
        conn.close()

    if not newest:
        return True
    try:
        newest_dt = datetime.fromisoformat(newest)
    except ValueError:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=SETS_FRESHNESS_DAYS)
    return newest_dt < cutoff


def sync_sets() -> None:
    """
    Fetch every Pokémon set's card count from the API and store it.
    Skips if set data was already synced recently. Network failure here
    is non-fatal — it just retries on the next startup.
    """
    if not sets_need_sync():
        print("Set data is fresh — skipping set sync.")
        return

    print("Syncing Pokémon set data from the API...")
    try:
        # ~165 sets exist; one page of 250 covers them all comfortably.
        response = requests.get(
            SETS_API,
            params={"pageSize": 250},
            headers=_api_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"Set sync failed (will retry next start): {e}")
        return
    except ValueError as e:
        print(f"Set sync returned invalid JSON: {e}")
        return

    timestamp = _now_iso()
    conn = sqlite3.connect(DATABASE)
    try:
        count = 0
        for s in data.get("data", []):
            set_id = s.get("id")
            if not set_id:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO sets
                (set_id, set_name, total, printed_total,
                 release_date, last_synced)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    set_id,
                    s.get("name"),
                    s.get("total"),
                    s.get("printedTotal"),
                    s.get("releaseDate"),
                    timestamp,
                ),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()

    print(f"Synced {count} Pokémon sets.")


# Sync set data once at startup (skips automatically if already fresh).
sync_sets()


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


class QuantityUpdateIn(BaseModel):
    """The frontend sends this when the user changes a card's quantity."""

    card_id: str
    quantity: int


class TargetUpdateIn(BaseModel):
    """The frontend sends this when the user sets or clears a price alert.
    A target_price of null (or 0 or less) clears the alert."""

    card_id: str
    target_price: float | None = None


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


@app.get("/price-history/{card_id}")
def price_history(card_id: str):
    """Return the recorded price snapshots for one card, oldest first."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT snapshot_date, market_price FROM price_snapshots "
            "WHERE card_id = ? ORDER BY snapshot_date ASC",
            (card_id,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    history = [
        {"date": row["snapshot_date"], "price": row["market_price"]} for row in rows
    ]
    return {"card_id": card_id, "history": history, "count": len(history)}


# ---------------------------------------------------------------------------
# ROUTES — watchlist (require a signed-in user)
# ---------------------------------------------------------------------------


@app.get("/watchlist")
def get_watchlist(authorization: str | None = Header(default=None)):
    """
    Return the signed-in user's watchlist.

    Each card's price is the CURRENT price from the cards table (kept
    fresh by searches) rather than the price frozen when the card was
    added. Condition, quantity, and the price-alert target come from
    the watchlist row.
    """
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT w.card_json, w.condition, w.quantity, w.target_price,
                   c.market_price AS current_price,
                   c.last_updated AS price_updated
            FROM watchlist w
            LEFT JOIN cards c ON w.card_id = c.card_id
            WHERE w.user_id = ?
            ORDER BY w.added_at
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    cards = []
    for row in rows:
        card = json.loads(row["card_json"])
        card["condition"] = row["condition"]
        card["quantity"] = row["quantity"]
        card["target_price"] = row["target_price"]

        # Use the current price from the cards table when we have one;
        # fall back to the price stored at add-time if we don't.
        current = row["current_price"]
        if current is not None and current > 0:
            card["market_price"] = current
        if row["price_updated"]:
            card["last_updated"] = row["price_updated"]

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


@app.post("/watchlist/quantity")
def update_quantity(
    payload: QuantityUpdateIn,
    authorization: str | None = Header(default=None),
):
    """Update the quantity of one card in the signed-in user's watchlist."""
    user_id = get_user_id(authorization)

    # Quantity can't go negative. Clamp anything below 0 up to 0.
    quantity = max(0, payload.quantity)

    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            "UPDATE watchlist SET quantity = ? " "WHERE user_id = ? AND card_id = ?",
            (quantity, user_id, payload.card_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "updated",
        "card_id": payload.card_id,
        "quantity": quantity,
    }


@app.post("/watchlist/target")
def update_target(
    payload: TargetUpdateIn,
    authorization: str | None = Header(default=None),
):
    """
    Set or clear the price-alert target for one watchlist card.

    A target_price of null, 0, or negative clears the alert (stores NULL).
    Otherwise the alert triggers when the card's price reaches that value.
    """
    user_id = get_user_id(authorization)

    # Anything that isn't a positive number means "no alert".
    target = payload.target_price
    if target is None or target <= 0:
        target = None

    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            "UPDATE watchlist SET target_price = ? "
            "WHERE user_id = ? AND card_id = ?",
            (target, user_id, payload.card_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "updated",
        "card_id": payload.card_id,
        "target_price": target,
    }


@app.get("/collection/history")
def collection_history(authorization: str | None = Header(default=None)):
    """
    Builds the data for the dashboard's value-over-time chart and the
    gainers/losers list, from the user's watchlist + recorded price
    snapshots.

    Both depend on price_snapshots, which only started recording
    recently — so this is sparse at first and fills in over time.
    """
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()

        # The user's watchlist: card_id, quantity, and stored card info.
        cursor.execute(
            "SELECT card_id, card_json, quantity FROM watchlist " "WHERE user_id = ?",
            (user_id,),
        )
        watch_rows = cursor.fetchall()

        quantities: dict[str, int] = {}
        card_info: dict[str, dict] = {}
        for row in watch_rows:
            quantities[row["card_id"]] = row["quantity"]
            card_info[row["card_id"]] = json.loads(row["card_json"])

        card_ids = list(quantities.keys())
        if not card_ids:
            return {"value_history": [], "movers": []}

        # Every price snapshot for those cards, oldest first.
        placeholders = ",".join("?" for _ in card_ids)
        cursor.execute(
            f"""
            SELECT snapshot_date, card_id, market_price
            FROM price_snapshots
            WHERE card_id IN ({placeholders})
            ORDER BY snapshot_date ASC
            """,
            card_ids,
        )
        snap_rows = cursor.fetchall()
    finally:
        conn.close()

    # Group snapshots by card. Each list is oldest-first (the query
    # was ORDER BY snapshot_date ASC).
    per_card: dict[str, list[tuple[str, float]]] = {}
    for row in snap_rows:
        per_card.setdefault(row["card_id"], []).append(
            (row["snapshot_date"], row["market_price"])
        )

    # --- VALUE OVER TIME ---
    # For each date, each card contributes its most recent price ON OR
    # BEFORE that date (carried forward). Without this, a day where only
    # some cards were snapshotted would look like the collection crashed.
    all_dates = sorted({row["snapshot_date"] for row in snap_rows})
    value_history = []
    for date in all_dates:
        total = 0.0
        for card_id in card_ids:
            last_price = None
            for snap_date, snap_price in per_card.get(card_id, []):
                if snap_date <= date:
                    last_price = snap_price
                else:
                    break
            if last_price is not None:
                total += last_price * quantities.get(card_id, 1)
        value_history.append({"date": date, "value": round(total, 2)})

    # --- MOVERS (gainers / losers) ---
    # A card needs at least two snapshots to have "moved" at all.
    movers = []
    for card_id, snaps in per_card.items():
        if len(snaps) < 2:
            continue
        old_price = snaps[0][1]
        new_price = snaps[-1][1]
        if old_price <= 0:
            continue
        change = new_price - old_price
        change_pct = (change / old_price) * 100
        info = card_info.get(card_id, {})
        movers.append(
            {
                "card_id": card_id,
                "pokemon_name": info.get("pokemon_name"),
                "small_image": info.get("small_image"),
                "old_price": round(old_price, 2),
                "new_price": round(new_price, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 1),
            }
        )

    # Biggest gainers first, biggest losers last.
    movers.sort(key=lambda m: m["change_pct"], reverse=True)

    return {"value_history": value_history, "movers": movers}


@app.get("/collection/sets")
def collection_sets(authorization: str | None = Header(default=None)):
    """
    Set-completion data: for each set the user owns cards from, how many
    distinct cards they have vs. how many the set contains.
    """
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()

        # The user's watchlist cards (each row = one distinct card).
        cursor.execute(
            "SELECT card_json FROM watchlist WHERE user_id = ?",
            (user_id,),
        )
        watch_rows = cursor.fetchall()

        # Count how many distinct cards the user owns per set name.
        owned_by_set: dict[str, int] = {}
        for row in watch_rows:
            info = json.loads(row["card_json"])
            set_name = info.get("set_name")
            if set_name:
                owned_by_set[set_name] = owned_by_set.get(set_name, 0) + 1

        if not owned_by_set:
            return {"sets": []}

        # Look up the total card count for each of those sets.
        set_names = list(owned_by_set.keys())
        placeholders = ",".join("?" for _ in set_names)
        cursor.execute(
            f"""
            SELECT set_name, printed_total, total
            FROM sets
            WHERE set_name IN ({placeholders})
            """,
            set_names,
        )
        set_rows = cursor.fetchall()
    finally:
        conn.close()

    # printed_total is the official set size (e.g. "102"); fall back to
    # total (which includes secret rares) if it's missing.
    totals = {
        row["set_name"]: (row["printed_total"] or row["total"] or 0) for row in set_rows
    }

    results = []
    for set_name, owned in owned_by_set.items():
        total = totals.get(set_name, 0)
        # Cap owned at the set size so the display never shows over 100%.
        owned_display = min(owned, total) if total else owned
        percent = round((owned_display / total) * 100) if total else 0
        results.append(
            {
                "set_name": set_name,
                "owned": owned_display,
                "total": total,
                "percent": percent,
            }
        )

    # Most-complete sets first.
    results.sort(key=lambda s: s["percent"], reverse=True)
    return {"sets": results}


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
