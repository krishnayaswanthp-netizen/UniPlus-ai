import Icon from './Icon';
import ConfidencePill from './ConfidencePill';

/**
 * Renders a list of IndustrialAttribute rows:
 * Field / Raw Value / Normalized Value / Unit / Confidence.
 */
export default function AttributeTable({ attributes = [], compact = false }) {
  if (!attributes.length) {
    return (
      <div className="flex flex-col items-center gap-3 py-10 text-center">
        <Icon name="database" size={32} className="text-outline" />
        <p className="font-sans text-body-md text-on-surface-variant">
          No attributes were extracted for this product.
        </p>
      </div>
    );
  }

  const pad = compact ? 'px-3 py-3' : 'px-4 py-5';

  return (
    <div className="w-full overflow-x-auto">
      <table className="technical-table w-full border-collapse text-left">
        <thead>
          <tr className="label-caps text-on-surface-variant">
            <th className={`${pad} w-2/5 font-semibold`}>Field</th>
            <th className={`${pad} w-1/5 font-semibold`}>Raw Value</th>
            <th className={`${pad} w-1/5 font-semibold`}>Normalized Value</th>
            <th className={`${pad} hidden w-1/12 font-semibold lg:table-cell`}>Unit</th>
            <th className={`${pad} text-right font-semibold`}>Confidence</th>
          </tr>
        </thead>
        <tbody className="font-sans text-body-md">
          {attributes.map((attr, idx) => (
            <tr key={`${attr.field_name}-${idx}`} className="transition-colors hover:bg-surface-container-low/60">
              <td className={`${pad} font-medium text-primary`}>{attr.field_name}</td>
              <td className={`${pad} text-on-surface-variant`}>{attr.raw_value || '—'}</td>
              <td className={`${pad} text-primary`}>
                {attr.normalized_value}
                {attr.unit ? (
                  <span className="ml-1 text-on-surface-variant lg:hidden">{attr.unit}</span>
                ) : null}
              </td>
              <td className={`${pad} hidden text-on-surface-variant lg:table-cell`}>{attr.unit || '—'}</td>
              <td className={`${pad} text-right`}>
                <ConfidencePill value={attr.confidence_score} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
