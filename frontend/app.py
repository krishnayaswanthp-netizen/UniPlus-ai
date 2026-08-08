"""UniPulse AI — B2B Industrial Product Intelligence Engine (Streamlit dashboard).

Interactive frontend for the FastAPI backend in ``app/main.py``:

- ``Tab 1`` Single Product Enrichment
    Manufacturer + part number (+ optional PDF datasheet) -> enriched specs.
- ``Tab 2`` Batch Catalog Processing
    CSV / Excel catalog -> concurrent enrichment, live progress, Excel export.
- ``Tab 3`` Security & Domain Whitelist Settings
    View / edit ``ALLOWED_DOMAINS`` in ``.env`` and test URLs against policy.

Run it from the project root with::

    streamlit run frontend/app.py

(or ``python run_frontend.py``) — the backend must be running on port 8000.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import threading
import time

import pandas as pd
import requests
import streamlit as st
import sys
from pathlib import Path

# Add project root directory to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
# Make the backend package importable regardless of the CWD the app is
# launched from (only lightweight modules are touched: config + security).


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

API_BASE = os.environ.get("UNIPULSE_API_BASE", "http://127.0.0.1:8000")
CATEGORIES = ["HVAC", "Plumbing", "Electrical", "General"]

PAGE_SINGLE = "🔍 Single Product Enrichment"
PAGE_BATCH = "📦 Batch Catalog Processing"
PAGE_SECURITY = "🛡️ Security & Domain Whitelist"

st.set_page_config(
    page_title="UniPulse AI",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [data-testid="stAppViewContainer"], .stMarkdown, .stText, .stCaption {
    font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif;
}
[data-testid="stAppViewContainer"] {
    background: linear-gradient(180deg, #f6f8fc 0%, #edf1f7 100%);
}
[data-testid="stHeader"] { background: transparent; }

/* Hero banner */
.hero {
    background: linear-gradient(135deg, #0b1220 0%, #14284f 48%, #1d4ed8 100%);
    border-radius: 18px;
    padding: 30px 34px 26px;
    margin-bottom: 18px;
    box-shadow: 0 14px 34px -12px rgba(13, 42, 105, .55);
    position: relative;
    overflow: hidden;
}
.hero::after {
    content: "";
    position: absolute;
    right: -70px; top: -70px;
    width: 280px; height: 280px;
    background: radial-gradient(circle, rgba(255,255,255,.16), transparent 65%);
}
.hero h1 {
    color: #ffffff; font-size: 1.85rem; font-weight: 800;
    letter-spacing: -.02em; margin: 0 0 8px;
}
.hero p { color: #bcd0ff; margin: 0 0 14px; font-size: .98rem; max-width: 860px; }
.hero .pill {
    display: inline-block; background: rgba(255,255,255,.12);
    color: #e2ecff; border: 1px solid rgba(255,255,255,.2);
    border-radius: 999px; padding: 4px 13px; font-size: .74rem;
    font-weight: 600; margin-right: 8px; letter-spacing: .02em;
}

/* KPI cards */
.kpi-card {
    background: #ffffff;
    border: 1px solid #e6ebf2;
    border-top: 4px solid var(--accent, #2563eb);
    border-radius: 14px;
    padding: 16px 18px;
    box-shadow: 0 8px 20px -10px rgba(15, 23, 42, .22);
    height: 100%;
}
.kpi-top { display: flex; justify-content: space-between; align-items: center; }
.kpi-label {
    font-size: .7rem; text-transform: uppercase; letter-spacing: .09em;
    color: #64748b; font-weight: 700;
}
.kpi-icon { font-size: 1.25rem; opacity: .9; }
.kpi-value { font-size: 1.65rem; font-weight: 800; color: #0f172a; margin-top: 6px; line-height: 1.1; }
.kpi-sub { font-size: .74rem; color: #94a3b8; margin-top: 4px; }

/* Attributes / results table */
.table-wrap {
    border-radius: 12px; overflow: hidden;
    border: 1px solid #e2e8f0;
    box-shadow: 0 6px 18px -10px rgba(15,23,42,.18);
    margin: 6px 0 10px;
}
.attr-table { width: 100%; border-collapse: collapse; background: #fff; font-size: .88rem; }
.attr-table thead th {
    background: #0f172a; color: #fff; text-align: left;
    padding: 10px 14px; font-size: .72rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: .06em;
}
.attr-table tbody tr:nth-child(even) { background: #f8fafc; }
.attr-table tbody td {
    padding: 11px 14px; border-top: 1px solid #eef2f7;
    vertical-align: top; color: #1e293b;
}
.attr-table a { color: #1d4ed8; text-decoration: none; font-weight: 600; }
.attr-table a:hover { text-decoration: underline; }
.muted { color: #94a3b8; font-size: .8rem; }

/* Confidence badges */
.badge {
    display: inline-block; border-radius: 999px; padding: 3px 11px;
    font-size: .74rem; font-weight: 700; white-space: nowrap;
}
.badge.high { background: #dcfce7; color: #166534; }
.badge.med  { background: #fef3c7; color: #92400e; }
.badge.low  { background: #fee2e2; color: #991b1b; }
.badge.none { background: #e2e8f0; color: #475569; }

/* Status pills (batch results) */
.pill { display: inline-block; border-radius: 999px; padding: 2px 10px; font-size: .72rem; font-weight: 700; }
.pill.ok   { background: #dcfce7; color: #166534; }
.pill.err  { background: #fee2e2; color: #991b1b; }

/* Full-width action buttons */
div[data-testid="stFormSubmitButton"] button,
div[data-testid="stDownloadButton"] button,
div[data-testid="stBaseButton-primary"] button {
    border-radius: 10px;
    font-weight: 600;
    width: 100%;
}
div[data-testid="stBaseButton-primary"] button {
    background: linear-gradient(135deg, #1d4ed8, #2563eb);
    border: none; color: #fff;
}
div[data-testid="stBaseButton-primary"] button:hover {
    background: linear-gradient(135deg, #1e40af, #1d4ed8);
    color: #fff;
}

/* Sidebar polish */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #ffffff 0%, #f2f6fb 100%);
    border-right: 1px solid #e2e8f0;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 { color: #0f172a; }
.sidebar-brand {
    color: #0f172a; font-weight: 800; font-size: 1.05rem; letter-spacing: -.01em;
}
.sidebar-hint {
    background: #eef2ff; border: 1px solid #e0e7ff; border-radius: 10px;
    padding: 10px 12px; font-size: .78rem; color: #334155; line-height: 1.5;
}
.sidebar-hint code { color: #1d4ed8; background: #e0e7ff; border-radius: 4px; padding: 1px 5px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label p { color: #1e293b; }
</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def _init_state() -> None:
    """Seed session-scoped counters and the export record log."""
    if "kpi" not in st.session_state:
        st.session_state["kpi"] = {
            "skus": 0,
            "time_ms": 0.0,
            "cost": 0.0,
            "confidences": [],
        }
    if "export_records" not in st.session_state:
        st.session_state["export_records"] = []
    if "api_base" not in st.session_state:
        st.session_state["api_base"] = API_BASE


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------


@st.cache_data(ttl=10, show_spinner=False)
def check_api(base: str) -> tuple[bool, str]:
    """Hit ``GET /health`` and report backend availability."""
    try:
        response = requests.get(f"{base}/health", timeout=3)
        if response.status_code == 200:
            data = response.json()
            return True, f"{data.get('app', 'UniPulse AI')} v{data.get('version', '?')}"
        return False, f"HTTP {response.status_code}"
    except requests.RequestException as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _show_api_error(response: requests.Response) -> None:
    """Render a FastAPI error payload (``{"detail": ...}``) readably."""
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text
    if isinstance(detail, list):  # pydantic validation errors
        parts = []
        for item in detail:
            if isinstance(item, dict):
                location = ".".join(str(p) for p in item.get("loc", []))
                parts.append(f"{location}: {item.get('msg', 'invalid')}")
        st.error(f"**Validation error** (HTTP {response.status_code}) — " + "; ".join(parts))
    else:
        st.error(f"**API error** (HTTP {response.status_code}): {detail}")


def _update_kpis(skus: int, time_ms: float, cost: float, confidences: list[float]) -> None:
    """Accumulate enrichment stats into the session KPI state."""
    kpi = st.session_state["kpi"]
    kpi["skus"] += skus
    kpi["time_ms"] += time_ms
    kpi["cost"] += cost
    kpi["confidences"].extend(confidences)


def _append_export_record(record: dict) -> None:
    """Log a product for Excel export, keeping one record per SKU (latest wins)."""
    records = st.session_state["export_records"]
    sku = record.get("sku_id")
    for index, existing in enumerate(records):
        if existing.get("sku_id") == sku:
            records[index] = record
            return
    records.append(record)


# ---------------------------------------------------------------------------
# Header + KPI cards
# ---------------------------------------------------------------------------


def render_header() -> None:
    st.markdown(
        """
        <div class="hero">
            <h1>🏭 UniPulse AI — B2B Industrial Product Intelligence Engine</h1>
            <p>Enrich manufacturer catalogs with structured, normalized technical
            specifications extracted from whitelisted web sources and uploaded PDF datasheets.</p>
            <span class="pill">FastAPI + Streamlit</span>
            <span class="pill">Whitelist-secured sourcing</span>
            <span class="pill">Pint-normalized units</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpi_cards() -> None:
    kpi = st.session_state["kpi"]
    skus = kpi["skus"]
    avg_conf = sum(kpi["confidences"]) / len(kpi["confidences"]) if kpi["confidences"] else 0.0
    speed = skus / (kpi["time_ms"] / 1000.0) if kpi["time_ms"] > 0 else 0.0

    cards = [
        ("Enriched SKUs", f"{skus:,}", "🧾", "#2563eb", "cumulative session total"),
        (
            "Avg. Confidence Score",
            f"{avg_conf:.1%}" if skus else "—",
            "🎯",
            "#16a34a",
            "mean overall confidence",
        ),
        (
            "Processing Speed",
            f"{speed:.2f} SKUs/s" if skus else "—",
            "⚡",
            "#f59e0b",
            "cumulative throughput",
        ),
        ("Estimated Cost", f"${kpi['cost']:,.6f}", "💰", "#8b5cf6", "cumulative LLM spend (USD)"),
    ]

    columns = st.columns(4)
    for column, (label, value, icon, accent, sub) in zip(columns, cards):
        column.markdown(
            f"""
            <div class="kpi-card" style="--accent:{accent}">
                <div class="kpi-top">
                    <span class="kpi-label">{html.escape(label)}</span>
                    <span class="kpi-icon">{icon}</span>
                </div>
                <div class="kpi-value">{html.escape(str(value))}</div>
                <div class="kpi-sub">{html.escape(sub)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Shared rendering helpers
# ---------------------------------------------------------------------------


def _confidence_badge(score: float) -> str:
    """Render a confidence score as a colored badge pill."""
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    if score >= 0.8:
        css, label = "high", "High"
    elif score >= 0.6:
        css, label = "med", "Medium"
    elif score > 0.0:
        css, label = "low", "Low"
    else:
        css, label = "none", "—"
    return f'<span class="badge {css}">{score:.0%} · {label}</span>'


def _source_link(source_url: str) -> str:
    """Render the source URL as a clickable link (or muted text for non-http)."""
    source_url = (source_url or "").strip()
    if not source_url:
        return '<span class="muted">—</span>'
    if source_url.startswith(("http://", "https://")):
        return (
            f'<a href="{html.escape(source_url)}" target="_blank" rel="noopener noreferrer">'
            f'🔗 {html.escape(source_url)}</a>'
        )
    # Local markers (upload://, mock://fallback, local://) are informational.
    return f'<span class="muted">📄 {html.escape(source_url)}</span>'


def render_attributes_table(attributes: list[dict]) -> None:
    """Render enriched attributes as a styled table with badges + links."""
    if not attributes:
        st.info(
            "No technical attributes were extracted for this product. "
            "The pipeline may not have found usable spec text, or the SKU is unknown."
        )
        return

    rows = []
    for attr in attributes:
        field = html.escape(str(attr.get("field_name", "")))
        raw = html.escape(str(attr.get("raw_value", "")))
        normalized = html.escape(str(attr.get("normalized_value", "")))
        unit = str(attr.get("unit") or "").strip()
        display_value = f"{normalized} {html.escape(unit)}" if unit else normalized
        rows.append(
            f"<tr>"
            f"<td><strong>{field}</strong></td>"
            f"<td>{raw}</td>"
            f"<td>{display_value}</td>"
            f"<td>{_confidence_badge(attr.get('confidence_score', 0.0))}</td>"
            f"<td>{_source_link(attr.get('source_url', ''))}</td>"
            f"</tr>"
        )

    st.markdown(
        """
        <div class="table-wrap">
            <table class="attr-table">
                <thead>
                    <tr>
                        <th>Field Name</th><th>Raw Value</th>
                        <th>Normalized Value + Unit</th>
                        <th>Confidence Score</th><th>Source URL</th>
                    </tr>
                </thead>
                <tbody>
        """
        + "\n".join(rows)
        + """
                </tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tab 1 — Single Product Enrichment
# ---------------------------------------------------------------------------


def tab_single() -> None:
    st.subheader("🔍 Single Product Enrichment")
    st.caption(
        "Provide a manufacturer + part number. The engine searches whitelisted web "
        "sources (optionally combined with an uploaded PDF datasheet) and returns "
        "structured, unit-normalized specifications."
    )

    with st.form("single_form"):
        col1, col2 = st.columns(2)
        manufacturer = col1.text_input("Manufacturer Name *", placeholder="e.g. Honeywell")
        part_number = col2.text_input("Part Number *", placeholder="e.g. TH6320U2008")
        col3, col4 = st.columns(2)
        category = col3.selectbox("Category *", CATEGORIES)
        pdf_file = col4.file_uploader("PDF Datasheet (optional)", type=["pdf"])
        raw_description = st.text_area(
            "Raw Description (optional)",
            height=88,
            placeholder="Optional: paste any known specs (e.g. '24 VAC thermostat, 800 CFM airflow')…",
        )
        submitted = st.form_submit_button("⚡ Enrich Product")

    if submitted:
        _enrich_single(
            manufacturer.strip(),
            part_number.strip(),
            category,
            pdf_file,
            (raw_description or "").strip() or None,
        )


def _enrich_single(
    manufacturer: str,
    part_number: str,
    category: str,
    pdf_file,
    raw_description: str | None,
) -> None:
    if not manufacturer or not part_number:
        st.error("⚠️ Manufacturer Name and Part Number are both required.")
        return

    base = st.session_state["api_base"]
    url = f"{base}/api/v1/enrich/single"
    with st.spinner("Searching whitelisted sources and extracting specs…"):
        try:
            if pdf_file is not None:
                response = requests.post(
                    url,
                    data={
                        "manufacturer_name": manufacturer,
                        "part_number": part_number,
                        "raw_description": raw_description or "",
                        "category": category,
                    },
                    files={"file": (pdf_file.name, pdf_file.getvalue(), "application/pdf")},
                    timeout=300,
                )
            else:
                response = requests.post(
                    url,
                    json={
                        "manufacturer_name": manufacturer,
                        "part_number": part_number,
                        "raw_description": raw_description,
                        "category": category,
                    },
                    timeout=300,
                )
        except requests.RequestException as exc:
            st.error(f"⚠️ Request failed — is the backend running at `{base}`? ({exc})")
            return

    if response.status_code != 200:
        _show_api_error(response)
        return

    body = response.json()
    _render_single_result(body, manufacturer, part_number)


def _render_single_result(body: dict, manufacturer: str, part_number: str) -> None:
    attributes = body.get("enriched_attributes", [])
    sku_id = body.get("sku_id", f"{manufacturer}-{part_number}")
    confidence = body.get("overall_confidence", 0.0)
    time_ms = body.get("processing_time_ms", 0.0)
    cost = body.get("estimated_cost_usd", 0.0)

    st.success(f"✅ Enriched **{sku_id}** — {len(attributes)} attributes extracted")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("SKU ID", sku_id)
    col2.metric("Overall Confidence", f"{confidence:.1%}")
    col3.metric("Processing Time", f"{time_ms:,.0f} ms")
    col4.metric("Estimated Cost", f"${cost:,.6f}")

    render_attributes_table(attributes)

    with st.expander("🔎 View as DataFrame / raw JSON"):
        st.dataframe(pd.DataFrame(attributes))
        st.json(body)

    # Update session KPIs + export log.
    _update_kpis(skus=1, time_ms=time_ms, cost=cost, confidences=[confidence])
    _append_export_record(
        {
            "sku_id": body.get("sku_id", sku_id),
            "category": body.get("category", ""),
            "manufacturer_name": manufacturer,
            "part_number": part_number,
            "overall_confidence": confidence,
            "processing_time_ms": time_ms,
            "estimated_cost_usd": cost,
            "enriched_attributes": attributes,
        }
    )


# ---------------------------------------------------------------------------
# Tab 2 — Batch Catalog Processing
# ---------------------------------------------------------------------------


def _preview_catalog(uploaded) -> pd.DataFrame | None:
    """Parse an uploaded CSV / Excel file for a client-side preview."""
    try:
        if uploaded.name.lower().endswith(".csv"):
            return pd.read_csv(uploaded)
        return pd.read_excel(uploaded, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001 — surface any parse problem
        st.error(f"⚠️ Could not parse catalog: {exc}")
        return None


def _warn_missing_headers(df: pd.DataFrame) -> None:
    """Warn when required backend columns are missing (header-tolerant)."""
    columns = {str(c).strip().lower() for c in df.columns}
    has_manufacturer = bool(columns & {"manufacturer", "manufacturer_name"})
    has_part = bool(columns & {"part_number", "partnumber", "part number"})
    if not (has_manufacturer and has_part):
        st.warning(
            "The backend expects **Manufacturer** and **Part_Number** columns "
            "(aliases like `part_number` / `Part Number` are accepted). Rows "
            "without them will be reported as errors."
        )


def tab_batch() -> None:
    st.subheader("📦 Batch Catalog Processing")
    st.caption(
        "Upload a **.csv** or **.xlsx** catalog with `Manufacturer` and `Part_Number` "
        "columns (plus optional `Category`). The backend enriches up to 500 rows "
        "concurrently (×8) and reports each row independently."
    )

    uploaded = st.file_uploader("Catalog file (.csv / .xlsx)", type=["csv", "xlsx"])

    if uploaded is not None:
        preview = _preview_catalog(uploaded)
        if preview is not None:
            st.caption(f"**{uploaded.name}** — {len(preview):,} rows detected")
            st.dataframe(preview.head(8))
            _warn_missing_headers(preview)

    if st.button("🚀 Process Catalog", type="primary", disabled=uploaded is None):
        _run_batch(uploaded)

    _render_export_section()


def _run_batch(uploaded) -> None:
    """Submit the catalog and animate a live progress bar while enriching."""
    base = st.session_state["api_base"]
    url = f"{base}/api/v1/enrich/batch"
    filename = uploaded.name
    mime = uploaded.type or (
        "text/csv"
        if filename.lower().endswith(".csv")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    content = uploaded.getvalue()

    # Guard against accidental re-processing of the same file (re-spends LLM
    # credits and would accumulate duplicate export records).
    identity = (uploaded.name, uploaded.size)
    if identity == st.session_state.get("last_batch_identity"):
        st.warning(
            "⚠️ This exact file (same name & size) was already processed this session. "
            "Re-running enriches it again (costing LLM credits) and accumulates results — "
            "only continue if the file has changed."
        )
        if not st.checkbox("Yes — process this identical file again", key="reprocess_confirm"):
            return

    state: dict = {"done": False, "response": None, "error": None}

    def worker() -> None:
        try:
            state["response"] = requests.post(
                url,
                files={"file": (filename, content, mime)},
                timeout=900,
            )
        except requests.RequestException as exc:
            state["error"] = exc
        finally:
            state["done"] = True

    threading.Thread(target=worker, daemon=True).start()

    holder = st.empty()
    with holder.container():
        progress = st.progress(0.0, text="Submitting catalog to the enrichment engine…")
        status_placeholder = st.empty()

    started = time.perf_counter()
    while not state["done"]:
        elapsed = time.perf_counter() - started
        fraction = min(0.9, elapsed / 8.0)  # animate up to 90% while in flight
        progress.progress(fraction, text=f"Enriching catalog… {elapsed:,.1f}s elapsed")
        time.sleep(0.1)
    elapsed = time.perf_counter() - started
    progress.progress(1.0, text=f"Completed in {elapsed:,.1f}s")
    status_placeholder.empty()
    holder.empty()

    if state["error"] is not None:
        st.error(f"⚠️ Batch request failed: {state['error']}")
        return

    response = state["response"]
    if response.status_code != 200:
        _show_api_error(response)
        return

    _render_batch_results(response.json(), elapsed)
    st.session_state["last_batch_identity"] = identity


def _render_batch_results(body: dict, wall_time_s: float) -> None:
    total = body.get("total", 0)
    succeeded = body.get("succeeded", 0)
    failed = body.get("failed", 0)
    results = body.get("results", [])
    success_rows = [r for r in results if r.get("status") == "success"]

    st.success(f"✅ Catalog processed — **{succeeded}/{total}** rows enriched successfully")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Rows", f"{total:,}")
    col2.metric("Succeeded", f"{succeeded:,}")
    col3.metric("Failed", f"{failed:,}")
    col4.metric("Success Rate", f"{succeeded / total:.0%}" if total else "—")

    if success_rows:
        confidences = [r.get("overall_confidence", 0.0) for r in success_rows]
        times = [r.get("processing_time_ms", 0.0) for r in success_rows]
        col5, col6, col7 = st.columns(3)
        col5.metric("Avg. Confidence", f"{sum(confidences) / len(confidences):.1%}")
        col6.metric("Avg. Latency", f"{sum(times) / len(times):,.0f} ms")
        col7.metric("Wall Time", f"{wall_time_s:,.1f} s")

        # Session KPIs (cost isn't reported per batch row, so only count/time).
        _update_kpis(skus=succeeded, time_ms=sum(times), cost=0.0, confidences=confidences)
        for row in success_rows:
            _append_export_record(
                {
                    "sku_id": row.get("sku_id", ""),
                    "category": row.get("category", ""),
                    "manufacturer_name": row.get("manufacturer_name", ""),
                    "part_number": row.get("part_number", ""),
                    "overall_confidence": row.get("overall_confidence", 0.0),
                    "processing_time_ms": row.get("processing_time_ms", 0.0),
                    "estimated_cost_usd": 0.0,
                    "enriched_attributes": row.get("enriched_attributes", []),
                }
            )
    st.caption(
        "Note: the batch API doesn't report per-row LLM cost, so batch runs "
        "update the SKU/time KPIs but not the Estimated Cost card."
    )

    table_rows = []
    for row in results:
        ok = row.get("status") == "success"
        table_rows.append(
            {
                "SKU": row.get("sku_id", ""),
                "Manufacturer": row.get("manufacturer_name", ""),
                "Part Number": row.get("part_number", ""),
                "Category": row.get("category", ""),
                "Status": "✅ Success" if ok else "❌ Error",
                "Confidence": f"{row.get('overall_confidence', 0.0):.0%}" if ok else "—",
                "Time (ms)": round(row.get("processing_time_ms", 0.0), 1) if ok else "—",
                "Error": (row.get("error") or "")[:100] or "—",
            }
        )
    st.markdown("**Per-row results**")
    if table_rows:
        st.dataframe(pd.DataFrame(table_rows))
    else:
        st.caption("No rows were returned by the backend.")

    with st.expander("🔎 View raw JSON response"):
        st.json(body)


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_export_xlsx(records: tuple, base: str) -> bytes | None:
    """Render accumulated enriched records as an .xlsx workbook via the API."""
    try:
        response = requests.get(
            f"{base}/api/v1/export/excel",
            json=list(records),
            timeout=120,
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return response.content


def _render_export_section() -> None:
    """Export button for everything enriched so far in this session."""
    records = st.session_state.get("export_records", [])
    if not records:
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        xlsx = _fetch_export_xlsx(tuple(records), st.session_state["api_base"])
        if xlsx:
            st.download_button(
                "📥 Export Formatted Excel",
                data=xlsx,
                file_name="unipulse_enrichment_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.warning("Export is temporarily unavailable (backend not reachable?).")
    with col2:
        if st.button("🧹 Reset history"):
            st.session_state["export_records"] = []
            st.session_state.pop("last_batch_identity", None)
            st.cache_data.clear()
            st.rerun()
    st.caption(
        f"Exports all **{len(records):,}** products enriched this session "
        "(two-sheet workbook: Products + Attributes)."
    )


# ---------------------------------------------------------------------------
# Tab 3 — Security & Domain Whitelist Settings
# ---------------------------------------------------------------------------

_ENV_PATH = os.path.join(PROJECT_ROOT, ".env")


def _read_env_allow_domains() -> list[str]:
    """Parse ``ALLOWED_DOMAINS`` straight from the ``.env`` file (if present)."""
    if not os.path.exists(_ENV_PATH):
        return []
    try:
        with open(_ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                if re.match(r"^\s*ALLOWED_DOMAINS\s*=", line):
                    value = line.split("=", 1)[1].strip()
                    try:
                        parsed = json.loads(value)
                        return list(parsed) if isinstance(parsed, list) else []
                    except json.JSONDecodeError:
                        cleaned = value.strip().strip("[]").strip("\"'")
                        return [d.strip() for d in cleaned.split(",") if d.strip()]
    except OSError:
        return []
    return []


def _write_env_allow_domains(domains: list[str]) -> bool:
    """Upsert ``ALLOWED_DOMAINS`` into ``.env`` without touching other keys."""
    line = f"ALLOWED_DOMAINS={json.dumps(domains)}\n"
    try:
        if os.path.exists(_ENV_PATH):
            with open(_ENV_PATH, "r", encoding="utf-8") as fh:
                content = fh.read()
            if re.search(r"^\s*ALLOWED_DOMAINS\s*=", content, flags=re.MULTILINE):
                content = re.sub(
                    r"^\s*ALLOWED_DOMAINS\s*=.*$",
                    line.rstrip("\n"),
                    content,
                    flags=re.MULTILINE,
                )
            else:
                content = content.rstrip() + "\n\n" + line
        else:
            content = line
        # Atomic write (temp file + rename) so a crash can't truncate .env.
        tmp_path = f"{_ENV_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, _ENV_PATH)
        return True
    except OSError:
        return False


def tab_security() -> None:
    st.subheader("🛡️ Security & Domain Whitelist Settings")
    st.caption(
        "Control which web sources the enrichment engine may scrape. "
        "The policy lives in `app/core/security.py`; the allow-list is loaded "
        "from `ALLOWED_DOMAINS` in `.env` **at backend startup**."
    )

    try:
        from app.core.config import settings
        from app.core.security import BLOCKED_RETAIL_DOMAINS, is_domain_allowed

        backend_available = True
    except Exception:  # backend package not importable in this environment
        settings = None
        BLOCKED_RETAIL_DOMAINS = frozenset()
        is_domain_allowed = None
        backend_available = False

    # --- Policy explainer -------------------------------------------------
    st.markdown("#### Whitelist policy")
    st.markdown(
        "1. Malformed URLs are rejected.\n"
        "2. **Consumer retail marketplaces** (Amazon, eBay, Walmart, …) are always blocked.\n"
        "3. If `ALLOWED_DOMAINS` is set it acts as an **exclusive** allow-list — only listed "
        "domains (and subdomains) are accepted.\n"
        "4. Otherwise, any non-retail domain passes, letting official manufacturer/distributor "
        "sites and direct PDF datasheets through by default."
    )

    # --- Allow-list editor + blocked domains --------------------------------
    file_domains = _read_env_allow_domains()
    settings_domains = (
        list(settings.allowed_domains or []) if backend_available and settings is not None else []
    )

    col_a, col_b = st.columns(2)
    saved_now = False
    with col_a:
        st.markdown("#### Allow-list editor")
        edited = st.text_area(
            "Allowed domains (one per line)",
            value="\n".join(file_domains or settings_domains),
            height=140,
            placeholder="e.g.\nhoneywellhome.com\ncarrier.com",
            help="Saves to ALLOWED_DOMAINS in .env. Requires a backend restart to take effect.",
        )
        save_col, reset_col = st.columns(2)
        if save_col.button("💾 Save allow-list", type="primary"):
            domains = [line.strip() for line in edited.splitlines() if line.strip()]
            if _write_env_allow_domains(domains):
                st.success(
                    f"Saved {len(domains)} domain(s) to `.env`. "
                    "Restart the backend (`uvicorn app.main:app --reload`) for it to take effect."
                )
                st.cache_data.clear()
                saved_now = True
            else:
                st.error("Could not write to `.env` — check file permissions.")
        if reset_col.button("🗑️ Clear allow-list"):
            if _write_env_allow_domains([]):
                st.success("Allow-list cleared. Restart the backend to apply.")
                st.cache_data.clear()
                saved_now = True
            else:
                st.error("Could not write to `.env`.")

    with col_b:
        st.markdown("#### Blocked retail domains")
        st.caption(
            "Hard-coded in `app/core/security.py` — always rejected, "
            "including subdomains and country mirrors."
        )
        blocked = sorted(BLOCKED_RETAIL_DOMAINS)
        if blocked:
            st.markdown("`" + "` · `".join(blocked) + "`")
        else:
            st.info("Backend package not importable here — showing a static list instead.")
            fallback = [
                "amazon.com", "amazon.co.uk", "ebay.com", "flipkart.com", "walmart.com",
                "aliexpress.com", "bestbuy.com", "homedepot.com", "lowes.com", "newegg.com",
                "target.com", "wayfair.com", "overstock.com",
            ]
            st.markdown("`" + "` · `".join(fallback) + "`")

    # --- Effective mode (re-read from disk right after a save) --------------
    if saved_now:
        active_domains = _read_env_allow_domains()
    elif settings_domains:
        active_domains = settings_domains
    else:
        active_domains = file_domains
    mode = "**Exclusive allow-list** — only whitelisted domains are scraped." if active_domains else (
        "**Default-allow** — any non-retail domain is accepted."
    )
    st.markdown(
        f"**Effective mode:** {mode}  "
        f"({len(active_domains)} domain(s) configured)"
    )

    # --- URL policy tester --------------------------------------------------
    st.markdown("#### Test a URL against the policy")
    test_url = st.text_input(
        "Candidate source URL",
        placeholder="https://www.honeywellhome.com/us/en/product/TH6320U2008",
    )
    if test_url.strip():
        if is_domain_allowed is None:
            st.warning("Backend package not importable — URL testing is unavailable.")
        else:
            allowed = is_domain_allowed(test_url.strip())
            if allowed:
                st.success(f"✅ Allowed — `{test_url.strip()}`")
            else:
                st.error(f"🚫 Blocked — `{test_url.strip()}`")

    if settings is not None:
        st.caption(
            "Note: `.env` values are read once when the backend process starts. "
            "Saved changes take effect after restarting the FastAPI server."
        )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def render_sidebar() -> str:
    st.sidebar.markdown(
        '<div class="sidebar-brand">🏭 UniPulse AI</div>'
        "<div style='color:#64748b;font-size:.76rem;margin-bottom:14px'>"
        "B2B Product Intelligence Engine</div>",
        unsafe_allow_html=True,
    )

    page = st.sidebar.radio(
        "Navigation",
        [PAGE_SINGLE, PAGE_BATCH, PAGE_SECURITY],
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")

    with st.sidebar.expander("⚙️ API Settings", expanded=True):
        api_base = st.text_input(
            "Backend base URL",
            value=st.session_state.get("api_base", API_BASE),
            key="api_base_input",
        )
        st.session_state["api_base"] = api_base.rstrip("/") or API_BASE
        ok, message = check_api(st.session_state["api_base"])
        if ok:
            st.markdown(f"🟢 **Online** — {message}")
        else:
            st.markdown(f"🔴 **Offline** — {message}")

    st.sidebar.markdown(
        """
        <div class="sidebar-hint">
            <strong>Run commands</strong><br/>
            Backend:<br/><code>uvicorn app.main:app --reload</code><br/>
            Frontend:<br/><code>python run_frontend.py</code>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return page


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    _init_state()
    page = render_sidebar()
    render_header()
    render_kpi_cards()
    st.markdown("---")

    if page == PAGE_SINGLE:
        tab_single()
    elif page == PAGE_BATCH:
        tab_batch()
    else:
        tab_security()


main()
