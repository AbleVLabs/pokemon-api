# pokemon_api.py
# ---------------------------------------------------------------------------
# EtherDex — backend. (v3.3 — Pokémon + MTG + Yu-Gi-Oh! + One Piece)
#
# ARCHITECTURE NOTE (the Phase 1 refactor):
# EtherDex now supports multiple card games through GAME ADAPTERS. Each
# game (Pokémon, MTG, Yu-Gi-Oh, One Piece...) is a pluggable data source
# that knows how to fetch and normalize ITS cards and sets. Everything
# downstream — the database, search, watchlist, dashboard — is
# game-agnostic and just works with normalized rows tagged by game.
#
# Isolation rule: one game's data source breaking must NEVER take down
# the others. Network failures are caught per-adapter, and cached data
# in SQLite keeps serving even when a source is down.
#
# Includes:
#   - GAME ADAPTERS — all four games live: Pokémon, MTG, Yu-Gi-Oh!, One Piece
#   - Card search (per-game API + local DB cache, with freshness TTL)
#   - Autocomplete name suggestions (Pokémon names; per-game later)
#   - Per-user WATCHLIST, stored in the database, protected by Clerk auth
#   - Per-card CONDITION (Near Mint, Lightly Played, etc.)
#   - Per-card QUANTITY owned (how many copies the user has)
#   - PRICE SNAPSHOTS — a dated price record every time cards are fetched
#   - SET DATA — each set's card count, for set-completion tracking
#   - PRICE ALERTS — an optional target price per watchlist card
# ---------------------------------------------------------------------------

import os
import csv
import json
import time
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

