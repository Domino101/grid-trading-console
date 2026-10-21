import React from 'react';
import type { RankedItem } from '../../services/api';
import { cleanText, formatPct, formatPrice, formatScore, formatUsd, formatZ, scoreTone } from './format';

interface Props {
  items: RankedItem[];
  isBenchmarkHedge: boolean;
  grossExposure: number;
  showDisabledFactors: boolean;
  universeSize: number;
  sizing?: { perSymbolNotional: number; shortSleeve: number; grossExposure: number };
  emptyMessage?: string;
}

export const RankingsTable: React.FC<Props> = ({
  items,
  isBenchmarkHedge,
  grossExposure,
  showDisabledFactors,
  universeSize,
  sizing,
  emptyMessage = '查無符合條件的幣種',
}) => {
  const columns = showDisabledFactors ? 8 : 6;
  return (
    <div className="cfp-table-wrap">
      <table className="cfp-table" aria-label={`${universeSize} 幣截面排行`}>
        <caption className="sr-only">多時距動能與資金費率加權的截面多空排行</caption>
        <thead>
          <tr>
            <th scope="col" className="is-rank">排名</th>
            <th scope="col">幣種 / 市價</th>
            <th scope="col">綜合評分</th>
            <th scope="col" className="is-num">動能（60%）</th>
            {showDisabledFactors && <th scope="col" className="is-num">買賣流（0%）</th>}
            <th scope="col" className="is-num">資金費率（40%）</th>
            {showDisabledFactors && <th scope="col" className="is-num">大戶多空（0%）</th>}
            <th scope="col" className="is-num">目標配置</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={columns} className="cfp-empty-cell">{emptyMessage}</td>
            </tr>
          ) : items.map((item) => {
            const isLong = item.side === 'long';
            const isShort = item.side === 'short';
            const hasSizing = Boolean(sizing) && (sizing?.grossExposure ?? 0) > 0;
            const notional = hasSizing && sizing
              ? (isLong ? sizing.perSymbolNotional : isBenchmarkHedge ? sizing.shortSleeve : sizing.perSymbolNotional)
              : (item.target_notional ?? 0);
            const weightPct = hasSizing && sizing
              ? (notional / sizing.grossExposure) * 100
              : grossExposure > 0 && notional > 0
                ? (notional / grossExposure) * 100
                : (item.target_weight ?? 0) * 100;
            return (
              <tr key={item.symbol} className={`cfp-rank-row ${isLong ? 'is-long' : isShort ? 'is-short' : ''}`}>
                <td>
                  <span className={`cfp-rank-pill ${isLong ? 'is-long' : isShort ? 'is-short' : 'is-neutral'}`}>
                    {isLong ? `#${item.rank} 多` : isShort ? (isBenchmarkHedge ? '對沖 空' : `#${item.rank} 空`) : `#${item.rank}`}
                  </span>
                </td>
                <td>
                  <div className="cfp-symbol-name">{item.symbol}</div>
                  <div className="cfp-price">{formatPrice(item.raw_params?.price)}</div>
                </td>
                <td>
                  <span className={`cfp-score-badge is-${scoreTone(item.score)}`}>{formatScore(item.score)}</span>
                  <div className="cfp-verdict">{cleanText(item.evaluations?.overall_eval) || '—'}</div>
                </td>
                <td className="is-num">
                  <div className={(item.raw_params?.price_change_pct ?? 0) >= 0 ? 'text-positive' : 'text-negative'}>
                    {formatPct(item.raw_params?.price_change_pct ?? 0)}
                  </div>
                  <div className="cfp-sub">Z {formatZ(item.signals?.mom_z)} · {cleanText(item.evaluations?.momentum_eval) || '—'}</div>
                </td>
                {showDisabledFactors && (
                  <td className="is-num cfp-muted">
                    <div>{item.raw_params?.taker_flow_ratio?.toFixed(3) ?? '—'}</div>
                    <div className="cfp-sub">Z {formatZ(item.signals?.flow_z)} · {cleanText(item.evaluations?.flow_eval) || '—'}</div>
                  </td>
                )}
                <td className="is-num">
                  <div className="cfp-funding">{formatPct(item.raw_params?.funding_rate_pct ?? 0, 4)}</div>
                  <div className="cfp-sub">反向 Z {formatZ(item.signals?.fund_z)} · {cleanText(item.evaluations?.funding_eval) || '—'}</div>
                </td>
                {showDisabledFactors && (
                  <td className="is-num cfp-muted">
                    <div>{item.raw_params?.top_trader_ratio?.toFixed(3) ?? '—'}</div>
                    <div className="cfp-sub">Z {formatZ(item.signals?.liq_z)} · {cleanText(item.evaluations?.liquidation_eval) || '—'}</div>
                  </td>
                )}
                <td className="is-num">
                  {notional > 0 ? (
                    <>
                      <div className={isLong ? 'text-positive' : isShort ? 'text-negative' : undefined}>{formatUsd(notional, 1)}</div>
                      <div className="cfp-sub">{weightPct.toFixed(1)}% {hasSizing ? '總名義' : '籃子權重'}</div>
                    </>
                  ) : (
                    <span className="cfp-muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};