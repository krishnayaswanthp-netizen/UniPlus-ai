import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { CATEGORIES, enrichSingle } from '../services/api';
import { useToasts } from '../context/ToastContext';
import { useWorkspace } from '../context/WorkspaceContext';
import AttributeTable from '../components/AttributeTable';
import Icon from '../components/Icon';
import { formatCurrency, formatDuration, formatFileSize, formatPercent } from '../utils/format';

const PROCESSING_PHASES = [
  'Searching technical sources',
  'Parsing reference material',
  'Extracting specifications',
  'Normalizing units',
];

/** Memoized: re-renders only when the result object or reset callback changes. */
const ResultSheet = memo(function ResultSheet({ result, onReset }) {
  const { runExport, exporting } = useWorkspace();
  const attributes = result.enriched_attributes || [];
  const description = `${result.category || 'Uncategorized'} · ${attributes.length} attribute(s)`;

  return (
    <div className="animate-fade-in space-y-6">
      {/* Header band */}
      <div className="flex flex-wrap items-end justify-between gap-6 rounded-t-lg border border-line/15 bg-surface-container-low p-8">
        <div>
          <div className="mb-2 flex items-center gap-3">
            <Icon name="check_circle" size={20} fill className="text-tertiary-fixed" />
            <span className="label-caps text-tertiary-fixed">Enrichment Complete</span>
          </div>
          <h2 className="font-display text-headline-md text-primary">{result.sku_id}</h2>
          <p className="mt-1 font-sans text-body-md text-on-surface-variant">{description}</p>
        </div>
        <div className="text-right">
          <div className="font-display text-[64px] leading-none text-primary">
            {formatPercent(result.overall_confidence)}
          </div>
          <div className="label-caps text-on-surface-variant">Overall Confidence</div>
        </div>
      </div>

      {/* Body */}
      <div className="rounded-b-lg border border-t-0 border-line/15 bg-surface p-6 md:p-10">
        <AttributeTable attributes={attributes} />

        {/* Meta + actions */}
        <div className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-line/10 pt-6">
          <div className="flex flex-wrap gap-3">
            <span className="inline-flex items-center gap-2 rounded border border-line/20 bg-surface-container-low px-3 py-1.5 font-sans text-label-caps uppercase tracking-wider text-primary">
              <Icon name="category" size={14} className="text-tertiary-fixed" />
              {result.category}
            </span>
            <span className="inline-flex items-center gap-2 rounded border border-line/20 bg-surface-container-low px-3 py-1.5 font-sans text-label-caps uppercase tracking-wider text-on-surface-variant">
              <Icon name="schedule" size={14} />
              {formatDuration(result.processing_time_ms)}
            </span>
            <span className="inline-flex items-center gap-2 rounded border border-line/20 bg-surface-container-low px-3 py-1.5 font-sans text-label-caps uppercase tracking-wider text-on-surface-variant">
              <Icon name="payments" size={14} />
              {formatCurrency(result.estimated_cost_usd)}
            </span>
          </div>
          <div className="flex gap-3">
            <button type="button" onClick={runExport} disabled={exporting} className="btn-primary !py-2.5">
              <Icon name={exporting ? 'sync' : 'download'} size={18} className={exporting ? 'animate-spin' : ''} />
              {exporting ? 'Exporting…' : 'Export to Excel'}
            </button>
            <button type="button" onClick={onReset} className="btn-ghost !py-2.5">
              <Icon name="refresh" size={18} />
              New Enrichment
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});

export default function SingleSkuPage() {
  const { setSingleResult } = useWorkspace();
  const { notify } = useToasts();

  const [manufacturer, setManufacturer] = useState('');
  const [partNumber, setPartNumber] = useState('');
  const [category, setCategory] = useState('General');
  const [notes, setNotes] = useState('');
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState('');
  const [dragging, setDragging] = useState(false);

  const [status, setStatus] = useState('idle'); // idle | submitting | done
  const [apiError, setApiError] = useState('');
  const [result, setResult] = useState(null);
  const [phaseIdx, setPhaseIdx] = useState(0);
  const fileInputRef = useRef(null);

  // Cycle the staged progress label while submitting.
  useEffect(() => {
    if (status !== 'submitting') return undefined;
    setPhaseIdx(0);
    const timer = window.setInterval(() => {
      setPhaseIdx((idx) => Math.min(idx + 1, PROCESSING_PHASES.length - 1));
    }, 1200);
    return () => window.clearInterval(timer);
  }, [status]);

  const validFile = (candidate) => {
    if (!candidate) return true;
    if (candidate.type !== 'application/pdf' && !/\.pdf$/i.test(candidate.name)) {
      setFileError('Datasheet must be a PDF file.');
      return false;
    }
    setFileError('');
    return true;
  };

  const handleFileDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer?.files?.[0];
    if (dropped && validFile(dropped)) setFile(dropped);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!manufacturer.trim() || !partNumber.trim()) {
      notify('Manufacturer name and part number are required.', 'error');
      return;
    }

    setStatus('submitting');
    setApiError('');
    try {
      const response = await enrichSingle({
        manufacturerName: manufacturer.trim(),
        partNumber: partNumber.trim(),
        category,
        rawDescription: notes,
        file,
      });
      // Attach identity fields so the Excel export's Products sheet shows
      // the manufacturer / part number alongside the SKU.
      const enriched = { ...response, manufacturer_name: manufacturer.trim(), part_number: partNumber.trim() };
      setResult(enriched);
      setSingleResult(enriched);
      setStatus('done');
      notify(`${response.sku_id} enriched in ${formatDuration(response.processing_time_ms)}.`, 'success');
    } catch (err) {
      setApiError(err.message);
      setStatus('idle');
    }
  };

  // Stable identity so the memoized ResultSheet doesn't re-render on parent churn.
  const handleReset = useCallback(() => {
    setResult(null);
    setSingleResult(null);
    setStatus('idle');
    setApiError('');
    setFile(null);
    setFileError('');
    setNotes('');
  }, []);

  return (
    <>
      {/* Page header */}
      <header className="mx-auto w-full max-w-shell px-6 pb-12 pt-14 md:px-container-padding">
        <h1 className="font-display text-headline-lg text-primary md:text-display-lg">
          Single SKU Research Desk
        </h1>
        <p className="mt-3 max-w-xl font-sans text-body-lg text-on-surface-variant">
          Enter raw product identifiers to initiate AI-driven enrichment. The engine consults
          technical documentation to normalize specifications.
        </p>
      </header>

      <div className="mx-auto w-full max-w-shell px-6 pb-24 md:px-container-padding">
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
          {/* Form / Result column */}
          <div className="lg:col-span-8">
            {status === 'done' && result ? (
              <ResultSheet result={result} onReset={handleReset} />
            ) : (
              <form onSubmit={handleSubmit} className="tactile-surface p-8 md:p-12">
                <h2 className="mb-8 inline-block border-b border-line/15 pb-4 font-display text-headline-sm text-primary">
                  Raw Input Entry
                </h2>

                {apiError && (
                  <div className="mb-8 flex items-start gap-3 rounded-lg border border-error/25 bg-error/5 p-4">
                    <Icon name="error" size={20} fill className="mt-0.5 shrink-0 text-error" />
                    <div>
                      <p className="font-sans text-body-md font-medium text-on-error-container">
                        Enrichment failed
                      </p>
                      <p className="mt-1 font-sans text-body-md text-on-error-container/80">{apiError}</p>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
                  <div>
                    <label htmlFor="mfg" className="field-label">
                      Manufacturer Name *
                    </label>
                    <input
                      id="mfg"
                      type="text"
                      className="field-input"
                      placeholder="e.g., Rockwell Automation"
                      value={manufacturer}
                      onChange={(e) => setManufacturer(e.target.value)}
                      required
                    />
                  </div>
                  <div>
                    <label htmlFor="part" className="field-label">
                      Part Number *
                    </label>
                    <input
                      id="part"
                      type="text"
                      className="field-input"
                      placeholder="e.g., 1756-L83E"
                      value={partNumber}
                      onChange={(e) => setPartNumber(e.target.value)}
                      required
                    />
                  </div>
                </div>

                <div className="mt-8">
                  <label htmlFor="category" className="field-label">
                    Category *
                  </label>
                  <select
                    id="category"
                    className="field-input"
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                  >
                    {CATEGORIES.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="mt-8">
                  <label htmlFor="notes" className="field-label">
                    Research Notes
                  </label>
                  <textarea
                    id="notes"
                    className="field-input resize-none"
                    rows={3}
                    placeholder="Optional context — application, environment, or known specs…"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                  />
                </div>

                {/* PDF datasheet dropzone */}
                <div className="mt-8">
                  <span className="field-label">PDF Datasheet (Optional)</span>
                  <div
                    role="button"
                    tabIndex={0}
                    aria-label="Upload a PDF datasheet"
                    onClick={() => fileInputRef.current?.click()}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click();
                    }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={handleFileDrop}
                    className={`dashed-border-subtle flex cursor-pointer items-center justify-between gap-4 rounded-lg border border-transparent px-5 py-5 transition-colors duration-300 ${
                      dragging ? 'bg-surface-container-high' : 'bg-surface-container-low/40 hover:bg-surface-container-low'
                    }`}
                  >
                    <div className="flex items-center gap-4">
                      <span className="flex h-10 w-10 items-center justify-center rounded border border-line/20 bg-surface-container">
                        <Icon name={file ? 'description' : 'upload_file'} size={20} className={file ? 'text-tertiary-fixed' : 'text-on-surface-variant'} />
                      </span>
                      <div>
                        <p className="font-sans text-body-md text-primary">
                          {file ? file.name : 'Drop a datasheet here, or click to browse'}
                        </p>
                        <p className="mt-0.5 font-sans text-label-sm text-on-surface-variant">
                          {file
                            ? `${formatFileSize(file.size)} · PDF`
                            : 'Recommended for higher extraction fidelity'}
                        </p>
                      </div>
                    </div>
                    {file ? (
                      <button
                        type="button"
                        className="text-on-surface-variant transition-colors hover:text-error"
                        onClick={(e) => {
                          e.stopPropagation();
                          setFile(null);
                        }}
                        aria-label="Remove datasheet"
                      >
                        <Icon name="close" size={20} />
                      </button>
                    ) : (
                      <Icon name="north_east" size={18} className="text-outline" />
                    )}
                  </div>
                  {fileError && <p className="mt-2 font-sans text-label-sm text-error">{fileError}</p>}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="application/pdf,.pdf"
                    className="hidden"
                    onChange={(e) => {
                      const picked = e.target.files?.[0];
                      if (picked && validFile(picked)) setFile(picked);
                      // Reset the input so re-selecting the same file fires onChange again.
                      e.target.value = '';
                    }}
                  />
                </div>

                <div className="flex flex-wrap items-center justify-end gap-4 pt-10">
                  {status === 'submitting' ? (
                    <div className="flex items-center gap-3 rounded-lg border border-tertiary-fixed/20 bg-tertiary-fixed/5 px-6 py-3">
                      <span className="h-2 w-2 animate-pulse-soft rounded-full bg-tertiary-fixed" />
                      <span className="font-sans text-body-md text-tertiary-fixed">
                        {PROCESSING_PHASES[phaseIdx]}…
                      </span>
                    </div>
                  ) : (
                    <button type="submit" className="btn-primary">
                      <Icon name="auto_awesome" size={18} fill />
                      Initiate Enrichment
                    </button>
                  )}
                </div>
              </form>
            )}
          </div>

          {/* Technical panel */}
          <aside className="lg:col-span-4">
            <div className="tactile-surface flex h-full flex-col gap-8 p-8">
              <div>
                <div className="mb-4 flex items-center gap-4">
                  <span className="flex h-12 w-12 items-center justify-center rounded border border-line/20 bg-surface-container">
                    <Icon name="analytics" size={22} className="text-muted-blue" />
                  </span>
                  <div>
                    <h3 className="font-display text-headline-sm text-primary">Technical Panel</h3>
                    <p className="font-sans text-label-sm text-on-surface-variant">
                      Product Intelligence Engine
                    </p>
                  </div>
                </div>
              </div>

              <div>
                <span className="label-caps text-on-surface-variant">Supported Categories</span>
                <div className="mt-3 flex flex-wrap gap-2">
                  {CATEGORIES.map((c) => (
                    <span
                      key={c}
                      className="rounded border border-line/20 bg-surface-container-low px-2.5 py-1 font-sans text-label-sm uppercase tracking-wider text-on-surface-variant"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              </div>

              <div className="flex-1">
                <span className="label-caps text-on-surface-variant">Enrichment Pipeline</span>
                <ol className="mt-3 space-y-0">
                  {[
                    ['Ingest', 'PDF datasheets + web sources'],
                    ['Extract', 'LLM-structured attribute extraction'],
                    ['Normalize', 'Unit conversion & taxonomy mapping'],
                    ['Validate', 'Confidence-scored output'],
                  ].map(([step, desc], i) => (
                    <li key={step} className="relative flex gap-4 pb-6 last:pb-0">
                      {i < 3 && (
                        <span className="absolute left-[7px] top-5 h-full w-px bg-line/30" />
                      )}
                      <span className="relative z-10 mt-1 h-4 w-4 shrink-0 rounded-full border border-outline-variant bg-surface-container-high text-center">
                        <span className="absolute inset-0 flex items-center justify-center font-mono text-[9px] text-on-surface-variant">
                          {i + 1}
                        </span>
                      </span>
                      <div>
                        <p className="font-sans text-body-md font-medium text-primary">{step}</p>
                        <p className="font-sans text-label-sm text-on-surface-variant">{desc}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </div>

              <div className="border-t border-line/10 pt-6">
                <p className="font-sans text-label-sm leading-relaxed text-on-surface-variant">
                  Tip: upload the manufacturer PDF datasheet to ground the extraction in
                  authoritative content — the engine cross-references both uploads and web
                  sources for every attribute.
                </p>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </>
  );
}
