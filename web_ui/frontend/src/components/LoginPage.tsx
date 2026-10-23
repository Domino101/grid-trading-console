import React, { useState, type FormEvent } from 'react';
import { KeyRound, AlertTriangle, ShieldCheck, Lock } from 'lucide-react';
import LanguageToggle from './LanguageToggle';
import { useI18n } from '../i18n/useI18n.ts';
import { t } from '../i18n/index.ts';

interface LoginPageProps {
  error?: string | null;
  onSubmit: (token: string) => void;
}

const LoginPage: React.FC<LoginPageProps> = ({ error, onSubmit }) => {
  const { lang } = useI18n();
  const [tokenInput, setTokenInput] = useState('');

  const saveToken = (event: FormEvent) => {
    event.preventDefault();
    const token = tokenInput.trim();
    if (!token) return;
    onSubmit(token);
  };

  return (
    <main className="token-gate">
      <div className="token-gate-lang">
        <LanguageToggle />
      </div>
      <form className="token-card" onSubmit={saveToken}>
        <div className="token-brand-header">
          <div className="brand-icon-wrapper" aria-hidden="true">GT</div>
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Grid Trading Pro</h2>
            <p className="muted" style={{ fontSize: '0.76rem' }}>{t("量化網格總控台 · 授權登入")}</p>
          </div>
        </div>

        <p className="muted" style={{ fontSize: '0.82rem' }}>
          {lang === 'zh' ? (
            <>
              {t("請輸入伺服器端環境變數")}{' '}<code>GRID_API_TOKEN</code> {' '}{t("或")}{' '}<code>.grid_api_token</code> {' '}{t("檔案中的 API 存取權杖。")}{' '}
            </>
          ) : (
            <>
              Enter the API access token from the <code>GRID_API_TOKEN</code> environment variable or the <code>.grid_api_token</code> file.
            </>
          )}
        </p>

        <div className="notice notice-warning" style={{ fontSize: '0.78rem', padding: '0.65rem 0.85rem' }}>
          <ShieldCheck size={16} style={{ flexShrink: 0 }} />
          <span>{t("安全提示：瀏覽器僅將 Token 保存在本地 Session，絕請勿輸入交易所私密金鑰 (API Secret)。")}</span>
        </div>

        {error && (
          <div className="notice notice-error" role="alert" style={{ fontSize: '0.82rem' }}>
            <AlertTriangle size={16} style={{ flexShrink: 0 }} />
            <span>{error}</span>
          </div>
        )}

        <label className="input-label">
          <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <KeyRound size={14} />
            <span>{t("API 存取權杖 (API Token)")}</span>
          </span>
          <input
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            autoComplete="off"
            type="password"
            placeholder={t("請輸入 API Token")}
            autoFocus
          />
        </label>

        <button className="btn btn-primary" type="submit" style={{ padding: '0.75rem', marginTop: '0.5rem' }}>
          <Lock size={15} />
          <span>{t("進入量化總控台")}</span>
        </button>
      </form>
    </main>
  );
};

export default LoginPage;
