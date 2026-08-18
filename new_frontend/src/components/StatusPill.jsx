import { memo } from 'react';
import Icon from './Icon';

const CONFIG = {
  success: {
    label: 'Enriched',
    icon: 'check_circle',
    dot: 'bg-tertiary-fixed',
    text: 'text-tertiary-fixed',
  },
  error: {
    label: 'Failed',
    icon: 'error',
    dot: 'bg-error',
    text: 'text-error',
  },
  processing: {
    label: 'Processing',
    icon: 'autorenew',
    dot: 'bg-tertiary-fixed-dim animate-pulse-soft',
    text: 'text-tertiary-fixed-dim',
  },
  idle: {
    label: 'Queued',
    icon: 'schedule',
    dot: 'bg-outline',
    text: 'text-on-surface-variant',
  },
};

/** Memoized: rendered per batch row — primitive props only. */
function StatusPill({ status = 'idle', label }) {
  const cfg = CONFIG[status] || CONFIG.idle;
  const text = label || cfg.label;
  return (
    <span className={`inline-flex items-center gap-2 ${cfg.text}`}>
      <Icon
        name={cfg.icon}
        size={15}
        className={status === 'processing' ? 'animate-spin' : ''}
      />
      <span className="label-caps">{text}</span>
    </span>
  );
}

export default memo(StatusPill);
