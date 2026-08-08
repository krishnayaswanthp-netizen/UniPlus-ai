import { useWorkspace } from '../context/WorkspaceContext';
import Icon from './Icon';

const STYLES = {
  success: {
    icon: 'check_circle',
    iconClass: 'text-tertiary-fixed',
    borderClass: 'border-tertiary-fixed/30',
  },
  error: {
    icon: 'error',
    iconClass: 'text-error',
    borderClass: 'border-error/30',
  },
  info: {
    icon: 'info',
    iconClass: 'text-on-surface-variant',
    borderClass: 'border-line/25',
  },
};

export default function Toasts() {
  const { toasts, dismissToast } = useWorkspace();

  return (
    <div
      aria-live="polite"
      className="fixed bottom-6 right-6 z-[100] flex w-[min(380px,calc(100vw-3rem))] flex-col gap-3"
    >
      {toasts.map((toast) => {
        const style = STYLES[toast.type] || STYLES.info;
        return (
          <div
            key={toast.id}
            className={`animate-fade-in flex items-start gap-3 rounded-lg border ${style.borderClass}
              bg-surface-container-high/95 px-4 py-3 shadow-tactile backdrop-blur`}
          >
            <Icon name={style.icon} size={20} fill className={style.iconClass} />
            <p className="flex-1 text-body-md leading-snug text-on-surface">{toast.message}</p>
            <button
              type="button"
              onClick={() => dismissToast(toast.id)}
              className="text-on-surface-variant transition-colors hover:text-primary"
              aria-label="Dismiss notification"
            >
              <Icon name="close" size={18} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
