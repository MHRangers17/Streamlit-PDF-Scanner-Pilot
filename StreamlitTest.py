
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

# Show table preview
if "result_df" in st.session_state:
    st.dataframe(st.session_state["result_df"], use_container_width=True)

# Download button
if "result_excel_bytes" in st.session_state:
    st.download_button(
        "Download Excel",
        data=st.session_state["result_excel_bytes"],
        file_name="extraction.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

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
        # Build summary by jurisdiction
        summary = (
            df.groupby(state_col)[amount_col]
            .sum()
            .reset_index()
            .rename(columns={state_col: "Jurisdiction", amount_col: "Total Amount"})
            .sort_values("Total Amount", ascending=False)
        )
        summary["State Code"] = summary["Jurisdiction"].apply(normalize_state)

        # ── Pie chart ─────────────────────────────────────────────────────────
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

        # ── US Choropleth map ──────────────────────────────────────────────────
        st.subheader("Invoice Amount by US State")

        # Only keep rows that resolved to a valid 2-letter code
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