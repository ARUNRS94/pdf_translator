"""
flask_api.py
------------
Flask REST API for the PDF Translator service.

Endpoints:
  POST /translate   - Upload PDF + target language -> returns translated PDF
  GET  /health      - Health check
  GET  /languages   - List supported languages

Run:
  python flask_api.py
  gunicorn -w 2 -t 300 flask_api:app
"""

import io
import logging
import os
import tempfile
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from pdf_extractor import extract_pdf
from translation_service import translate_all_pages
from pdf_renderer import render_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

SUPPORTED_LANGUAGES = [
    "French", "German", "Spanish", "Italian", "Portuguese",
    "Dutch", "Polish", "Russian", "Japanese", "Chinese (Simplified)",
    "Chinese (Traditional)", "Korean", "Arabic", "Hindi", "Turkish",
    "Swedish", "Danish", "Norwegian", "Finnish", "Greek",
]

ALLOWED_EXTENSIONS = {"pdf"}

# Page limit for testing / resource control.
# Set MAX_TRANSLATE_PAGES=10 in .env to only translate pages 1-10.
# 0 or unset = no limit (translate all pages).
MAX_TRANSLATE_PAGES: int = int(os.getenv("MAX_TRANSLATE_PAGES", "0"))

if MAX_TRANSLATE_PAGES > 0:
    logger.info("Page cap ENABLED: max %d pages per request", MAX_TRANSLATE_PAGES)
else:
    logger.info("Page cap DISABLED: all pages will be translated")


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _apply_page_cap(pages: list) -> tuple:
    """Slice pages to MAX_TRANSLATE_PAGES if cap is active.
    Returns: (pages_to_use, total_in_pdf, was_capped)
    """
    total = len(pages)
    if MAX_TRANSLATE_PAGES > 0 and total > MAX_TRANSLATE_PAGES:
        logger.info("Page cap: using %d of %d pages", MAX_TRANSLATE_PAGES, total)
        return pages[:MAX_TRANSLATE_PAGES], total, True
    return pages, total, False


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "pdf-translator",
        "max_translate_pages": MAX_TRANSLATE_PAGES or "unlimited",
    }), 200


@app.route("/languages", methods=["GET"])
def languages():
    return jsonify({"languages": SUPPORTED_LANGUAGES}), 200


@app.route("/translate", methods=["POST"])
def translate():
    """
    Form fields:
      file            (required) - PDF file upload
      target_language (required) - E.g. "French"

    Response headers (on success):
      X-Total-Pages        - Total pages in uploaded PDF
      X-Translated-Pages   - Pages actually translated
      X-Page-Cap-Active    - "true" if cap was applied
      X-Page-Cap-Limit     - Cap value or "unlimited"
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    logger.info("[%s] /translate received", request_id)

    if "file" not in request.files:
        return jsonify({"error": "No file part in the request."}), 400

    file = request.files["file"]
    if file.filename == "" or not _allowed_file(file.filename):
        return jsonify({"error": "Invalid or missing PDF file."}), 400

    target_language = request.form.get("target_language", "").strip()
    if not target_language:
        return jsonify({"error": "target_language is required."}), 400

    if target_language not in SUPPORTED_LANGUAGES:
        return jsonify({
            "error": f"Unsupported language: '{target_language}'.",
            "supported_languages": SUPPORTED_LANGUAGES,
        }), 400

    tmp_dir = tempfile.mkdtemp(prefix="pdf_translator_")
    input_path = os.path.join(tmp_dir, f"{request_id}_input.pdf")
    output_path = os.path.join(tmp_dir, f"{request_id}_output.pdf")

    try:
        file.save(input_path)
        logger.info("[%s] Saved upload: %s", request_id, input_path)

        # Step 1: Extract
        logger.info("[%s] Step 1: Extracting PDF", request_id)
        all_pages = extract_pdf(input_path)
        logger.info("[%s] Extracted %d pages total", request_id, len(all_pages))

        # Step 2: Apply page cap
        pages, total_pages, was_capped = _apply_page_cap(all_pages)
        if was_capped:
            logger.info(
                "[%s] Cap active: translating pages 1-%d of %d",
                request_id, len(pages), total_pages,
            )

        # Step 3: Translate
        logger.info("[%s] Step 3: Translating %d pages -> %s", request_id, len(pages), target_language)
        translated_pages = translate_all_pages(pages, target_language)

        # Step 4: Render
        logger.info("[%s] Step 4: Rendering output PDF", request_id)
        render_pdf(translated_pages, output_path)

        elapsed = time.time() - start_time
        logger.info("[%s] Done in %.1fs", request_id, elapsed)

        stem = Path(file.filename).stem
        cap_suffix = f"_pages1-{len(pages)}" if was_capped else ""
        download_name = (
            f"translated_{target_language.lower().replace(' ', '_')}"
            f"{cap_suffix}_{stem}.pdf"
        )

        with open(output_path, "rb") as f:
            pdf_bytes = f.read()

        resp = send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
        resp.headers["X-Total-Pages"] = str(total_pages)
        resp.headers["X-Translated-Pages"] = str(len(translated_pages))
        resp.headers["X-Page-Cap-Active"] = "true" if was_capped else "false"
        resp.headers["X-Page-Cap-Limit"] = (
            str(MAX_TRANSLATE_PAGES) if MAX_TRANSLATE_PAGES > 0 else "unlimited"
        )
        return resp

    except ValueError as exc:
        logger.error("[%s] Validation error: %s", request_id, exc)
        return jsonify({"error": str(exc)}), 400

    except RuntimeError as exc:
        logger.error("[%s] Runtime error: %s", request_id, exc)
        return jsonify({"error": str(exc)}), 500

    except Exception as exc:
        logger.error("[%s] Unexpected: %s\n%s", request_id, exc, traceback.format_exc())
        return jsonify({"error": "An unexpected error occurred. Check server logs."}), 500

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


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    logger.info("Starting Flask API on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=debug)
