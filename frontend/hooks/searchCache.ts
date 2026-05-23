/**
 * searchCache.ts
 * --------------
 * A simple in-memory cache for search results.
 *
 * How it works:
 *   - This is a plain JavaScript Map declared at the module level (top of file).
 *   - It lives in memory for as long as the page is open.
 *   - A full page refresh clears it. Navigating between pages does NOT clear it.
 *   - Every component that imports this file shares the SAME cache instance.
 *
 * Why a module-level Map (not React state or Context)?
 *   - Doesn't trigger React re-renders when we write to it (cache writes should
 *     be invisible to the UI).
 *   - No provider boilerplate. Just import and use.
 *   - Same pattern used internally by SWR and React Query.
 *
 * Features:
 *   - Normalized keys (case-insensitive, whitespace-trimmed)
 *   - TTL (entries expire after 5 minutes — prices can change)
 *   - Size cap (oldest entry is evicted when we hit 50 entries)
 *
 * No React imports here on purpose. This is a pure utility.
 */

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

// 5 minutes in milliseconds. Prices change, so we don't want forever-stale data.
const TTL_MS = 5 * 60 * 1000;

// Cap the cache so it can't grow unbounded if a user does hundreds of searches.
const MAX_ENTRIES = 50;

// The actual cache. Declared once at module load.
// `unknown` because this cache could in theory hold different shapes; the
// consumer (useCards) knows what shape it's getting back.
const cache = new Map<string, CacheEntry<unknown>>();

/**
 * Build a normalized cache key from the search inputs.
 *
 * Normalization rules:
 *   - Lowercase everything (so "Pikachu" and "pikachu" hit the same entry)
 *   - Trim whitespace (so " pikachu " and "pikachu" match)
 *   - Join with a separator that can't appear in user input
 *
 * Example output:
 *   buildKey("Pikachu", "highest", "rare", "10", "100")
 *   → "pikachu|highest|rare|10|100"
 */
export function buildKey(
  search: string,
  sort: string,
  rarity: string,
  minPrice: string,
  maxPrice: string
): string {
  const normalize = (s: string) => s.trim().toLowerCase();
  return [
    normalize(search),
    normalize(sort),
    normalize(rarity),
    normalize(minPrice),
    normalize(maxPrice),
  ].join('|');
}

/**
 * Read from the cache. Returns the cached data, or `null` if:
 *   - the key has never been cached, OR
 *   - the cached entry has expired (older than TTL_MS)
 *
 * Expired entries are deleted on read (lazy cleanup — no background timer needed).
 */
export function getFromCache<T>(key: string): T | null {
  const entry = cache.get(key);

  if (!entry) {
    return null;
  }

  // Check expiry
  const age = Date.now() - entry.timestamp;
  if (age > TTL_MS) {
    cache.delete(key);
    return null;
  }

  return entry.data as T;
}

/**
 * Write to the cache.
 *
 * If the cache is full, we delete the oldest entry first.
 * Map preserves insertion order, so `cache.keys().next().value` gives us
 * the oldest key — a poor-man's LRU that works well enough for this use case.
 */
export function setInCache<T>(key: string, data: T): void {
  // Enforce the size cap
  if (cache.size >= MAX_ENTRIES) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey !== undefined) {
      cache.delete(oldestKey);
    }
  }

  cache.set(key, {
    data,
    timestamp: Date.now(),
  });
}