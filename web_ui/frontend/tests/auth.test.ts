import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, detailMessage } from '../src/utils/auth.ts';

test('401 and 403 are unauthorized, other statuses are not', () => {
  assert.equal(new ApiError('denied', 401).isUnauthorized, true);
  assert.equal(new ApiError('forbidden', 403).isUnauthorized, true);
  assert.equal(new ApiError('missing', 404).isUnauthorized, false);
  assert.equal(new ApiError('conflict', 409).isUnauthorized, false);
});

test('detailMessage prefers FastAPI string or nested message', () => {
  assert.equal(detailMessage({ detail: 'LIVE 啟動需要 GRID_LIVE_TRADING_ENABLED=true' }, 'x'), 'LIVE 啟動需要 GRID_LIVE_TRADING_ENABLED=true');
  assert.equal(detailMessage({ detail: { message: '參數驗證失敗' } }, 'x'), '參數驗證失敗');
  assert.equal(detailMessage({}, 'fallback'), 'fallback');
});
