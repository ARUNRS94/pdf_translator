import io
import os

import requests
import streamlit as st

API_BASE_URL = os.getenv("PDF_TRANSLATOR_API", "http://localhost:5000")

st.set_page_config(page_title="PDF Translator", page_icon="📄", layout="wide")
st.title("Enterprise PDF Translator")
st.caption("Upload a PDF and get a layout-preserving translated PDF.")

langs = [
    "French",
    "Spanish",
    "German",
    "Italian",
    "Portuguese",
    "Japanese",
    "Korean",
    "Chinese (Simplified)",
]
target_language = st.selectbox("Target language", options=langs, index=0)
max_pages = st.number_input(
    "Max pages to translate (testing)",
    min_value=1,
    value=10,
    step=1,
    help="Only first N pages are translated; remaining pages are kept unchanged.",
)
uploaded = st.file_uploader("Upload PDF", type=["pdf"])

if st.button("Translate", type="primary", disabled=uploaded is None):
    if uploaded is None:
        st.warning("Please upload a PDF.")
    else:
        with st.spinner("Translating PDF. This may take time for 300+ pages..."):
            files = {"file": (uploaded.name, uploaded.getvalue(), "application/pdf")}
            data = {"target_language": target_language, "max_pages": str(max_pages)}

            try:
                resp = requests.post(f"{API_BASE_URL}/translate", files=files, data=data, timeout=1800)
                if resp.status_code != 200:
                    st.error(f"Translation failed: {resp.text}")
                else:
                    st.success("Translation complete")
                    st.download_button(
                        label="Download translated PDF",
                        data=io.BytesIO(resp.content),
                        file_name=f"translated_{uploaded.name}",
                        mime="application/pdf",
                    )
            except requests.RequestException as exc:
                st.error(f"API request failed: {exc}")
