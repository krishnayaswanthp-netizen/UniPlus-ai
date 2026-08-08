import { useEffect, useState } from 'react';
import { checkBackendHealth } from '../services/api';
import Icon from './Icon';

/**
 * Probes GET /health once on mount. When the backend is unreachable a slim
 * dismissible banner is shown so failures never look like app bugs.
 */
export default function BackendBanner() {
  const [status, setStatus] = useState('checking'); // checking | online | offline
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    checkBackendHealth().then((ok) => {
      if (alive) setStatus(ok ? 'online' : 'offline');
    });
    return () => {
      alive = false;
    };
  }, []);

  if (status !== 'offline' || dismissed) return null;

  return (
    <div className="border-b border-error/25 bg-error/5">
      <div className="mx-auto flex w-full max-w-shell items-center gap-3 px-6 py-3 md:px-container-padding">
        <Icon name="link_off" size={18} className="shrink-0 text-error" />
        <p className="flex-1 font-sans text-body-md text-on-error-container">
          Backend unreachable at <code className="font-mono">http://127.0.0.1:8000</code> — start it
          with <code className="font-mono">uvicorn app.main:app --reload</code> to enable enrichment.
        </p>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="text-on-surface-variant transition-colors hover:text-primary"
          aria-label="Dismiss backend warning"
        >
          <Icon name="close" size={16} />
        </button>
      </div>
    </div>
  );
}
