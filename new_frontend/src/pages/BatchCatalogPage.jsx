import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { enrichBatch, getBatchStatus } from '../services/api';
import { useToasts } from '../context/ToastContext';
import { useWorkspace } from '../context/WorkspaceContext';
import AttributeTable from '../components/AttributeTable';
import ConfidencePill from '../components/ConfidencePill';
import Icon from '../components/Icon';
import StatCard from '../components/StatCard';
import StatusPill from '../components/StatusPill';
import { formatDuration, formatFileSize, formatPercent } from '../utils/format';

const ACCEPTED = ['.csv', '.xlsx'];
const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB client-side cap
const MAX_BATCH_ROWS = 2000; // mirrors backend _MAX_BATCH_ROWS

const SkeletonResultRow = memo(function SkeletonResultRow({ index }) {
  return (
    <tr className="animate-pulse border-b border-line/10 bg-surface-container-low/30">
      <td className="px-4 py-5">
        <div className="flex items-center gap-3">
          <div className="h-4 w-4 rounded bg-surface-container-high" />
          <div>
            <div className="h-4 w-28 rounded bg-surface-container-high" />
            <div className="mt-1 h-3 w-12 rounded bg-surface-container-high" />
          </div>
        </div>
      </td>
      <td className="px-4 py-5"><div className="h-4 w-24 rounded bg-surface-container-high" /></td>
      <td className="px-4 py-5"><div className="h-4 w-20 rounded bg-surface-container-high" /></td>
      <td className="px-4 py-5"><div className="h-5 w-16 rounded-full bg-surface-container-high" /></td>
      <td className="px-4 py-5 text-right"><div className="ml-auto h-4 w-12 rounded bg-surface-container-high" /></td>
      <td className="hidden px-4 py-5 text-right lg:table-cell"><div className="ml-auto h-4 w-12 rounded bg-surface-container-high" /></td>
    </tr>
  );
});

//: Identifier columns the backend can enrich from. Normalized form:
//: lowercase, whitespace/underscore/hyphen removed ("Part Number" ->
//: "partnumber"). At least one must be present in the header row. Includes
//: the official hackathon dataset headers (Mfg_Part_Num / Part_Manuf /
//: Part_Desc) alongside the human-friendly aliases.
const REQUIRED_IDENTIFIER_COLUMNS = [
  // Part number
  'mfgpartnum',
  'mfgpartnumber',
  'partnum',
  'partnumber',
  'partno',
  'sku',
  'part',
  // Manufacturer
  'partmanuf',
  'partmanufacturer',
  'mfgmanuf',
  'manufacturername',
  'manufacturer',
  'mfr',
  'brand',
  // Description
  'partdesc',
  'partdescription',
  'rawdescription',
  'description',
  // Category
  'category',
];