app = FastAPI(title="EtherDex API", version="3.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# SMALL SHARED HELPERS
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    """Today's date as YYYY-MM-DD (UTC) — used as the snapshot date."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# GAME ADAPTERS
#
# Every game EtherDex supports is one adapter class. An adapter's job is
# small and strict: talk to that game's API and return NORMALIZED rows.
#
# A normalized CARD row is a dict with exactly these keys:
#   card_id, card_name, set_name, card_number, rarity,
#   market_price, small_image, large_image
#
# A normalized SET row is a dict with exactly these keys:
#   set_id, set_name, total, printed_total, release_date
#
# Nothing outside this section knows or cares which game's API is on the
# other end. To add a new game later: write an adapter, register it in
# GAMES. That's the whole job.
# ---------------------------------------------------------------------------


class GameAdapter:
    """Base class for game data sources."""

    game: str = ""  # short key stored in the database, e.g. "pokemon"
    display_name: str = ""  # human-friendly name, e.g. "Pokémon"

    def search_cards(self, name: str) -> list[dict]:
        """Fetch cards matching a name. Returns normalized card rows.
        Network errors should be handled INSIDE the adapter — return
        whatever was fetched successfully (possibly an empty list)."""
        raise NotImplementedError

    def fetch_sets(self) -> list[dict]:
        """Fetch every set for this game. Returns normalized set rows.
        MAY raise requests.RequestException — the sync layer catches it
        per-adapter, so one game's failure never affects the others."""
        raise NotImplementedError

    def autocomplete(self, q: str) -> list[str]:
        """Name suggestions for the search box. Optional — games
        without a suggestion source just return no suggestions."""
        return []

    # "on_demand": cards are fetched per search (Pokémon, MTG, Yu-Gi-Oh).
    # "full_sync": the WHOLE catalog is synced upfront and searches run
    # entirely from the local database (One Piece).
    sync_strategy: str = "on_demand"

    def fetch_all_cards(self) -> list[dict]:
        """Full-catalog fetch, only for sync_strategy='full_sync' games."""
        raise NotImplementedError


class PokemonAdapter(GameAdapter):
    """Pokémon TCG, via the official pokemontcg.io API."""

    game = "pokemon"
    display_name = "Pokémon"

    CARDS_API = "https://api.pokemontcg.io/v2/cards"
    SETS_API = "https://api.pokemontcg.io/v2/sets"

    def _headers(self) -> dict:
        return {"X-Api-Key": API_KEY} if API_KEY else {}

    @staticmethod
    def _extract_market_price(card: dict) -> float:
        tcgplayer = card.get("tcgplayer") or {}
        prices = tcgplayer.get("prices") or {}
        for price_data in prices.values():
            if isinstance(price_data, dict):
                market = price_data.get("market")
                if isinstance(market, (int, float)) and market > 0:
                    return float(market)
        return 0.0

    def _normalize_card(self, card: dict) -> dict | None:
        card_id = card.get("id")
        if not card_id:
            return None
        images = card.get("images") or {}
        card_set = card.get("set") or {}
        return {
            "card_id": card_id,
            "card_name": card.get("name"),
            "set_name": card_set.get("name"),
            "card_number": card.get("number"),
            "rarity": card.get("rarity"),
            "market_price": self._extract_market_price(card),
            "small_image": images.get("small"),
            "large_image": images.get("large"),
        }

    def search_cards(self, name: str) -> list[dict]:
        query_string = f'name:"*{name}*"'
        rows: list[dict] = []

        for page in range(1, MAX_PAGES + 1):
            try:
                response = requests.get(
                    self.CARDS_API,
                    params={
                        "q": query_string,
                        "pageSize": PAGE_SIZE,
                        "page": page,
                    },
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                print(f"[{self.game}] API request failed on page {page}: {e}")
                break
            except ValueError as e:
                print(f"[{self.game}] API returned invalid JSON on page {page}: {e}")
                break

            page_cards = data.get("data") or []
            if not page_cards:
                break

            print(f"  [{self.game}] fetched page {page}: {len(page_cards)} cards")

            for card in page_cards:
                row = self._normalize_card(card)
                if row is not None:
                    rows.append(row)

            if len(page_cards) < PAGE_SIZE:
                break

        return rows

    def fetch_sets(self) -> list[dict]:
        # ~165 sets exist; one page of 250 covers them all comfortably.
        response = requests.get(
            self.SETS_API,
            params={"pageSize": 250},
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        rows: list[dict] = []
        for s in data.get("data", []):
            set_id = s.get("id")
            if not set_id:
                continue
            rows.append(
                {
                    "set_id": set_id,
                    "set_name": s.get("name"),
                    "total": s.get("total"),
                    "printed_total": s.get("printedTotal"),
                    "release_date": s.get("releaseDate"),
                }
            )
        return rows

    def autocomplete(self, q: str) -> list[str]:
        query = q.strip().lower()
        if not query:
            return []
        return [n for n in POKEMON_NAMES if n.lower().startswith(query)][:8]


class MTGAdapter(GameAdapter):
    """Magic: The Gathering, via the Scryfall API (free, no key).

    Scryfall is the gold-standard MTG API. Their etiquette rules, which
    we follow: identify yourself with a User-Agent, and keep 50-100ms
    between requests.
    """

    game = "mtg"
    display_name = "Magic: The Gathering"

    CARDS_API = "https://api.scryfall.com/cards/search"
    SETS_API = "https://api.scryfall.com/sets"
    AUTOCOMPLETE_API = "https://api.scryfall.com/cards/autocomplete"

    HEADERS = {
        "User-Agent": "EtherDex/3.3 (TCG collection tracker)",
        "Accept": "application/json",
    }

    @staticmethod
    def _extract_price(card: dict) -> float:
        """Scryfall prices are strings (or null): usd, usd_foil, etc.
        Prefer the regular price, fall back to foil/etched."""
        prices = card.get("prices") or {}
        for key in ("usd", "usd_foil", "usd_etched"):
            value = prices.get(key)
            if value:
                try:
                    price = float(value)
                except ValueError:
                    continue
                if price > 0:
                    return price
        return 0.0

    @staticmethod
    def _extract_images(card: dict) -> tuple[str | None, str | None]:
        """Double-faced cards (transform/modal) keep their images on
        card_faces instead of the top level — use the front face."""
        uris = card.get("image_uris")
        if not uris:
            faces = card.get("card_faces") or []
            if faces and faces[0].get("image_uris"):
                uris = faces[0]["image_uris"]
        if not uris:
            return None, None
        return uris.get("small"), uris.get("normal") or uris.get("large")

    def _normalize_card(self, card: dict) -> dict | None:
        card_id = card.get("id")
        if not card_id:
            return None
        small_image, large_image = self._extract_images(card)
        return {
            "card_id": card_id,
            "card_name": card.get("name"),
            "set_name": card.get("set_name"),
            "card_number": card.get("collector_number"),
            # Scryfall rarities are lowercase ("mythic") — title-case
            # them so they display like the Pokémon ones.
            "rarity": (card.get("rarity") or "").title(),
            "market_price": self._extract_price(card),
            "small_image": small_image,
            "large_image": large_image,
        }

    def search_cards(self, name: str) -> list[dict]:
        rows: list[dict] = []

        # unique=prints — every PRINTING, not one card per name. Each
        # printing is a separately collectible card with its own price,
        # exactly like Pokémon cards across different sets.
        params: dict | None = {"q": name, "unique": "prints", "order": "name"}
        url = self.CARDS_API

        for page in range(1, MAX_PAGES + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self.HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )
                # Scryfall answers "no cards matched" with a 404 —
                # that's an empty result, not an error.
                if response.status_code == 404:
                    break
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                print(f"[{self.game}] API request failed on page {page}: {e}")
                break
            except ValueError as e:
                print(f"[{self.game}] API returned invalid JSON on page {page}: {e}")
                break

            page_cards = data.get("data") or []
            print(f"  [{self.game}] fetched page {page}: {len(page_cards)} cards")

            for card in page_cards:
                row = self._normalize_card(card)
                if row is not None:
                    rows.append(row)

            if not data.get("has_more"):
                break
            # Scryfall hands us the full next-page URL, query included.
            url = data.get("next_page")
            params = None
            if not url:
                break
            time.sleep(0.1)  # polite rate limiting, per Scryfall's docs

        return rows

    def fetch_sets(self) -> list[dict]:
        rows: list[dict] = []
        url: str | None = self.SETS_API
        params: dict | None = None

        while url:
            response = requests.get(
                url,
                params=params,
                headers=self.HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            for s in data.get("data", []):
                code = s.get("code")
                if not code:
                    continue
                rows.append(
                    {
                        "set_id": code,
                        "set_name": s.get("name"),
                        "total": s.get("card_count"),
                        # printed_size is the official set size when known;
                        # card_count (everything Scryfall has) is the fallback.
                        "printed_total": s.get("printed_size") or s.get("card_count"),
                        "release_date": s.get("released_at"),
                    }
                )

            url = data.get("next_page") if data.get("has_more") else None
            params = None
            if url:
                time.sleep(0.1)

        return rows

    def autocomplete(self, q: str) -> list[str]:
        query = q.strip()
        if len(query) < 2:
            return []  # Scryfall wants at least 2 characters
        try:
            response = requests.get(
                self.AUTOCOMPLETE_API,
                params={"q": query},
                headers=self.HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return (response.json().get("data") or [])[:8]
        except (requests.RequestException, ValueError):
            return []


class YGOAdapter(GameAdapter):
    """Yu-Gi-Oh!, via the YGOPRODeck API (free, no key).

    YGOPRODeck returns one entry per card NAME with every printing in a
    card_sets list — so this adapter expands each card into one row per
    printing, matching how Pokémon and MTG cards are stored. A whole
    search comes back in a single response (no pagination).
    """

    game = "ygo"
    display_name = "Yu-Gi-Oh!"

    CARDS_API = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
    SETS_API = "https://db.ygoprodeck.com/api/v7/cardsets.php"

    HEADERS = {"User-Agent": "EtherDex/3.3 (TCG collection tracker)"}

    @staticmethod
    def _base_price(card: dict) -> float:
        """Card-level price, used when a printing has no price of its own."""
        prices = (card.get("card_prices") or [{}])[0]
        for key in ("tcgplayer_price", "cardmarket_price", "ebay_price"):
            value = prices.get(key)
            if value:
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    return price
        return 0.0

    def _expand_card(self, card: dict) -> list[dict]:
        """One YGOPRODeck card entry -> one normalized row PER PRINTING."""
        cid = card.get("id")
        name = card.get("name")
        if not cid or not name:
            return []

        images = (card.get("card_images") or [{}])[0]
        small_image = images.get("image_url_small")
        large_image = images.get("image_url")
        fallback_price = self._base_price(card)

        printings = card.get("card_sets") or []
        if not printings:
            # No set data yet (e.g. anime-only or unreleased) — one bare row.
            return [
                {
                    "card_id": str(cid),
                    "card_name": name,
                    "set_name": None,
                    "card_number": None,
                    "rarity": None,
                    "market_price": fallback_price,
                    "small_image": small_image,
                    "large_image": large_image,
                }
            ]

        rows: list[dict] = []
        seen: set[str] = set()
        for s in printings:
            set_code = s.get("set_code") or ""
            # The same set_code can appear in several rarities, each its
            # own collectible — fold the rarity code into the card id.
            rarity_code = (s.get("set_rarity_code") or "").strip("()")
            card_id = f"{cid}-{set_code}" + (f"-{rarity_code}" if rarity_code else "")
            if card_id in seen:
                continue
            seen.add(card_id)

            try:
                price = float(s.get("set_price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                price = fallback_price

            rows.append(
                {
                    "card_id": card_id,
                    "card_name": name,
                    "set_name": s.get("set_name"),
                    "card_number": set_code,
                    "rarity": s.get("set_rarity"),
                    "market_price": price,
                    "small_image": small_image,
                    "large_image": large_image,
                }
            )
        return rows

    def search_cards(self, name: str) -> list[dict]:
        rows: list[dict] = []
        try:
            response = requests.get(
                self.CARDS_API,
                params={"fname": name},
                headers=self.HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            # YGOPRODeck answers "no matches" with a 400 — that is an
            # empty result, not an error.
            if response.status_code == 400:
                return []
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"[{self.game}] API request failed: {e}")
            return []
        except ValueError as e:
            print(f"[{self.game}] API returned invalid JSON: {e}")
            return []

        for card in data.get("data") or []:
            rows.extend(self._expand_card(card))

        print(f"  [{self.game}] fetched {len(rows)} printings")
        return rows

    def fetch_sets(self) -> list[dict]:
        response = requests.get(
            self.SETS_API,
            headers=self.HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()  # a plain list of sets

        rows: list[dict] = []
        for s in data or []:
            code = s.get("set_code")
            if not code:
                continue
            rows.append(
                {
                    "set_id": code,
                    "set_name": s.get("set_name"),
                    "total": s.get("num_of_cards"),
                    "printed_total": s.get("num_of_cards"),
                    "release_date": s.get("tcg_date"),
                }
            )
        return rows

    def autocomplete(self, q: str) -> list[str]:
        query = q.strip()
        if len(query) < 2:
            return []
        try:
            response = requests.get(
                self.CARDS_API,
                params={"fname": query, "num": 8, "offset": 0},
                headers=self.HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 400:
                return []
            response.raise_for_status()
            names = [c.get("name") for c in response.json().get("data") or []]
            return [n for n in names if n][:8]
        except (requests.RequestException, ValueError):
            return []


class OnePieceAdapter(GameAdapter):
    """One Piece Card Game, via the community OPTCGAPI (free, no key).

    There is no official Bandai API, and OPTCGAPI is set-based (no name
    search) — so this adapter uses FULL-SYNC: the whole catalog (~20
    main sets + ~28 starter decks, a few thousand cards) is synced into
    the local database upfront, and every search runs locally. After the
    first sync, One Piece works entirely from cache — if the source has
    a bad day, EtherDex doesn't even notice.

    Prices: OPTCGAPI scrapes TCGplayer market prices daily, so One Piece
    ships WITH price data — and because the weekly sync covers the whole
    catalog, it builds full price-snapshot history automatically.
    (Schema validated against the live API on 2026-06-11.)
    """

    game = "onepiece"
    display_name = "One Piece"
    sync_strategy = "full_sync"

    SETS_API = "https://optcgapi.com/api/allSets/"
    DECKS_API = "https://optcgapi.com/api/allDecks/"
    SET_CARDS_API = "https://optcgapi.com/api/sets/{sid}/"
    DECK_CARDS_API = "https://optcgapi.com/api/decks/{sid}/"

    HEADERS = {"User-Agent": "EtherDex/3.3 (TCG collection tracker)"}

    RARITY_NAMES = {
        "C": "Common",
        "UC": "Uncommon",
        "R": "Rare",
        "SR": "Super Rare",
        "SEC": "Secret Rare",
        "L": "Leader",
        "SP": "Special",
        "P": "Promo",
        "TR": "Treasure Rare",
    }

    def _fetch_json(self, url: str):
        response = requests.get(url, headers=self.HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def fetch_sets(self) -> list[dict]:
        """Main sets + starter decks, normalized together. Card totals
        are unknown here (the API doesn't list them) — the full catalog
        sync fills them in by counting actual cards per set."""
        rows: list[dict] = []
        for s in self._fetch_json(self.SETS_API) or []:
            set_id = s.get("set_id")
            if set_id:
                rows.append(
                    {
                        "set_id": set_id,
                        "set_name": s.get("set_name"),
                        "total": None,
                        "printed_total": None,
                        "release_date": None,
                    }
                )
        time.sleep(0.1)
        for d in self._fetch_json(self.DECKS_API) or []:
            deck_id = d.get("structure_deck_id")
            if deck_id:
                rows.append(
                    {
                        "set_id": deck_id,
                        "set_name": d.get("structure_deck_name"),
                        "total": None,
                        "printed_total": None,
                        "release_date": None,
                    }
                )
        return rows

    def _normalize_card(self, c: dict) -> dict | None:
        code = c.get("card_set_id")
        name = c.get("card_name")
        if not code or not name:
            return None

        # market_price first, inventory_price as the fallback.
        price = 0.0
        for key in ("market_price", "inventory_price"):
            try:
                value = float(c.get(key) or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                price = value
                break

        image = c.get("card_image")
        rarity = (c.get("rarity") or "").strip()
        return {
            # card_image_id distinguishes alternate arts when it differs
            # from the plain card code.
            "card_id": c.get("card_image_id") or code,
            "card_name": name,
            "set_name": c.get("set_name"),
            "card_number": code,
            "rarity": self.RARITY_NAMES.get(rarity, rarity),
            "market_price": price,
            "small_image": image,
            "large_image": image,
            # Internal: which set this row belongs to, for set totals.
            "_set_id": c.get("set_id"),
        }

    def fetch_all_cards(self) -> list[dict]:
        # Which sets and decks exist right now, from the live lists.
        try:
            set_ids = [s.get("set_id") for s in self._fetch_json(self.SETS_API) or []]
            time.sleep(0.1)
            deck_ids = [
                d.get("structure_deck_id")
                for d in self._fetch_json(self.DECKS_API) or []
            ]
        except (requests.RequestException, ValueError) as e:
            print(f"[{self.game}] could not list sets/decks: {e}")
            return []

        targets = [(self.SET_CARDS_API, s) for s in set_ids if s]
        targets += [(self.DECK_CARDS_API, d) for d in deck_ids if d]

        rows: list[dict] = []
        seen_ids: dict[str, int] = {}
        consecutive_failures = 0

        for template, sid in targets:
            # If several requests in a row fail, the source is down —
            # stop hammering it and serve whatever we already have.
            if consecutive_failures >= 3:
                print(f"[{self.game}] aborting sync — source looks down.")
                break
            try:
                cards = self._fetch_json(template.format(sid=sid))
                consecutive_failures = 0
            except (requests.RequestException, ValueError) as e:
                print(f"[{self.game}] failed to fetch {sid}: {e}")
                consecutive_failures += 1
                continue

            for c in cards or []:
                row = self._normalize_card(c)
                if row is None:
                    continue
                # Alternate arts can share a card id — keep every
                # printing by suffixing repeats.
                cid = row["card_id"]
                if cid in seen_ids:
                    seen_ids[cid] += 1
                    row["card_id"] = f"{cid}-alt{seen_ids[cid]}"
                else:
                    seen_ids[cid] = 1
                rows.append(row)

            print(f"  [{self.game}] {sid}: {len(cards or [])} cards")
            time.sleep(0.1)

        return rows

    def autocomplete(self, q: str) -> list[str]:
        # The whole catalog lives locally — suggest from our own DB.
        return local_name_autocomplete(self.game, q)


# The registry of every game EtherDex knows. Adding a game later means
# writing its adapter and adding one line here.
GAMES: dict[str, GameAdapter] = {
    "pokemon": PokemonAdapter(),
    "mtg": MTGAdapter(),
    "ygo": YGOAdapter(),
    "onepiece": OnePieceAdapter(),
}

DEFAULT_GAME = "pokemon"


def get_adapter(game: str | None) -> GameAdapter:
    """Look up a game's adapter, or 400 if the game isn't supported."""
    key = (game or DEFAULT_GAME).strip().lower()
    adapter = GAMES.get(key)
    if adapter is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown game '{game}'. Available: {', '.join(sorted(GAMES))}.",
        )
    return adapter


# ---------------------------------------------------------------------------
# POKÉMON NAME LIST (for autocomplete)
# (Pokémon-specific for now; becomes per-game in a later phase.)
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
    """
    Create the cards, watchlist, price_snapshots, and sets tables, and
    run the v3.0 multi-game migrations on databases created by earlier
    versions. Every migration is safe and preserves existing data.
    """
    conn = sqlite3.connect(DATABASE)

    # Cards cache table. One row per card, tagged with its game.
    # Note on the primary key: card_id formats can't collide across our
    # supported games (Pokémon "swsh4-25", MTG UUIDs, Yu-Gi-Oh numeric
    # ids), so card_id alone stays the key — which also keeps watchlist
    # and price_snapshots references simple.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            card_id       TEXT PRIMARY KEY,
            game          TEXT NOT NULL DEFAULT 'pokemon',
            card_name     TEXT,
            set_name      TEXT,
            card_number   TEXT,
            rarity        TEXT,
            market_price  REAL,
            small_image   TEXT,
            large_image   TEXT,
            last_updated  TEXT
        )
        """)

    # MIGRATION (pre-3.0 databases): pokemon_name becomes the
    # game-neutral card_name. Data is preserved by the rename.
    try:
        conn.execute("ALTER TABLE cards RENAME COLUMN pokemon_name TO card_name")
    except sqlite3.OperationalError:
        pass  # already renamed (or fresh install)

    # MIGRATION (pre-3.0 databases): tag every existing card as Pokémon.
    try:
        conn.execute(
            "ALTER TABLE cards ADD COLUMN game TEXT NOT NULL DEFAULT 'pokemon'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # The old name-index is replaced by game-aware indexes.
    conn.execute("DROP INDEX IF EXISTS idx_pokemon_name")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_card_name ON cards(card_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_game ON cards(game)")

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

    # MIGRATION (pre-3.0): tag every existing watchlist row as Pokémon.
    try:
        conn.execute(
            "ALTER TABLE watchlist ADD COLUMN game TEXT NOT NULL " "DEFAULT 'pokemon'"
        )
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

    # sets — one row per set PER GAME, with its total card count.
    # Powers set-completion tracking on the dashboard.
    # The primary key is (game, set_id) because short set codes CAN
    # collide across games (Pokémon "neo1" vs MTG "neo" style codes).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sets (
            game           TEXT NOT NULL DEFAULT 'pokemon',
            set_id         TEXT NOT NULL,
            set_name       TEXT,
            total          INTEGER,
            printed_total  INTEGER,
            release_date   TEXT,
            last_synced    TEXT,
            PRIMARY KEY (game, set_id)
        )
        """)

    # MIGRATION (pre-3.0 databases): the old sets table had set_id as its
    # primary key and no game column. Rebuild it with the composite key,
    # carrying every existing row over tagged as Pokémon.
    set_cols = [row[1] for row in conn.execute("PRAGMA table_info(sets)")]
    if set_cols and "game" not in set_cols:
        conn.execute("ALTER TABLE sets RENAME TO sets_old")
        conn.execute("""
            CREATE TABLE sets (
                game           TEXT NOT NULL DEFAULT 'pokemon',
                set_id         TEXT NOT NULL,
                set_name       TEXT,
                total          INTEGER,
                printed_total  INTEGER,
                release_date   TEXT,
                last_synced    TEXT,
                PRIMARY KEY (game, set_id)
            )
            """)
        conn.execute("""
            INSERT INTO sets
            (game, set_id, set_name, total, printed_total,
             release_date, last_synced)
            SELECT 'pokemon', set_id, set_name, total, printed_total,
                   release_date, last_synced
            FROM sets_old
            """)
        conn.execute("DROP TABLE sets_old")

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
# GENERIC SYNC + QUERY LAYER (game-agnostic)
#
# These functions work for EVERY game. They take an adapter (or a game
# key), store normalized rows tagged with the game, and read them back
# filtered by game. No game-specific logic lives here.
# ---------------------------------------------------------------------------


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


def is_data_fresh(game: str, name: str) -> bool:
    """True if this game's cached cards matching the name are all fresh."""
    conn = sqlite3.connect(DATABASE)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MIN(last_updated) FROM cards "
            "WHERE game = ? AND card_name LIKE ?",
            (game, f"%{name}%"),
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


def refresh_cards(adapter: GameAdapter, name: str) -> int:
    """
    Fetch cards matching a name through the game's adapter and store
    them (tagged with the game), recording today's price snapshots.
    """
    rows = adapter.search_cards(name)
    if not rows:
        print(f"[{adapter.game}] no cards fetched for '{name}'")
        return 0

    timestamp = _now_iso()
    conn = sqlite3.connect(DATABASE)
    try:
        for row in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO cards
                (card_id, game, card_name, set_name, card_number,
                 rarity, market_price, small_image, large_image,
                 last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["card_id"],
                    adapter.game,
                    row["card_name"],
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
        conn.commit()
    finally:
        conn.close()

    print(f"[{adapter.game}] total cards stored for '{name}': {len(rows)}")
    return len(rows)


def query_local_db(
    game: str,
    name: str,
    rarity: str,
    sort: str,
    min_price: float,
    max_price: float,
) -> list[dict]:
    """
    Read cards for one game from the local cache.

    NOTE: card_name is ALSO returned as pokemon_name. That keeps the
    current frontend working unchanged through the refactor; the alias
    is dropped once the frontend moves to card_name in a later phase.
    """
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT card_id, game, card_name,
                   card_name AS pokemon_name,
                   set_name, card_number,
                   rarity, market_price, small_image, large_image,
                   last_updated
            FROM cards
            WHERE game = ?
            AND card_name LIKE ?
            AND market_price >= ?
            AND market_price <= ?
        """
        params: list = [game, f"%{name}%", min_price, max_price]

        if rarity:
            query += " AND rarity LIKE ? "
            params.append(f"%{rarity}%")

        if sort == "price_desc":
            query += " ORDER BY market_price DESC "
        elif sort == "price_asc":
            query += " ORDER BY market_price ASC "
        elif sort == "name_asc":
            query += " ORDER BY card_name ASC "
        elif sort == "name_desc":
            query += " ORDER BY card_name DESC "

        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def local_name_autocomplete(game: str, q: str) -> list[str]:
    """Name suggestions straight from our own database — used by
    full-sync games, whose whole catalog is already local."""
    query = q.strip()
    if not query:
        return []
    conn = sqlite3.connect(DATABASE)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT card_name FROM cards "
            "WHERE game = ? AND card_name LIKE ? "
            "ORDER BY card_name LIMIT 8",
            (game, f"{query}%"),
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def cards_need_full_sync(game: str) -> bool:
    """True if a full-sync game's catalog is missing or stale."""
    conn = sqlite3.connect(DATABASE)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(last_updated) FROM cards WHERE game = ?", (game,))
        newest = cursor.fetchone()[0]
    finally:
        conn.close()

    if not newest:
        return True
    try:
        newest_dt = datetime.fromisoformat(newest)
    except ValueError:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)
    return newest_dt < cutoff


def full_sync_cards(adapter: GameAdapter) -> int:
    """
    Sync a full-sync game's ENTIRE catalog into the local database,
    recording price snapshots, then fill in each set's card totals by
    counting what was actually stored.
    """
    print(f"[{adapter.game}] full catalog sync starting...")
    rows = adapter.fetch_all_cards()
    if not rows:
        print(f"[{adapter.game}] full sync got nothing (will retry next start).")
        return 0

    timestamp = _now_iso()
    set_counts: dict[str, int] = {}

    conn = sqlite3.connect(DATABASE)
    try:
        for row in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO cards
                (card_id, game, card_name, set_name, card_number,
                 rarity, market_price, small_image, large_image,
                 last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["card_id"],
                    adapter.game,
                    row["card_name"],
                    row["set_name"],
                    row["card_number"],
                    row["rarity"],
                    row["market_price"],
                    row["small_image"],
                    row["large_image"],
                    timestamp,
                ),
            )
            record_snapshot(conn, row["card_id"], row["market_price"])
            set_id = row.get("_set_id")
            if set_id:
                set_counts[set_id] = set_counts.get(set_id, 0) + 1

        # The sets list endpoint has no card counts — our own counts ARE
        # the totals, since we just stored the whole catalog.
        for set_id, count in set_counts.items():
            conn.execute(
                "UPDATE sets SET total = ?, printed_total = ? "
                "WHERE game = ? AND set_id = ?",
                (count, count, adapter.game, set_id),
            )
        conn.commit()
    finally:
        conn.close()

    print(
        f"[{adapter.game}] full sync stored {len(rows)} cards "
        f"across {len(set_counts)} sets."
    )
    return len(rows)


def sync_full_catalog_games() -> None:
    """Startup catalog sync for full-sync games — isolated per adapter,
    same rule as everywhere: one source failing never affects the rest."""
    for adapter in GAMES.values():
        if adapter.sync_strategy != "full_sync":
            continue
        if not cards_need_full_sync(adapter.game):
            print(f"[{adapter.game}] catalog is fresh — skipping full sync.")
            continue
        try:
            full_sync_cards(adapter)
        except Exception as e:
            print(f"[{adapter.game}] full sync failed (will retry next start): {e}")


def sets_need_sync(game: str) -> bool:
    """True if this game's sets are missing or haven't synced recently."""
    conn = sqlite3.connect(DATABASE)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(last_synced) FROM sets WHERE game = ?", (game,))
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


def sync_all_sets() -> None:
    """
    Sync set data for EVERY registered game, one adapter at a time.

    THE ISOLATION BOUNDARY LIVES HERE: each adapter syncs inside its own
    try/except, so one game's API being down never affects the others.
    Failures are non-fatal — that game just retries on the next startup,
    and its cached set data keeps serving in the meantime.
    """
    for adapter in GAMES.values():
        if not sets_need_sync(adapter.game):
            print(f"[{adapter.game}] set data is fresh — skipping set sync.")
            continue

        print(f"[{adapter.game}] syncing set data from the API...")
        try:
            rows = adapter.fetch_sets()
        except requests.RequestException as e:
            print(f"[{adapter.game}] set sync failed (will retry next start): {e}")
            continue
        except ValueError as e:
            print(f"[{adapter.game}] set sync returned invalid JSON: {e}")
            continue

        timestamp = _now_iso()
        conn = sqlite3.connect(DATABASE)
        try:
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sets
                    (game, set_id, set_name, total, printed_total,
                     release_date, last_synced)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        adapter.game,
                        row["set_id"],
                        row["set_name"],
                        row["total"],
                        row["printed_total"],
                        row["release_date"],
                        timestamp,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        print(f"[{adapter.game}] synced {len(rows)} sets.")


# Sync set data once at startup (each game skips automatically if fresh),
# then full catalogs for full-sync games (One Piece).
sync_all_sets()
sync_full_catalog_games()


# ---------------------------------------------------------------------------
# REQUEST MODELS
# ---------------------------------------------------------------------------


class WatchCardIn(BaseModel):
    """The card data the frontend sends when adding to the watchlist.
    `game` defaults to pokemon so the current frontend (which doesn't
    send it yet) keeps working unchanged."""

    card_id: str
    pokemon_name: str
    set_name: str | None = ""
    rarity: str | None = ""
    market_price: float | None = 0
    small_image: str | None = ""
    large_image: str | None = ""
    game: str | None = "pokemon"


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
# ROUTES — general
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return {"message": "EtherDex API is running!"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "3.3",
        "games": sorted(GAMES.keys()),
        "api_key_set": bool(API_KEY),
        "clerk_key_set": bool(CLERK_SECRET_KEY),
        "freshness_days": FRESHNESS_DAYS,
    }


@app.get("/games")
def list_games():
    """Every game EtherDex supports — the frontend's game switcher will
    be built from this list in a later phase."""
    return {"games": [{"key": a.game, "name": a.display_name} for a in GAMES.values()]}


@app.get("/pokemon-names")
def pokemon_names(q: str = ""):
    """Kept for the current frontend; /autocomplete is the game-aware
    version the multi-game frontend will use."""
    return {"names": GAMES["pokemon"].autocomplete(q)}


@app.get("/autocomplete")
def autocomplete(q: str = "", game: str = "pokemon"):
    """Game-aware name suggestions for the search box."""
    adapter = get_adapter(game)
    return {"names": adapter.autocomplete(q), "game": adapter.game}


@app.get("/search")
def search_cards(
    name: str = "",
    rarity: str = "",
    sort: str = "",
    min_price: float = 0,
    max_price: float = 999999,
    game: str = "pokemon",
):
    """Game-aware search. `game` defaults to pokemon, so the current
    frontend keeps working without sending it."""
    adapter = get_adapter(game)

    name = name.strip()
    if not name:
        return {"cards": []}

    if adapter.sync_strategy == "full_sync":
        # The whole catalog lives locally — resync only if it's missing
        # or stale (self-heals if the startup sync had failed).
        if cards_need_full_sync(adapter.game):
            full_sync_cards(adapter)
    elif is_data_fresh(adapter.game, name):
        print(f"[{adapter.game}] '{name}' — serving fresh data from DB.")
    else:
        print(f"[{adapter.game}] '{name}' — data missing or stale, fetching...")
        refresh_cards(adapter, name)

    results = query_local_db(adapter.game, name, rarity, sort, min_price, max_price)
    return {"cards": results, "count": len(results), "game": adapter.game}


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
    the watchlist row. The JOIN matches on game as well as card_id so
    cards from different games can never cross wires.
    """
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT w.card_json, w.condition, w.quantity, w.target_price,
                   w.game,
                   c.market_price AS current_price,
                   c.last_updated AS price_updated
            FROM watchlist w
            LEFT JOIN cards c
                   ON w.card_id = c.card_id AND w.game = c.game
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
        card["game"] = row["game"]
        # card_name mirrors pokemon_name so newer consumers can use the
        # game-neutral key; the frontend still reads pokemon_name today.
        card["card_name"] = card.get("card_name") or card.get("pokemon_name")

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
    """Add a card to the signed-in user's watchlist, tagged with its game."""
    user_id = get_user_id(authorization)

    # Validate the game (defaults to pokemon for the current frontend).
    adapter = get_adapter(card.game)

    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO watchlist
            (user_id, card_id, card_json, added_at, game)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, card.card_id, card.model_dump_json(), _now_iso(), adapter.game),
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


# ---------------------------------------------------------------------------
# ROUTES — collection (require a signed-in user)
# ---------------------------------------------------------------------------


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

    Game-aware: a set's total is looked up within the card's own game,
    so identically-named sets in different games can never collide.
    """
    user_id = get_user_id(authorization)

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()

        # The user's watchlist cards (each row = one distinct card).
        cursor.execute(
            "SELECT card_json, game FROM watchlist WHERE user_id = ?",
            (user_id,),
        )
        watch_rows = cursor.fetchall()

        # Count distinct cards owned per (game, set name).
        owned_by_set: dict[tuple[str, str], int] = {}
        for row in watch_rows:
            info = json.loads(row["card_json"])
            set_name = info.get("set_name")
            game = row["game"] or DEFAULT_GAME
            if set_name:
                key = (game, set_name)
                owned_by_set[key] = owned_by_set.get(key, 0) + 1

        if not owned_by_set:
            return {"sets": []}

        # Look up each set's total card count, within its own game.
        totals: dict[tuple[str, str], int] = {}
        games_in_watchlist = {g for (g, _) in owned_by_set}
        for game in games_in_watchlist:
            names = [n for (g, n) in owned_by_set if g == game]
            placeholders = ",".join("?" for _ in names)
            cursor.execute(
                f"""
                SELECT set_name, printed_total, total
                FROM sets
                WHERE game = ? AND set_name IN ({placeholders})
                """,
                [game, *names],
            )
            for row in cursor.fetchall():
                totals[(game, row["set_name"])] = (
                    row["printed_total"] or row["total"] or 0
                )
    finally:
        conn.close()

    results = []
    for (game, set_name), owned in owned_by_set.items():
        total = totals.get((game, set_name), 0)
        # Cap owned at the set size so the display never shows over 100%.
        owned_display = min(owned, total) if total else owned
        percent = round((owned_display / total) * 100) if total else 0
        results.append(
            {
                "set_name": set_name,
                "game": game,
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
