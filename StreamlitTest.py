
import io
import os
import base64
import json
import streamlit as st
import pandas as pd
import pdfplumber
import pytesseract
import anthropic
import plotly.express as px
from pdf2image import convert_from_bytes


# ── State name → 2-letter abbreviation lookup ────────────────────────────────

STATE_ABBREVS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}

def normalize_state(value: str) -> str:
    """Return a 2-letter state code; pass through values already 2 chars."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    if len(v) == 2:
        return v.upper()
    return STATE_ABBREVS.get(v.lower(), v)


# ── Helpers ──────────────────────────────────────────────────────────────────

def extract_from_pdf(pdf_file):
    """Python OCR extraction using pdfplumber + Tesseract fallback."""
    pdf_bytes = pdf_file.read()
    rows = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            meta = {"Source": pdf_file.name, "Page": page_num}

            tables = page.extract_tables()
            if tables:
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    headers = [
                        (cell if cell else f"Col{i}")
                        for i, cell in enumerate(table[0])
                    ]
                    for data_row in table[1:]:
                        row = dict(meta)
                        for header, value in zip(headers, data_row):
                            row[header] = value
                        rows.append(row)
                continue

            text = page.extract_text()
            if text and text.strip():
                for line in text.splitlines():
                    if line.strip():
                        rows.append({**meta, "Text": line.strip()})
                continue

            images = convert_from_bytes(
                pdf_bytes, first_page=page_num, last_page=page_num
            )
            ocr_text = pytesseract.image_to_string(images[0])
            for line in ocr_text.splitlines():
                if line.strip():
                    rows.append({**meta, "Text": line.strip()})

    return rows


def extract_with_claude(pdf_file):
    """Send the PDF to Claude API and return structured invoice data as row-dicts."""
    api_key = st.secrets.get("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    pdf_bytes = pdf_file.read()
    b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64,
                    }
                },
                {
                    "type": "text",
                    "text": (
                        "Extract all invoice or sales data from this document into a structured JSON array.\n"
                        "Return ONLY a valid JSON array — no markdown, no explanation, no code fences.\n"
                        "Each element should represent one line item or invoice record.\n"
                        "Include every field you can identify, such as: Invoice Number, Date, Vendor, "
                        "Customer, State, Amount, Quantity, Description, etc.\n"
                        "Example: [{\"Invoice Number\": \"1001\", \"Date\": \"2024-01-15\", "
                        "\"Amount\": 1500.00, \"State\": \"TX\"}]"
                    )
                }
            ]
        }]
    )

    raw = next((b.text for b in response.content if b.type == "text"), "[]").strip()

    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    data = json.loads(raw)
    for row in data:
        row["Source"] = pdf_file.name
    return data


def build_excel(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to an Excel file in memory and return raw bytes."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Extracted Data")
    return buffer.getvalue()


def run_quality_check(df: pd.DataFrame) -> pd.DataFrame:
    """
    Scan df for data quality issues and return a flagged subset.

    Checks:
      - Missing / blank values in non-metadata columns
      - Numeric outliers via IQR (values beyond Q1-1.5*IQR or Q3+1.5*IQR)
      - Negative values in amount-like columns

    Returns a copy of the flagged rows with an 'Issues' column prepended.
    """
    skip_cols = {"source", "page"}
    check_cols = [c for c in df.columns if c.lower() not in skip_cols]
    numeric_cols = [
        c for c in df.select_dtypes(include="number").columns
        if c.lower() not in skip_cols
    ]

    issues: dict[int, list[str]] = {}

    # Missing / blank values
    for col in check_cols:
        for idx, val in df[col].items():
            is_missing = pd.isna(val) or (isinstance(val, str) and val.strip() == "")
            if is_missing:
                issues.setdefault(idx, []).append(f"Missing: {col}")

    # Outliers and negatives in numeric columns
    for col in numeric_cols:
        series = df[col].dropna()
        if len(series) >= 4:
            q1, q3 = series.quantile(0.25), series.quantile(0.75)
            iqr = q3 - q1
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        else:
            lower, upper = None, None

        for idx, val in df[col].items():
            if pd.isna(val):
                continue
            if val < 0:
                issues.setdefault(idx, []).append(f"Negative value: {col} = {val:,.2f}")
            elif lower is not None:
                if val < lower or val > upper:
                    direction = "high" if val > upper else "low"
                    issues.setdefault(idx, []).append(
                        f"Outlier ({direction}): {col} = {val:,.2f}"
                    )

    if not issues:
        return pd.DataFrame()

    flagged = df.loc[sorted(issues.keys())].copy()
    flagged.insert(0, "⚠ Issues", ["; ".join(v) for v in
                                    [issues[i] for i in sorted(issues.keys())]])
    return flagged


# ── UI ────────────────────────────────────────────────────────────────────────

st.title("PDF Extractor")

uploaded = st.file_uploader(
    "Upload PDF", type="pdf", accept_multiple_files=True
)
if uploaded:
    st.success(f"{len(uploaded)} file(s) ready.")
    st.session_state["pdfs"] = uploaded

method = st.radio(
    "Extraction method",
    ["Claude AI (recommended)", "Python OCR (pdfplumber)"],
    horizontal=True,
)

process_clicked = st.button("Run Extract")

if process_clicked:
    pdfs = st.session_state.get("pdfs", [])
    if not pdfs:
        st.warning("Please upload at least one PDF first.")
    else:
        all_rows = []
        use_claude = method.startswith("Claude")
        with st.spinner("Processing with Claude AI…" if use_claude else "Processing with Python OCR…"):
            for pdf_file in pdfs:
                pdf_file.seek(0)
                try:
                    if use_claude:
                        rows = extract_with_claude(pdf_file)
                    else:
                        rows = extract_from_pdf(pdf_file)
                    all_rows.extend(rows)
                except Exception as e:
                    st.error(f"Error processing {pdf_file.name}: {e}")

        if all_rows:
            df = pd.DataFrame(all_rows)
            st.session_state["result_df"] = df
            st.session_state["result_excel_bytes"] = build_excel(df)
            st.success(f"Extracted {len(df)} rows from {len(pdfs)} file(s).")
        else:
            st.error("No data could be extracted from the uploaded file(s).")

# ── Raw data table ────────────────────────────────────────────────────────────

if "result_df" in st.session_state:
    st.dataframe(st.session_state["result_df"], use_container_width=True)

if "result_excel_bytes" in st.session_state:
    st.download_button(
        "Download Excel",
        data=st.session_state["result_excel_bytes"],
        file_name="extraction.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ── Data Quality Review ───────────────────────────────────────────────────────

if "result_df" in st.session_state:
    st.divider()
    st.header("Data Quality Review")

    df = st.session_state["result_df"]
    flagged = run_quality_check(df)

    total = len(df)
    n_flagged = len(flagged)
    n_clean = total - n_flagged

    # Summary metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Rows", total)
    m2.metric("Rows with Issues", n_flagged, delta=f"{n_flagged/total*100:.0f}%" if total else "0%",
              delta_color="inverse")
    m3.metric("Clean Rows", n_clean)

    if flagged.empty:
        st.success("No data quality issues detected.")
    else:
        st.warning(f"{n_flagged} row(s) flagged. Review and correct below, then click **Apply Corrections**.")

        # Show flagged-only view (read-only, colour-coded)
        with st.expander("Flagged rows — details", expanded=True):
            st.dataframe(
                flagged,
                use_container_width=True,
                column_config={"⚠ Issues": st.column_config.TextColumn(width="large")},
            )

    # Editable full table — users can fix any cell
    st.subheader("Edit Data")
    st.caption("Correct any values below, then click **Apply Corrections** to update the dataset.")

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key="data_editor",
    )

    if st.button("Apply Corrections"):
        st.session_state["result_df"] = edited_df
        st.session_state["result_excel_bytes"] = build_excel(edited_df)
        st.success("Dataset updated. Download or run analysis using the corrected data.")
        st.rerun()

# ── Analysis buttons ──────────────────────────────────────────────────────────

if "result_df" in st.session_state:
    st.divider()
    df = st.session_state["result_df"]

    state_col = next((c for c in df.columns if "state" in c.lower()), None)
    numeric_cols = [c for c in df.select_dtypes(include="number").columns if c.lower() != "page"]
    amount_col = numeric_cols[0] if numeric_cols else None

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Summarize Sales by State"):
            if state_col and amount_col:
                summary = (
                    df.groupby(state_col)[amount_col]
                    .sum()
                    .reset_index()
                    .rename(columns={state_col: "State", amount_col: "Total Sales"})
                    .sort_values("Total Sales", ascending=False)
                )
                st.subheader("Sales by State")
                st.dataframe(summary, use_container_width=True)
            else:
                missing = []
                if not state_col:
                    missing.append("a column containing 'state'")
                if not amount_col:
                    missing.append("a numeric amount column")
                st.warning(f"Could not find {' or '.join(missing)}. Available columns: {list(df.columns)}")

    with col2:
        if st.button("Top 3 Largest Invoices"):
            if amount_col:
                top3 = df.nlargest(3, amount_col)
                st.subheader("Top 3 Largest Invoices")
                st.dataframe(top3, use_container_width=True)
            else:
                st.warning(f"Could not find a numeric amount column. Available columns: {list(df.columns)}")

# ── Dashboard ─────────────────────────────────────────────────────────────────

if "result_df" in st.session_state:
    st.divider()
    st.header("Dashboard")

    df = st.session_state["result_df"]
    state_col = next((c for c in df.columns if "state" in c.lower()), None)
    numeric_cols = [c for c in df.select_dtypes(include="number").columns if c.lower() != "page"]
    amount_col = numeric_cols[0] if numeric_cols else None

    if not state_col or not amount_col:
        missing = []
        if not state_col:
            missing.append("state")
        if not amount_col:
            missing.append("numeric amount")
        st.info(f"Dashboard requires a {' and '.join(missing)} column. Available columns: {list(df.columns)}")
    else:
        summary = (
            df.groupby(state_col)[amount_col]
            .sum()
            .reset_index()
            .rename(columns={state_col: "Jurisdiction", amount_col: "Total Amount"})
            .sort_values("Total Amount", ascending=False)
        )
        summary["State Code"] = summary["Jurisdiction"].apply(normalize_state)

        # Pie chart
        st.subheader("Invoice Amount by Jurisdiction")
        pie = px.pie(
            summary,
            names="Jurisdiction",
            values="Total Amount",
            hole=0.35,
            color_discrete_sequence=px.colors.qualitative.Plotly,
        )
        pie.update_traces(textposition="inside", textinfo="percent+label")
        pie.update_layout(showlegend=True, margin=dict(t=30, b=0, l=0, r=0))
        st.plotly_chart(pie, use_container_width=True)

        # US choropleth map
        st.subheader("Invoice Amount by US State")
        map_data = summary[summary["State Code"].str.len() == 2].copy()

        if map_data.empty:
            st.info(
                "No valid US state codes found. "
                "Make sure the state column contains US state names or abbreviations."
            )
        else:
            choro = px.choropleth(
                map_data,
                locations="State Code",
                locationmode="USA-states",
                color="Total Amount",
                scope="usa",
                color_continuous_scale="Blues",
                hover_name="Jurisdiction",
                hover_data={"Total Amount": ":,.2f", "State Code": False},
                labels={"Total Amount": "Invoice Total ($)"},
            )
            choro.update_layout(
                geo=dict(showlakes=True, lakecolor="lightblue"),
                margin=dict(t=30, b=0, l=0, r=0),
                coloraxis_colorbar=dict(title="Invoice Total ($)"),
            )
            st.plotly_chart(choro, use_container_width=True)