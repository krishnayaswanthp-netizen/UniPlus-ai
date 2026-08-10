import { useEffect } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ToastProvider } from './context/ToastContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import BackendBanner from './components/BackendBanner';
import Footer from './components/Footer';
import Toasts from './components/Toasts';
import TopNav from './components/TopNav';
import BatchCatalogPage from './pages/BatchCatalogPage';
import HomePage from './pages/HomePage';
import SingleSkuPage from './pages/SingleSkuPage';

/** Reset scroll position on route change. */
function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pathname]);
  return null;
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
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/enrich" element={<SingleSkuPage />} />
              <Route path="/batch" element={<BatchCatalogPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <Footer />
          <Toasts />
        </div>
      </WorkspaceProvider>
    </ToastProvider>
  );
}
