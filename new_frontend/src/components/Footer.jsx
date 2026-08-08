import { Link } from 'react-router-dom';
import { API_BASE_URL } from '../services/api';

export default function Footer() {
  return (
    <footer className="mt-auto border-t border-line/15 bg-surface-container-low">
      <div className="mx-auto grid w-full max-w-shell grid-cols-12 gap-element-gap px-6 py-16 md:px-container-padding">
        <div className="col-span-12 flex flex-col gap-5 md:col-span-5">
          <div className="font-display text-headline-lg text-primary">UniPulse AI</div>
          <p className="max-w-xs font-sans text-body-md text-on-surface-variant">
            © 2026 UniPulse AI. Precision intelligence for industrial scales.
          </p>
        </div>

        <div className="col-span-12 flex flex-col gap-4 md:col-span-3">
          <span className="label-caps text-on-surface-variant">Product</span>
          <Link to="/enrich" className="font-sans text-body-md text-on-surface-variant underline-offset-4 transition-colors hover:text-primary hover:underline">
            Single SKU Enrichment
          </Link>
          <Link to="/batch" className="font-sans text-body-md text-on-surface-variant underline-offset-4 transition-colors hover:text-primary hover:underline">
            Batch Catalog Intelligence
          </Link>
        </div>

        <div className="col-span-12 flex flex-col gap-4 md:col-span-4">
          <span className="label-caps text-on-surface-variant">Platform</span>
          <a
            href={`${API_BASE_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            className="font-sans text-body-md text-on-surface-variant underline-offset-4 transition-colors hover:text-primary hover:underline"
          >
            API Reference (OpenAPI)
          </a>
          <span className="font-mono text-label-sm text-on-surface-variant opacity-70">
            {API_BASE_URL}
          </span>
        </div>
      </div>
    </footer>
  );
}
