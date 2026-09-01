import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseHash, formatHash } from '../src/utils/router.ts';

test('parseHash handles empty or default hashes', () => {
  assert.deepEqual(parseHash(''), { tab: 'dashboard', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#'), { tab: 'dashboard', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#/'), { tab: 'dashboard', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#/dashboard'), { tab: 'dashboard', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#dashboard'), { tab: 'dashboard', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#!/dashboard'), { tab: 'dashboard', gridId: null, dcaBotId: null });
});

test('parseHash handles top-level tabs correctly', () => {
  assert.deepEqual(parseHash('#/market'), { tab: 'market', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#market'), { tab: 'market', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#/configs'), { tab: 'configs', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#configs'), { tab: 'configs', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#/dca'), { tab: 'dca', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#dca'), { tab: 'dca', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#/dca-create'), { tab: 'dca-create', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#dca-create'), { tab: 'dca-create', gridId: null, dcaBotId: null });
});

test('parseHash handles Grid Detail with id param or path', () => {
  assert.deepEqual(parseHash('#/detail?id=grid_btc_usdt'), { tab: 'detail', gridId: 'grid_btc_usdt', dcaBotId: null });
  assert.deepEqual(parseHash('#detail?id=grid_123'), { tab: 'detail', gridId: 'grid_123', dcaBotId: null });
  assert.deepEqual(parseHash('#/detail/grid_456'), { tab: 'detail', gridId: 'grid_456', dcaBotId: null });
  assert.deepEqual(parseHash('#/grid/grid_789'), { tab: 'detail', gridId: 'grid_789', dcaBotId: null });
  // Missing id falls back to dashboard
  assert.deepEqual(parseHash('#/detail'), { tab: 'dashboard', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#/detail?id='), { tab: 'dashboard', gridId: null, dcaBotId: null });
});

test('parseHash handles DCA Detail with id param or path', () => {
  assert.deepEqual(parseHash('#/dca?id=dca_eth_usdt'), { tab: 'dca', gridId: null, dcaBotId: 'dca_eth_usdt' });
  assert.deepEqual(parseHash('#dca?id=dca_sol_usdt'), { tab: 'dca', gridId: null, dcaBotId: 'dca_sol_usdt' });
  assert.deepEqual(parseHash('#/dca/dca_bot_99'), { tab: 'dca', gridId: null, dcaBotId: 'dca_bot_99' });
});

test('parseHash falls back to dashboard on invalid hash', () => {
  assert.deepEqual(parseHash('#/unknown_route'), { tab: 'dashboard', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#/random/123/456'), { tab: 'dashboard', gridId: null, dcaBotId: null });
});

test('formatHash correctly formats route state into URL hash', () => {
  assert.equal(formatHash({ tab: 'dashboard' }), '#/dashboard');
  assert.equal(formatHash({ tab: 'market' }), '#/market');
  assert.equal(formatHash({ tab: 'configs' }), '#/configs');
  assert.equal(formatHash({ tab: 'dca' }), '#/dca');
  assert.equal(formatHash({ tab: 'dca', dcaBotId: 'dca_bot_1' }), '#/dca?id=dca_bot_1');
  assert.equal(formatHash({ tab: 'dca-create' }), '#/dca-create');
  assert.equal(formatHash({ tab: 'detail', gridId: 'grid_100' }), '#/detail?id=grid_100');
  assert.equal(formatHash({ tab: 'detail', gridId: null }), '#/dashboard');
});

test('parseHash handles grid-backtest tab', () => {
  assert.deepEqual(parseHash('#/grid-backtest'), { tab: 'grid-backtest', gridId: null, dcaBotId: null });
  assert.deepEqual(parseHash('#grid-backtest'), { tab: 'grid-backtest', gridId: null, dcaBotId: null });
  assert.equal(formatHash({ tab: 'grid-backtest' }), '#/grid-backtest');
});
