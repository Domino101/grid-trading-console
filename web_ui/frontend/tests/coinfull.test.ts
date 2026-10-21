import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  compareSizing,
  computeManualSizing,
  computePositionStats,
  computeScheduleSizing,
  formatPrice,
  formatQty,
  formatScore,
  formatTaipei,
  formatUsd,
  isLiveReal,
  isManagedPosition,
  modeCopy,
  relativeAge,
  scoreTone,
  splitPositions,
} from '../src/components/coinfull/format.ts';
import type { CoinFullPrimePosition } from '../src/services/api.ts';

const position = (overrides: Partial<CoinFullPrimePosition>): CoinFullPrimePosition => ({
  symbol: 'BTCUSDT',
  raw_symbol: 'BTCUSDT',
  side: 'long',
  size: 1,
  signed_size: 1,
  entry_price: 100,
  mark_price: 110,
  unrealized_pnl: 10,
  notional_usdt: 110,
  ...overrides,
});

test('Taipei timestamps do not depend on the browser locale', () => {
  assert.match(formatTaipei('2026-10-21T13:23:56Z'), /2026\/10\/21/);
  assert.match(formatTaipei('2026-10-21T13:23:56Z'), /21:23/);
  assert.equal(formatTaipei(null), '—');
  assert.equal(formatTaipei('not-a-date'), 'not-a-date');
});

test('price and quantity formatting adapt to asset magnitude', () => {
  assert.equal(formatPrice(83_743.6), '$83,743.60');
  assert.equal(formatPrice(1.23456), '$1.2346');
  assert.equal(formatPrice(0.00161), '$0.00161');
  assert.equal(formatPrice(0), '—');
  assert.equal(formatQty(23.32), '23.32');
  assert.equal(formatQty(0.0001234), '0.000123');
  assert.equal(formatScore(1.2432), '+1.2432');
  assert.equal(formatScore(-0.3609), '-0.3609');
  assert.equal(formatUsd(null), '—');
});

test('relative age never reports negative time', () => {
  const now = Date.parse('2026-10-21T13:00:00Z');
  assert.equal(relativeAge('2026-10-21T12:59:30Z', now), '剛剛');
  assert.equal(relativeAge('2026-10-21T12:30:00Z', now), '30 分鐘前');
  assert.equal(relativeAge('2026-10-21T10:00:00Z', now), '3 小時前');
  assert.equal(relativeAge('2026-10-21T14:00:00Z', now), '剛剛');
});

test('only CoinFull-owned positions count as managed', () => {
  assert.equal(isManagedPosition(position({ role: 'coinfull_prime' })), true);
  assert.equal(isManagedPosition(position({ role: 'external_bot' })), false);
  assert.equal(isManagedPosition(position({ role: 'unmanaged' })), false);
  assert.equal(isManagedPosition(position({ role: undefined, is_managed: true, is_protected: true })), false);
  assert.equal(isManagedPosition(position({ role: undefined, is_managed: true })), true);

  const split = splitPositions([
    position({ role: 'coinfull_prime', side: 'long' }),
    position({ role: 'external_bot', side: 'long', notional_usdt: 2497.11, unrealized_pnl: 82.2 }),
    position({ role: 'external_bot', side: 'short', notional_usdt: 100, unrealized_pnl: -1 }),
  ]);
  assert.equal(split.managed.length, 1);
  assert.equal(split.external.length, 2);
  assert.equal(computePositionStats(split.managed).totalPnl, 10);
  assert.equal(computePositionStats(split.external).totalPnl, 81.2);
  assert.equal(computePositionStats(split.external).netExposure, 2397.11);
});

test('fixed sizing is blocked when margin exceeds available balance', () => {
  const manual = computeManualSizing({
    mode: 'fixed',
    capital: 1000,
    balancePct: 50,
    leverage: 1,
    balanceUsdt: 764.97,
    targetCount: 5,
  });
  assert.equal(manual.affordable, false);
  assert.ok(Math.abs(manual.shortfall - 235.03) < 0.001);
  assert.equal(manual.grossExposure, 1000);
  assert.equal(manual.perSymbolNotional, 100);
});

test('percent sizing uses the live balance and stays affordable', () => {
  const manual = computeManualSizing({
    mode: 'percent',
    capital: 1000,
    balancePct: 50,
    leverage: 2,
    balanceUsdt: 764.97,
    targetCount: 5,
  });
  assert.equal(manual.affordable, true);
  assert.equal(manual.capital, 382.485);
  assert.equal(manual.grossExposure, 764.97);
  assert.equal(manual.perSymbolNotional, 76.497);
});

test('schedule sizing understands percent and fixed configs', () => {
  const percent = computeScheduleSizing({
    capital: null,
    balancePct: 50,
    leverage: 1,
    targetCount: 5,
    balanceUsdt: 764.97,
  });
  assert.equal(percent.mode, 'percent');
  assert.equal(percent.perSymbolNotional, 38.2485);

  const fixed = computeScheduleSizing({
    capital: 500,
    balancePct: null,
    leverage: 2,
    targetCount: 5,
    balanceUsdt: 764.97,
  });
  assert.equal(fixed.mode, 'fixed');
  assert.equal(fixed.grossExposure, 1000);
  assert.equal(fixed.perSymbolNotional, 100);

  const unknown = computeScheduleSizing({
    capital: null,
    balancePct: null,
    leverage: 1,
    targetCount: 5,
    balanceUsdt: 764.97,
  });
  assert.equal(unknown.mode, 'unknown');
});

test('cross-mode sizing always reports a mismatch', () => {
  const manual = computeManualSizing({
    mode: 'fixed',
    capital: 1000,
    balancePct: 50,
    leverage: 1,
    balanceUsdt: 764.97,
    targetCount: 5,
  });
  const schedulePercent = computeScheduleSizing({
    capital: null,
    balancePct: 50,
    leverage: 1,
    targetCount: 5,
    balanceUsdt: 764.97,
  });
  const mismatch = compareSizing(manual, schedulePercent, 50, 1);
  assert.equal(mismatch.aligned, false);
  assert.match(mismatch.reasons.join(' '), /固定本金/);
  assert.match(mismatch.reasons.join(' '), /餘額百分比/);

  const scheduleFixed = computeScheduleSizing({
    capital: 1000,
    balancePct: null,
    leverage: 1,
    targetCount: 5,
    balanceUsdt: 764.97,
  });
  assert.equal(compareSizing(manual, scheduleFixed, 50, 1).aligned, true);
});

test('live mode detection and copy match the trading environment', () => {
  assert.equal(isLiveReal('live', false), true);
  assert.equal(isLiveReal('live', true), false);
  assert.equal(isLiveReal('testnet', false), false);
  assert.equal(modeCopy('live', false).tone, 'danger');
  assert.equal(modeCopy('live', true).tone, 'warning');
  assert.equal(modeCopy('paper', false).label, 'Paper · 本地模擬');
  assert.equal(scoreTone(1.2), 'strong-long');
  assert.equal(scoreTone(-1.2), 'strong-short');
  assert.equal(scoreTone(0), 'neutral');
});