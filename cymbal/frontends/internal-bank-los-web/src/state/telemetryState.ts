import type { TelemetryStreamEvent } from '../api/types';

export interface LosTelemetryState {
  connected: boolean;
  reconnectCount: number;
  events: TelemetryStreamEvent[];
  seenIds: Record<string, boolean>;
}

export type LosTelemetryAction =
  | { type: 'CONNECTED' }
  | { type: 'DISCONNECTED' }
  | { type: 'EVENT_RECEIVED'; event: TelemetryStreamEvent }
  | { type: 'CLEAR_EVENTS' };

export const initialLosTelemetryState: LosTelemetryState = {
  connected: false,
  reconnectCount: 0,
  events: [],
  seenIds: {},
};

export function losTelemetryReducer(
  state: LosTelemetryState,
  action: LosTelemetryAction
): LosTelemetryState {
  switch (action.type) {
    case 'CONNECTED':
      return {
        ...state,
        connected: true,
      };
    case 'DISCONNECTED':
      return {
        ...state,
        connected: false,
        reconnectCount: state.reconnectCount + 1,
      };
    case 'EVENT_RECEIVED': {
      const ev = action.event;
      if (ev.event_type === 'heartbeat') {
        return { ...state, connected: true };
      }
      if (state.seenIds[ev.event_id]) {
        return state;
      }
      const nextEvents = [ev, ...state.events].slice(0, 60);
      return {
        ...state,
        connected: true,
        events: nextEvents,
        seenIds: {
          ...state.seenIds,
          [ev.event_id]: true,
        },
      };
    }
    case 'CLEAR_EVENTS':
      return {
        ...state,
        events: [],
        seenIds: {},
      };
    default:
      return state;
  }
}
