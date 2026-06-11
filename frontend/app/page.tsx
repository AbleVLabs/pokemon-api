'use client';

import { useState, useMemo, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useAuth } from '@clerk/nextjs';
import * as XLSX from 'xlsx';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import useCards from '../hooks/useCards';

const CARDS_PER_PAGE = 24;
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

// ---- MULTI-GAME SUPPORT ----
// The list of games comes from the backend's /games endpoint at runtime,
// so new games appear in the switcher automatically when adapters ship.
interface GameInfo {
  key: string;
  name: string;
}

const FALLBACK_GAMES: GameInfo[] = [{ key: 'pokemon', name: 'Pokémon' }];

// Per-game UI text. Unknown games fall back to 'default', so a brand-new
// adapter works in the UI before this map even knows about it.
const GAME_UI: Record<
  string,
  {
    placeholder: string;
    examples: string[];
    rarities: string[];
    priceSource: string;
    tag: string;
  }
> = {
  pokemon: {
    placeholder: 'Search any Pokémon (e.g. Charizard)...',
    examples: ['Charizard', 'Pikachu', 'Mewtwo', 'Eevee', 'Rayquaza'],
    rarities: ['Common', 'Uncommon', 'Rare', 'Promo'],
    priceSource: 'TCGplayer',
    tag: 'PKMN',
  },
  mtg: {
    placeholder: 'Search any Magic card (e.g. Lightning Bolt)...',
    examples: ['Black Lotus', 'Lightning Bolt', 'Sol Ring', 'Counterspell', 'Llanowar Elves'],
    rarities: ['Common', 'Uncommon', 'Rare', 'Mythic'],
    priceSource: 'Scryfall',
    tag: 'MTG',
  },
  ygo: {
    placeholder: 'Search any Yu-Gi-Oh! card (e.g. Dark Magician)...',
    examples: [
      'Dark Magician',
      'Blue-Eyes White Dragon',
      'Exodia the Forbidden One',
      'Pot of Greed',
      'Ash Blossom & Joyous Spring',
    ],
    rarities: ['Common', 'Rare', 'Super Rare', 'Ultra Rare', 'Secret Rare'],
    priceSource: 'YGOPRODeck',
    tag: 'YGO',
  },
  onepiece: {
    placeholder: 'Search any One Piece card (e.g. Monkey D. Luffy)...',
    examples: [
      'Monkey D. Luffy',
      'Roronoa Zoro',
      'Nami',
      'Shanks',
      'Trafalgar Law',
    ],
    rarities: ['Common', 'Uncommon', 'Rare', 'Super Rare', 'Secret Rare', 'Leader'],
    priceSource: 'TCGplayer via OPTCGAPI',
    tag: 'OP',
  },
  default: {
    placeholder: 'Search any card...',
    examples: [],
    rarities: ['Common', 'Uncommon', 'Rare'],
    priceSource: 'market data',
    tag: 'TCG',
  },
};

const gameUI = (key: string) => GAME_UI[key] || GAME_UI.default;

