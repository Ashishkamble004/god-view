import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from '../mocks/server';

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  readyState = 1;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private listeners: Record<string, Array<(ev: MessageEvent) => void>> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
    setTimeout(() => {
      if (this.onopen) {
        this.onopen(new Event('open'));
      }
    }, 0);
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void) {
    if (!this.listeners[type]) {
      this.listeners[type] = [];
    }
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: (ev: MessageEvent) => void) {
    if (!this.listeners[type]) return;
    this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
  }

  emit(data: unknown, type = 'message') {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    if (type === 'message' && this.onmessage) {
      this.onmessage(event);
    }
    (this.listeners[type] || []).forEach((l) => l(event));
  }

  close() {
    this.readyState = 2;
  }
}

Object.defineProperty(window, 'EventSource', {
  writable: true,
  value: MockEventSource,
});

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  MockEventSource.instances = [];
  window.location.hash = '';
});
afterAll(() => server.close());
