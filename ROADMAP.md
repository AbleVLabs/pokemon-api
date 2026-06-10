# EtherDex — Roadmap

**Product:** EtherDex — *The Collector's Index*
**By:** AbleVLabs

---

## What EtherDex Is

A focused, fast, trustworthy **TCG collection tracker**.
Not just a price checker — a place where your collection is indexed,
valued, and tracked over time. "Robinhood for cards."

Launching with **Pokémon, Magic: The Gathering, and Yu-Gi-Oh**, with
**One Piece** following close behind. Survey-validated: collectors want
more than one game.

## Strategic Thesis — How We Win

Main competitors: **Collectr** (multi-TCG, funded, social + a physical
"Vault" service), **Holodex** (Pokémon card scanner), and **Moxfield**
(entrenched in MTG deckbuilding).

We do NOT beat them on breadth or feature count. We beat them by being:

1. **Focused** — a great collection *tracker*, not a sprawling
   everything-app. Tracking is the product. Deck tools are not.
2. **Trustworthy** — visibly honest about where prices come from and how
   fresh they are. Price-trust is where competitors lose credibility.
3. **Fair** — a genuinely useful free tier, no hostile paywall.
4. **Early where it counts** — One Piece is the fastest-growing TCG with
   the weakest tooling ecosystem. Being the trustworthy tracker there is
   our most differentiating play.

Realistic goal: carve a defensible niche as the tracker people trust —
not "destroy Collectr." Win the long game by shipping one stable layer
at a time.

---

## Status

**Done:** card search, pagination, filters, autocomplete, freshness
cache, one-command startup, per-user watchlist (Clerk accounts), card
detail modal, EtherDex branding, condition selector + guide, price
history recording + chart, mobile-responsive check, onboarding polish,
**quantity owned** (stepper + line totals), **Collection Dashboard**
(total value, stats, value-over-time chart, gainers/losers),
**Trust & Transparency layer** (price source + freshness in card modal),
**Set Completion tracking** (per-set progress bars),
**Watchlist Price Refresh** (live prices via cards-table JOIN),
**Price Alerts** (full feature: target input, Save/Clear, triggered
highlights, panel banner, header badge — backend v2.5 + frontend).

**In progress:** Multi-TCG refactor — Phase 1 (backend foundation:
adapter architecture, game-tagged schema, v3.0) delivered.

---

## Roadmap (in order)

1. **Price Alerts frontend** — "Alert me at $X" input per watchlist
   card, 🔔 triggered highlight, banner in the watchlist panel.
   In-app only for v1 (no email — no notification infrastructure).
   Backend already shipped in v2.5.

2. **Multi-TCG refactor** — make the data model TCG-agnostic:
   a `game` column on cards/sets, game-neutral naming, and a
   per-game **adapter architecture** so each game is a pluggable
   data source. If one source breaks, the others don't notice and
   cached data keeps serving. This is the foundation everything
   after it stands on.

3. **MTG** — via **Scryfall** (free, no key, the gold-standard MTG
   API). ~30K cards; biggest collector market; volatile prices make
   alerts shine. Competitive caveat: Moxfield owns MTG deck tools —
   we compete on *tracking*, not decks.

4. **Yu-Gi-Oh** — via **YGOPRODeck** (free, no key, comprehensive).
   Weakest entrenched competition of the major TCGs.

5. **One Piece** — via community APIs (apitcg.com / optcgapi.com —
   no official Bandai API). Built LAST of the four, after the stable
   sources prove the plumbing. Honest caveat: card data exists;
   *price* data is the weak point (TCGplayer has OP prices but no
   open API). First task of this phase = validate the source in
   code. May launch collection-tracking-first with thinner pricing.

6. **LAUNCH — free.** No payments at launch. The goal is real users,
   real feedback, real signal. Target communities: r/PokemonTCG,
   r/pkmntcgcollections, OP TCG Discords, MTG/YGO collector spaces.

7. **Payments + tiers** — Stripe subscriptions, post-launch, once
   the free core proves sticky. Free / Enthusiast / Collector /
   Shop Owner. Free tier kept GENEROUS (~10 cards) so the upgrade
   feels like a reward, not a hostage situation.

8. **Card scanning** — single-card first, via an existing recognition
   API (do NOT build the computer vision). Condition stays
   user-declared, never photo-guessed. Built last — our
   hardest-to-perfect feature.

---

## Deferred (not dead — revisit with real user signal)

- **Deck builder + exports** (Untap, MTGO, etc.) — a whole second
  product category. Moxfield (MTG) and Limitless (Pokémon) own it.
  Only revisit if launched users ask for it loudly.
- **Email/push price alerts** — needs an email service + a 24/7
  scheduler. Clean v2 once in-app alerts prove used.
- **"Drops below $X" buy-watch alerts** — clean v2 of alerts.
- **Daily background price re-fetch** for all watchlisted cards —
  upgrade from "fresh as of last search" to "fresh daily."

## Explicitly NOT Doing

- **Dragon Ball Z** — no usable data source exists. Hard technical no.
  Revisit only if a real API appears.
- **Marketplace** (buying/selling).
- **A physical Vault / consignment service** — an operations business,
  not a software feature.
- **Heavy social features** early.
- **AI price "predictions"** — we show honest trends, not
  fortune-telling.

---

## Working Principles

- One stable layer at a time. Each phase ships something usable.
- Commit to GitHub at every milestone — a commit is the restore point.
- Adapter isolation: one game's data source breaking must never take
  down the others.
- Simplicity over cleverness. Beginner-readable, production-capable.