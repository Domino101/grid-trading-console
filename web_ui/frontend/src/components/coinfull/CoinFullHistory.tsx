import React, { useState } from 'react';
import { ChevronDown, ChevronRight, History, RefreshCw } from 'lucide-react';
import type { CoinFullPrimeController } from './useCoinFullPrime';
import { formatPrice, formatQty, formatTaipei, formatUsd } from './format';

interface Props {
  controller: CoinFullPrimeController;
}

export const CoinFullHistory: React.FC<Props> = ({ controller }) => {
  const {
    accounts,
    historyBatches,
    tradeFills,
    historyLoading,
    historyViewTab,
    setHistoryViewTab,
    includeSimulated,
    setIncludeSimulated,
    expandedBatchId,
    setExpandedBatchId,
    historyAccountFilter,
    setHistoryAccountFilter,
    actions,
  } = controller;
  const [visibleCount, setVisibleCount] = useState(8);
  const accountName = (id: string): string => accounts.find((account) => account.id === id)?.name ?? id;
  const batches = historyBatches.slice(0, visibleCount);

  return (
    <section className="cfp-panel cfp-history" aria-label="調倉歷史與成交紀錄">
      <header className="cfp-section-header cfp-history-header">
        <div className="cfp-section-title">
          <History size={18} aria-hidden="true" />
          <h3>調倉歷史與成交紀錄</h3>
          <span className="badge badge-neutral">
            {historyViewTab === 'batches' ? `${historyBatches.length} 次換倉` : `${tradeFills.length} 筆成交`}
          </span>
        </div>

        <div className="cfp-history-controls">
          <div className="cfp-tab-pills" role="tablist" aria-label="歷史檢視">
            <button
              type="button"
              role="tab"
              aria-selected={historyViewTab === 'batches'}
              className={historyViewTab === 'batches' ? 'is-active' : ''}
              onClick={() => setHistoryViewTab('batches')}
            >
              調倉批次
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={historyViewTab === 'fills'}
              className={historyViewTab === 'fills' ? 'is-active' : ''}
              onClick={() => setHistoryViewTab('fills')}
            >
              逐筆成交
            </button>
          </div>

          <label className="cfp-compact-field">
            <span className="sr-only">帳號篩選</span>
            <select value={historyAccountFilter} onChange={(event) => setHistoryAccountFilter(event.target.value)}>
              <option value="all">全部帳號</option>
              <option value="binance_live">Binance 實盤</option>
              <option value="binance_testnet">Binance 測試網</option>
              <option value="paper">Paper</option>
            </select>
          </label>

          <label className="cfp-check">
            <input
              type="checkbox"
              checked={includeSimulated}
              onChange={(event) => setIncludeSimulated(event.target.checked)}
            />
            <span>含模擬成交</span>
          </label>

          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => void actions.refreshHistory()}
            disabled={historyLoading}
          >
            <RefreshCw size={13} className={historyLoading ? 'spin' : ''} aria-hidden="true" />
            <span>{historyLoading ? '載入中…' : '刷新'}</span>
          </button>
        </div>
      </header>

      {historyViewTab === 'batches' ? (
        <div className="cfp-history-body">
          {historyBatches.length === 0 ? (
            <div className="cfp-empty"><strong>尚無換倉批次</strong></div>
          ) : (
            <>
              {batches.map((batch) => {
                const expanded = expandedBatchId === batch.id;
                const closed = batch.closed_symbols?.length ?? 0;
                const opened = batch.opened_symbols?.length ?? 0;
                return (
                  <article key={batch.id} className="cfp-batch">
                    <button
                      type="button"
                      className="cfp-batch-head"
                      aria-expanded={expanded}
                      onClick={() => setExpandedBatchId(expanded ? null : batch.id)}
                    >
                      <span className="cfp-batch-time">
                        {expanded ? <ChevronDown size={15} aria-hidden="true" /> : <ChevronRight size={15} aria-hidden="true" />}
                        {formatTaipei(batch.timestamp)}
                      </span>
                      <span className={`badge ${batch.dry_run ? 'badge-warning' : 'badge-success'}`}>
                        {batch.dry_run ? '模擬' : '真實發單'}
                      </span>
                      <span className="cfp-batch-account">{accountName(batch.account_id)}</span>
                      <span className="cfp-batch-meta">
                        {batch.orders_count || batch.orders?.length || 0} 筆
                        {closed > 0 && ` · 平倉 ${closed}`}
                        {opened > 0 && ` · 開倉 ${opened}`}
                      </span>
                      <span className="cfp-batch-capital">
                        {batch.capital ? `${formatUsd(batch.capital)} · ${batch.leverage || 1}x` : '—'}
                      </span>
                    </button>

                    {expanded && (
                      <div className="cfp-batch-detail">
                        {batch.orders && batch.orders.length > 0 ? (
                          <div className="cfp-table-wrap">
                            <table className="cfp-table is-compact">
                              <thead>
                                <tr>
                                  <th scope="col">標的</th>
                                  <th scope="col">方向</th>
                                  <th scope="col">操作</th>
                                  <th scope="col" className="is-num">數量</th>
                                  <th scope="col" className="is-num">價格</th>
                                  <th scope="col" className="is-num">名義</th>
                                  <th scope="col">原因</th>
                                  <th scope="col">狀態</th>
                                </tr>
                              </thead>
                              <tbody>
                                {batch.orders.map((order, index) => (
                                  <tr key={`${order.symbol}-${index}`}>
                                    <td className="is-mono">{order.symbol}</td>
                                    <td><span className={`badge ${order.side === 'buy' ? 'badge-long' : 'badge-short'}`}>{order.side.toUpperCase()}</span></td>
                                    <td>{order.reduce_only ? '平倉' : '開倉／調倉'}</td>
                                    <td className="is-num is-mono">{formatQty(order.amount)}</td>
                                    <td className="is-num is-mono">{formatPrice(order.price)}</td>
                                    <td className="is-num is-mono">{formatUsd(order.notional_usdt)}</td>
                                    <td className="cfp-muted">{order.reason}</td>
                                    <td>
                                      <span className={`badge ${order.status === 'filled' ? 'badge-success' : order.status === 'simulated' ? 'badge-neutral' : 'badge-danger'}`}>
                                        {order.status === 'filled' ? '已成交' : order.status === 'simulated' ? '模擬' : order.status}
                                      </span>
                                      {order.error && <span className="cfp-error-text">{order.error}</span>}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        ) : (
                          <p className="cfp-muted">此批次沒有訂單明細（早期紀錄）。</p>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
              {visibleCount < historyBatches.length && (
                <button type="button" className="btn btn-outline cfp-load-more" onClick={() => setVisibleCount((count) => count + 8)}>
                  載入更多（{historyBatches.length - visibleCount}）
                </button>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="cfp-table-wrap cfp-fills-wrap">
          {tradeFills.length === 0 ? (
            <div className="cfp-empty"><strong>尚無成交流水</strong></div>
          ) : (
            <table className="cfp-table">
              <thead>
                <tr>
                  <th scope="col">時間</th>
                  <th scope="col">帳號</th>
                  <th scope="col">標的</th>
                  <th scope="col">方向</th>
                  <th scope="col">角色</th>
                  <th scope="col" className="is-num">價格</th>
                  <th scope="col" className="is-num">數量</th>
                  <th scope="col" className="is-num">名義</th>
                  <th scope="col" className="is-num">手續費</th>
                  <th scope="col">模式</th>
                </tr>
              </thead>
              <tbody>
                {tradeFills.map((fill) => (
                  <tr key={fill.fill_id}>
                    <td className="is-mono cfp-muted">{formatTaipei(fill.time)}</td>
                    <td><span className="badge badge-neutral">{accountName(fill.account_id)}</span></td>
                    <td className="is-mono">{fill.symbol}</td>
                    <td>
                      <span className={`badge ${fill.side === 'buy' ? 'badge-long' : fill.side === 'funding' ? 'badge-neutral' : 'badge-short'}`}>
                        {fill.side === 'funding' ? '資金費率' : fill.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="cfp-muted">
                      {fill.role === 'funding' ? '費率 Carry' : fill.role.includes('exit') ? '平倉退出' : '建倉／加減'}
                    </td>
                    <td className="is-num is-mono">{fill.role === 'funding' ? '—' : formatPrice(fill.price)}</td>
                    <td className="is-num is-mono">{fill.role === 'funding' ? '—' : formatQty(fill.quantity)}</td>
                    <td className="is-num is-mono">{fill.role === 'funding' ? '—' : formatUsd(fill.notional_usdt)}</td>
                    <td className="is-num is-mono cfp-muted">{fill.fee > 0 ? `${fill.fee} ${fill.fee_currency || 'USDT'}` : '—'}</td>
                    <td>
                      <span className={`badge ${fill.is_simulated ? 'badge-warning' : 'badge-success'}`}>
                        {fill.is_simulated ? '模擬' : '實盤'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
};