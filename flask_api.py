"""
flask_api.py
------------
Flask REST API for the PDF Translator service.

Endpoints:
  POST /translate   — Upload PDF + target language → returns translated PDF
  GET  /health      — Health check
  GET  /languages   — List supported languages

Run with:
  python flask_api.py

Or via gunicorn (production):
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

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit

SUPPORTED_LANGUAGES = [
    "French", "German", "Spanish", "Italian", "Portuguese",
    "Dutch", "Polish", "Russian", "Japanese", "Chinese (Simplified)",
    "Chinese (Traditional)", "Korean", "Arabic", "Hindi", "Turkish",
    "Swedish", "Danish", "Norwegian", "Finnish", "Greek",
]

ALLOWED_EXTENSIONS = {"pdf"}


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "pdf-translator"}), 200


@app.route("/languages", methods=["GET"])
def languages():
    """Return the list of supported target languages."""
    return jsonify({"languages": SUPPORTED_LANGUAGES}), 200


@app.route("/translate", methods=["POST"])
def translate():
    """
    Translate an uploaded PDF into the specified target language.

    Form fields:
      file            (required) — PDF file upload
      target_language (required) — E.g. "French"

    Returns:
      200 + translated PDF binary (application/pdf)
      400 on bad request
      500 on internal error
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    logger.info("[%s] /translate request received", request_id)

    # ── Validate inputs ────────────────────────────────────────────────────────
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

    # ── Save uploaded file to temp location ────────────────────────────────────
    tmp_dir = tempfile.mkdtemp(prefix="pdf_translator_")
    input_path = os.path.join(tmp_dir, f"{request_id}_input.pdf")
    output_path = os.path.join(tmp_dir, f"{request_id}_output.pdf")

    try:
        file.save(input_path)
        logger.info("[%s] Saved upload to %s", request_id, input_path)

        # ── Pipeline: Extract → Translate → Render ─────────────────────────────
        logger.info("[%s] Step 1: Extracting PDF", request_id)
        pages = extract_pdf(input_path)
        logger.info("[%s] Extracted %d pages", request_id, len(pages))

        logger.info("[%s] Step 2: Translating to %s", request_id, target_language)
        translated_pages = translate_all_pages(pages, target_language)

        logger.info("[%s] Step 3: Rendering translated PDF", request_id)
        render_pdf(translated_pages, output_path)

        elapsed = time.time() - start_time
        logger.info("[%s] Pipeline complete in %.1fs", request_id, elapsed)

        # ── Stream output back to caller ───────────────────────────────────────
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"translated_{target_language.lower()}_{Path(file.filename).stem}.pdf",
        )

    except ValueError as exc:
        logger.error("[%s] Validation error: %s", request_id, exc)
        return jsonify({"error": str(exc)}), 400

    except RuntimeError as exc:
        logger.error("[%s] Runtime error: %s", request_id, exc)
        return jsonify({"error": str(exc)}), 500

    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] Unexpected error: %s\n%s", request_id, exc, traceback.format_exc())
        return jsonify({"error": "An unexpected error occurred. Please check server logs."}), 500

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


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    logger.info("Starting PDF Translator Flask API on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=debug)
