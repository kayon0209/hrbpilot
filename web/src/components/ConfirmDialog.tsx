import { useEffect, useRef } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Accessible confirmation dialog built on the native <dialog> element.
 * Renders nothing when closed; Esc / backdrop click trigger onCancel.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = '确认',
  cancelLabel = '取消',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    const handleCancel = (event: Event) => {
      event.preventDefault()
      onCancel()
    }
    const handleBackdropClick = (event: MouseEvent) => {
      const rect = dialog.getBoundingClientRect()
      const clickedInside =
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom
      if (!clickedInside) onCancel()
    }
    dialog.addEventListener('cancel', handleCancel)
    dialog.addEventListener('click', handleBackdropClick)
    return () => {
      dialog.removeEventListener('cancel', handleCancel)
      dialog.removeEventListener('click', handleBackdropClick)
    }
  }, [onCancel])

  function handleConfirm() {
    ref.current?.close()
    onConfirm()
  }

  function handleCancel() {
    ref.current?.close()
    onCancel()
  }

  if (!open) return null
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="confirm-dialog-title" aria-describedby="confirm-dialog-message">
      <h2 id="confirm-dialog-title">{title}</h2>
      <p id="confirm-dialog-message">{message}</p>
      <div className="confirm-dialog__actions">
        <button type="button" className="secondary-button" onClick={handleCancel} autoFocus>
          {cancelLabel}
        </button>
        <button
          type="button"
          className={danger ? 'primary-button confirm-dialog__danger' : 'primary-button'}
          onClick={handleConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </dialog>
  )
}