// Mirrors the backend's `_normalize_header`: lowercase + strip ALL
// non-alphanumerics ("Part No." -> "partno"), so frontend inspection and
// backend column matching agree on every header variant.
function normalizeHeader(value) {
  return String(value || '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]/g, '');
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
          'mfg_part_num, part_manuf, part_desc, manufacturer, part_number, or category.',
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

/**
 * Page-number window for the quick-jump buttons: always surfaces the first
 * few pages and the last page, plus a small window around the active page so
 * deep navigation stays smooth (renders as "[1] [2] [3] … [Last]").
 */
function getPageWindow(current, total) {
  if (total <= 5) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const wanted = new Set([1, 2, 3, total, current - 1, current, current + 1]);
  return [...wanted]
    .filter((page) => page >= 1 && page <= total)
    .sort((a, b) => a - b);
}

/**
 * Memoized per-row result component. Receives a stable `onToggle` callback
 * (from useCallback) plus reference-stable `row`/`index` props, so only the
 * row whose `expanded` flag actually changes re-renders during batch status
 * updates — the other 499 rows are skipped.
 */
const BatchResultRow = memo(function BatchResultRow({ row, index, expanded, onToggle }) {
  const hasAttributes = row.status === 'success' && (row.enriched_attributes || []).length > 0;
  const hasError = row.status === 'error' && row.error;

  return (
    <>
      <tr className="group cursor-pointer transition-colors duration-200 hover:bg-surface-container-low" onClick={() => onToggle(index)}>
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
});

/**
 * Dedicated Stage-2 processing card, shown while the backend enriches the
 * uploaded catalog. The batch endpoint is a single HTTP request that returns
 * every row at once, so per-row progress is estimated client-side (see the
 * ramp interval in the page component) and snaps to the real count when the
 * response lands. It replaces the dropzone so the UI clearly signals that
 * AI extraction & unit normalization are running.
 */
const ProcessingCard = memo(function ProcessingCard({ completedItems, totalItems }) {
  const known = totalItems != null && totalItems > 0;
  // Estimated progress, floored at 5% so a running stage never shows a dead
  // 0% bar (e.g. while the first item is still in flight).
  const pct = known
    ? Math.max(5, Math.min(100, Math.round((completedItems / totalItems) * 100)))
    : 0;
  const current = known ? Math.min(completedItems + 1, totalItems) : completedItems;

  return (
    <div
      role="status"
      aria-live="polite"
      className="dashed-border-subtle animate-fade-in relative flex min-h-[320px] flex-col items-center justify-center overflow-hidden rounded-lg p-10 text-center md:p-16"
    >
      <div className="pointer-events-none absolute -bottom-20 -right-20 opacity-5">
        <Icon name="description" size={300} />
      </div>

      {/* Pulsing aura + spinning gear */}
      <div className="relative flex h-20 w-20 items-center justify-center">
        <span className="absolute inset-0 animate-ping rounded-full bg-tertiary-fixed/15" />
        <span className="absolute inset-3 rounded-full bg-tertiary-fixed/10" />
        <Icon name="autorenew" size={36} className="relative z-10 animate-spin text-tertiary-fixed" />
      </div>

      <h3 className="mt-8 font-display text-headline-md text-primary">
        Enriching Industrial Specs…
      </h3>
      <p className="mt-2 font-sans text-body-md text-on-surface-variant">
        Scraping datasheets &amp; normalizing technical units…
      </p>

      {known ? (
        <div className="mt-8 w-full max-w-md">
          <div className="flex items-center justify-between font-mono text-label-sm text-on-surface-variant">
            <span>
              Processing item {current.toLocaleString()} of {totalItems.toLocaleString()}
            </span>
            <span>{pct}%</span>
          </div>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-surface-container">
            <div
              className="h-full bg-tertiary-fixed transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="mt-8 w-full max-w-md">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-container">
            <div className="h-full w-1/3 animate-pulse-soft rounded-full bg-tertiary-fixed" />
          </div>
        </div>
      )}
    </div>
  );
});

export default function BatchCatalogPage() {
  const { setBatchResult, runExport, exporting } = useWorkspace();
  const { notify } = useToasts();

  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState('');
  const [schemaWarning, setSchemaWarning] = useState('');
  const [dragging, setDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [status, setStatusState] = useState('idle'); // idle | uploading | processing | streaming | done
  const statusRef = useRef('idle');
  const [activeJobId, setActiveJobId] = useState(null);

  const setStatus = (next) => {
    statusRef.current = next;
    setStatusState(next);
  };
  const [apiError, setApiError] = useState('');
  const [result, setResult] = useState(null);
  const [totalItems, setTotalItems] = useState(null);
  const [completedItems, setCompletedItems] = useState(0);
  const [expanded, setExpanded] = useState(new Set());
  // Results-table pagination + search.
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(20); // 20 rows per page
  const [searchQuery, setSearchQuery] = useState('');
  const fileInputRef = useRef(null);

  // Poll backend for micro-chunk background processing status
  useEffect(() => {
    if (!activeJobId || status === 'done') return undefined;

    let isMounted = true;
    let timer = null;

    const pollJob = async () => {
      try {
        const job = await getBatchStatus(activeJobId);
        if (!isMounted) return;

        setTotalItems(job.total_rows);
        setCompletedItems(job.completed_rows);

        const mappedResult = {
          total: job.total_rows,
          succeeded: job.succeeded_count,
          failed: job.failed_count,
          results: job.records || [],
          is_complete: job.is_complete,
        };
        setResult(mappedResult);
        setBatchResult(mappedResult);

        if (job.completed_rows >= 4 || (job.records && job.records.length > 0)) {
          if (statusRef.current === 'uploading' || statusRef.current === 'processing') {
            setStatus('streaming');
          }
        }

        if (job.is_complete) {
          setStatus('done');
          setActiveJobId(null);
          if (timer) window.clearInterval(timer);
          notify(
            `Batch complete — ${job.succeeded_count} enriched, ${job.failed_count} failed.`,
            job.failed_count === 0 ? 'success' : 'info'
          );
        }
      } catch (err) {
        console.error('Error polling batch status:', err);
        const statusCode = err?.response?.status || err?.status;
        if (statusCode === 404) {
          if (timer) window.clearInterval(timer);
          if (isMounted) {
            setActiveJobId(null);
            setStatus('idle');
            setApiError('Batch session expired or server restarted. Please re-run the batch.');
            notify('Batch session expired or server restarted. Please re-run the batch.', 'error');
          }
        }
      }
    };

    pollJob();
    timer = window.setInterval(pollJob, 2000);
    return () => {
      isMounted = false;
      if (timer) window.clearInterval(timer);
    };
  }, [activeJobId, status]);

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
    setSearchQuery('');
    setCurrentPage(1);
    setActiveJobId(null);
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

    const ext = (target.name.toLowerCase().match(/\.[a-z0-9]+$/) || [''])[0];
    if (!ext || !ACCEPTED.includes(ext)) {
      setFileError('Batch file must be a .csv or .xlsx spreadsheet.');
      return;
    }
    if (target.size > MAX_FILE_SIZE_BYTES) {
      notify('File size exceeds 25 MB limit.', 'error');
      return;
    }

    setTotalItems(null);
    setCompletedItems(0);
    setStatus('uploading');
    setUploadProgress(0);

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
            const pct = Math.round((progressEvent.loaded / progressEvent.total) * 100);
            setUploadProgress(pct);
            if (pct >= 100 && statusRef.current === 'uploading') {
              setStatus('processing');
            }
          }
        },
      });

      if (response && response.job_id) {
        setActiveJobId(response.job_id);
        setStatus('processing');
      } else {
        setResult(response);
        setBatchResult(response);
        setCompletedItems(response.total);
        setStatus('done');
        setSearchQuery('');
        setCurrentPage(1);
        notify(
          `Batch complete — ${response.succeeded} enriched, ${response.failed} failed.`,
          response.failed === 0 ? 'success' : 'info'
        );
      }
    } catch (err) {
      setApiError(err.message);
      setStatus('idle');
    }
  };

  // Stable identity (functional setState, no deps) so memoized rows skip
  // re-renders while unrelated state churns during upload/processing.
  const toggleRow = useCallback((idx) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }, []);

  // Expensive-ish derived value: recomputed only when the result changes.
  const avgConfidence = useMemo(() => averageConfidence(result), [result]);
  const busy = status === 'uploading' || status === 'processing';

  // ---- Results-table filtering + pagination -------------------------------
  // Case-insensitive search across the visible fields. Rows are paired with
  // their ORIGINAL index into `result.results` so the "Row N" label and the
  // `expanded` Set stay correct while a filter or page is active.
  const filteredResults = useMemo(() => {
    const rows = result?.results || [];
    const query = searchQuery.trim().toLowerCase();
    const pairs = rows.map((row, index) => ({ row, index }));
    if (!query) return pairs;
    return pairs.filter(({ row }) =>
      [row.sku_id, row.manufacturer_name, row.part_number, row.category, row.status].some(
        (field) => String(field || '').toLowerCase().includes(query)
      )
    );
  }, [result, searchQuery]);

  const totalPages = Math.max(1, Math.ceil(filteredResults.length / pageSize));
  // Clamp the active page to the (possibly shrunken) filtered result set so
  // slicing/display never go out of range even for one render.
  const safePage = Math.min(currentPage, totalPages);
  const paginatedResults = filteredResults.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize
  );
  const rangeStart = filteredResults.length === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const rangeEnd = Math.min(safePage * pageSize, filteredResults.length);

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
          {/* Upload / processing zone */}
          <div className="lg:col-span-8">
            {status === 'processing' ? (
              <ProcessingCard completedItems={completedItems} totalItems={totalItems} />
            ) : (
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
                        setSearchQuery('');
                        setCurrentPage(1);
                        clearErrors();
                        resetInputValue();
                      }}
                      className="btn-ghost mt-8"
                    >
                      <Icon name="upload_file" size={18} />
                      New Upload
                    </button>
                </div>
              ) : status === 'uploading' ? (
                <div className="z-10 w-full max-w-md text-center">
                  <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full border border-line/20 bg-surface-container">
                    <Icon name="upload_file" size={32} className="text-tertiary-fixed" />
                  </div>
                  <h3 className="font-display text-headline-md text-primary">Uploading catalog…</h3>
                  <div className="mt-6 h-1 w-full overflow-hidden rounded-full bg-surface-container">
                    <div
                      className="h-full bg-tertiary-fixed transition-all duration-200"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                  <p className="mt-4 font-mono text-label-sm text-on-surface-variant">
                    {uploadProgress}%
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
                      <button
                        type="button"
                        onClick={(e) => {
                          // Stop the click from bubbling to the dropzone, which
                          // would otherwise open the file picker dialog.
                          e.stopPropagation();
                          handleProcess();
                        }}
                        className="btn-primary"
                      >
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
                    Supported columns:{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Mfg_Part_Num</code>,{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Part_Manuf</code>,{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Part_Desc</code>
                    <span className="mx-2 text-outline">or</span>
                    <code className="font-mono text-tertiary-fixed-dim">Manufacturer</code>,{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Part_Number</code>,{' '}
                    <code className="font-mono text-tertiary-fixed-dim">Category</code>
                    <span className="mx-2 text-outline">·</span>up to {MAX_BATCH_ROWS.toLocaleString()} rows
                    <span className="mx-2 text-outline">·</span>max 25 MB
                  </p>
                </div>
              )}
              </div>
            )}

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
              disabled={exporting || !result || !result.is_complete || result.succeeded === 0}
              className={`w-full ${result && result.is_complete ? 'btn-primary' : 'btn-ghost'}`}
            >
              <Icon
                name={exporting ? 'sync' : result && result.is_complete ? 'check_circle' : 'download'}
                size={18}
                className={exporting ? 'animate-spin' : ''}
              />
              {exporting
                ? 'Exporting…'
                : !result
                ? 'Export Results to Excel'
                : !result.is_complete
                ? `Processing Catalog (${Math.round((completedItems / (totalItems || 1)) * 100)}%)…`
                : 'Export Results to Excel'}
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
              <div className="flex flex-wrap items-center gap-3">
                <div className="relative">
                  <Icon
                    name="search"
                    size={18}
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-outline"
                  />
                  <input
                    type="search"
                    value={searchQuery}
                    onChange={(e) => {
                      setSearchQuery(e.target.value);
                      setCurrentPage(1);
                    }}
                    placeholder="Search SKU, manufacturer, part…"
                    aria-label="Filter results"
                    className="w-64 max-w-full rounded-lg border border-line/25 bg-surface-container py-2 pl-9 pr-3 font-sans text-body-md text-primary outline-none transition-colors placeholder:text-on-surface-variant/60 focus:border-tertiary-fixed/60"
                  />
                </div>
                <span className="font-mono text-label-sm text-on-surface-variant">
                  {result.succeeded}/{result.total || totalItems} succeeded
                </span>
              </div>
            </div>

            {completedItems < rangeStart && totalItems > 0 && (
              <div className="mb-6 flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-amber-300">
                <Icon name="hourglass_top" size={20} className="animate-spin text-amber-400" />
                <p className="font-sans text-body-sm">
                  ⏳ Record #{rangeStart} is currently processing in the background. Meanwhile, feel free to review the records already loaded above!
                </p>
              </div>
            )}

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
                  {paginatedResults.map(({ row, index }) => (
                    <BatchResultRow
                      key={`${row.sku_id}-${index}`}
                      row={row}
                      index={index}
                      expanded={expanded.has(index)}
                      onToggle={toggleRow}
                    />
                  ))}
                  {completedItems < rangeStart &&
                    Array.from({ length: 10 }).map((_, i) => (
                      <SkeletonResultRow key={`skeleton-${i}`} index={i} />
                    ))}
                  {paginatedResults.length === 0 && completedItems >= rangeStart && (
                    <tr>
                      <td colSpan={6} className="px-4 py-14 text-center">
                        <div className="flex flex-col items-center gap-3">
                          <Icon
                            name={searchQuery ? 'search_off' : 'inbox'}
                            size={32}
                            className="text-outline"
                          />
                          <p className="font-sans text-body-md text-on-surface-variant">
                            {searchQuery
                              ? `No rows match “${searchQuery}”. Try a different search.`
                              : 'No results to display.'}
                          </p>
                        </div>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination controls */}
            <div className="mt-6 flex flex-col items-center justify-between gap-4 border-t border-line/10 pt-6 md:flex-row">
              <p className="font-mono text-label-sm text-on-surface-variant">
                Showing {rangeStart}–{rangeEnd} of {filteredResults.length.toLocaleString()} items
                <span className="mx-2 text-outline">(Page {safePage} of {totalPages})</span>
              </p>
              <nav aria-label="Pagination" className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
                  disabled={currentPage <= 1}
                  className="btn-ghost !px-4"
                >
                  <Icon name="chevron_left" size={18} />
                  Previous
                </button>

                {getPageWindow(safePage, totalPages).map((page, i, arr) => {
                  const gap = i > 0 && page - arr[i - 1] > 1;
                  return (
                    <Fragment key={page}>
                      {gap && (
                        <span className="px-1 font-mono text-label-sm text-outline">…</span>
                      )}
                      <button
                        type="button"
                        onClick={() => setCurrentPage(page)}
                        aria-current={page === safePage ? 'page' : undefined}
                        className={
                          page === safePage
                            ? 'btn-primary !px-3 !py-2'
                            : 'btn-ghost !px-3 !py-2'
                        }
                      >
                        {page}
                      </button>
                    </Fragment>
                  );
                })}

                <button
                  type="button"
                  onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))}
                  disabled={currentPage >= totalPages}
                  className="btn-ghost !px-4"
                >
                  Next
                  <Icon name="chevron_right" size={18} />
                </button>
              </nav>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
