import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { exportExcel } from '../services/api';
import { useToasts } from './ToastContext';

const WorkspaceContext = createContext(null);

/**
 * Session-level workspace state:
 *  - singleResult  — last single-SKU enrichment response
 *  - batchResult   — last batch enrichment response
 *  - exportableResults — the records the top-nav "Export Data" button writes
 *
 * Toast state lives in ToastContext (see ToastContext.jsx for why): keeping
 * it here would churn this context value every time a toast is added or
 * auto-dismissed, re-rendering every consumer — including the attribute
 * table trees — for no reason.
 *
 * NOTE: WorkspaceProvider calls ``useToasts()`` for its export notifications,
 * so it must be rendered inside a ``<ToastProvider>`` (App.jsx does this).
 */
export function WorkspaceProvider({ children }) {
  const [singleResult, setSingleResult] = useState(null);
  const [batchResult, setBatchResult] = useState(null);
  const [exporting, setExporting] = useState(false);
  const { notify } = useToasts();

  const exportableResults = useMemo(() => {
    if (batchResult) return batchResult.results.filter((r) => r.status === 'success');
    if (singleResult) return [singleResult];
    return [];
  }, [batchResult, singleResult]);

  const runExport = useCallback(async () => {
    if (exporting) return;
    setExporting(true);
    try {
      await exportExcel(exportableResults);
      notify(`Exported ${exportableResults.length} record(s) to Excel.`, 'success');
    } catch (err) {
      notify(err.message || 'Export failed.', 'error');
    } finally {
      setExporting(false);
    }
  }, [exportableResults, exporting, notify]);

  const value = useMemo(
    () => ({
      singleResult,
      setSingleResult,
      batchResult,
      setBatchResult,
      exportableResults,
      exporting,
      runExport,
    }),
    [singleResult, batchResult, exportableResults, exporting, runExport]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace must be used within <WorkspaceProvider>');
  return ctx;
}
