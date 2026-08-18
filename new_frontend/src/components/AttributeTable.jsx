import { memo } from 'react';
import Icon from './Icon';
import ConfidencePill from './ConfidencePill';

/**
 * Memoized single attribute row. Rows only re-render when their own
 * attribute (reference-stable within a result) or the shared `pad` string
 * changes — expanding/contracting unrelated rows never touches them.
 */
const AttributeRow = memo(function AttributeRow({ attr, pad }) {
  return (
    <tr className="transition-colors hover:bg-surface-container-low/60">
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
  );
});

/**
 * Renders a list of IndustrialAttribute rows:
 * Field / Raw Value / Normalized Value / Unit / Confidence.
 *
 * Memoized so the (reference-stable) attributes prop skips re-renders while
 * unrelated state churns around the table.
 */
function AttributeTable({ attributes = [], compact = false }) {
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
            <AttributeRow key={`${attr.field_name}-${idx}`} attr={attr} pad={pad} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(AttributeTable);
