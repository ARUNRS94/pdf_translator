"""
streamlit_app.py
----------------
Streamlit UI for the PDF Translator application.

Features:
  - PDF file upload
  - Target language selection
  - Real-time progress bar
  - Download button for translated PDF
  - Error display with diagnostics

Run with:
  streamlit run streamlit_app.py
"""

import io
import logging
import os
import tempfile
import time
from pathlib import Path

import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PDF Translator",
    page_icon="📄",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Import pipeline modules ────────────────────────────────────────────────────
# These are resolved from the same directory as this script.
try:
    from pdf_extractor import extract_pdf
    from translation_service import translate_all_pages
    from pdf_renderer import render_pdf
    PIPELINE_AVAILABLE = True
except Exception as _import_err:
    PIPELINE_AVAILABLE = False
    _IMPORT_ERROR_MSG = str(_import_err)

SUPPORTED_LANGUAGES = [
    "French", "German", "Spanish", "Italian", "Portuguese",
    "Dutch", "Polish", "Russian", "Japanese", "Chinese (Simplified)",
    "Chinese (Traditional)", "Korean", "Arabic", "Hindi", "Turkish",
    "Swedish", "Danish", "Norwegian", "Finnish", "Greek",
]

# ── Styles ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .main-title { font-size: 2rem; font-weight: 700; color: #1a1a2e; }
    .subtitle   { font-size: 1rem; color: #555; margin-bottom: 1.5rem; }
    .step-label { font-size: 0.85rem; color: #888; font-weight: 600;
                  text-transform: uppercase; letter-spacing: 0.05em; }
    .success-box { background: #e8f5e9; border-left: 4px solid #43a047;
                   padding: 1rem; border-radius: 4px; margin: 1rem 0; }
    .error-box   { background: #fdecea; border-left: 4px solid #e53935;
                   padding: 1rem; border-radius: 4px; margin: 1rem 0; }
    .info-box    { background: #e3f2fd; border-left: 4px solid #1e88e5;
                   padding: 1rem; border-radius: 4px; margin: 1rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown('<div class="main-title">📄 PDF Translator</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Upload a PDF document and translate it into your '
    'chosen language while preserving the original formatting.</div>',
    unsafe_allow_html=True,
)

# ── Import error guard ─────────────────────────────────────────────────────────
if not PIPELINE_AVAILABLE:
    st.error(
        f"**Dependency error:** Could not import translation pipeline.\n\n"
        f"`{_IMPORT_ERROR_MSG}`\n\n"
        "Please ensure all required packages are installed:\n"
        "```\npip install -r requirements.txt\n```"
    )
    st.stop()

# ── Sidebar: configuration info ────────────────────────────────────────────────
with st.sidebar:
    st.header("ℹ️ About")
    st.markdown(
        "**PDF Translator** extracts text from your PDF, "
        "translates it using an enterprise LLM, and reconstructs "
        "the document with the same layout.\n\n"
        "**Preserved:** headings, paragraphs, tables, lists, "
        "line breaks, spacing, page order.\n\n"
        "**Not changed:** numbers, dates, URLs, codes, symbols."
    )
    st.markdown("---")
    st.markdown("**Max file size:** 200 MB")
    st.markdown("**Supported:** Text-based PDFs (not scanned images)")

# ── Main form ──────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 2])

with col1:
    st.markdown('<div class="step-label">Step 1 — Upload PDF</div>', unsafe_allow_html=True)
    uploaded_file = st.file_uploader(
        label="Choose a PDF file",
        type=["pdf"],
        help="Maximum file size: 200 MB. Text-based PDFs only.",
        label_visibility="collapsed",
    )

with col2:
    st.markdown('<div class="step-label">Step 2 — Select Language</div>', unsafe_allow_html=True)
    target_language = st.selectbox(
        label="Target language",
        options=SUPPORTED_LANGUAGES,
        index=0,
        label_visibility="collapsed",
    )

st.markdown("---")

# ── Translation button ─────────────────────────────────────────────────────────
translate_btn = st.button(
    "🌐 Translate PDF",
    type="primary",
    use_container_width=True,
    disabled=(uploaded_file is None),
)

# ── Pipeline execution ─────────────────────────────────────────────────────────
if translate_btn and uploaded_file is not None:
    start_time = time.time()

    # Persist session state for download
    st.session_state.pop("translated_bytes", None)
    st.session_state.pop("output_filename", None)
    st.session_state.pop("error_message", None)

    status_container = st.empty()
    progress_bar = st.progress(0)
    log_container = st.empty()

    tmp_dir = tempfile.mkdtemp(prefix="streamlit_pdf_")
    input_path = os.path.join(tmp_dir, "input.pdf")
    output_path = os.path.join(tmp_dir, "output.pdf")

    try:
        # ── Step 1: Save upload ────────────────────────────────────────────────
        status_container.info("📥 Saving uploaded file…")
        with open(input_path, "wb") as f:
            f.write(uploaded_file.read())
        file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
        log_container.caption(f"File saved: {uploaded_file.name} ({file_size_mb:.1f} MB)")
        progress_bar.progress(5)

        # ── Step 2: Extract ────────────────────────────────────────────────────
        status_container.info("🔍 Extracting text and structure from PDF…")
        pages = extract_pdf(input_path)
        total_pages = len(pages)
        log_container.caption(f"Extracted {total_pages} pages from PDF.")
        progress_bar.progress(20)

        if total_pages == 0:
            raise ValueError("No pages could be extracted from this PDF. "
                             "The file may be empty, image-only, or password-protected.")

        # ── Step 3: Translate ──────────────────────────────────────────────────
        status_container.info(f"🌐 Translating {total_pages} pages into **{target_language}**…")

        def update_progress(current: int, total: int):
            pct = 20 + int((current / total) * 60)  # 20%–80%
            progress_bar.progress(pct)
            log_container.caption(
                f"Translating page {current}/{total} → {target_language}"
            )

        translated_pages = translate_all_pages(
            pages,
            target_language,
            progress_callback=update_progress,
        )
        progress_bar.progress(80)

        # ── Step 4: Render ─────────────────────────────────────────────────────
        status_container.info("📝 Reconstructing translated PDF…")
        render_pdf(translated_pages, output_path)
        progress_bar.progress(98)

        # ── Done ───────────────────────────────────────────────────────────────
        with open(output_path, "rb") as f:
            translated_bytes = f.read()

        elapsed = time.time() - start_time
        stem = Path(uploaded_file.name).stem
        output_filename = f"translated_{target_language.lower().replace(' ', '_')}_{stem}.pdf"

        st.session_state["translated_bytes"] = translated_bytes
        st.session_state["output_filename"] = output_filename

        progress_bar.progress(100)
        status_container.success(
            f"✅ Translation complete in {elapsed:.1f}s — "
            f"{total_pages} pages translated into {target_language}."
        )
        log_container.empty()

    except ValueError as exc:
        logger.error("Validation error: %s", exc)
        st.session_state["error_message"] = str(exc)
        status_container.error(f"❌ Input error: {exc}")

    except RuntimeError as exc:
        logger.error("Runtime error: %s", exc)
        st.session_state["error_message"] = str(exc)
        status_container.error(f"❌ Translation error: {exc}")

    except Exception as exc:  # noqa: BLE001
        import traceback as tb
        logger.exception("Unexpected error: %s", exc)
        st.session_state["error_message"] = str(exc)
        status_container.error(f"❌ Unexpected error: {exc}")
        with st.expander("🔍 Diagnostic details"):
            st.code(tb.format_exc())

    finally:
        # Clean up temp files
        for path in [input_path, output_path]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:  # noqa: BLE001
                pass
        try:
            os.rmdir(tmp_dir)
        except Exception:  # noqa: BLE001
            pass

# ── Download section ───────────────────────────────────────────────────────────
if "translated_bytes" in st.session_state:
    st.markdown("---")
    st.markdown("### 📥 Download Translated PDF")
    st.download_button(
        label=f"⬇️ Download — {st.session_state['output_filename']}",
        data=st.session_state["translated_bytes"],
        file_name=st.session_state["output_filename"],
        mime="application/pdf",
        use_container_width=True,
        type="primary",
    )
    size_kb = len(st.session_state["translated_bytes"]) / 1024
    st.caption(f"File size: {size_kb:.1f} KB")

# ── Info footer ────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    "<small>Powered by Cepheid OS Enterprise LLM · "
    "GeneXpert PDF Translator · "
    "For internal use only.</small>",
    unsafe_allow_html=True,
)
