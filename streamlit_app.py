"""
streamlit_app.py
----------------
Streamlit UI for the PDF Translator application.

Run with:  streamlit run streamlit_app.py
"""

import io
import logging
import os
import tempfile
import time
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="PDF Translator",
    page_icon="📄",
    layout="centered",
    initial_sidebar_state="collapsed",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

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

# Page cap: read from env, but also allow the user to override in the UI.
_ENV_CAP: int = int(os.getenv("MAX_TRANSLATE_PAGES", "0"))

st.markdown("""
<style>
.main-title { font-size: 2rem; font-weight: 700; color: #1a1a2e; }
.subtitle   { font-size: 1rem; color: #555; margin-bottom: 1.5rem; }
.step-label { font-size: 0.85rem; color: #888; font-weight: 600;
              text-transform: uppercase; letter-spacing: 0.05em; }
.cap-badge  { display: inline-block; background: #fff3cd; color: #856404;
              border: 1px solid #ffc107; border-radius: 4px;
              padding: 2px 8px; font-size: 0.8rem; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">📄 PDF Translator</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Upload a PDF and translate it while preserving '
    'all original formatting.</div>',
    unsafe_allow_html=True,
)

if not PIPELINE_AVAILABLE:
    st.error(
        f"**Dependency error:** Could not import translation pipeline.\n\n"
        f"`{_IMPORT_ERROR_MSG}`\n\n"
        "Install packages:\n```\npip install -r requirements.txt\n```"
    )
    st.stop()

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    st.markdown("**Page Limit (Testing)**")
    st.caption(
        "Cap the number of pages translated. "
        "Useful for testing without processing the full document. "
        "Set to 0 for no limit."
    )

    max_pages = st.number_input(
        label="Max pages to translate",
        min_value=0,
        max_value=500,
        value=_ENV_CAP,
        step=1,
        help=(
            "0 = translate all pages. "
            "Any positive number limits translation to that many pages "
            "starting from page 1."
        ),
    )

    if max_pages > 0:
        st.markdown(
            f'<span class="cap-badge">⚠️ Page cap: {max_pages} pages</span>',
            unsafe_allow_html=True,
        )
    else:
        st.success("No page limit — full document will be translated.")

    st.markdown("---")
    st.header("ℹ️ About")
    st.markdown(
        "**Preserved:** headings, paragraphs, tables, lists, line breaks, page order.\n\n"
        "**Not changed:** numbers, dates, URLs, codes, symbols."
    )
    st.markdown("**Max upload:** 200 MB")
    st.markdown("**Supported:** Text-based PDFs (not scanned images)")

# ── Main form ──────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 2])

with col1:
    st.markdown('<div class="step-label">Step 1 — Upload PDF</div>', unsafe_allow_html=True)
    uploaded_file = st.file_uploader(
        label="Choose a PDF file",
        type=["pdf"],
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

# Show a concise summary of what will happen before the button
if uploaded_file and max_pages > 0:
    st.info(
        f"ℹ️ **Page cap active:** Only the first **{max_pages} page(s)** of "
        f"'{uploaded_file.name}' will be translated. "
        "Adjust in the sidebar to change the limit."
    )

st.markdown("---")
translate_btn = st.button(
    "🌐 Translate PDF",
    type="primary",
    use_container_width=True,
    disabled=(uploaded_file is None),
)

# ── Pipeline ───────────────────────────────────────────────────────────────────
if translate_btn and uploaded_file is not None:
    start_time = time.time()
    st.session_state.pop("translated_bytes", None)
    st.session_state.pop("output_filename", None)
    st.session_state.pop("cap_info", None)

    status_box = st.empty()
    progress_bar = st.progress(0)
    log_line = st.empty()

    tmp_dir = tempfile.mkdtemp(prefix="streamlit_pdf_")
    input_path = os.path.join(tmp_dir, "input.pdf")
    output_path = os.path.join(tmp_dir, "output.pdf")

    try:
        # Save upload
        status_box.info("📥 Saving uploaded file…")
        with open(input_path, "wb") as f:
            f.write(uploaded_file.read())
        file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
        log_line.caption(f"Saved: {uploaded_file.name} ({file_size_mb:.1f} MB)")
        progress_bar.progress(5)

        # Extract
        status_box.info("🔍 Extracting PDF structure…")
        all_pages = extract_pdf(input_path)
        total_pages = len(all_pages)
        log_line.caption(f"Extracted {total_pages} pages.")
        progress_bar.progress(15)

        if total_pages == 0:
            raise ValueError(
                "No pages could be extracted. The file may be empty, "
                "image-only, or password-protected."
            )

        # Apply page cap
        was_capped = False
        pages = all_pages
        if max_pages > 0 and total_pages > max_pages:
            pages = all_pages[:max_pages]
            was_capped = True
            logger.info("Page cap applied: %d of %d pages", max_pages, total_pages)

        pages_to_translate = len(pages)
        progress_bar.progress(20)

        # Translate
        cap_label = (
            f"pages 1–{pages_to_translate} (of {total_pages})"
            if was_capped
            else f"all {total_pages} pages"
        )
        status_box.info(f"🌐 Translating {cap_label} into **{target_language}**…")

        def update_progress(current: int, total: int):
            pct = 20 + int((current / total) * 60)
            progress_bar.progress(pct)
            log_line.caption(f"Translating page {current}/{total}")

        translated_pages = translate_all_pages(
            pages,
            target_language,
            progress_callback=update_progress,
        )
        progress_bar.progress(82)

        # Render
        status_box.info("📝 Reconstructing translated PDF…")
        render_pdf(translated_pages, output_path)
        progress_bar.progress(98)

        with open(output_path, "rb") as f:
            translated_bytes = f.read()

        elapsed = time.time() - start_time
        stem = Path(uploaded_file.name).stem
        cap_suffix = f"_pages1-{pages_to_translate}" if was_capped else ""
        output_filename = (
            f"translated_{target_language.lower().replace(' ', '_')}"
            f"{cap_suffix}_{stem}.pdf"
        )

        st.session_state["translated_bytes"] = translated_bytes
        st.session_state["output_filename"] = output_filename
        st.session_state["cap_info"] = {
            "was_capped": was_capped,
            "translated": pages_to_translate,
            "total": total_pages,
            "elapsed": elapsed,
            "language": target_language,
        }

        progress_bar.progress(100)
        log_line.empty()

        if was_capped:
            status_box.warning(
                f"⚠️ **Page cap applied** — Translated pages 1–{pages_to_translate} "
                f"of {total_pages} total pages into {target_language} "
                f"in {elapsed:.1f}s. "
                f"Set cap to 0 in the sidebar to translate the full document."
            )
        else:
            status_box.success(
                f"✅ Translation complete — {total_pages} pages translated "
                f"into {target_language} in {elapsed:.1f}s."
            )

    except ValueError as exc:
        logger.error("Validation error: %s", exc)
        status_box.error(f"❌ Input error: {exc}")

    except RuntimeError as exc:
        logger.error("Runtime error: %s", exc)
        status_box.error(f"❌ Translation error: {exc}")

    except Exception as exc:
        import traceback as tb
        logger.exception("Unexpected: %s", exc)
        status_box.error(f"❌ Unexpected error: {exc}")
        with st.expander("🔍 Diagnostic details"):
            st.code(tb.format_exc())

    finally:
        for path in [input_path, output_path]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        try:
            os.rmdir(tmp_dir)
        except Exception:
            pass

# ── Download ───────────────────────────────────────────────────────────────────
if "translated_bytes" in st.session_state:
    st.markdown("---")
    info = st.session_state.get("cap_info", {})

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Pages Translated", info.get("translated", "—"))
    col_b.metric("Total in PDF", info.get("total", "—"))
    col_c.metric("Time (s)", f"{info.get('elapsed', 0):.1f}")

    st.download_button(
        label=f"⬇️ Download — {st.session_state['output_filename']}",
        data=st.session_state["translated_bytes"],
        file_name=st.session_state["output_filename"],
        mime="application/pdf",
        use_container_width=True,
        type="primary",
    )
    size_kb = len(st.session_state["translated_bytes"]) / 1024
    st.caption(f"Output size: {size_kb:.1f} KB")

st.markdown("---")
st.markdown(
    "<small>PDF Translator · Enterprise Edition · For internal use only.</small>",
    unsafe_allow_html=True,
)
