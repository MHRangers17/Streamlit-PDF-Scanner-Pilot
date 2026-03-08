
import streamlit as st


st.title("PDF Extractor")
pdf = st.file_uploader("Upload PDF", type="pdf")
if pdf:
    st.success("Received file")


