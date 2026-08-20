import { useEffect, useState } from 'react';
import { checkBackendHealth, API_BASE_URL } from '../services/api';
import Icon from './Icon';

export default function BackendBanner() {
  const [status, setStatus] = useState('checking'); // checking | online | offline
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;

    const probe = async () => {
      const ok = await checkBackendHealth();
      if (alive) {
        setStatus(ok ? 'online' : 'offline');
      }
    };

    probe();
    const interval = setInterval(probe, 5000);

    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  if (status !== 'offline' || dismissed) return null;

  return (
    <div className="border-b border-error/25 bg-error/5">
      <div className="mx-auto flex w-full max-w-shell items-center gap-3 px-6 py-3 md:px-container-padding">
        <Icon name="link_off" size={18} className="shrink-0 text-error" />
        <p className="flex-1 font-sans text-body-md text-on-error-container">
          Backend unreachable at <code className="font-mono text-xs">{API_BASE_URL}</code> — waking up service.
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