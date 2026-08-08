import Icon from './Icon';
import { confidenceTone, formatPercent } from '../utils/format';

const TONES = {
  high: 'bg-tertiary-fixed/10 text-tertiary-fixed border-tertiary-fixed/20',
  medium: 'bg-surface-container-high text-on-surface border-line/25',
  low: 'bg-error/10 text-error border-error/25',
  neutral: 'bg-surface-container text-on-surface-variant border-line/25',
};

export default function ConfidencePill({ value, showIcon = true, className = '' }) {
  const tone = TONES[confidenceTone(value)] || TONES.neutral;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 font-sans text-label-sm ${tone} ${className}`}
      title={value === null || value === undefined ? 'No confidence score' : `Confidence: ${formatPercent(value)}`}
    >
      {showIcon && <Icon name="verified" size={14} fill />}
      {formatPercent(value)}
    </span>
  );
}
