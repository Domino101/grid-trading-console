import React from 'react';
import { Languages } from 'lucide-react';
import { useI18n } from '../i18n/useI18n.ts';

interface LanguageToggleProps {
  compact?: boolean;
}

const LanguageToggle: React.FC<LanguageToggleProps> = ({ compact = false }) => {
  const { lang, setLang, t } = useI18n();
  const next = lang === 'zh' ? 'en' : 'zh';

  return (
    <button
      className={`lang-toggle${compact ? ' is-compact' : ''}`}
      type="button"
      onClick={() => setLang(next)}
      title={lang === 'zh' ? t('切換至英文介面') : t('切換至中文介面')}
      aria-label={lang === 'zh' ? t('切換至英文介面') : t('切換至中文介面')}
      aria-live="polite"
    >
      <Languages size={14} aria-hidden="true" />
      <span className={`lang-toggle-option${lang === 'zh' ? ' is-active' : ''}`}>ZH</span>
      <span className="lang-toggle-sep" aria-hidden="true">/</span>
      <span className={`lang-toggle-option${lang === 'en' ? ' is-active' : ''}`}>EN</span>
    </button>
  );
};

export default LanguageToggle;
