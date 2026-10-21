import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, ShieldAlert } from 'lucide-react';

export interface ConfirmDialogDetail {
  label: string;
  value: string;
  tone?: 'positive' | 'negative' | 'warning' | 'neutral';
}

interface ConfirmDialogProps {
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  details?: ConfirmDialogDetail[];
  note?: string;
  requireText?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  title,
  body,
  confirmLabel,
  danger,
  details,
  note,
  requireText,
  onConfirm,
  onCancel,
}) => {
  const [typed, setTyped] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const matches = !requireText || typed.trim() === requireText;

  useEffect(() => {
    if (requireText) inputRef.current?.focus();
  }, [requireText]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const submit = () => {
    if (matches) onConfirm();
  };

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="dialog confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="confirm-dialog-heading">
          {danger ? (
            <ShieldAlert size={22} color="var(--trading-red)" aria-hidden="true" />
          ) : (
            <AlertTriangle size={22} color="var(--accent-amber)" aria-hidden="true" />
          )}
          <h3 id="confirm-title">{title}</h3>
        </div>

        <p id="confirm-body" className="confirm-dialog-body">
          {body}
        </p>

        {details && details.length > 0 && (
          <dl className="dialog-details">
            {details.map((detail) => (
              <div key={detail.label} className="dialog-detail-row">
                <dt>{detail.label}</dt>
                <dd className={detail.tone ? `text-${detail.tone}` : undefined}>{detail.value}</dd>
              </div>
            ))}
          </dl>
        )}

        {note && <p className="confirm-dialog-note">{note}</p>}

        {requireText && (
          <label className="confirm-text-field">
            <span>
              請輸入 <code>{requireText}</code> 以確認
            </span>
            <input
              ref={inputRef}
              type="text"
              value={typed}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setTyped(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') submit();
              }}
              aria-label={`輸入 ${requireText} 以確認`}
            />
          </label>
        )}

        <footer className="confirm-dialog-actions">
          <button className="btn btn-outline" type="button" onClick={onCancel}>
            取消
          </button>
          <button
            className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`}
            type="button"
            onClick={submit}
            disabled={!matches}
          >
            {danger ? <AlertTriangle size={14} /> : <Check size={14} />}
            <span>{confirmLabel}</span>
          </button>
        </footer>
      </div>
    </div>
  );
};

export default ConfirmDialog;
