interface KpiCardProps {
  label: string;
  value: string;
  hint: string;
  tone?: 'positive' | 'negative' | '';
}

const KpiCard = ({ label, value, hint, tone = '' }: KpiCardProps) => (
  <article className="kpi-card">
    <span>{label}</span>
    <strong className={tone}>{value}</strong>
    <small>{hint}</small>
  </article>
);

export default KpiCard;
