'use client';

import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '@clerk/nextjs';
import Link from 'next/link';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

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
}

export default function Dashboard() {
  const { getToken, isSignedIn, isLoaded } = useAuth();

  const [watchlist, setWatchlist] = useState<WatchCard[]>([]);
  const [loading, setLoading] = useState(true);

  // Load the user's watchlist — the whole dashboard is built from it.
  useEffect(() => {
    if (!isLoaded) return;

    if (!isSignedIn) {
      setWatchlist([]);
      setLoading(false);
      return;
    }

    const load = async () => {
      setLoading(true);
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
        // network hiccup — leave as-is
      } finally {
        setLoading(false);
      }
    };

    load();
  }, [isLoaded, isSignedIn, getToken]);

  // Every stat below is calculated from the watchlist cards.
  const stats = useMemo(() => {
    const totalValue = watchlist.reduce(
      (sum, c) => sum + (c.market_price || 0) * (c.quantity ?? 1),
      0
    );

    const uniqueCards = watchlist.length;

    const totalItems = watchlist.reduce(
      (sum, c) => sum + (c.quantity ?? 1),
      0
    );

    // Most valuable single card (by its market price).
    let mostValuable: WatchCard | null = null;
    for (const c of watchlist) {
      if (
        !mostValuable ||
        (c.market_price || 0) > (mostValuable.market_price || 0)
      ) {
        mostValuable = c;
      }
    }

    // Top 6 cards by value, highest first.
    const topCards = [...watchlist]
      .sort((a, b) => (b.market_price || 0) - (a.market_price || 0))
      .slice(0, 6);

    // How many cards are in each condition.
    const conditionCounts: Record<string, number> = {};
    for (const c of watchlist) {
      const cond = c.condition || 'Near Mint';
      conditionCounts[cond] = (conditionCounts[cond] || 0) + 1;
    }

    return {
      totalValue,
      uniqueCards,
      totalItems,
      mostValuable,
      topCards,
      conditionCounts,
    };
  }, [watchlist]);

  return (
    <main className="min-h-screen bg-zinc-950 text-white">
      {/* TOP BAR */}
      <div className="border-b border-zinc-900">
        <div className="max-w-6xl mx-auto px-6 py-5 flex items-center justify-between">
          <Link
            href="/"
            className="text-zinc-400 hover:text-yellow-400 transition-colors"
          >
            ← Back to Search
          </Link>
          <span
            className="tracking-widest text-lg"
            style={{ fontFamily: 'var(--font-orbitron)', fontWeight: 900 }}
          >
            ETHER<span className="text-yellow-400">DEX</span>
          </span>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-10">
        <h1 className="text-3xl md:text-4xl font-bold mb-2">
          Collection Dashboard
        </h1>
        <p className="text-zinc-500 mb-10">
          An overview of everything in your watchlist.
        </p>

        {/* STATE: loading */}
        {(!isLoaded || loading) && (
          <p className="text-zinc-500">Loading your collection...</p>
        )}

        {/* STATE: signed out */}
        {isLoaded && !loading && !isSignedIn && (
          <div className="text-center py-16">
            <p className="text-zinc-400 text-lg mb-3">
              Sign in to see your collection dashboard.
            </p>
            <p className="text-zinc-500">
              Use the sign-in bar at the top of the page.
            </p>
          </div>
        )}

        {/* STATE: signed in, empty collection */}
        {isLoaded && !loading && isSignedIn && watchlist.length === 0 && (
          <div className="text-center py-16">
            <p className="text-zinc-400 text-lg mb-3">
              Your collection is empty.
            </p>
            <p className="text-zinc-500 mb-6">
              Add cards to your watchlist and they&apos;ll show up here.
            </p>
            <Link
              href="/"
              className="inline-block px-6 py-3 rounded-lg bg-yellow-400
                         text-black font-semibold hover:bg-yellow-300
                         transition-colors"
            >
              Search for cards
            </Link>
          </div>
        )}

        {/* STATE: signed in, has cards — the dashboard */}
        {isLoaded && !loading && isSignedIn && watchlist.length > 0 && (
          <div>
            {/* Total value hero */}
            <div
              className="bg-zinc-900 border border-zinc-800 rounded-2xl p-8 mb-6
                         shadow-[0_0_30px_rgba(250,204,21,0.08)]"
            >
              <p className="text-zinc-500 text-sm uppercase tracking-wide mb-2">
                Total Collection Value
              </p>
              <p className="text-5xl md:text-6xl font-bold text-yellow-400">
                ${stats.totalValue.toFixed(2)}
              </p>
            </div>

            {/* Stat cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-12">
              <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
                <p className="text-zinc-500 text-sm mb-1">Unique Cards</p>
                <p className="text-2xl font-bold">{stats.uniqueCards}</p>
              </div>
              <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
                <p className="text-zinc-500 text-sm mb-1">Total Items Owned</p>
                <p className="text-2xl font-bold">{stats.totalItems}</p>
              </div>
              <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
                <p className="text-zinc-500 text-sm mb-1">Most Valuable Card</p>
                <p className="text-lg font-bold truncate">
                  {stats.mostValuable?.pokemon_name || '—'}
                </p>
                {stats.mostValuable &&
                  stats.mostValuable.market_price > 0 && (
                    <p className="text-yellow-400 text-sm">
                      ${stats.mostValuable.market_price.toFixed(2)}
                    </p>
                  )}
              </div>
            </div>

            {/* Most valuable cards list */}
            <h2 className="text-xl font-semibold mb-4">Most Valuable Cards</h2>
            <div className="flex flex-col gap-3 mb-12">
              {stats.topCards.map((card) => {
                const qty = card.quantity ?? 1;
                return (
                  <div
                    key={card.card_id}
                    className="flex items-center gap-4 bg-zinc-900
                               border border-zinc-800 rounded-xl p-3"
                  >
                    <img
                      src={card.small_image}
                      alt={card.pokemon_name}
                      className="w-12 rounded-md flex-shrink-0"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="font-semibold truncate">
                        {card.pokemon_name}
                      </p>
                      <p className="text-zinc-500 text-xs truncate">
                        {card.set_name || 'Unknown set'} · Qty {qty}
                      </p>
                    </div>
                    <p className="text-yellow-400 font-bold flex-shrink-0">
                      {card.market_price > 0
                        ? `$${card.market_price.toFixed(2)}`
                        : 'No price'}
                    </p>
                  </div>
                );
              })}
            </div>

            {/* Collection by condition */}
            <h2 className="text-xl font-semibold mb-4">
              Collection by Condition
            </h2>
            <div
              className="bg-zinc-900 border border-zinc-800 rounded-xl p-5
                         flex flex-col gap-2"
            >
              {Object.entries(stats.conditionCounts).map(([cond, count]) => (
                <div key={cond} className="flex justify-between">
                  <span className="text-zinc-300">{cond}</span>
                  <span className="text-zinc-400">
                    {count} {count === 1 ? 'card' : 'cards'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}