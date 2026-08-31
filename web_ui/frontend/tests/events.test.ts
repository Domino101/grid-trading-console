import assert from 'node:assert/strict';
import { test } from 'node:test';
import { connectGridEvents, isRefreshableEvent } from '../src/services/events.ts';

test('isRefreshableEvent accepts runtime/lifecycle and rejects noise', () => {
  assert.equal(isRefreshableEvent({ event: 'runtime' }), true);
  assert.equal(isRefreshableEvent({ event: 'lifecycle', status: 'running' }), true);
  assert.equal(isRefreshableEvent({ event: 'unknown-kind' }), false);
  assert.equal(isRefreshableEvent({}), false);
  assert.equal(isRefreshableEvent(null), false);
  assert.equal(isRefreshableEvent(undefined), false);
});

test('connectGridEvents is a no-op handle outside a browser (node)', () => {
  // Node test env: no window/WebSocket — the handle must still be safe.
  const handle = connectGridEvents(() => {
    assert.fail('no events should arrive without a browser socket');
  });
  assert.equal(handle.isConnected(), false);
  handle.close(); // must not throw
});
