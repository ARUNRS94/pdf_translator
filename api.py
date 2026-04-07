import logging
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from pdf_translator.pdf_service import PDFTranslationError, translate_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("pdf_translator_api")

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/translate")
def translate_endpoint():
    if "file" not in request.files:
        return jsonify({"error": "Missing file field"}), 400

    uploaded = request.files["file"]
    target_language = request.form.get("target_language", "French")

    if not uploaded.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400

    try:
        input_bytes = uploaded.read()
        output_bytes = translate_pdf(input_bytes, target_language)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        Path(tmp.name).write_bytes(output_bytes)
        tmp.close()

        return send_file(
            tmp.name,
            as_attachment=True,
            download_name=f"translated_{uploaded.filename}",
            mimetype="application/pdf",
        )
    except PDFTranslationError as exc:
        logger.exception("PDF translation error")
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected translation failure")
        return jsonify({"error": f"Unhandled server error: {exc}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
