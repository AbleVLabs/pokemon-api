# EtherDex — The Collector's Index

A multi-TCG collection tracker that treats your card collection like a
portfolio: search cards across four games, track what you own, watch
prices move, and see your collection's value over time.

**Games supported:** Pokémon · Magic: The Gathering · Yu-Gi-Oh! · One Piece

Built by [AbleVLabs](https://ablevlabs.com) — *Research. Innovate. Create.*

---

## Features

- **Multi-game card search** with autocomplete, filters (rarity, price,
  set), sorting, and pagination — one search bar, four card games
- **Watchlist / collection tracking** per user (Clerk authentication),
  with card condition (6-tier), quantity owned, and line totals
- **Price alerts** — set a target price per card; EtherDex flags cards
  that reach it, with in-app highlights and a header badge
- **Collection Dashboard** — total value, value-over-time chart,
  gainers & losers, most valuable cards, condition breakdown, and
  per-set completion progress
- **Trust & transparency** — every price names its source and its
  freshness ("Price from Scryfall · updated today")
- **Excel export** of your collection, with per-game tagging
- **Price history** — daily snapshots power per-card and whole-collection
  charts

## Architecture

The backend is built around a **game adapter pattern**: every card game
is a pluggable data source behind one small interface. Adding a game
means writing one adapter class and registering it — the database,
search, watchlist, dashboard, and even the frontend's game switcher
(driven by a `/games` endpoint) pick it up automatically.

**Failure isolation is the core design rule:** each adapter syncs and
fetches inside its own error boundary, so one game's API being down
never affects the others — cached data in SQLite keeps serving.

Two sync strategies coexist:

| Strategy | Games | How it works |
| --- | --- | --- |
| On-demand | Pokémon, MTG, Yu-Gi-Oh! | Cards are fetched per search and cached with a freshness TTL |
| Full-sync | One Piece | The whole catalog (~48 sets) syncs in a background thread at startup; searches run entirely locally |

The full-sync strategy exists because One Piece has no official API —
its community source is set-based — and it turns the shakiest data
source into the most resilient game in the app: after one sync, it
works even if the source goes down for a week.

## Tech Stack

- **Backend:** FastAPI (Python), raw SQLite3, Clerk auth verification
- **Frontend:** Next.js 16 (TypeScript), TailwindCSS, Recharts, Clerk
- **Data sources:** [Pokémon TCG API](https://pokemontcg.io),
  [Scryfall](https://scryfall.com/docs/api) (MTG),
  [YGOPRODeck](https://ygoprodeck.com/api-guide/) (Yu-Gi-Oh!),
  [OPTCGAPI](https://optcgapi.com) (One Piece)

## Running Locally

Prerequisites: Python 3.11+, Node.js 18+, a free [Clerk](https://clerk.com)
application (for sign-in).

```bash
git clone https://github.com/AbleVLabs/pokemon-api.git
cd pokemon-api

# Backend dependencies
python -m venv venv
venv\Scripts\activate          # Windows (use source venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

# Frontend dependencies
npm install
cd frontend && npm install && cd ..
```

Create two environment files:

`.env` (project root):

```
CLERK_SECRET_KEY=sk_test_...
POKEMON_TCG_API_KEY=optional_but_recommended
```

`frontend/.env.local`:

```
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
```

Then start everything with one command from the project root:

```bash
npm run dev
```

Backend: `http://127.0.0.1:8000` (docs at `/docs`) · Frontend:
`http://localhost:3000`

The first start downloads set data for all four games and the full One
Piece catalog in the background — the app is usable immediately while
that fills in.

## Legal

EtherDex is not affiliated with, endorsed, or sponsored by Nintendo,
The Pokémon Company, Wizards of the Coast, Konami, Bandai, or their
affiliates. All card images, names, and related marks are the property
of their respective owners. Price data is provided for informational
purposes only.