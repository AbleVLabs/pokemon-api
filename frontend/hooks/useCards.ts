'use client';

import { useEffect, useState } from 'react';

interface Card {
  pokemon_name: string;
  card_id: string;
  set_name: string;
  card_number: string;
  rarity: string;
  market_price: number;
  small_image: string;
  large_image: string;
  game?: string;
  card_name?: string;
  last_updated?: string;
}

interface UseCardsProps {
  search: string;
  game: string;
  sortBy: string;
  rarityFilter: string;
  minPrice: string;
  maxPrice: string;
}

export default function useCards({
  search,
  game,
  sortBy,
  rarityFilter,
  minPrice,
  maxPrice,
}: UseCardsProps) {
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchCards = async () => {
      // Make sure search is always treated as a string
      const safeSearch = String(search || '').trim();

      // Don't search if empty
      if (!safeSearch) {
        setCards([]);
        return;
      }

      setLoading(true);
      setError('');

      try {
        const params = new URLSearchParams();

        params.append('name', safeSearch);
        params.append('game', game);

        if (sortBy) {
          params.append('sort', sortBy);
        }

        if (rarityFilter) {
          params.append('rarity', rarityFilter);
        }

        if (minPrice) {
          params.append('min_price', minPrice);
        }

        if (maxPrice) {
          params.append('max_price', maxPrice);
        }

        // Uses .env.local automatically
        const API_URL =
          process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

        const response = await fetch(
          `${API_URL}/search?${params.toString()}`
        );

        if (!response.ok) {
          throw new Error(`HTTP Error: ${response.status}`);
        }

        const data = await response.json();

        console.log('API RESPONSE:', data);

        setCards(data.cards || []);
      } catch (err) {
        console.error('Error fetching cards:', err);

        setError(
          'An error occurred while fetching cards. Please try again.'
        );

        setCards([]);
      } finally {
        setLoading(false);
      }
    };

    fetchCards();
  }, [search, game, sortBy, rarityFilter, minPrice, maxPrice]);

  return {
    cards,
    loading,
    error,
  };
}