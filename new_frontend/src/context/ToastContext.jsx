import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

const ToastContext = createContext(null);

/**
 * Session-level toast state, isolated from WorkspaceContext on purpose.
 *
 * Toasts are short-lived (auto-dismiss after ~6s), so keeping them in the
 * workspace context meant every toast add/dismiss churned the context value
 * and re-rendered every workspace consumer — including the large attribute
 * table trees on the Single SKU / Batch pages. Splitting them out keeps the
 * workspace value stable while toasts come and go.
 */
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const toastId = useRef(0);

  const notify = useCallback((message, type = 'info') => {
    const id = ++toastId.current;
    setToasts((prev) => [...prev, { id, message, type }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const value = useMemo(
    () => ({ toasts, notify, dismissToast }),
    [toasts, notify, dismissToast]
  );

  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

export function useToasts() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToasts must be used within <ToastProvider>');
  return ctx;
}
