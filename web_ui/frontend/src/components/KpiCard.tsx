import React from 'react';

interface KpiCardProps {
  label: string;
  value: string;
  hint: string;
  tone?: 'positive' | 'negative' | '';
  icon?: React.ReactNode;
  subHint?: string;
}

const KpiCard: React.FC<KpiCardProps> = ({ label, value, hint, tone = '', icon, subHint }) => {
  const toneClass = tone === 'positive' ? 'tone-positive positive' : tone === 'negative' ? 'tone-negative negative' : '';

  return (
    <article className={`kpi-card ${tone ? `tone-${tone}` : ''}`} aria-label={label}>
      <header className="kpi-card-header">
        <span>{label}</span>
        {icon && <span className="kpi-icon">{icon}</span>}
      </header>
      <strong className={`font-mono ${toneClass}`}>{value}</strong>
      <small>{hint}</small>
      {subHint && <small className="text-muted" style={{ fontSize: '0.72rem' }}>{subHint}</small>}
    </article>
  );
};

export default KpiCard;
