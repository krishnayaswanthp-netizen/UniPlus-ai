import { useRef, useState } from 'react';
import { enrichBatch } from '../services/api';
import { useWorkspace } from '../context/WorkspaceContext';
import AttributeTable from '../components/AttributeTable';
import ConfidencePill from '../components/ConfidencePill';
import Icon from '../components/Icon';
import StatCard from '../components/StatCard';
import StatusPill from '../components/StatusPill';
import { formatDuration, formatFileSize, formatPercent } from '../utils/format';

const ACCEPTED = ['.csv', '.xlsx'];
const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB client-side cap
const MAX_BATCH_ROWS = 500;

//: Identifier columns the backend can enrich from. Normalized form:
//: lowercase, whitespace/underscore/hyphen removed ("Part Number" ->
//: "partnumber"). At least one must be present in the header row.
const REQUIRED_IDENTIFIER_COLUMNS = [
  'partnumber',
  'part',
  'sku',
  'manufacturer',
  'manufacturername',
  'category',
];

function normalizeHeader(value) {
  return String(value || '')
    .toLowerCase()
    .trim()
    .replace(/[\s_\-]+/g, '');
}

/** Split one CSV line into fields, honoring double-quoted cells. */
function parseCsvLine(line) {
  const fields = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          current += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        current += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      fields.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  fields.push(current);
  return fields;
}

/**
 * Client-side pre-flight inspection of an upload.
 *
 * CSV files get full schema/header + data-row validation; Excel files cannot
 * be inspected without a heavier client-side parser, so only the empty-file
 * guard applies there (the backend validates their contents).
 *
 * Returns { ok, error?, headers?, dataRowCount? }.
 */
async function inspectBatchFile(file) {
  const ext = (file.name.toLowerCase().match(/\.[a-z0-9]+$/) || [''])[0];

  if (ext === '.csv') {
    const text = (await file.text()).replace(/^\uFEFF/, '');
    const lines = text.split(/\r?\n/).filter((line) => line.trim() !== '');
    if (!lines.length) {
      return { ok: false, error: 'The file is empty — no header row or data found.' };
    }
    const headers = parseCsvLine(lines[0]).map((cell) => cell.trim());
    const dataRowCount = lines
      .slice(1)
      .filter((line) => parseCsvLine(line).some((cell) => cell.trim() !== '')).length;

    if (dataRowCount === 0) {
      return { ok: false, error: 'The file has a header row but no data rows to enrich.' };
    }
    const hasIdentifier = headers.some((header) =>
      REQUIRED_IDENTIFIER_COLUMNS.includes(normalizeHeader(header))
    );
    if (!hasIdentifier) {
      return {
        ok: false,
        error:
          'Missing required identifier columns. Expected at least one of: ' +
          'part_number, part, sku, manufacturer, manufacturer_name, or category.',
        headers,
      };
    }
    return { ok: true, headers, dataRowCount };
  }

  if (file.size === 0) {
    return { ok: false, error: 'The file is empty — it contains no data.' };
  }
  return { ok: true, dataRowCount: null };
}

function averageConfidence(result) {
  const successes = (result?.results || []).filter((r) => r.status === 'success');
  if (!successes.length) return null;
  const sum = successes.reduce((acc, r) => acc + (r.overall_confidence || 0), 0);
  return sum / successes.length;
}

