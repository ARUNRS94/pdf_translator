# PDF Translator (Flask API + Streamlit UI)

Production-oriented PDF Translator application that translates large PDFs (300+ pages) into a target language while preserving page geometry and text positioning.

## Architecture

End-to-end flow:

1. **PDF Upload** (Streamlit)  
   User uploads a `.pdf` and chooses a target language.
2. **Text Extraction** (Flask service, PyMuPDF)  
   Extract page/text span metadata (`bbox`, font, size, color, order).
3. **Chunking**  
   Spans are grouped by character budget (`MAX_CHARS=3500`) to keep requests deterministic and bounded.
4. **Translation**  
   Each chunk is translated through `chat_completion_mk()` with `temperature=0`.
5. **Validation + Fallback**  
   Output is validated for structure and length ratio; bad translations revert to source text.
6. **PDF Reassembly**  
   Pages are cloned, source text regions are redacted, and translated text is inserted into original bounding boxes.
7. **Output Generation**  
   API returns translated PDF bytes for download in Streamlit.
   Optional `max_pages` limit supports fast testing by translating only the first N pages.

## Key behaviors implemented

- Deterministic LLM translation (`temperature=0`).
- Translation rules enforced in prompt: preserve ordering, avoid rewriting URLs/emails/code/numbers.
- Timeout/retry logic in translation client.
- UTF-8 safe handling via Python `str` throughout request/response flow.
- Clear error classes and API diagnostic responses.
- Graceful fallback to original text when rendering/validation fails.

## Project structure

- `streamlit_app.py` - UI upload/download app.
- `api.py` - Flask API (`/health`, `/translate`).
- `pdf_translator/config.py` - dotenv/env configuration.
- `pdf_translator/translator.py` - `chat_completion_mk` + retry + JSON parsing.
- `pdf_translator/pdf_service.py` - parsing/chunking/translation/reconstruction.
- `pdf_translator/models.py` - data models.

## Environment variables

Create `.env`:

```bash
endpoint=https://your-enterprise-llm-endpoint
api_key_ramesh=your_api_key
PDF_TRANSLATOR_API=http://localhost:5000
```

## Run

```bash
pip install -r requirements.txt
python api.py
streamlit run streamlit_app.py

Use the **Max pages to translate (testing)** field in Streamlit (or `max_pages` form field in API) to limit translation scope during test runs.
```

## Notes on formatting fidelity

The service preserves page dimensions, text span order, and bounding boxes. Exact visual identity can still be impacted by target-language expansion/compression and font glyph coverage; when risk is detected (validation or rendering failures), source text is retained for that span.
