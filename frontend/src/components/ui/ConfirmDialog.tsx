import { useEffect, useRef } from "react";
import { Icon } from "./Icon";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog({ open, title, description, confirmLabel = "确认删除", busy = false, onCancel, onConfirm }: ConfirmDialogProps) {
  const cancelButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onCancel(); };
    document.addEventListener("keydown", escape);
    cancelButton.current?.focus();
    return () => { document.removeEventListener("keydown", escape); previous?.focus(); };
  }, [open, busy, onCancel]);
  if (!open) return null;
  return <div className="confirm-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel(); }}>
    <section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description">
      <span className="confirm-icon"><Icon name="trash" size={22}/></span>
      <div><h2 id="confirm-title">{title}</h2><p id="confirm-description">{description}</p></div>
      <footer><button ref={cancelButton} className="button button-secondary" type="button" disabled={busy} onClick={onCancel}>取消</button><button className="button button-danger confirm-danger" type="button" disabled={busy} onClick={onConfirm}>{busy ? "删除中…" : confirmLabel}</button></footer>
    </section>
  </div>;
}