function BatchResultRow({ row, index, expanded, onToggle }) {
  const hasAttributes = row.status === 'success' && (row.enriched_attributes || []).length > 0;
  const hasError = row.status === 'error' && row.error;

  return (
    <>
      <tr className="group cursor-pointer transition-colors duration-200 hover:bg-surface-container-low" onClick={onToggle}>
        <td className="px-4 py-5">
          <div className="flex items-center gap-3">
            <Icon
              name={expanded ? 'expand_more' : 'chevron_right'}
              size={18}
              className="shrink-0 text-outline transition-transform duration-200"
            />
            <div>
              <div className="font-sans text-body-md font-medium text-primary">{row.sku_id}</div>
              <div className="font-sans text-label-sm text-on-surface-variant">Row {index + 1}</div>
            </div>
          </div>
        </td>
        <td className="px-4 py-5 font-sans text-body-md text-secondary">{row.manufacturer_name}</td>
        <td className="px-4 py-5 font-mono text-body-md text-primary">{row.part_number}</td>
        <td className="px-4 py-5">
          <StatusPill status={row.status} label={row.status === 'success' ? 'Enriched' : 'Failed'} />
        </td>
        <td className="px-4 py-5 text-right">
          {row.status === 'success' ? (
            <ConfidencePill value={row.overall_confidence} />
          ) : (
            <span className="font-sans text-label-sm text-on-surface-variant">—</span>
          )}
        </td>
        <td className="hidden px-4 py-5 text-right font-mono text-body-md text-on-surface-variant lg:table-cell">
          {formatDuration(row.processing_time_ms)}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-line/10 bg-surface-container-lowest/60">
          <td colSpan={6} className="px-6 py-6">
            {hasAttributes ? (
              <div>
                <p className="label-caps mb-4 text-on-surface-variant">
                  Extracted Attributes — {row.sku_id}
                </p>
                <AttributeTable attributes={row.enriched_attributes} compact />
              </div>
            ) : (
              <div className="flex items-start gap-3">
                <Icon name="warning" size={20} className="mt-0.5 shrink-0 text-error" />
                <div>
                  <p className="font-sans text-body-md font-medium text-on-error-container">
                    {hasError ? 'This row could not be enriched' : 'No attributes extracted'}
                  </p>
                  {hasError && (
                    <p className="mt-1 font-sans text-body-md text-on-error-container/80">{row.error}</p>
                  )}
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function BatchCatalogPage() {
  const { setBatchResult, runExport, exporting, notify } = useWorkspace();

  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState('');
  const [schemaWarning, setSchemaWarning] = useState('');
  const [dragging, setDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [status, setStatus] = useState('idle'); // idle | uploading | processing | done
  const [apiError, setApiError] = useState('');
  const [result, setResult] = useState(null);
  const [totalItems, setTotalItems] = useState(null);
  const [completedItems, setCompletedItems] = useState(0);
  const [expanded, setExpanded] = useState(new Set());
  const fileInputRef = useRef(null);

  const clearErrors = () => {
    setFileError('');
    setSchemaWarning('');
    setApiError('');
  };

  const resetInputValue = () => {
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const acceptFile = (candidate) => {
    setFile(candidate);
    setResult(null);
    setExpanded(new Set());
    setTotalItems(null);
    setCompletedItems(0);
    clearErrors();
  };

  const validFile = (candidate) => {
    if (!candidate) return false;
    const ext = candidate.name.toLowerCase().match(/\.[a-z0-9]+$/)?.[0];
    if (!ext || !ACCEPTED.includes(ext)) {
      setFileError('Batch file must be a .csv or .xlsx spreadsheet.');
      return false;
    }
    if (candidate.size > MAX_FILE_SIZE_BYTES) {
      setFileError('');
      notify('File size exceeds 25 MB limit.', 'error');
      return false;
    }
    setFileError('');
    return true;
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (busy) return; // don't swap the file under an in-flight upload
    const dropped = e.dataTransfer?.files?.[0];
    if (dropped && validFile(dropped)) {
      acceptFile(dropped);
      resetInputValue();
    }
  };

  const handleProcess = async () => {
    if (!file) {
      notify('Select a CSV or Excel catalog first.', 'error');
      return;
    }
    clearErrors();

    const target = file;

    // Pre-flight: extension + size are already checked on selection, but
    // re-check defensively before spending an upload on a bad file.
    const ext = (target.name.toLowerCase().match(/\.[a-z0-9]+$/) || [''])[0];
    if (!ext || !ACCEPTED.includes(ext)) {
      setFileError('Batch file must be a .csv or .xlsx spreadsheet.');
      return;
    }
    if (target.size > MAX_FILE_SIZE_BYTES) {
      notify('File size exceeds 25 MB limit.', 'error');
      return;
    }

    // Mark the UI busy BEFORE the async inspection so the user cannot swap
    // or clear the file while it is being read.
    setTotalItems(null);
    setCompletedItems(0);
    setStatus('uploading');
    setUploadProgress(0);

    // Schema/header + empty-file guard (full inspection for CSV).
    let inspection;
    try {
      inspection = await inspectBatchFile(target);
    } catch {
      setSchemaWarning('Could not read the file — it may be corrupted or unsupported.');
      setStatus('idle');
      return;
    }
    if (!inspection.ok) {
      setSchemaWarning(inspection.error);
      setStatus('idle');
      return;
    }
    if (inspection.dataRowCount !== null && inspection.dataRowCount > MAX_BATCH_ROWS) {
      setSchemaWarning(
        `This catalog has ${inspection.dataRowCount.toLocaleString()} data rows, ` +
          `which exceeds the ${MAX_BATCH_ROWS.toLocaleString()}-row processing limit.`
      );
      setStatus('idle');
      return;
    }

    setTotalItems(inspection.dataRowCount);
    try {
      const response = await enrichBatch(target, {
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            setUploadProgress(Math.round((progressEvent.loaded / progressEvent.total) * 100));
          }
        },
      });
      setResult(response);
      setBatchResult(response);
      setCompletedItems(response.total);
      setStatus('done');
      notify(
        `Batch complete — ${response.succeeded} enriched, ${response.failed} failed.`,
        response.failed === 0 ? 'success' : 'info'
      );
    } catch (err) {
      setApiError(err.message);
      setStatus('idle');
    }
  };

  const toggleRow = (idx) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const avgConfidence = averageConfidence(result);
  const busy = status === 'uploading' || status === 'processing';

  return (
    <>
      {/* Page header */}
      <header className="mx-auto w-full max-w-shell px-6 pb-14 pt-14 md:px-container-padding">
        <div className="max-w-3xl border-b border-line/10 pb-10">
          <h1 className="font-display text-headline-lg text-primary md:text-display-lg">
            Batch Processing.
          </h1>
          <p className="mt-4 font-sans text-body-lg text-on-surface-variant">
            Upload and process large catalogs. The engine extracts dimensional data, material
            specifications, and cross-references external datasets to enrich raw SKUs with precision.
          </p>
        </div>
      </header>

      <div className="mx-auto w-full max-w-shell px-6 pb-24 md:px-container-padding">
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
          {/* Upload zone */}
          <div className="lg:col-span-8">
            <div
              aria-label="Upload a CSV or Excel catalog"
              onClick={() => {
                if (!busy) fileInputRef.current?.click();
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              className={`dashed-border-subtle group relative flex min-h-[320px] cursor-pointer flex-col items-center justify-center overflow-hidden rounded-lg p-10 transition-colors duration-500 md:p-16 ${
                dragging
                  ? 'bg-surface-container-high'
                  : 'bg-surface hover:bg-surface-container-low'
              }`}
            >
              <div className="pointer-events-none absolute -bottom-20 -right-20 opacity-5 transition-transform duration-700 group-hover:scale-110">
                <Icon name="description" size={300} />
              </div>

              {status === 'done' && result ? (
                <div className="z-10 text-center">
                  <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full border border-tertiary-fixed/30 bg-tertiary-fixed/10">
                    <Icon name="task_alt" size={36} className="text-tertiary-fixed" />
                  </div>
                  <h3 className="font-display text-headline-md text-primary">Catalog processed</h3>
                  <p className="mx-auto mt-2 max-w-md font-sans text-body-md text-on-surface-variant">
                    <span className="font-medium text-tertiary-fixed">{result.succeeded}</span> of{' '}
                    {result.total} rows enriched. Review the queue below or start a new upload.
                  </p>
                    <button
                      type="button"
                      onClick={() => {
                        setFile(null);
                        setResult(null);
                        setStatus('idle');
                        setTotalItems(null);
                        setCompletedItems(0);
                        clearErrors();
                        resetInputValue();
                      }}
                      className="btn-ghost mt-8"
                    >
                      <Icon name="upload_file" size={18} />
                      New Upload
                    </button>
                </div>
              ) : busy ? (
                <div className="z-10 w-full max-w-md text-center">
                  <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full border border-line/20 bg-surface-container">
                    <Icon
                      name={status === 'uploading' ? 'upload_file' : 'autorenew'}
                      size={32}
                      className={status === 'processing' ? 'animate-spin text-tertiary-fixed' : 'text-tertiary-fixed'}
                    />
                  </div>
                  <h3 className="font-display text-headline-md text-primary">
                    {status === 'uploading' ? 'Uploading catalog…' : 'Enriching rows…'}
                  </h3>
                  {status === 'uploading' ? (
                    <div className="mt-6 h-1 w-full overflow-hidden rounded-full bg-surface-container">
                      <div
                        className="h-full bg-tertiary-fixed transition-all duration-200"
                        style={{ width: `${uploadProgress}%` }}
                      />
                    </div>
                  ) : (
                    <div className="mt-6 w-full">
                      {totalItems != null ? (
                        <>
                          <div className="flex items-center justify-between font-mono text-label-sm text-on-surface-variant">
                            <span>
                              {completedItems.toLocaleString()} /{' '}
                              {totalItems.toLocaleString()} items completed
                            </span>
                            <span>
                              {Math.round((completedItems / totalItems) * 100)}%
                            </span>
                          </div>
                          <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-surface-container">
                            <div
                              className="h-full bg-tertiary-fixed transition-all duration-300"
                              style={{
                                width: `${(completedItems / totalItems) * 100}%`,
                              }}
                            />
                          </div>
                        </>
                      ) : (
                        <div className="flex items-center justify-center gap-3">
                          <span className="h-2 w-2 animate-pulse-soft rounded-full bg-tertiary-fixed" />
                          <span className="font-sans text-body-md text-tertiary-fixed">
                            The engine is processing rows concurrently — this may take a minute.
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                  <p className="mt-4 font-mono text-label-sm text-on-surface-variant">
                    {status === 'uploading' ? `${uploadProgress}%` : file?.name}
                  </p>
                </div>
              ) : (
                <div className="z-10 text-center">
                  <div className="mx-auto mb-8 flex h-20 w-20 items-center justify-center rounded-full border border-line/10 bg-surface-container-highest">
                    <Icon name="upload_file" size={32} className="text-primary" />
                  </div>
                  <h3 className="font-display text-headline-md text-primary">
                    {file ? 'Ready to process' : 'Ingest Catalog Data'}
                  </h3>
                  <p className="mx-auto mt-2 max-w-md font-sans text-body-md text-on-surface-variant">
                    {file ? (
                      <>
                        <span className="font-medium text-primary">{file.name}</span>
                        <span className="mx-2 text-outline">·</span>
                        {formatFileSize(file.size)}
                      </>
                    ) : (
                      'Drag and drop your raw inventory file here. Supported formats: .csv, .xlsx.'
                    )}
                  </p>

                  {file ? (
                    <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                      <button type="button" onClick={handleProcess} className="btn-primary">
                        <Icon name="play_arrow" size={18} fill />
                        Process Catalog
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setFile(null);
                          setTotalItems(null);
                          setCompletedItems(0);
                          clearErrors();
                          resetInputValue();
                        }}
                        className="btn-ghost"
                      >
                        <Icon name="close" size={18} />
                        Remove
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="btn-primary mt-8"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (!busy) fileInputRef.current?.click();
                      }}
                    >
                      Select Files
                    </button>
                  )}

                  <p className="mt-6 font-sans text-label-sm text-on-surface-variant">
                    Required columns:{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Manufacturer</code>,{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Part_Number</code>,{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Category</code>
                    <span className="mx-2 text-outline">·</span>up to {MAX_BATCH_ROWS.toLocaleString()} rows
                    <span className="mx-2 text-outline">·</span>max 25 MB
                  </p>
                </div>
              )}
            </div>

            {fileError && (
              <p className="mt-3 flex items-center gap-2 font-sans text-label-sm text-error">
                <Icon name="error" size={16} /> {fileError}
              </p>
            )}
            {schemaWarning && (
              <div className="mt-6 flex items-start gap-3 rounded-lg border border-warning/40 bg-warning/10 p-4">
                <Icon name="warning" size={20} fill className="mt-0.5 shrink-0 text-warning" />
                <div>
                  <p className="font-sans text-body-md font-medium text-on-warning-container">
                    File validation failed
                  </p>
                  <p className="mt-1 font-sans text-body-md text-on-warning-container/80">
                    {schemaWarning}
                  </p>
                </div>
              </div>
            )}
            {apiError && (
              <div className="mt-6 flex items-start gap-3 rounded-lg border border-error/25 bg-error/5 p-4">
                <Icon name="error" size={20} fill className="mt-0.5 shrink-0 text-error" />
                <div>
                  <p className="font-sans text-body-md font-medium text-on-error-container">
                    Batch processing failed
                  </p>
                  <p className="mt-1 font-sans text-body-md text-on-error-container/80">{apiError}</p>
                </div>
              </div>
            )}

            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx"
              className="hidden"
              onChange={(e) => {
                const picked = e.target.files?.[0];
                if (picked && validFile(picked)) {
                  acceptFile(picked);
                }
                // Reset the input so re-selecting the same file fires onChange again.
                resetInputValue();
              }}
            />
          </div>

          {/* Metrics column */}
          <div className="flex flex-col gap-8 lg:col-span-4">
            <StatCard
              icon="inventory_2"
              label="Products Processed"
              value={result ? result.total.toLocaleString() : '—'}
              sub={result ? `Batch complete` : 'Awaiting upload'}
            />
            <div className="grid grid-cols-2 gap-4">
              <StatCard
                icon="check_circle"
                label="Succeeded"
                value={result ? result.succeeded.toLocaleString() : '—'}
                progress={result && result.total ? result.succeeded / result.total : undefined}
              />
              <StatCard
                icon="cancel"
                label="Failed"
                value={result ? result.failed.toLocaleString() : '—'}
                sub={result && result.failed === 0 ? 'Zero failures' : undefined}
              />
            </div>
            <StatCard
              icon="insights"
              label="Avg Confidence"
              value={avgConfidence !== null ? formatPercent(avgConfidence) : '—'}
              progress={avgConfidence ?? undefined}
            />
            <button
              type="button"
              onClick={runExport}
              disabled={exporting || !result || result.succeeded === 0}
              className="btn-ghost w-full"
            >
              <Icon name={exporting ? 'sync' : 'download'} size={18} className={exporting ? 'animate-spin' : ''} />
              {exporting ? 'Exporting…' : 'Export Results to Excel'}
            </button>
          </div>
        </div>

        {/* Results queue */}
        {result && (
          <section className="mt-20">
            <div className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-line/15 pb-6">
              <div>
                <h2 className="font-display text-headline-lg text-primary">Processing Results</h2>
                <p className="mt-1 font-sans text-body-md text-on-surface-variant">
                  Per-row enrichment status for <span className="text-primary">{file?.name}</span>
                </p>
              </div>
              <span className="font-mono text-label-sm text-on-surface-variant">
                {result.succeeded}/{result.total} succeeded
              </span>
            </div>

            <div className="w-full overflow-x-auto">
              <table className="w-full border-collapse text-left">
                <thead>
                  <tr className="label-caps border-b border-line/10 text-on-surface-variant">
                    <th className="px-4 py-5 font-semibold">SKU</th>
                    <th className="px-4 py-5 font-semibold">Manufacturer</th>
                    <th className="px-4 py-5 font-semibold">Part Number</th>
                    <th className="px-4 py-5 font-semibold">Status</th>
                    <th className="px-4 py-5 text-right font-semibold">Confidence</th>
                    <th className="hidden px-4 py-5 text-right font-semibold lg:table-cell">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/10">
                  {result.results.map((row, idx) => (
                    <BatchResultRow
                      key={`${row.sku_id}-${idx}`}
                      row={row}
                      index={idx}
                      expanded={expanded.has(idx)}
                      onToggle={() => toggleRow(idx)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
