/**
 * useInjuryEvents
 * ===============
 * React hook that subscribes to the backend SSE stream at
 * GET /api/events/injuries and returns the current injury list.
 *
 * Usage:
 *   const { players, loading, error } = useInjuryEvents();
 */

import { useEffect, useRef, useState } from 'react';

export interface InjuryPlayer {
  player_name: string;
  status: string;
  detail?: string;
  report_date?: string;
  team?: string;
}

interface InjuryEventPayload {
  type: 'injury_update';
  timestamp: string;
  players: InjuryPlayer[];
  count: number;
  error?: string;
}

interface UseInjuryEventsResult {
  players: InjuryPlayer[];
  lastUpdated: string | null;
  loading: boolean;
  error: string | null;
}

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export function useInjuryEvents(): UseInjuryEventsResult {
  const [players, setPlayers] = useState<InjuryPlayer[]>([]);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelay = useRef(3000);

  const connect = () => {
    if (esRef.current) {
      esRef.current.close();
    }

    const url = `${API_BASE}/api/events/injuries`;
    const es = new EventSource(url);
    esRef.current = es;
    setLoading(true);
    setError(null);

    es.onopen = () => {
      setLoading(false);
      retryDelay.current = 3000; // reset back-off
    };

    es.onmessage = (event: MessageEvent) => {
      try {
        const data: InjuryEventPayload = JSON.parse(event.data as string);
        setPlayers(data.players ?? []);
        setLastUpdated(data.timestamp ?? null);
        if (data.error) {
          setError(data.error);
        } else {
          setError(null);
        }
        setLoading(false);
      } catch {
        setError('Failed to parse injury event');
      }
    };

    es.onerror = () => {
      setLoading(false);
      setError('Connection to injury stream lost — reconnecting…');
      es.close();
      esRef.current = null;
      // Exponential back-off up to 30 s
      const delay = Math.min(retryDelay.current, 30_000);
      retryDelay.current = delay * 2;
      retryRef.current = setTimeout(connect, delay);
    };
  };

  useEffect(() => {
    connect();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      esRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { players, lastUpdated, loading, error };
}
