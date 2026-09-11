import type { ReactNode, RefObject } from "react";

export function ConfirmDialog({
  dialogRef,
  returnFocusRef,
  title,
  confirmLabel,
  pendingLabel,
  pending,
  onConfirm,
  children,
}: {
  dialogRef: RefObject<HTMLDialogElement | null>;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
  title: string;
  confirmLabel: string;
  pendingLabel: string;
  pending: boolean;
  onConfirm: () => void;
  children: ReactNode;
}) {
  const close = () => {
    if (!pending) dialogRef.current?.close();
  };
  return (
    <dialog
      ref={dialogRef}
      className="confirm-dialog"
      aria-labelledby={`${title.replaceAll(" ", "-").toLowerCase()}-title`}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClose={() => returnFocusRef.current?.focus()}
    >
      <div className="confirm-dialog__body">
        <h2 id={`${title.replaceAll(" ", "-").toLowerCase()}-title`}>{title}</h2>
        {children}
        <div className="form-actions">
          <button className="button" type="button" disabled={pending} onClick={close}>Cancel</button>
          <button className="button button--primary" type="button" disabled={pending} onClick={onConfirm}>
            {pending ? pendingLabel : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
