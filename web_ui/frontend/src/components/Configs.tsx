import React, { useState, useEffect } from 'react';
import { api } from '../services/api';
import { Save, FileText } from 'lucide-react';

const Configs: React.FC = () => {
  const [configs, setConfigs] = useState<string[]>([]);
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null);
  const [content, setContent] = useState<string>('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchConfigs();
  }, []);

  const fetchConfigs = async () => {
    try {
      const list = await api.getConfigs();
      setConfigs(list);
    } catch (e) {
      console.error(e);
    }
  };

  const loadConfig = async (path: string) => {
    try {
      const text = await api.getConfig(path);
      setContent(text);
      setSelectedConfig(path);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSave = async () => {
    if (!selectedConfig) return;
    setSaving(true);
    try {
      await api.updateConfig(selectedConfig, content);
      alert('Saved successfully');
    } catch (e) {
      alert('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', gap: '1rem', height: '100%' }}>
      <div className="glass-panel" style={{ width: '250px', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <FileText size={20} className="text-accent" />
          ConfigFiles
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', overflowY: 'auto' }}>
          {configs.map(c => (
            <button 
              key={c}
              className={`btn btn-outline ${selectedConfig === c ? 'active' : ''}`}
              onClick={() => loadConfig(c)}
              style={{ justifyContent: 'flex-start', textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', border: selectedConfig === c ? '1px solid var(--accent-primary)' : '1px solid var(--border-color)', color: selectedConfig === c ? 'var(--accent-primary)' : 'var(--text-primary)' }}
            >
              {c.split('/').pop()}
            </button>
          ))}
        </div>
      </div>

      <div className="glass-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold">
            {selectedConfig ? selectedConfig.split('/').pop() : 'Select a configuration file'}
          </h2>
          <button 
            className="btn btn-primary" 
            onClick={handleSave} 
            disabled={!selectedConfig || saving}
          >
            <Save size={16} />
            {saving ? 'Saving...' : 'Save Config'}
          </button>
        </div>
        
        <textarea
          value={content}
          onChange={e => setContent(e.target.value)}
          disabled={!selectedConfig}
          style={{ 
            flex: 1, 
            background: 'rgba(0,0,0,0.3)', 
            border: '1px solid var(--border-color)', 
            borderRadius: 'var(--radius-sm)',
            color: 'var(--text-primary)',
            padding: '1rem',
            fontFamily: 'monospace',
            resize: 'none',
            outline: 'none',
            fontSize: '14px'
          }}
        />
      </div>
    </div>
  );
};

export default Configs;
