/**
 * WebSocket client for backend push events (/ws/grid-events, Phase 2 P2-4).
 *
 * Semantics (audit F6 fix):
 * - Events are *hints that data changed*: on `runtime` / `lifecycle` the UI
 *   refreshes its snapshot immediately instead of waiting for the next poll.
 * - The connection auto-reconnects with capped exponential backoff; while
 *   disconnected the regular polling path remains the source of truth, and
 *   after reconnect a refresh reconciles anything missed (facts rebuild).
 * - Same-origin relative URL only (no hardcoded localhost in the browser).
 */

import { getStoredToken } from '../utils/auth.ts';

export interface GridEvent {
  event?: string;
  config_path?: string;
  bot_id?: string;
  status?: string;
  [key: string]: unknown;
}

/** Events that should trigger an immediate dashboard snapshot refresh. */
export const isRefreshableEvent = (event: GridEvent | null | undefined): boolean => {
  if (!event || typeof event.event !== 'string') return false;
  return event.event === 'runtime' || event.event === 'lifecycle';
};

export interface GridEventsHandle {
  close: () => void;
  isConnected: () => boolean;
}

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;

export const connectGridEvents = (
  onEvent: (event: GridEvent) => void,
  onStateChange?: (connected: boolean) => void,
): GridEventsHandle => {
  if (typeof window === 'undefined') {
    return { close: () => undefined, isConnected: () => false };
  }

  let socket: WebSocket | null = null;
  let disposed = false;
  let connected = false;
  let attempt = 0;
  let timer: number | undefined;

  const scheduleReconnect = () => {
    if (disposed) return;
    const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** attempt);
    attempt += 1;
    timer = window.setTimeout(open, delay);
  };

  const open = () => {
    if (disposed) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = encodeURIComponent(getStoredToken());
    try {
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/grid-events?token=${token}`);
    } catch {
      scheduleReconnect();
      return;
    }
    socket.onopen = () => {
      attempt = 0;
      if (!connected) {
        connected = true;
        onStateChange?.(true);
      }
    };
    socket.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data) as GridEvent);
      } catch {
        // Malformed frame: ignore; the snapshot poll stays authoritative.
      }
    };
    socket.onclose = () => {
      if (connected) {
        connected = false;
        onStateChange?.(false);
      }
      scheduleReconnect();
    };
    socket.onerror = () => {
      try {
        socket?.close();
      } catch {
        // ignore
      }
    };
  };

  open();

  return {
    close: () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      try {
        socket?.close();
      } catch {
        // ignore
      }
    },
    isConnected: () => connected,
  };
};
