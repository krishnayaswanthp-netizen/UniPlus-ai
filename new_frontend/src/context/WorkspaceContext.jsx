import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from 'react';
import { exportExcel } from '../services/api';

const WorkspaceContext = createContext(null);

/**
 * Session-level workspace state:
 *  - singleResult  — last single-SKU enrichment response
 *  - batchResult   — last batch enrichment response
 *  - exportableResults — the records the top-nav "Export Data" button writes
 *  - notify()      — push a toast message (type: "success" | "error" | "info")
 */
export function WorkspaceProvider({ children }) {
  const [singleResult, setSingleResult] = useState(null);
  const [batchResult, setBatchResult] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [toasts, setToasts] = useState([]);
  const toastId = useRef(0);

  const notify = useCallback((message, type = 'info') => {
    const id = ++toastId.current;
    setToasts((prev) => [...prev, { id, message, type }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

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
      notify,
      toasts,
      dismissToast,
    }),
    [
      singleResult,
      batchResult,
      exportableResults,
      exporting,
      runExport,
      notify,
      toasts,
      dismissToast,
    ]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace must be used within <WorkspaceProvider>');
  return ctx;
}
