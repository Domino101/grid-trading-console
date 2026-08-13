import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canAdvanceCreateStep, freshnessText, money, nextPollDelay, number } from '../src/utils/format.ts';

test('money and number keep unknown values as em dash', () => {
  assert.equal(money(null), '—');
  assert.equal(money(undefined), '—');
  assert.equal(number(null), '—');
  assert.match(money(12.5), /\$12\.50/);
});

test('freshness distinguishes unavailable from synced data', () => {
  assert.equal(freshnessText('unavailable', null), '尚未同步');
  assert.equal(freshnessText('stale', 1), '資料過期');
  assert.equal(freshnessText('live', null), '已同步');
});

test('create wizard only advances when the current step is valid', () => {
  const form = {
    symbol: 'BTC/USDT',
    exchange: 'binance',
    lower_price: 60000,
    upper_price: 70000,
    grid_interval: 100,
    order_amount: 0.001,
  };
  assert.equal(canAdvanceCreateStep(1, form, 'paper', '', false), true);
  assert.equal(canAdvanceCreateStep(1, { ...form, symbol: '' }, 'paper', '', false), false);
  assert.equal(canAdvanceCreateStep(2, form, 'paper', '', false), true);
  assert.equal(canAdvanceCreateStep(2, { ...form, lower_price: 80000 }, 'paper', '', false), false);
  assert.equal(canAdvanceCreateStep(2, form, 'paper', '', true), false);
  assert.equal(canAdvanceCreateStep(3, form, 'paper', '', false), true);
  assert.equal(canAdvanceCreateStep(3, form, 'live', 'yes', false), false);
  assert.equal(canAdvanceCreateStep(3, form, 'live', 'CONFIRM LIVE', false), true);
});

test('poll backoff grows exponentially and caps', () => {
  assert.equal(nextPollDelay(0), 15_000);
  assert.equal(nextPollDelay(1), 30_000);
  assert.equal(nextPollDelay(2), 60_000);
  assert.equal(nextPollDelay(8), 60_000);
});
