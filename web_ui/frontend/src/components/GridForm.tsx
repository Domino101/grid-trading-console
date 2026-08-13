import React, { useState } from 'react';
import { api, type CreateGridRequest } from '../services/api';
import { Settings, CheckCircle, AlertTriangle } from 'lucide-react';

const GridForm: React.FC = () => {
  const [formData, setFormData] = useState<CreateGridRequest>({
    symbol: 'BTC/USDT',
    exchange: 'binance',
    grid_type: 'spot',
    lower_price: 50000,
    upper_price: 100000,
    grid_interval: 1000,
    order_amount: 0.001,
  });

  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{type: 'success' | 'error', text: string} | null>(null);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'number' ? Number(value) : value
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      await api.createGrid(formData);
      setMessage({ type: 'success', text: '網格創建成功！' });
    } catch (error: unknown) {
      const text = error instanceof Error ? error.message : '網格創建失敗';
      setMessage({ type: 'error', text });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-panel" style={{ flex: 1, padding: '2rem', display: 'flex', flexDirection: 'column', gap: '1.5rem', maxWidth: '600px', margin: '0 auto' }}>
      <h2 className="text-2xl font-semibold flex items-center gap-2">
        <Settings size={24} className="text-accent" />
        創建網格策略
      </h2>
      
      {message && (
        <div className={`p-4 rounded flex items-center gap-2 ${message.type === 'success' ? 'bg-green-900/30 text-green-400 border border-green-800' : 'bg-red-900/30 text-red-400 border border-red-800'}`}>
          {message.type === 'success' ? <CheckCircle size={18} /> : <AlertTriangle size={18} />}
          {message.text}
        </div>
      )}

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <label className="text-sm font-medium text-muted">交易對 (Symbol)</label>
          <input 
            type="text" 
            name="symbol" 
            value={formData.symbol} 
            onChange={handleChange} 
            className="form-input bg-black/30 border border-gray-800 rounded p-2 text-white" 
            required 
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <label className="text-sm font-medium text-muted">交易所 (Exchange)</label>
          <input 
            type="text" 
            name="exchange" 
            value={formData.exchange} 
            onChange={handleChange} 
            className="form-input bg-black/30 border border-gray-800 rounded p-2 text-white" 
            required 
          />
        </div>

        <div style={{ display: 'flex', gap: '1rem' }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label className="text-sm font-medium text-muted">下限價格 (Lower Price)</label>
            <input 
              type="number" 
              name="lower_price" 
              value={formData.lower_price || ''} 
              onChange={handleChange} 
              className="form-input bg-black/30 border border-gray-800 rounded p-2 text-white" 
              required 
              step="any"
            />
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label className="text-sm font-medium text-muted">上限價格 (Upper Price)</label>
            <input 
              type="number" 
              name="upper_price" 
              value={formData.upper_price || ''} 
              onChange={handleChange} 
              className="form-input bg-black/30 border border-gray-800 rounded p-2 text-white" 
              required 
              step="any"
            />
          </div>
        </div>

        <div style={{ display: 'flex', gap: '1rem' }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label className="text-sm font-medium text-muted">每格大小 (Grid Interval)</label>
            <input 
              type="number" 
              name="grid_interval" 
              value={formData.grid_interval || ''} 
              onChange={handleChange} 
              className="form-input bg-black/30 border border-gray-800 rounded p-2 text-white" 
              required 
              step="any"
            />
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label className="text-sm font-medium text-muted">每格下單量 (Order Amount)</label>
            <input 
              type="number" 
              name="order_amount" 
              value={formData.order_amount || ''} 
              onChange={handleChange} 
              className="form-input bg-black/30 border border-gray-800 rounded p-2 text-white" 
              required 
              step="any"
            />
          </div>
        </div>

        <button 
          type="submit" 
          disabled={loading}
          className="btn btn-primary mt-4 py-2 px-4 rounded font-medium flex justify-center items-center gap-2 disabled:opacity-50"
          style={{ background: 'var(--accent-primary)', color: '#000' }}
        >
          {loading ? '創建中...' : '創建網格'}
        </button>
      </form>
    </div>
  );
};

export default GridForm;
