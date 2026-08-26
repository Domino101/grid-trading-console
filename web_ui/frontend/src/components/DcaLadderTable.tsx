import React, { useMemo } from 'react';
import type { DcaLevelView } from '../services/api';
import { number } from '../utils/format';
import { dcaSlotStatusMeta } from '../utils/dca';

interface DcaLadderTableProps {
    levels: DcaLevelView[];
    entryPrice: number | null;
    currentPrice: number | null;
    averagePrice: number | null;
}

// 安全單階梯狀態表（Phase 7 稽核 7.8）：已成交/掛單中/未觸及三態，
// 標出當前價位置與均價線。純顯示——不做任何階梯數學（G-4），
// 偏移%、價格、數量、名目全部來自 /ladder 回應欄位或既有 format 換算。
const DcaLadderTable: React.FC<DcaLadderTableProps> = ({
    levels,
    entryPrice,
    currentPrice,
    averagePrice,
}) => {
    // 以「當前價」在階梯序列中的插入位置，決定畫線位置（顯示層排序，非運算階梯）。
    const priceMarkerIndex = useMemo(() => {
        if (currentPrice == null || levels.length === 0) return null;
        // 階梯依價格排序（多單遞減、空單遞增皆可）；找出第一個「越過現價」的層
        const sorted = [...levels].sort((a, b) => (a.index) - (b.index));
        for (let i = 0; i < sorted.length; i += 1) {
            const p = sorted[i].planned_price ?? sorted[i].actual_price;
            if (p == null) continue;
            // 現價高於某層計畫價（多單場景）→ 標在該層之前
            if (currentPrice >= p) return sorted[i].index;
        }
        return null;
    }, [levels, currentPrice]);

    if (levels.length === 0) {
        return (
            <p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>
                目前無鎖定方向的實際階梯（armed / cooling 狀態不掛單）。
            </p>
        );
    }

    return (
        <div className="table-wrapper" style={{ marginTop: '0.75rem' }}>
            <table className="pro-table dca-ladder-detail-table">
                <thead>
                    <tr>
                        <th>層級</th>
                        <th>角色</th>
                        <th>偏移%</th>
                        <th>價格</th>
                        <th>數量</th>
                        <th>名目</th>
                        <th>狀態</th>
                    </tr>
                </thead>
                <tbody>
                    {levels.map((level) => {
                        const price = level.filled_price ?? level.actual_price ?? level.planned_price;
                        const qty = level.filled_quantity ?? level.planned_quantity;
                        const notional = price != null && qty != null ? price * qty : null;
                        // 偏移%：相對入場價的有號距離（顯示層換算，非階梯數學）
                        const deviation = price != null && entryPrice != null && entryPrice !== 0
                            ? ((price - entryPrice) / entryPrice) * 100
                            : null;
                        const meta = dcaSlotStatusMeta(level.status);
                        const isPriceMarker = priceMarkerIndex === level.index;
                        return (
                            <tr key={`${level.role}-${level.index}`} className={isPriceMarker ? 'dca-ladder-price-row' : ''}>
                                <td className="font-mono">{level.index === 0 ? '基礎' : `SO${String(level.index).padStart(2, '0')}`}</td>
                                <td>{level.role === 'base' ? '基礎單' : '安全單'}</td>
                                <td className="font-mono">{deviation == null ? '—' : `${deviation >= 0 ? '+' : ''}${deviation.toFixed(2)}%`}</td>
                                <td className="font-mono">{number(price)}</td>
                                <td className="font-mono">{number(qty)}</td>
                                <td className="font-mono">{number(notional, 2)}</td>
                                <td>
                                    <span className={`dca-slot-badge ${meta.className}`}>{meta.label}</span>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            <div className="dca-ladder-legend" role="note">
                <span><span className="dca-legend-dot dca-slot-filled" /> 已成交</span>
                <span><span className="dca-legend-dot dca-slot-active" /> 掛單中</span>
                <span><span className="dca-legend-dot dca-slot-waiting" /> 未觸及</span>
                {averagePrice != null && (
                    <span className="font-mono dca-legend-avg">均價線: {number(averagePrice)}</span>
                )}
                {currentPrice != null && (
                    <span className="font-mono dca-legend-current">當前價: {number(currentPrice)}</span>
                )}
            </div>
        </div>
    );
};

export default DcaLadderTable;
