import React from 'react';
import { dcaStatusMeta } from '../utils/dca';

interface DcaStatusBadgeProps {
    status: string | null | undefined;
}

// §4.6 狀態機徽章：每狀態獨立視覺與中文文案（稽核 5.11）；
// 語意不只靠顏色——徽章皆含文字標籤（稽核 5.15）。
const DcaStatusBadge: React.FC<DcaStatusBadgeProps> = ({ status }) => {
    const meta = dcaStatusMeta(status);
    return (
        <span
          className={meta.badgeClass}
          data-status={status ?? 'unknown'}
          aria-label={`DCA 狀態：${meta.label}`}
        >
            {meta.label}
        </span>
    );
};

export default DcaStatusBadge;