// Turn an ISO timestamp into a friendly "how fresh" label for prices.
function formatFreshness(iso?: string): string {
  if (!iso) return 'recently';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'recently';
  const days = Math.floor((Date.now() - then) / (1000 * 60 * 60 * 24));
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

// The card conditions, with a short description to help users choose.
const CONDITIONS = [
  {
    name: 'Mint',
    desc: 'Perfect or virtually perfect condition with no visible wear, damage, or manufacturing defects.',
  },
  {
    name: 'Near Mint',
    desc: 'Appears almost pack-fresh. Very minor imperfections may be visible on close inspection, with no noticeable wear or damage.',
  },
  {
    name: 'Lightly Played',
    desc: 'Minor wear from handling or play, such as small edge wear, light scratches, or slight surface marks. No major flaws.',
  },
  {
    name: 'Moderately Played',
    desc: 'Noticeable wear including multiple scratches, edge wear, whitening, scuffing, or light creasing.',
  },
  {
    name: 'Heavily Played',
    desc: 'Heavy visible wear with significant scratching, whitening, edge damage, creasing, or other cosmetic flaws.',
  },
  {
    name: 'Damaged',
    desc: 'Major defects affecting the card, such as bends, tears, water damage, writing, holes, peeling, or severe creasing.',
  },
];

interface PricePoint {
  date: string;
  price: number;
}

// Small price-history chart shown inside the card detail modal.
// Handles three cases: no data, one data point, and a full trend line.
function PriceHistoryChart({ cardId }: { cardId: string }) {
  const [history, setHistory] = useState<PricePoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const load = async () => {
      try {
        const res = await fetch(`${API_URL}/price-history/${cardId}`);
        const data = await res.json();
        if (!cancelled) setHistory(data.history || []);
      } catch {
        if (!cancelled) setHistory([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [cardId]);

  if (loading) {
    return <p className="text-zinc-600 text-sm">Loading price history...</p>;
  }

  // No data yet — recording is new, so this is normal.
  if (history.length === 0) {
    return (
      <p className="text-zinc-600 text-sm">
        Price history will appear here as data is collected over time.
      </p>
    );
  }

  // Only one snapshot — a line needs at least two points, so show the value.
  if (history.length === 1) {
    return (
      <p className="text-zinc-400 text-sm">
        First price recorded:{' '}
        <span className="text-yellow-400 font-semibold">
          ${history[0].price.toFixed(2)}
        </span>{' '}
        on {history[0].date}. The chart appears once more data is collected.
      </p>
    );
  }

  // Two or more snapshots — draw the trend line.
  return (
    <div className="w-full h-48">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={history} margin={{ top: 5, right: 10, bottom: 5, left: -10 }}>
          <XAxis
            dataKey="date"
            tick={{ fill: '#71717a', fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: '#3f3f46' }}
          />
          <YAxis
            tick={{ fill: '#71717a', fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: '#3f3f46' }}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              background: '#18181b',
              border: '1px solid #3f3f46',
              borderRadius: '8px',
              color: '#fff',
            }}
            formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Price']}
          />
          <Line
            type="monotone"
            dataKey="price"
            stroke="#facc15"
            strokeWidth={2}
            dot={{ fill: '#facc15', r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface WatchCard {
  card_id: string;
  pokemon_name: string;
  set_name: string;
  rarity: string;
  market_price: number;
  small_image: string;
  large_image: string;
  condition?: string;
  quantity?: number;
  target_price?: number | null;
  game?: string;
  last_updated?: string;
}

export default function Home() {
  const { getToken, isSignedIn, isLoaded } = useAuth();

  // Which game is active, and which games the backend supports.
  const [games, setGames] = useState<GameInfo[]>(FALLBACK_GAMES);
  const [activeGame, setActiveGame] = useState('pokemon');

  const [search, setSearch] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  const [sortBy, setSortBy] = useState('');
  const [rarityFilter, setRarityFilter] = useState('');
  const [minPrice, setMinPrice] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [setFilter, setSetFilter] = useState('');

  const [visibleCount, setVisibleCount] = useState(CARDS_PER_PAGE);

  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);

  const [watchlist, setWatchlist] = useState<WatchCard[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedCard, setSelectedCard] = useState<WatchCard | null>(null);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [conditionGuideOpen, setConditionGuideOpen] = useState(false);

  // Price alert drafts — what the user has typed into a target-price box
  // but not yet saved. Keyed by card_id so each card edits independently.
  const [targetDrafts, setTargetDrafts] = useState<Record<string, string>>({});

  const { cards, loading, error } = useCards({
    search: searchTerm,
    game: activeGame,
    sortBy,
    rarityFilter,
    minPrice,
    maxPrice,
  });

  // Load the supported games once — new adapters appear automatically.
  useEffect(() => {
    const loadGames = async () => {
      try {
        const res = await fetch(`${API_URL}/games`);
        if (res.ok) {
          const data = await res.json();
          if (data.games?.length) setGames(data.games);
        }
      } catch {
        // backend unreachable — keep the Pokémon-only fallback
      }
    };
    loadGames();
  }, []);

  // Switching games keeps the typed search (it re-runs against the new
  // game automatically) and resets everything tied to the old results.
  const switchGame = (key: string) => {
    if (key === activeGame) return;
    setActiveGame(key);
    setVisibleCount(CARDS_PER_PAGE);
    setSetFilter('');
    setRarityFilter('');
    setSuggestions([]);
  };

  // --- WATCHLIST: load from the backend when the user is signed in ---
  useEffect(() => {
    if (!isLoaded) return;

    if (!isSignedIn) {
      setWatchlist([]);
      return;
    }

    const loadWatchlist = async () => {
      setWatchlistLoading(true);
      try {
        const token = await getToken();
        const res = await fetch(`${API_URL}/watchlist`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setWatchlist(data.cards || []);
        }
      } catch {
        // network hiccup — leave watchlist as-is
      } finally {
        setWatchlistLoading(false);
      }
    };

    loadWatchlist();
  }, [isLoaded, isSignedIn, getToken]);

  const isWatched = useCallback(
    (cardId: string) => watchlist.some((c) => c.card_id === cardId),
    [watchlist]
  );

  const toggleWatch = async (card: WatchCard) => {
    if (!isSignedIn) {
      alert('Please sign in to use your watchlist.');
      return;
    }

    const token = await getToken();
    const alreadyIn = isWatched(card.card_id);

    try {
      if (alreadyIn) {
        await fetch(`${API_URL}/watchlist/remove/${card.card_id}`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        });
        setWatchlist((prev) =>
          prev.filter((c) => c.card_id !== card.card_id)
        );
      } else {
        const tagged = { ...card, game: card.game || activeGame };
        await fetch(`${API_URL}/watchlist/add`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(tagged),
        });
        setWatchlist((prev) => [...prev, tagged]);
      }
    } catch {
      alert('Could not update watchlist. Please try again.');
    }
  };

  // Change a watchlist card's condition — updates the screen, then the backend.
  const changeCondition = async (cardId: string, condition: string) => {
    setWatchlist((prev) =>
      prev.map((c) => (c.card_id === cardId ? { ...c, condition } : c))
    );
    try {
      const token = await getToken();
      await fetch(`${API_URL}/watchlist/condition`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ card_id: cardId, condition }),
      });
    } catch {
      alert('Could not save the condition. Please try again.');
    }
  };

  // Change a watchlist card's quantity — updates the screen, then the backend.
  // Quantity never goes below 0 (0 just means "own none right now").
  const changeQuantity = async (cardId: string, quantity: number) => {
    const safeQuantity = Math.max(0, quantity);
    setWatchlist((prev) =>
      prev.map((c) =>
        c.card_id === cardId ? { ...c, quantity: safeQuantity } : c
      )
    );
    try {
      const token = await getToken();
      await fetch(`${API_URL}/watchlist/quantity`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ card_id: cardId, quantity: safeQuantity }),
      });
    } catch {
      alert('Could not save the quantity. Please try again.');
    }
  };

  // --- PRICE ALERTS ---

  // Save a card's target price — updates the screen, then the backend.
  // Passing null clears the alert (the backend column is nullable).
  const saveTarget = async (cardId: string, target: number | null) => {
    setWatchlist((prev) =>
      prev.map((c) =>
        c.card_id === cardId ? { ...c, target_price: target } : c
      )
    );
    // The draft is committed (or cleared), so drop it.
    setTargetDrafts((prev) => {
      const next = { ...prev };
      delete next[cardId];
      return next;
    });
    try {
      const token = await getToken();
      await fetch(`${API_URL}/watchlist/target`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ card_id: cardId, target_price: target }),
      });
    } catch {
      alert('Could not save the price alert. Please try again.');
    }
  };

  // Validate and submit whatever is typed in a card's target box.
  const submitTargetDraft = (card: WatchCard) => {
    const raw = (targetDrafts[card.card_id] ?? '').trim();
    if (raw === '') {
      // Empty box + Save = clear the alert.
      saveTarget(card.card_id, null);
      return;
    }
    const parsed = Number(raw);
    if (Number.isNaN(parsed) || parsed <= 0) {
      alert('Please enter a price above $0.');
      return;
    }
    saveTarget(card.card_id, Number(parsed.toFixed(2)));
  };

  // What the target input should display: the in-progress draft if one
  // exists, otherwise the saved target from the backend.
  const targetInputValue = (card: WatchCard) => {
    if (card.card_id in targetDrafts) return targetDrafts[card.card_id];
    return card.target_price != null ? String(card.target_price) : '';
  };

  // True when the box's contents differ from what's saved — controls
  // whether the Save button is shown.
  const targetIsDirty = (card: WatchCard) => {
    if (!(card.card_id in targetDrafts)) return false;
    const saved = card.target_price != null ? String(card.target_price) : '';
    return targetDrafts[card.card_id].trim() !== saved;
  };

  // A target counts as "reached" when the card has a live price at or
  // above the target the user set.
  const targetReached = (card: WatchCard) =>
    card.target_price != null &&
    card.market_price > 0 &&
    card.market_price >= card.target_price;

  // How many alerts are currently triggered — shown as a badge on the
  // Watchlist button so it's visible without opening the panel.
  const alertsHit = useMemo(
    () => watchlist.filter((c) => targetReached(c)).length,
    [watchlist]
  );

  // Total value now multiplies each card's price by how many you own.
  const watchlistTotal = useMemo(
    () =>
      watchlist.reduce(
        (sum, c) => sum + (c.market_price || 0) * (c.quantity ?? 1),
        0
      ),
    [watchlist]
  );

  const exportToExcel = () => {
    if (watchlist.length === 0) return;

    const rows = watchlist.map((card) => {
      const unitPrice =
        card.market_price && card.market_price > 0
          ? Number(card.market_price.toFixed(2))
          : 0;
      const quantity = card.quantity ?? 1;
      return {
        'Game': gameUI(card.game || 'pokemon').tag,
        'Card Name': card.pokemon_name,
        'Set': card.set_name || 'Unknown',
        'Rarity': card.rarity || 'Unknown',
        'Condition': card.condition || 'Near Mint',
        'Quantity': quantity,
        'Unit Price (USD)': unitPrice,
        'Line Total (USD)': Number((unitPrice * quantity).toFixed(2)),
      };
    });

    const blank = {
      'Game': '',
      'Card Name': '', 'Set': '', 'Rarity': '', 'Condition': '',
      'Quantity': '' as unknown as number,
      'Unit Price (USD)': '' as unknown as number,
      'Line Total (USD)': '' as unknown as number,
    };
    rows.push(blank);
    rows.push({
      'Game': '',
      'Card Name': 'TOTAL',
      'Set': '',
      'Rarity': '',
      'Condition': '',
      'Quantity': '' as unknown as number,
      'Unit Price (USD)': '' as unknown as number,
      'Line Total (USD)': Number(watchlistTotal.toFixed(2)),
    });

    const worksheet = XLSX.utils.json_to_sheet(rows);
    worksheet['!cols'] = [
      { wch: 8 },
      { wch: 28 }, { wch: 26 }, { wch: 18 },
      { wch: 16 }, { wch: 10 }, { wch: 16 }, { wch: 16 },
    ];

    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, 'Watchlist');

    const today = new Date().toISOString().split('T')[0];
    XLSX.writeFile(workbook, `watchlist-${today}.xlsx`);
  };

  const runSearch = (term?: string) => {
    const value = term ?? search;
    setSearch(value);
    setSearchTerm(value);
    setVisibleCount(CARDS_PER_PAGE);
    setSetFilter('');
    setShowSuggestions(false);
  };

  useEffect(() => {
    const term = search.trim();
    if (term.length < 2) {
      setSuggestions([]);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(
          `${API_URL}/autocomplete?q=${encodeURIComponent(term)}&game=${activeGame}`
        );
        const data = await res.json();
        setSuggestions(data.names || []);
      } catch {
        setSuggestions([]);
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [search, activeGame]);

  const availableSets = useMemo(() => {
    const sets = new Set<string>();
    for (const card of cards) {
      if (card.set_name) sets.add(card.set_name);
    }
    return Array.from(sets).sort();
  }, [cards]);

  const filteredCards = useMemo(() => {
    if (!setFilter) return cards;
    return cards.filter((card) => card.set_name === setFilter);
  }, [cards, setFilter]);

  const visibleCards = filteredCards.slice(0, visibleCount);
  const hasMore = visibleCount < filteredCards.length;

  const controlClass =
    'p-3 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-200 ' +
    'focus:outline-none focus:border-yellow-500 transition-colors';

  return (
    <main className="min-h-screen bg-zinc-950 text-white flex flex-col">
      {/* HEADER */}
      <div className="relative border-b border-zinc-900 overflow-hidden">
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              'radial-gradient(ellipse 60% 80% at 50% 0%, rgba(250,204,21,0.14), transparent 70%)',
          }}
        />

        {/* Top row: EtherDex logo badge (left) + Watchlist button (right) */}
        <div className="relative max-w-7xl mx-auto px-6 pt-5 flex justify-between items-center">
          <div className="rounded-2xl border border-zinc-700 overflow-hidden
                          shadow-[0_0_25px_rgba(250,204,21,0.15)]">
            <img
              src="/EtherDexLogo.png"
              alt="EtherDex"
              className="h-32 w-32 object-cover block"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = 'none';
              }}
            />
          </div>

          <div className="flex items-center gap-3">
            <Link
              href="/dashboard"
              className="flex items-center gap-2 px-4 py-2 rounded-lg
                         bg-zinc-900 border border-zinc-800 text-zinc-200
                         hover:border-yellow-500 hover:text-yellow-400 transition-colors"
            >
              <span>📊</span>
              <span className="font-medium">Dashboard</span>
            </Link>

            <button
              onClick={() => setPanelOpen(true)}
              className="flex items-center gap-2 px-4 py-2 rounded-lg
                         bg-zinc-900 border border-zinc-800 text-zinc-200
                         hover:border-yellow-500 hover:text-yellow-400 transition-colors"
            >
              <span>♡</span>
              <span className="font-medium">Watchlist</span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-400 text-black font-bold">
                {watchlist.length}
              </span>
              {/* Lights up when one or more price alerts have been reached */}
              {alertsHit > 0 && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-green-400 text-black font-bold">
                  🎯 {alertsHit}
                </span>
              )}
            </button>
          </div>
        </div>

        {/* Main title — Orbitron font, the EtherDex brand look */}
        <div className="relative max-w-7xl mx-auto px-6 pb-14 pt-10 text-center">
          <h1
            className="text-6xl md:text-7xl tracking-widest
                       drop-shadow-[0_2px_20px_rgba(250,204,21,0.3)]"
            style={{ fontFamily: 'var(--font-orbitron)', fontWeight: 900 }}
          >
            ETHER<span className="text-yellow-400">DEX</span>
          </h1>
          <p
            className="mt-5 text-sm md:text-base uppercase text-yellow-400/90"
            style={{
              fontFamily: 'var(--font-orbitron)',
              fontWeight: 700,
              letterSpacing: '0.4em',
            }}
          >
            The Collector&apos;s Index
          </p>
        </div>
      </div>

      {/* BODY */}
      <div className="flex-1 max-w-7xl mx-auto w-full px-6 py-8">

        {/* GAME SWITCHER */}
        <div className="flex flex-wrap gap-2 mb-5">
          {games.map((g) => (
            <button
              key={g.key}
              onClick={() => switchGame(g.key)}
              className={
                'px-5 py-2.5 rounded-full text-sm font-semibold border transition-colors ' +
                (activeGame === g.key
                  ? 'bg-yellow-400 text-black border-yellow-400'
                  : 'bg-zinc-900 text-zinc-300 border-zinc-800 hover:border-yellow-500 hover:text-yellow-400')
              }
            >
              {g.name}
            </button>
          ))}
        </div>

        {/* SEARCH + AUTOCOMPLETE */}
        <div className="flex gap-3 mb-6">
          <div className="flex-1 relative">
            <input
              type="text"
              placeholder={gameUI(activeGame).placeholder}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setShowSuggestions(true);
              }}
              onFocus={() => setShowSuggestions(true)}
              onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
              onKeyDown={(e) => e.key === 'Enter' && runSearch()}
              className="w-full p-5 text-lg rounded-xl bg-zinc-900 border border-zinc-800
                         text-white placeholder-zinc-600
                         focus:outline-none focus:border-yellow-500
                         focus:shadow-[0_0_0_4px_rgba(250,204,21,0.1)]
                         transition-all"
            />
            {showSuggestions && suggestions.length > 0 && (
              <ul className="absolute z-20 left-0 right-0 mt-2 bg-zinc-900
                             border border-zinc-800 rounded-xl overflow-hidden shadow-xl">
                {suggestions.map((name) => (
                  <li key={name}>
                    <button
                      onMouseDown={() => runSearch(name)}
                      className="w-full text-left px-5 py-3 text-zinc-200
                                 hover:bg-zinc-800 hover:text-yellow-400 transition-colors"
                    >
                      {name}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <button
            onClick={() => runSearch()}
            className="bg-yellow-400 hover:bg-yellow-300 text-black font-semibold
                       px-10 rounded-xl transition-colors"
          >
            Search
          </button>
        </div>

        {/* FILTER BAR */}
        <div className="flex flex-wrap gap-3 mb-8">
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className={controlClass}>
            <option value="">Sort By</option>
            <option value="price_desc">Price: High to Low</option>
            <option value="price_asc">Price: Low to High</option>
            <option value="name_asc">Name: A to Z</option>
            <option value="name_desc">Name: Z to A</option>
          </select>

          <select value={rarityFilter} onChange={(e) => setRarityFilter(e.target.value)} className={controlClass}>
            <option value="">All Rarities</option>
            {gameUI(activeGame).rarities.map((r) => (
              <option key={r} value={r.toLowerCase()}>
                {r}
              </option>
            ))}
          </select>

          <select
            value={setFilter}
            onChange={(e) => {
              setSetFilter(e.target.value);
              setVisibleCount(CARDS_PER_PAGE);
            }}
            className={controlClass}
            disabled={availableSets.length === 0}
          >
            <option value="">All Sets</option>
            {availableSets.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>

          <input
            type="number" placeholder="Min Price" value={minPrice}
            onChange={(e) => setMinPrice(e.target.value)}
            className={`${controlClass} w-32`}
          />
          <input
            type="number" placeholder="Max Price" value={maxPrice}
            onChange={(e) => setMaxPrice(e.target.value)}
            className={`${controlClass} w-32`}
          />
        </div>

        {/* SKELETON LOADING */}
        {loading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="bg-zinc-900 rounded-2xl p-4 border border-zinc-800 animate-pulse">
                <div className="w-full aspect-[3/4] bg-zinc-800 rounded-xl mb-4" />
                <div className="h-5 bg-zinc-800 rounded w-3/4 mb-2" />
                <div className="h-4 bg-zinc-800 rounded w-1/2 mb-2" />
                <div className="h-6 bg-zinc-800 rounded w-1/3 mt-4" />
              </div>
            ))}
          </div>
        )}

        {error && <p className="text-center text-red-400 text-lg my-12">{error}</p>}

        {!loading && !error && filteredCards.length === 0 && searchTerm && (
          <p className="text-center text-zinc-500 text-lg my-12">
            No cards found. Try another name.
          </p>
        )}

        {/* EMPTY STATE */}
        {!loading && !error && !searchTerm && (
          <div className="text-center py-16">
            <div className="mx-auto mb-6 w-16 h-16 rounded-full border-4 border-yellow-400/30
                            flex items-center justify-center">
              <div className="w-6 h-6 rounded-full bg-yellow-400/40" />
            </div>

            <h2 className="text-2xl md:text-3xl font-semibold mb-2">
              Track what your cards are worth
            </h2>
            <p className="text-zinc-500 mb-10 max-w-xl mx-auto">
              Search any card to see live prices, rarities, and sets — then build
              your collection and watch its value over time.
            </p>

            {/* How it works — 3 quick steps */}
            <div className="flex flex-col sm:flex-row justify-center gap-4 mb-10 max-w-2xl mx-auto">
              {[
                { icon: '🔍', title: 'Search', text: 'Find any card by name' },
                { icon: '📊', title: 'Track prices', text: 'See market value & history' },
                { icon: '♥', title: 'Build your collection', text: 'Save cards to your watchlist' },
              ].map((step) => (
                <div
                  key={step.title}
                  className="flex-1 bg-zinc-900 border border-zinc-800 rounded-xl p-4"
                >
                  <div className="text-2xl mb-2">{step.icon}</div>
                  <p className="font-semibold text-zinc-200">{step.title}</p>
                  <p className="text-zinc-500 text-sm">{step.text}</p>
                </div>
              ))}
            </div>

            {/* Account nudge — only for signed-out visitors */}
            {!isSignedIn && (
              <p className="text-zinc-500 text-sm mb-10">
                <span className="text-yellow-400">Sign in</span> (top-right) to
                build your collection and access it from any device.
              </p>
            )}

            <p className="text-zinc-500 mb-4">Try one of these:</p>
            <div className="flex flex-wrap justify-center gap-3">
              {gameUI(activeGame).examples.map((name) => (
                <button
                  key={name}
                  onClick={() => runSearch(name)}
                  className="px-5 py-2.5 rounded-full bg-zinc-900 border border-zinc-800
                             text-zinc-300 hover:border-yellow-500 hover:text-yellow-400
                             transition-colors"
                >
                  {name}
                </button>
              ))}
            </div>
          </div>
        )}

        {!loading && !error && filteredCards.length > 0 && (
          <p className="text-zinc-500 text-sm mb-5">
            Showing {visibleCards.length} of {filteredCards.length} cards
          </p>
        )}

        {/* CARD GRID */}
        {!loading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
            {visibleCards.map((card) => {
              const watched = isWatched(card.card_id);
              return (
                <div
                  key={card.card_id}
                  onClick={() => setSelectedCard(card)}
                  className="group bg-zinc-900 rounded-2xl p-4 border border-zinc-800
                             hover:border-yellow-500/60 hover:-translate-y-1
                             hover:shadow-[0_8px_30px_rgba(250,204,21,0.12)]
                             transition-all duration-200 animate-[fadeIn_0.3s_ease-out]
                             cursor-pointer"
                >
                  <div className="overflow-hidden rounded-xl mb-4">
                    <img
                      src={card.large_image}
                      alt={card.pokemon_name}
                      loading="lazy"
                      className="w-full group-hover:scale-[1.03] transition-transform duration-200"
                    />
                  </div>

                  <h2 className="text-lg font-semibold mb-2 truncate">{card.pokemon_name}</h2>
                  <p className="text-zinc-500 text-sm truncate">{card.set_name || 'Unknown set'}</p>

                  <div className="flex items-center justify-between mt-3">
                    <span className="text-xs px-2 py-1 rounded-md bg-zinc-800 text-zinc-300 border border-zinc-700">
                      {card.rarity || 'Unknown'}
                    </span>
                    {card.market_price && card.market_price > 0 ? (
                      <span className="text-yellow-400 text-xl font-bold">
                        ${Number(card.market_price).toFixed(2)}
                      </span>
                    ) : (
                      <span className="text-zinc-600 text-sm">No price</span>
                    )}
                  </div>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleWatch(card);
                    }}
                    className={
                      'w-full mt-3 py-2 rounded-lg text-sm font-medium border transition-colors ' +
                      (watched
                        ? 'bg-yellow-400 text-black border-yellow-400 hover:bg-yellow-300'
                        : 'bg-transparent text-zinc-300 border-zinc-700 hover:border-yellow-500 hover:text-yellow-400')
                    }
                  >
                    {watched ? '♥ In Watchlist' : '♡ Add to Watchlist'}
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {/* LOAD MORE */}
        {!loading && hasMore && (
          <div className="flex justify-center mt-10">
            <button
              onClick={() => setVisibleCount((c) => c + CARDS_PER_PAGE)}
              className="bg-zinc-900 hover:bg-zinc-800 text-zinc-200 font-medium
                         px-10 py-3.5 rounded-xl border border-zinc-800
                         hover:border-zinc-700 transition-colors"
            >
              Load More
            </button>
          </div>
        )}
      </div>

      {/* FOOTER */}
      <footer className="border-t border-zinc-900 mt-8">
        <div className="max-w-7xl mx-auto px-6 py-7 flex items-center justify-center gap-3">
          <div className="group relative z-10 w-12 h-12 rounded-xl bg-black
                          border border-zinc-800 flex items-center justify-center
                          overflow-hidden cursor-pointer
                          hover:scale-[2.4] hover:border-yellow-500
                          transition-transform duration-300">
            <img
              src="/AbleVLabs.png"
              alt="AbleVLabs"
              className="w-full h-full object-contain"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = 'none';
              }}
            />
          </div>
          <div className="text-left">
            <p className="text-zinc-400 text-sm font-medium">Powered by AbleVLabs</p>
            <p className="text-zinc-600 text-xs">Research. Innovate. Create.</p>
          </div>
        </div>
      </footer>

      {/* WATCHLIST SLIDE-OUT PANEL */}
      <div
        onClick={() => setPanelOpen(false)}
        className={
          'fixed inset-0 bg-black/60 z-40 transition-opacity duration-300 ' +
          (panelOpen ? 'opacity-100' : 'opacity-0 pointer-events-none')
        }
      />

      <aside
        className={
          'fixed top-0 right-0 h-full w-full max-w-md bg-zinc-950 border-l border-zinc-800 ' +
          'z-50 flex flex-col transition-transform duration-300 ' +
          (panelOpen ? 'translate-x-0' : 'translate-x-full')
        }
      >
        <div className="flex items-center justify-between px-6 py-5 border-b border-zinc-800">
          <h2 className="text-xl font-bold">
            Watchlist <span className="text-zinc-500 text-base">({watchlist.length})</span>
          </h2>
          <button
            onClick={() => setPanelOpen(false)}
            className="text-zinc-400 hover:text-white text-2xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {!isSignedIn ? (
            <p className="text-zinc-500 text-center mt-12">
              Sign in (top-right) to save cards to your watchlist
              and access them from any device.
            </p>
          ) : watchlistLoading ? (
            <p className="text-zinc-500 text-center mt-12">Loading your watchlist...</p>
          ) : watchlist.length === 0 ? (
            <p className="text-zinc-500 text-center mt-12">
              Your watchlist is empty. Add cards with the
              <span className="text-yellow-400"> ♡ </span> button.
            </p>
          ) : (
            <div>
              {/* Alerts summary — only shown when at least one target is reached */}
              {alertsHit > 0 && (
                <div className="mb-4 bg-green-400/10 border border-green-400/30 rounded-xl p-3">
                  <p className="text-green-400 text-sm font-semibold">
                    🎯 {alertsHit} price {alertsHit === 1 ? 'alert' : 'alerts'} reached
                  </p>
                  <p className="text-zinc-400 text-xs mt-1">
                    Cards marked below have hit your target price.
                  </p>
                </div>
              )}

              {/* Condition guide — expandable reference */}
              <button
                onClick={() => setConditionGuideOpen((o) => !o)}
                className="text-yellow-400/90 text-xs mb-3 hover:text-yellow-400 transition-colors"
              >
                {conditionGuideOpen ? '▾' : '▸'} What do the conditions mean?
              </button>
              {conditionGuideOpen && (
                <div className="mb-4 bg-zinc-900 border border-zinc-800 rounded-xl p-3 flex flex-col gap-2">
                  {CONDITIONS.map((c) => (
                    <div key={c.name}>
                      <p className="text-zinc-200 text-sm font-medium">{c.name}</p>
                      <p className="text-zinc-500 text-xs">{c.desc}</p>
                    </div>
                  ))}
                </div>
              )}

              {/* Watchlist cards */}
              <div className="flex flex-col gap-3">
                {watchlist.map((card) => {
                  const quantity = card.quantity ?? 1;
                  const reached = targetReached(card);
                  return (
                    <div
                      key={card.card_id}
                      className={
                        'bg-zinc-900 rounded-xl p-3 border transition-colors ' +
                        (reached ? 'border-green-400/50' : 'border-zinc-800')
                      }
                    >
                      <div className="flex gap-3">
                        <img
                          src={card.small_image}
                          alt={card.pokemon_name}
                          className="w-16 rounded-md flex-shrink-0"
                        />
                        <div className="flex-1 min-w-0">
                          <p className="font-semibold truncate">{card.pokemon_name}</p>
                          <p className="text-zinc-500 text-xs truncate">
                            <span className="text-zinc-600 font-semibold mr-1">
                              {gameUI(card.game || 'pokemon').tag}
                            </span>
                            {card.set_name || 'Unknown set'}
                          </p>
                          <p className="text-yellow-400 font-bold mt-1">
                            {card.market_price && card.market_price > 0
                              ? `$${Number(card.market_price).toFixed(2)}`
                              : 'No price'}
                          </p>
                          {/* Line total — shown when you own more than one */}
                          {quantity !== 1 && card.market_price > 0 && (
                            <p className="text-zinc-500 text-xs">
                              × {quantity} = $
                              {(card.market_price * quantity).toFixed(2)}
                            </p>
                          )}
                        </div>
                        <button
                          onClick={() => toggleWatch(card)}
                          className="text-zinc-500 hover:text-red-400 text-sm self-start"
                        >
                          Remove
                        </button>
                      </div>

                      {/* Condition selector */}
                      <div className="mt-3">
                        <label className="text-zinc-500 text-xs">Card condition</label>
                        <select
                          value={card.condition || 'Near Mint'}
                          onChange={(e) => changeCondition(card.card_id, e.target.value)}
                          className="w-full mt-1 p-2 rounded-lg bg-zinc-950 border border-zinc-800
                                     text-zinc-200 text-sm
                                     focus:outline-none focus:border-yellow-500 transition-colors"
                        >
                          {CONDITIONS.map((c) => (
                            <option key={c.name} value={c.name}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      </div>

                      {/* Quantity stepper */}
                      <div className="mt-3 flex items-center justify-between">
                        <label className="text-zinc-500 text-xs">Quantity owned</label>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() =>
                              changeQuantity(card.card_id, quantity - 1)
                            }
                            disabled={quantity <= 0}
                            aria-label="Decrease quantity"
                            className="w-8 h-8 rounded-lg bg-zinc-950 border border-zinc-800
                                       text-zinc-200 text-lg leading-none
                                       flex items-center justify-center
                                       hover:border-yellow-500 hover:text-yellow-400
                                       transition-colors
                                       disabled:opacity-40 disabled:cursor-not-allowed
                                       disabled:hover:border-zinc-800 disabled:hover:text-zinc-200"
                          >
                            −
                          </button>
                          <span className="w-8 text-center font-semibold text-zinc-200">
                            {quantity}
                          </span>
                          <button
                            onClick={() =>
                              changeQuantity(card.card_id, quantity + 1)
                            }
                            aria-label="Increase quantity"
                            className="w-8 h-8 rounded-lg bg-zinc-950 border border-zinc-800
                                       text-zinc-200 text-lg leading-none
                                       flex items-center justify-center
                                       hover:border-yellow-500 hover:text-yellow-400
                                       transition-colors"
                          >
                            +
                          </button>
                        </div>
                      </div>

                      {/* Price alert target */}
                      <div className="mt-3">
                        <label className="text-zinc-500 text-xs">
                          Price alert — flag when price reaches
                        </label>
                        <div className="flex gap-2 mt-1">
                          <div className="relative flex-1">
                            <span className="absolute left-3 top-1/2 -translate-y-1/2
                                             text-zinc-500 text-sm pointer-events-none">
                              $
                            </span>
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              placeholder="No alert set"
                              value={targetInputValue(card)}
                              onChange={(e) =>
                                setTargetDrafts((prev) => ({
                                  ...prev,
                                  [card.card_id]: e.target.value,
                                }))
                              }
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') submitTargetDraft(card);
                              }}
                              className="w-full pl-7 pr-2 py-2 rounded-lg bg-zinc-950
                                         border border-zinc-800 text-zinc-200 text-sm
                                         focus:outline-none focus:border-yellow-500
                                         transition-colors"
                            />
                          </div>

                          {/* Save appears only when the box differs from what's saved */}
                          {targetIsDirty(card) && (
                            <button
                              onClick={() => submitTargetDraft(card)}
                              className="px-3 py-2 rounded-lg bg-yellow-400 hover:bg-yellow-300
                                         text-black text-sm font-semibold transition-colors"
                            >
                              Save
                            </button>
                          )}

                          {/* Clear appears only when an alert is saved */}
                          {card.target_price != null && !targetIsDirty(card) && (
                            <button
                              onClick={() => saveTarget(card.card_id, null)}
                              className="px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800
                                         text-zinc-400 text-sm
                                         hover:border-red-400 hover:text-red-400
                                         transition-colors"
                            >
                              Clear
                            </button>
                          )}
                        </div>

                        {/* Alert status line */}
                        {card.target_price != null && card.market_price > 0 && (
                          reached ? (
                            <p className="mt-2 text-xs font-semibold text-green-400">
                              🎯 Target reached — now $
                              {Number(card.market_price).toFixed(2)}, target $
                              {Number(card.target_price).toFixed(2)}
                            </p>
                          ) : (
                            <p className="mt-2 text-xs text-zinc-500">
                              ${(card.target_price - card.market_price).toFixed(2)} below
                              your ${Number(card.target_price).toFixed(2)} target
                            </p>
                          )
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {isSignedIn && watchlist.length > 0 && (
          <div className="border-t border-zinc-800 px-6 py-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-zinc-400">Total value</span>
              <span className="text-yellow-400 text-xl font-bold">
                ${watchlistTotal.toFixed(2)}
              </span>
            </div>
            <button
              onClick={exportToExcel}
              className="w-full py-3 rounded-lg bg-yellow-400 hover:bg-yellow-300
                         text-black font-semibold transition-colors"
            >
              ⤓ Export to Spreadsheet
            </button>
          </div>
        )}
      </aside>

      {/* CARD DETAIL MODAL */}
      {selectedCard && (
        <div
          onClick={() => setSelectedCard(null)}
          className="fixed inset-0 z-[60] bg-black/80 flex items-center justify-center
                     p-4 animate-[fadeIn_0.2s_ease-out]"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="bg-zinc-900 border border-zinc-800 rounded-2xl
                       max-w-3xl w-full max-h-[90vh] overflow-y-auto
                       flex flex-col md:flex-row gap-6 p-6 relative"
          >
            <button
              onClick={() => setSelectedCard(null)}
              className="absolute top-4 right-4 text-zinc-400 hover:text-white
                         text-3xl leading-none z-10"
            >
              ×
            </button>

            <div className="flex-shrink-0 md:w-1/2">
              <img
                src={selectedCard.large_image}
                alt={selectedCard.pokemon_name}
                className="w-full rounded-xl"
              />
            </div>

            <div className="flex-1 flex flex-col">
              <h2 className="text-3xl font-bold mb-1">
                {selectedCard.pokemon_name}
              </h2>
              <p className="text-zinc-500 mb-6">
                {selectedCard.set_name || 'Unknown set'}
              </p>

              <div className="flex flex-col gap-3 mb-6">
                <div className="flex justify-between border-b border-zinc-800 pb-2">
                  <span className="text-zinc-500">Rarity</span>
                  <span className="text-zinc-200">
                    {selectedCard.rarity || 'Unknown'}
                  </span>
                </div>
                <div className="flex justify-between border-b border-zinc-800 pb-2">
                  <span className="text-zinc-500">Set</span>
                  <span className="text-zinc-200">
                    {selectedCard.set_name || 'Unknown'}
                  </span>
                </div>
                <div className="flex justify-between border-b border-zinc-800 pb-2">
                  <span className="text-zinc-500">Market Price</span>
                  <div className="text-right">
                    <span className="text-yellow-400 text-2xl font-bold">
                      {selectedCard.market_price && selectedCard.market_price > 0
                        ? `$${Number(selectedCard.market_price).toFixed(2)}`
                        : 'No price'}
                    </span>
                    <p className="text-zinc-500 text-xs mt-1">
                      Price from {gameUI(selectedCard.game || 'pokemon').priceSource}
                      {' · updated '}
                      {formatFreshness(selectedCard.last_updated)}
                    </p>
                  </div>
                </div>
              </div>

              {/* Price history chart */}
              <div className="mb-6">
                <h3 className="text-zinc-300 text-sm font-semibold mb-2">
                  Price History
                </h3>
                <PriceHistoryChart cardId={selectedCard.card_id} />
              </div>

              <button
                onClick={() => toggleWatch(selectedCard)}
                className={
                  'mt-auto w-full py-3 rounded-lg text-sm font-medium border transition-colors ' +
                  (isWatched(selectedCard.card_id)
                    ? 'bg-yellow-400 text-black border-yellow-400 hover:bg-yellow-300'
                    : 'bg-transparent text-zinc-300 border-zinc-700 hover:border-yellow-500 hover:text-yellow-400')
                }
              >
                {isWatched(selectedCard.card_id)
                  ? '♥ In Watchlist'
                  : '♡ Add to Watchlist'}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}