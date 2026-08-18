import { lazy, Suspense, useEffect } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ToastProvider } from './context/ToastContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import BackendBanner from './components/BackendBanner';
import Footer from './components/Footer';
import Toasts from './components/Toasts';
import TopNav from './components/TopNav';
import HomePage from './pages/HomePage';

// Code-split the heavy interactive pages: each becomes its own chunk that
// loads only when its route is visited, shrinking the initial bundle.
const SingleSkuPage = lazy(() => import('./pages/SingleSkuPage'));
const BatchCatalogPage = lazy(() => import('./pages/BatchCatalogPage'));

/** Reset scroll position on route change. */
function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pathname]);
  return null;
}

/** Minimal loading state shown while a lazy page chunk is fetched. */
function PageFallback() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <span className="h-2 w-2 animate-pulse-soft rounded-full bg-tertiary-fixed" />
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <WorkspaceProvider>
        <div className="flex min-h-screen flex-col">
          <ScrollToTop />
          <TopNav />
          <BackendBanner />
          <main className="flex-1">
            <Suspense fallback={<PageFallback />}>
              <Routes>
                <Route path="/" element={<HomePage />} />
                <Route path="/enrich" element={<SingleSkuPage />} />
                <Route path="/batch" element={<BatchCatalogPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </main>
          <Footer />
          <Toasts />
        </div>
      </WorkspaceProvider>
    </ToastProvider>
  );
}
