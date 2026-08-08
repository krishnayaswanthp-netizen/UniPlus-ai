# UniPulse AI — Frontend (React + Vite + Tailwind)

The UniPulse AI frontend: a dark "obsidian editorial" industrial AI SaaS interface that
drives the FastAPI product-intelligence engine. It replaces the static design mockups that
were extracted into `design-archive/` (kept for reference) with a production-shaped
React/Vite application.

## Stack

- **React 18** + **Vite 5** — fast dev server & build tooling
- **Tailwind CSS 3** — custom `obsidian` design tokens (surfaces, typography, radii)
- **React Router 6** — `/` (landing), `/enrich` (single SKU), `/batch` (batch catalog)
- **Axios** — API client (multipart uploads + upload progress + blob downloads)
- **Lucide React** — Sun/Moon icons for the theme toggle
- Fonts: **EB Garamond** (display) + **Inter** (data/UI), **Material Symbols Outlined** (icons)

## Project structure

```
new_frontend/
├── index.html               # fonts, Material Symbols, app mount
├── package.json
├── vite.config.js           # React plugin + optional /api dev proxy
├── tailwind.config.js       # obsidian editorial design tokens
├── src/
│   ├── main.jsx             # entry
│   ├── App.jsx              # router + shell (nav, banner, toasts, footer)
│   ├── index.css            # design-system utilities (surfaces, buttons, tables)
│   ├── services/api.js      # ★ API service layer — all backend calls
│   ├── context/WorkspaceContext.jsx  # shared results + export + toasts
│   ├── components/          # TopNav, Footer, AttributeTable, badges, StatCard…
│   ├── pages/               # HomePage, SingleSkuPage, BatchCatalogPage
│   └── utils/format.js      # percent/duration/currency/file-size formatters
└── design-archive/          # original zip-extracted HTML mockups (reference only)
```

## Backend wiring

The API service layer lives in `src/services/api.js` and targets
`http://127.0.0.1:8000` (override with `VITE_API_BASE_URL` in `.env`):

| UI feature              | Endpoint                     | Method / payload                                             |
| ----------------------- | ---------------------------- | ------------------------------------------------------------ |
| Single SKU enrichment   | `/api/v1/enrich/single`      | `POST` multipart: `manufacturer_name`, `part_number`, `category`, optional `raw_description`, optional `file` (PDF) |
| Batch catalog upload    | `/api/v1/enrich/batch`       | `POST` multipart: `file` (`.csv` / `.xlsx`, ≤ 500 rows)      |
| Excel export            | `/api/v1/export/excel`       | `POST` JSON body (record array), `responseType: 'blob'` → `.xlsx` download |

Error handling: every network failure / FastAPI rejection is normalized into an
`ApiError` with a human-readable message (FastAPI's `{"detail": …}` and 422
validation arrays are parsed automatically) and surfaced via inline banners + toasts.

> **Note on export:** the endpoint is `POST` (not `GET`) because browser clients
> cannot reliably send request bodies on `GET`, and large batches exceed URL length
> limits. The frontend requests `responseType: 'blob'`; error responses are also
> blobs, so their JSON `detail` is parsed out and surfaced as a toast.

## Getting started

### 1. Start the FastAPI backend (in the repo root)

```bash
uvicorn app.main:app --reload
```

Requires the API keys in `.env` (e.g. `GROQ_API_KEY`) — enrichment returns 503
without them. Verify: http://127.0.0.1:8000/health and http://127.0.0.1:8000/docs.

### 2. Install frontend dependencies

```bash
cd new_frontend
npm install
```

### 3. Run the dev server

```bash
npm run dev
```

Open http://localhost:5173. The app calls `http://127.0.0.1:8000` directly
(CORS is wide open on the backend for development). A convenience proxy for
`/api` and `/health` is also configured in `vite.config.js` as a fallback.

### Production build

```bash
npm run build      # emits dist/
npm run preview    # serves the production build locally
```

## Batch upload format

The uploaded file must be CSV or Excel with these columns (header-tolerant):

- `Manufacturer` (aliases: `Manufacturer_Name`, `manufacturer`)
- `Part_Number` (aliases: `PartNumber`, `Part Number`, `part_number`)
- `Category` (one of `HVAC`, `Plumbing`, `Electrical`, `General`)
- `Description` (optional, aliases: `Raw_Description`, `raw_description`)

## Theme system (light / dark)

The app ships with two themes driven by CSS variables (`src/index.css`) and
Tailwind's `dark` class strategy — component classes are theme-agnostic:

- **Dark — Obsidian Editorial** (default): `#080B10` canvas, `#11161F` surfaces,
  bone-white `#F4F1EA` CTAs, olive `#E1E5C9` accents.
- **Light — B2B Industrial**: `#F8FAFC` canvas, `#FFFFFF` surfaces, ink `#0F172A`
  CTAs, olive `#4D7C0F` accents.

Use the **Sun/Moon toggle** (Lucide React) in the top navigation bar. The choice
persists in `localStorage` (`unipulse-theme`), is applied before first paint by an
inline script in `index.html` (no flash), and color transitions animate smoothly
via a temporary `theme-anim` class on `<html>`. `ThemeProvider`
(`src/context/ThemeContext.jsx`) exposes `theme` + `toggleTheme` for any component.

## Design system

The Garamond display type, Inter data type, uppercase tracked labels, subtle
tactile shadows, and the full token grid (canvas/surface/ink/line/accent/status)
are defined in `tailwind.config.js` + `src/index.css` — consolidated from the
archived mockups with broken utility names (`rounded-DEFAULT`, `px-container`),
dead cursor styles, and external image references fixed.
