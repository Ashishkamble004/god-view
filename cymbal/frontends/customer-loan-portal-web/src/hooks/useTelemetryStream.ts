import { useEffect, useReducer, useRef } from 'react';
import { getTelemetryStreamUrl } from '../api/client';
import type { TelemetryStreamEvent } from '../api/types';
import {
  initialTelemetryState,
  telemetryReducer,
  type TelemetryState,
} from '../state/journeyState';

export function useTelemetryStream(
  journeyId: string | null,
  onEvent?: (event: TelemetryStreamEvent) => void
): TelemetryState {
  const [state, dispatch] = useReducer(telemetryReducer, initialTelemetryState);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!journeyId) {
      return;
    }

    const handleCustomTelemetry = (evt: Event) => {
      const customEvt = evt as CustomEvent<TelemetryStreamEvent>;
      const payload = customEvt.detail;
      if (!payload) return;
      if (payload.journey_id && payload.journey_id !== journeyId) {
        return;
      }
      dispatch({ type: 'EVENT_RECEIVED', event: payload });
      onEventRef.current?.(payload);
    };

    if (typeof window !== 'undefined') {
      window.addEventListener('cymbal:telemetry', handleCustomTelemetry);
    }

    if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
      return () => {
        if (typeof window !== 'undefined') {
          window.removeEventListener('cymbal:telemetry', handleCustomTelemetry);
        }
      };
    }

    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let unmounted = false;

    const connect = () => {
      if (unmounted) return;
      try {
        es = new window.EventSource(getTelemetryStreamUrl(journeyId));
        es.onopen = () => {
          if (unmounted) return;
          attempt = 0;
          dispatch({ type: 'CONNECTED' });
        };
        es.onmessage = (msg) => {
          if (unmounted || !msg.data) return;
          try {
            const parsed = JSON.parse(msg.data) as TelemetryStreamEvent;
            if (parsed.journey_id && parsed.journey_id !== journeyId) {
              return;
            }
            dispatch({ type: 'EVENT_RECEIVED', event: parsed });
            onEventRef.current?.(parsed);
          } catch {
            // Ignore malformed SSE lines
          }
        };
        es.onerror = () => {
          if (unmounted) return;
          es?.close();
          attempt += 1;
          dispatch({ type: 'DISCONNECTED', attempt });
          const delayMs = Math.min(15000, 1000 * Math.pow(2, Math.min(attempt, 4)));
          retryTimer = setTimeout(connect, delayMs);
        };
      } catch {
        attempt += 1;
        dispatch({ type: 'DISCONNECTED', attempt });
      }
    };

    connect();

    return () => {
      unmounted = true;
      if (typeof window !== 'undefined') {
        window.removeEventListener('cymbal:telemetry', handleCustomTelemetry);
      }
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
  }, [journeyId]);

  return state;
}
