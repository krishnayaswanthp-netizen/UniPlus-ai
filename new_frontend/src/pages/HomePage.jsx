import { Link } from 'react-router-dom';
import Icon from '../components/Icon';

const PIPELINE = [
  {
    phase: 'PHASE 01',
    title: 'RAW',
    body: 'Ingest unstructured PDFs, messy supplier portals, and legacy PIM exports.',
    accent: false,
  },
  {
    phase: 'PHASE 02',
    title: 'RESEARCH',
    body: 'Autonomous agents cross-reference manufacturer catalogs to resolve ambiguities.',
    accent: false,
  },
  {
    phase: 'PHASE 03',
    title: 'EXTRACTION',
    body: 'Domain-specific models isolate technical specifications, dimensions, and tolerances.',
    accent: false,
  },
  {
    phase: 'PHASE 04',
    title: 'NORMALIZATION',
    body: 'Units are converted and mapped to standard taxonomies such as ETIM.',
    accent: false,
  },
  {
    phase: 'PHASE 05',
    title: 'VALIDATION',
    body: 'Multi-modal verification ensures output matches physical product reality.',
    accent: true,
  },
];

export default function HomePage() {
  return (
    <>
      {/* Hero — editorial & asymmetric */}
      <section className="mx-auto w-full max-w-shell px-6 pb-24 pt-20 md:px-container-padding">
        <div className="relative grid grid-cols-12 gap-8">
          <div className="col-span-12 z-10 md:col-span-8 lg:col-span-7">
            <span className="label-caps mb-6 inline-flex items-center gap-2 rounded border border-tertiary-fixed/20 bg-tertiary-fixed/5 px-3 py-1.5 text-tertiary-fixed">
              <span className="h-1.5 w-1.5 rounded-full bg-tertiary-fixed" />
              UniPulse Intelligence Engine · v0.1
            </span>
            <h1 className="font-display text-[44px] leading-[1.05] tracking-tight text-primary md:text-[84px] md:leading-[92px]">
              Industrial intelligence,
              <br />
              without the manual work.
            </h1>
            <p className="mt-8 max-w-xl font-sans text-body-lg text-on-surface-variant">
              Precision data extraction and normalization for complex industrial catalogs.
              Transform raw manufacturer data into structured, validated attributes with
              cognitive automation.
            </p>
            <div className="mt-10 flex flex-wrap gap-4">
              <Link to="/enrich" className="btn-primary">
                <Icon name="auto_awesome" size={18} fill />
                Enrich a Single SKU
              </Link>
              <Link to="/batch" className="btn-ghost">
                <Icon name="inventory_2" size={18} />
                Process Batch Catalog
              </Link>
            </div>
          </div>

          {/* Floating technical metric */}
          <div className="col-span-3 col-start-10 mt-16 hidden lg:block">
            <div className="border-l border-line/20 py-4 pl-8">
              <span className="label-caps block text-on-surface-variant">Extraction Accuracy</span>
              <div className="font-display text-metric-xl leading-none text-primary">
                99.8<span className="text-headline-lg">%</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Technical breakdown strip */}
      <section className="relative border-y border-line/15 bg-surface-container-lowest py-20">
        <div className="technical-line-v absolute inset-y-0 left-1/4 opacity-30" />
        <div className="technical-line-v absolute inset-y-0 left-3/4 opacity-30" />
        <div className="mx-auto grid w-full max-w-shell grid-cols-1 gap-6 px-6 md:grid-cols-3 md:px-container-padding">
          {[
            {
              icon: 'view_timeline',
              title: 'Deterministic pipeline',
              body: 'Five auditable stages from raw ingestion to validation — every step traceable to source documents.',
            },
            {
              icon: 'straighten',
              title: 'Unit normalization',
              body: 'Physical quantities are converted and standardized (SI, imperial, torque, flow, temperature).',
            },
            {
              icon: 'verified_user',
              title: 'Confidence-scored output',
              body: 'Every attribute carries a confidence score so downstream systems can trust or review automatically.',
            },
          ].map((item) => (
            <div key={item.title} className="flex gap-5">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded border border-line/20 bg-surface-container">
                <Icon name={item.icon} size={22} className="text-tertiary-fixed" />
              </span>
              <div>
                <h3 className="font-display text-headline-sm text-primary">{item.title}</h3>
                <p className="mt-2 font-sans text-body-md text-on-surface-variant">{item.body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Pipeline story */}
      <section className="mx-auto w-full max-w-shell px-6 py-24 md:px-container-padding">
        <div className="border-t border-line/15 pt-16">
          <h2 className="mb-16 max-w-2xl font-display text-headline-lg text-primary">
            A deterministic pipeline for unstructured industrial data.
          </h2>
          <div className="relative grid grid-cols-1 gap-0 md:grid-cols-5">
            <div className="absolute left-0 right-0 top-6 z-0 hidden h-px bg-line/25 md:block" />
            {PIPELINE.map((step) => (
              <div key={step.phase} className="relative z-10 pr-8 pb-10 md:pb-0">
                <div
                  className={`relative mb-8 h-3 w-3 rounded-full border-2 border-background md:mt-[18px] ${
                    step.accent
                      ? 'bg-tertiary-fixed shadow-[0_0_15px_rgba(225,229,201,0.5)]'
                      : 'bg-surface-variant'
                  }`}
                />
                <span
                  className={`label-caps block ${step.accent ? 'text-tertiary-fixed' : 'text-on-surface-variant'}`}
                >
                  {step.phase}
                </span>
                <h3 className="mt-4 font-sans text-body-lg text-primary">{step.title}</h3>
                <p className="mt-2 font-sans text-body-md leading-relaxed text-on-surface-variant">
                  {step.body}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* CTA cards */}
        <div className="mt-24 grid grid-cols-1 gap-6 md:grid-cols-2">
          <Link to="/enrich" className="tactile-surface group block p-10 transition-colors duration-300 hover:border-line/40">
            <div className="flex items-start justify-between">
              <span className="flex h-12 w-12 items-center justify-center rounded border border-line/20 bg-surface-container">
                <Icon name="search" size={22} className="text-tertiary-fixed" />
              </span>
              <Icon name="arrow_forward" size={20} className="text-outline transition-transform duration-300 group-hover:translate-x-1 group-hover:text-primary" />
            </div>
            <h3 className="mt-8 font-display text-headline-md text-primary">Single SKU Research</h3>
            <p className="mt-3 max-w-md font-sans text-body-md text-on-surface-variant">
              Enter a manufacturer and part number — optionally attach a PDF datasheet — and receive
              a confidence-scored, normalized specification sheet.
            </p>
          </Link>
          <Link to="/batch" className="tactile-surface group block p-10 transition-colors duration-300 hover:border-line/40">
            <div className="flex items-start justify-between">
              <span className="flex h-12 w-12 items-center justify-center rounded border border-line/20 bg-surface-container">
                <Icon name="inventory_2" size={22} className="text-tertiary-fixed" />
              </span>
              <Icon name="arrow_forward" size={20} className="text-outline transition-transform duration-300 group-hover:translate-x-1 group-hover:text-primary" />
            </div>
            <h3 className="mt-8 font-display text-headline-md text-primary">Batch Catalog Intelligence</h3>
            <p className="mt-3 max-w-md font-sans text-body-md text-on-surface-variant">
              Upload a CSV or Excel catalog and let the engine enrich every row concurrently — with
              per-row status and one-click Excel export.
            </p>
          </Link>
        </div>
      </section>
    </>
  );
}
