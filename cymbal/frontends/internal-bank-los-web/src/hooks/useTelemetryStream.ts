import { useEffect, useReducer, useRef } from 'react';
import type { TelemetryStreamEvent } from '../api/types';
import {
  initialLosTelemetryState,
  losTelemetryReducer,
} from '../state/telemetryState';

export function useTelemetryStream(
  onEvent?: (event: TelemetryStreamEvent) => void
) {
  const [state, dispatch] = useReducer(
    losTelemetryReducer,
    initialLosTelemetryState
  );
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const loadInitialSnapshotViaFetch = async () => {
      try {
        const res = await fetch('/api/v1/telemetry/stream', {
          headers: { Accept: 'text/event-stream' },
        });
        if (!res.ok || cancelled) return;
        const text = await res.text();
        if (cancelled) return;
        dispatch({ type: 'CONNECTED' });
        const lines = text.split('\n');
        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith('data:')) {
            const rawJson = trimmed.replace(/^data:\s*/, '');
            try {
              const parsed = JSON.parse(rawJson) as TelemetryStreamEvent;
              dispatch({ type: 'EVENT_RECEIVED', event: parsed });
              onEventRef.current?.(parsed);
            } catch {
              // Ignore non-JSON frame
            }
          }
        }
      } catch {
        // Ignore snapshot fetch errors
      }
    };

    if (import.meta.env.VITE_ENABLE_MSW === 'true') {
      void loadInitialSnapshotViaFetch();
    }

    if (typeof window === 'undefined' || typeof EventSource === 'undefined') {
      return () => {
        cancelled = true;
      };
    }

    const connect = () => {
      if (cancelled) return;
      try {
        es = new EventSource('/api/v1/telemetry/stream');
        es.onopen = () => {
          if (cancelled) return;
          attempt = 0;
          dispatch({ type: 'CONNECTED' });
        };
        es.onmessage = (msg) => {
          if (cancelled || !msg.data) return;
          try {
            const parsed = JSON.parse(msg.data) as TelemetryStreamEvent;
            dispatch({ type: 'EVENT_RECEIVED', event: parsed });
            onEventRef.current?.(parsed);
          } catch {
            // Ignore malformed keepalive frames
          }
        };
        es.onerror = () => {
          es?.close();
          if (cancelled) return;
          if (import.meta.env.VITE_ENABLE_MSW === 'true') {
            dispatch({ type: 'CONNECTED' });
            return;
          }
          dispatch({ type: 'DISCONNECTED' });
          const delayMs = Math.min(1000 * Math.pow(2, attempt), 15000);
          attempt += 1;
          retryTimer = setTimeout(connect, delayMs);
        };
      } catch {
        dispatch({ type: 'DISCONNECTED' });
      }
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
  }, []);

  return {
    telemetry: state,
    pushLocalEvent: (event: TelemetryStreamEvent) => {
      dispatch({ type: 'EVENT_RECEIVED', event });
      onEventRef.current?.(event);
    },
  };
}
