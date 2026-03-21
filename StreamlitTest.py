
import io
import streamlit as st
import pandas as pd
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes


# ── Helpers ──────────────────────────────────────────────────────────────────

def extract_from_pdf(pdf_file):
    """Return a list of row-dicts extracted from one PDF.

    Strategy per page:
      1. Tables found  → each data row becomes a dict with column headers.
      2. Text found    → each non-empty line becomes a {"Text": …} row.
      3. No text       → page is scanned; run Tesseract OCR then split lines.
    """
    pdf_bytes = pdf_file.read()
    rows = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            meta = {"Source": pdf_file.name, "Page": page_num}

            # 1) Table extraction
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
                continue  # done with this page

            # 2) Plain-text extraction
            text = page.extract_text()
            if text and text.strip():
                for line in text.splitlines():
                    if line.strip():
                        rows.append({**meta, "Text": line.strip()})
                continue

            # 3) OCR fallback for scanned pages
            images = convert_from_bytes(
                pdf_bytes, first_page=page_num, last_page=page_num
            )
            ocr_text = pytesseract.image_to_string(images[0])
            for line in ocr_text.splitlines():
                if line.strip():
                    rows.append({**meta, "Text": line.strip()})

    return rows


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

process_clicked = st.button("Run OCR + Extract")

if process_clicked:
    pdfs = st.session_state.get("pdfs", [])
    if not pdfs:
        st.warning("Please upload at least one PDF first.")
    else:
        all_rows = []
        with st.spinner("Processing…"):
            for pdf_file in pdfs:
                pdf_file.seek(0)
                try:
                    rows = extract_from_pdf(pdf_file)
                    all_rows.extend(rows)
                except Exception as e:
                    st.error(f"Error processing {pdf_file.name}: {e}")

        if all_rows:
            df = pd.DataFrame(all_rows)
            st.session_state["result_df"] = df
            st.session_state["result_excel_bytes"] = build_excel(df)
            st.success(
                f"Extracted {len(df)} rows from {len(pdfs)} file(s)."
            )
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
