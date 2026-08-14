import React, { useEffect } from 'react';
import { AlertTriangle, Trash2, StopCircle } from 'lucide-react';

interface ConfirmDialogProps {
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  title,
  body,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}) => {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
          {danger ? (
            <Trash2 size={20} color="var(--trading-red)" />
          ) : (
            <AlertTriangle size={20} color="var(--accent-amber)" />
          )}
          <h3 id="confirm-title" style={{ fontSize: '1.1rem', fontWeight: 600 }}>
            {title}
          </h3>
        </div>

        <p className="text-secondary" style={{ fontSize: '0.86rem', lineHeight: 1.6 }}>
          {body}
        </p>

        <footer style={{ display: 'flex', gap: '0.65rem', justifyContent: 'flex-end', marginTop: '0.5rem' }}>
          <button className="btn btn-outline" type="button" onClick={onCancel}>
            取消
          </button>
          <button
            className={`btn ${danger ? 'btn-danger' : 'btn-warning'}`}
            type="button"
            onClick={onConfirm}
          >
            {danger ? <Trash2 size={14} /> : <StopCircle size={14} />}
            <span>{confirmLabel}</span>
          </button>
        </footer>
      </div>
    </div>
  );
};

export default ConfirmDialog;
