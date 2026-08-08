/** Formatting helpers shared across the UI. */

/** 0.9834 -> "98.3%" (empty string when nullish). */
export function formatPercent(value) {
  if (value === null || value === undefined || value === '') return '—';
  return `${(Number(value) * 100).toFixed(1)}%`;
}

/** 1234.56 -> "1.23s" | "842ms". */
export function formatDuration(ms) {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '—';
  const n = Number(ms);
  if (n >= 1000) return `${(n / 1000).toFixed(2)}s`;
  return `${Math.round(n)}ms`;
}

/** 0.000184 -> "$0.0002" | 12.5 -> "$12.50". */
export function formatCurrency(usd) {
  if (usd === null || usd === undefined || Number.isNaN(Number(usd))) return '—';
  const n = Number(usd);
  if (n < 0.01) return `$${n.toFixed(6)}`;
  return `$${n.toFixed(2)}`;
}

/** 489102 -> "489.1 KB" | 2 * 1024 * 1024 -> "2 MB". */
export function formatFileSize(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let n = Number(bytes);
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * Tonal label for a confidence score:
 * high -> olive (tertiary-fixed), medium -> neutral, low -> error.
 */
export function confidenceTone(value) {
  if (value === null || value === undefined) return 'neutral';
  if (value >= 0.9) return 'high';
  if (value >= 0.7) return 'medium';
  return 'low';
}
