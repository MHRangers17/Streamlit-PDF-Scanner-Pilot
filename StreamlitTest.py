
import streamlit as st


st.title("PDF Extractor")
pdf = st.file_uploader("Upload PDF", type="pdf", accept_multiple_files=True)
if pdf:
    st.success("Received file")
    st.session_state["pdfs"] = pdf

process_clicked = st.button("Run OCR + Extract")


if process_clicked:
    pdfs = st.session_state.get("pdfs", [])
    # 1) OCR step (if scanned)
    # 2) Extract text/fields/tables
    # 3) Build dataframe
    # 4) Create Excel bytes
    # st.session_state["result_excel_bytes"] = excel_bytes

# --- UI: Download results ---
if "result_excel_bytes" in st.session_state:
    st.download_button(
        "Download Excel",
        data=st.session_state["result_excel_bytes"],
        file_name="extraction.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
