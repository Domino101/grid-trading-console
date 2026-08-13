import { useState, type FormEvent } from 'react';

interface LoginPageProps {
  error?: string | null;
  onSubmit: (token: string) => void;
}

const LoginPage = ({ error, onSubmit }: LoginPageProps) => {
  const [tokenInput, setTokenInput] = useState('');

  const saveToken = (event: FormEvent) => {
    event.preventDefault();
    const token = tokenInput.trim();
    if (!token) return;
    onSubmit(token);
  };

  return (
    <main className="token-gate">
      <form className="strategy-card token-card" onSubmit={saveToken}>
        <p className="eyebrow">Grid Engine</p>
        <h2>登入總控台</h2>
        <p className="muted">輸入伺服器上的 GRID_API_TOKEN 或 .grid_api_token。瀏覽器只把 token 存在本機，不要貼交易所 API 金鑰。</p>
        {error && <div className="notice notice-error" role="alert">{error}</div>}
        <label>API Token
          <input value={tokenInput} onChange={event => setTokenInput(event.target.value)} autoComplete="off" type="password" />
        </label>
        <button className="btn btn-primary" type="submit">進入總控台</button>
      </form>
    </main>
  );
};

export default LoginPage;
