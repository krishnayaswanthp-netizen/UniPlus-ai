import Icon from './Icon';

export default function StatCard({ icon, label, value, sub, progress }) {
  return (
    <div className="tactile-surface relative flex-1 overflow-hidden p-8">
      <div className="absolute right-0 top-0 p-4 opacity-10">
        <Icon name={icon} size={64} />
      </div>
      <span className="label-caps block text-on-surface-variant">{label}</span>
      <div className="mt-3 font-display text-metric-xl leading-none text-primary">{value}</div>
      {typeof progress === 'number' ? (
        <div className="mt-5 h-1 w-full overflow-hidden rounded-full bg-surface-container">
          <div
            className="h-full bg-tertiary-fixed transition-all duration-700"
            style={{ width: `${Math.min(100, Math.max(0, progress * 100))}%` }}
          />
        </div>
      ) : (
        sub && (
          <div className="mt-4 flex items-center gap-2 label-caps text-tertiary-fixed-dim">
            <span className="h-2 w-2 rounded-full bg-tertiary-fixed-dim" />
            {sub}
          </div>
        )
      )}
    </div>
  );
}
