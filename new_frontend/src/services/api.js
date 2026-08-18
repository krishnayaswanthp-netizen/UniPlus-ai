/**
 * UniPulse AI — API service layer.
 *
 * Thin, typed(-ish) wrapper around the FastAPI backend at
 * `VITE_API_BASE_URL` (defaults to http://127.0.0.1:8000).
 *
 * Endpoints wired here:
 *   POST /api/v1/enrich/single  — multipart/form-data single-SKU enrichment
 *   POST /api/v1/enrich/batch   — multipart/form-data CSV/Excel batch upload
 *   POST /api/v1/export/excel   — render enriched records to a .xlsx blob
 *
 * All network errors are normalized into `ApiError` instances so the UI can
 * render a single, human-readable message (FastAPI's `{"detail": ...}` shape
 * is parsed out automatically, including 422 validation-error arrays).
 */

import axios from 'axios';

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
).replace(/\/+$/, '');

/** Valid `category` values — mirrors the backend `Category` Literal. */
export const CATEGORIES = ['HVAC', 'Plumbing', 'Electrical', 'General'];

/** Long timeout: single enrichment performs web search + LLM extraction. */
const REQUEST_TIMEOUT_MS = 5 * 60 * 1000;

export class ApiError extends Error {
  constructor(message, status = 0, details = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: REQUEST_TIMEOUT_MS,
});

/**
 * Normalize any axios failure into an ApiError with a readable message.
 */
function normalizeError(err) {
  if (axios.isAxiosError(err) && err.response) {
    const data = err.response.data;
    let message = data?.detail;
    if (Array.isArray(message)) {
      // FastAPI 422 validation errors: [{ loc, msg, type }, ...]
      message = message.map((item) => item.msg).join('; ');
    } else if (typeof message !== 'string') {
      message = err.response.statusText || 'Request failed';
    }
    return new ApiError(
      message || `Request failed with status ${err.response.status}`,
      err.response.status,
      data
    );
  }
  if (axios.isAxiosError(err) && err.code === 'ECONNABORTED') {
    return new ApiError(
      'The request timed out — the enrichment engine may be busy. Please try again.',
      0
    );
  }
  if (axios.isAxiosError(err) && err.request) {
    return new ApiError(
      `Cannot reach the UniPulse backend at ${API_BASE_URL}. ` +
        'Is the API server running? (uvicorn app.main:app --reload)',
      0
    );
  }
  return new ApiError(err.message || 'Unexpected error', 0);
}

/**
 * Enrich a single product.
 *
 * @param {object} payload
 * @param {string} payload.manufacturerName — required
 * @param {string} payload.partNumber        — required
 * @param {string} payload.category          — one of CATEGORIES (required)
 * @param {string} [payload.rawDescription]  — optional context / research notes
 * @param {File}   [payload.file]            — optional PDF datasheet
 * @returns {Promise<object>} ProductEnrichmentResponse
 */
export async function enrichSingle({
  manufacturerName,
  partNumber,
  category,
  rawDescription = '',
  file = null,
}) {
  const form = new FormData();
  form.append('manufacturer_name', manufacturerName);
  form.append('part_number', partNumber);
  form.append('category', category);
  if (rawDescription && rawDescription.trim()) {
    form.append('raw_description', rawDescription.trim());
  }
  if (file) form.append('file', file);

  try {
    // Do NOT set Content-Type manually — axios lets the browser add the
    // multipart boundary for FormData.
    const { data } = await http.post('/api/v1/enrich/single', form);
    return data;
  } catch (err) {
    throw normalizeError(err);
  }
}

/**
 * Enrich a batch catalog file (.csv / .xlsx).
 *
 * @param {File} file
 * @param {object} [options]
 * @param {(progressEvent: import('axios').AxiosProgressEvent) => void} [options.onUploadProgress]
 * @returns {Promise<object>} — `{ total, succeeded, failed, results[] }`
 */
export async function enrichBatch(file, { onUploadProgress } = {}) {
  const form = new FormData();
  form.append('file', file);

  try {
    const { data } = await http.post('/api/v1/enrich/batch', form, {
      onUploadProgress,
    });
    return data;
  } catch (err) {
    throw normalizeError(err);
  }
}

/**
 * Poll job status and progress for a batch catalog enrichment job.
 *
 * @param {string} jobId
 * @returns {Promise<object>} — `{ job_id, total_rows, completed_rows, succeeded_count, failed_count, is_complete, avg_confidence, records[] }`
 */
export async function getBatchStatus(jobId) {
  try {
    const { data } = await http.get(`/api/v1/enrich/batch/${jobId}/status`);
    return data;
  } catch (err) {
    throw normalizeError(err);
  }
}

/**
 * Extract FastAPI's `detail` message from an error blob (JSON text).
 * Works for Blob, ArrayBuffer and string payloads.
 * @param {Blob | ArrayBuffer | string} payload
 * @returns {Promise<string | null>}
 */
async function readErrorDetail(payload) {
  try {
    const blob = payload instanceof Blob ? payload : new Blob([payload]);
    const text = await blob.text();
    const data = JSON.parse(text);
    if (typeof data?.detail === 'string') return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map((item) => item.msg).join('; ');
  } catch {
    /* not a JSON error body */
  }
  return null;
}

/**
 * Download enriched records as an .xlsx workbook.
 *
 * POST /api/v1/export/excel with `responseType: 'blob'` so the workbook is
 * received as a binary blob and triggered as a browser download. The record
 * array travels as the JSON request body — query strings are limited
 * (~8 KB in browsers) and would truncate large batches.
 *
 * FastAPI error bodies arrive as blobs too (because of `responseType:
 * 'blob'`), so failures are parsed out of the blob (or ArrayBuffer/string)
 * and surfaced as `ApiError` with the backend's `detail` message.
 *
 * @param {Array<object>} results — records shaped like ProductEnrichmentResponse
 * @returns {Promise<true>} resolves once the download has been triggered
 */
export async function exportExcel(results) {
  if (!Array.isArray(results) || results.length === 0) {
    throw new ApiError('Nothing to export — enrich at least one SKU first.', 422);
  }

  try {
    const response = await http.post('/api/v1/export/excel', results, {
      responseType: 'blob',
    });

    const blob = response.data;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'unipulse_enrichment_export.xlsx';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    return true;
  } catch (err) {
    // With `responseType: 'blob'`, FastAPI JSON errors arrive as blobs —
    // read them out so the user sees the backend's real message.
    if (axios.isAxiosError(err) && err.response) {
      const data = err.response.data;
      if (data instanceof Blob || data instanceof ArrayBuffer || typeof data === 'string') {
        const detail = await readErrorDetail(data);
        throw new ApiError(
          detail || `Export failed (${err.response.status})`,
          err.response.status
        );
      }
    }
    throw normalizeError(err);
  }
}

/**
 * Simple liveness probe — used by the UI to surface a friendly banner when
 * the backend is unreachable.
 * @returns {Promise<boolean>}
 */
export async function checkBackendHealth() {
  try {
    const { data } = await http.get('/health', { timeout: 4000 });
    return data?.status === 'ok';
  } catch {
    return false;
  }
}
