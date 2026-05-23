# EtherDex — Roadmap

**Product:** EtherDex — *The Collector's Index*
**By:** AbleVLabs

---

## What EtherDex Is

A focused, fast, trustworthy **Pokémon card collection tracker**.
Not just a price checker — a place where your collection is indexed,
valued, and tracked over time. "Robinhood for cards."

## Strategic Thesis — How We Win

Main competitors: **Collectr** (multi-TCG, funded, social + a physical
"Vault" service) and **Holodex** (Pokémon card scanner).

We do NOT beat them on breadth or feature count. We beat them by being:

1. **Focused** — a great Pokémon tracker, not a sprawling everything-app.
2. **Trustworthy** — visibly honest about where prices come from and how
   fresh they are. Price-trust is where both competitors lose credibility.
3. **Fair** — a genuinely useful free tier, no hostile paywall.

Realistic goal: carve a defensible niche as the tracker people trust —
not "destroy Collectr." Win the long game by shipping one stable layer
at a time.

---

## Status

**Done:** card search, pagination, filters, autocomplete, freshness
cache, one-command startup, per-user watchlist (Clerk accounts), card
detail modal, EtherDex branding, condition selector + guide, price
history recording + chart, mobile-responsive check, onboarding polish.

**In progress:** quantity owned on watchlist cards.

---

## Roadmap (in order)

1. **Quantity owned** — `[−] N [+]` stepper per watchlist card.
   Prerequisite for the collection dashboard.

2. **Collection Dashboard** — total collection value, value-over-time
   chart, gainers/losers. THE headline feature. Makes EtherDex a
   *tracker*, not a *checker*.

3. **Trust & Transparency layer** — show price source + freshness on
   every card ("Price from TCGplayer · updated 2 days ago"). Cheap to
   build (we already store `last_updated`); attacks competitors' biggest
   weakness.

4. **Set completion tracking** — "47 of 102 in Plasma Blast." Deepens
   the collection experience.

5. **Price alerts** — "card hit $X" / weekly digest. Needs notification
   infrastructure, so it comes after the cheaper wins.

6. **Payments + tiers** — Stripe subscriptions. Free / Enthusiast /
   Collector / Shop Owner. Free tier kept GENEROUS (~10 cards) so the
   upgrade feels like a reward, not a hostage situation.

7. **Card scanning** — single-card first, via an existing recognition
   API (do NOT build the computer vision). Condition stays user-declared,
   never photo-guessed. Built last — our hardest-to-perfect feature.

---

## Explicitly NOT Doing

- **Multi-TCG breadth** — a post-revenue question, not a now question.
  Win Pokémon first. The brand (EtherDex) leaves the door open later.
- **Marketplace** (buying/selling).
- **A physical Vault / consignment service** — an operations business,
  not a software feature.
- **Heavy social features** early.
- **AI price "predictions"** — we show honest trends, not fortune-telling.

---

## Working Principles

- One stable layer at a time. Each phase ships something usable.
- Commit to GitHub at every milestone — a commit is the restore point.
- Simplicity over cleverness. Beginner-readable, production-capable.