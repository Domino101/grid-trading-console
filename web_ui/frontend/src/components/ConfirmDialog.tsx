import { useEffect } from 'react';

interface ConfirmDialogProps {
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const ConfirmDialog = ({ title, body, confirmLabel, danger, onConfirm, onCancel }: ConfirmDialogProps) => {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title" onClick={event => event.stopPropagation()}>
        <h3 id="confirm-title">{title}</h3>
        <p>{body}</p>
        <footer>
          <button className="btn btn-outline" type="button" onClick={onCancel}>取消</button>
          <button className={`btn ${danger ? 'btn-danger' : 'btn-warning'}`} type="button" onClick={onConfirm}>{confirmLabel}</button>
        </footer>
      </div>
    </div>
  );
};

export default ConfirmDialog;
