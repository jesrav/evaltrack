import { useEffect, useId, useRef } from "react";

export interface ConfirmRequest {
  /** The question, asked in the title. */
  title: string;
  /** What the reader needs before answering it, one paragraph each. */
  body: string[];
  /** Names the destructive action, e.g. "Delete run". */
  confirmLabel: string;
}

interface Props extends ConfirmRequest {
  onConfirm: () => void;
  onCancel: () => void;
}

/** A modal question before a destructive action.
 *
 *  Focus moves to Cancel on open and back to whatever opened the dialog on
 *  close. Not to the confirm button: the ✕ that opens this is reachable by Tab,
 *  and a key held on a focused button repeats, so focusing the destructive
 *  answer lets one held Enter open the dialog and answer it. Escape, the
 *  backdrop and Cancel all cancel.
 *
 *  Tab stays inside the panel. A modal that leaks focus into the page behind it
 *  lets a keyboard user act on runs the dialog is asking about. */
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
}: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    const opener = document.activeElement;
    cancelButton.current?.focus();
    return () => {
      if (opener instanceof HTMLElement) opener.focus();
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key !== "Tab") return;
      const buttons = Array.from(
        panel.current?.querySelectorAll("button") ?? [],
      );
      const edge = e.shiftKey ? buttons[0] : buttons.at(-1);
      const wrap = e.shiftKey ? buttons.at(-1) : buttons[0];
      if (!edge || !wrap || document.activeElement !== edge) return;
      e.preventDefault();
      wrap.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="confirm-backdrop" onClick={onCancel}>
      <div
        className="confirm"
        ref={panel}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="confirm-title" id={titleId}>
          {title}
        </h2>
        {body.map((line) => (
          <p className="confirm-line" key={line}>
            {line}
          </p>
        ))}
        <div className="confirm-actions">
          <button
            type="button"
            className="page-action"
            ref={cancelButton}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button type="button" className="page-delete" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
