# PDF Translator — Enterprise Application

> Translate large PDF documents (300+ pages) into any target language  
> while preserving exact layout, formatting, and structure.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE LAYER                         │
│                                                                      │
│   Streamlit App (streamlit_app.py)     Flask API (flask_api.py)      │
│   • File upload widget                 • POST /translate             │
│   • Language selector                  • GET  /health                │
│   • Real-time progress bar             • GET  /languages             │
│   • Download button                    • Returns PDF binary          │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       PIPELINE CORE          │
                    │                             │
                    │  1. pdf_extractor.py        │
                    │     └─ pdfplumber            │
                    │     └─ PageData / TextBlock  │
                    │                             │
                    │  2. translation_service.py  │
                    │     └─ Chunker               │
                    │     └─ LLM calls (per block) │
                    │     └─ Validation            │
                    │                             │
                    │  3. pdf_renderer.py         │
                    │     └─ ReportLab             │
                    │     └─ Tables/Headings/Lists │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       LLM CLIENT             │
                    │  llm_client.py               │
                    │  • chat_completion_mk()      │
                    │  • Retry logic (3 attempts)  │
                    │  • Temperature = 0           │
                    │  • UTF-8 safe                │
                    └─────────────────────────────┘
```

### End-to-End Flow

```
PDF Upload
   │
   ▼
pdf_extractor.py  ─── pdfplumber ──► List[PageData]
                                        │
                                        │ Each PageData contains List[TextBlock]
                                        │ block_type: heading | paragraph |
                                        │             list_item | table | raw
                                        ▼
translation_service.py ── chunk_text() ──► LLM (per block)
                       ── translate_table_rows() ──► LLM (per cell)
                       ── validate_translation() ──► fallback if invalid
                                        │
                                        ▼
pdf_renderer.py ── ReportLab ──► output.pdf
                               • Headings (H1/H2/H3 by font size)
                               • Paragraphs (justified)
                               • Tables (bordered, striped rows)
                               • List items (bulleted)
                               • Page breaks between pages
```

---

## Project Structure

```
pdf_translator/
├── .env.example            ← Copy to .env and fill in credentials
├── requirements.txt
├── llm_client.py           ← Enterprise LLM API wrapper
├── pdf_extractor.py        ← PDF text/structure extraction
├── translation_service.py  ← Chunking + translation + validation
├── pdf_renderer.py         ← ReportLab PDF reconstruction
├── flask_api.py            ← REST API server
├── streamlit_app.py        ← Web UI
└── README.md
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and set:
#   endpoint=https://your-enterprise-llm-api/v1/chat
#   api_key_ramesh=your-secret-key
```

### 3. Run the Streamlit UI

```bash
streamlit run streamlit_app.py
```

Open http://localhost:8501 in your browser.

### 4. Run the Flask API (optional, for programmatic access)

```bash
python flask_api.py
# or production:
# gunicorn -w 2 -t 300 flask_api:app
```

---

## API Usage

### Translate a PDF

```bash
curl -X POST http://localhost:5000/translate \
  -F "file=@document.pdf" \
  -F "target_language=French" \
  --output translated_french_document.pdf
```

### Health check

```bash
curl http://localhost:5000/health
# {"status": "ok", "service": "pdf-translator"}
```

### List supported languages

```bash
curl http://localhost:5000/languages
```

---

## Supported Languages

French, German, Spanish, Italian, Portuguese, Dutch, Polish, Russian,
Japanese, Chinese (Simplified), Chinese (Traditional), Korean, Arabic,
Hindi, Turkish, Swedish, Danish, Norwegian, Finnish, Greek.

---

## Translation Rules

| Content Type | Action |
|---|---|
| Human-readable prose | **Translated** |
| Headings & labels | **Translated** |
| Table cell text | **Translated** |
| Numbers, dates, symbols | **Preserved as-is** |
| URLs, emails, file paths | **Preserved as-is** |
| Code snippets | **Preserved as-is** |
| Acronyms / product names | **Preserved as-is** |

---

## Formatting Preserved

- Heading hierarchy (H1 / H2 / H3 by font size)
- Paragraphs with justified alignment
- Bullet and numbered lists
- Tables (borders, header row highlight, alternating row colors)
- Page breaks between pages
- Line breaks within paragraphs

---

## Error Handling

| Scenario | Behavior |
|---|---|
| LLM API timeout | Retry up to 3× with exponential backoff |
| Translation validation fails | Fall back to original text |
| Malformed PDF | `ValueError` returned to caller with diagnostic |
| Individual page extraction fails | Fallback to raw text extraction |
| Rendering fails | `RuntimeError` with full traceback in logs |

---

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `endpoint` | Enterprise LLM API endpoint URL | ✅ |
| `api_key_ramesh` | API authentication key | ✅ |
| `FLASK_PORT` | Flask server port (default: 5000) | ❌ |
| `FLASK_DEBUG` | Enable Flask debug mode (`true`/`false`) | ❌ |

---

## Limitations

- **Image-only / scanned PDFs**: Text cannot be extracted without OCR. Add `pytesseract` + `pdf2image` for OCR support.
- **Complex vector graphics**: Diagrams and images are not reproduced in the output PDF.
- **Right-to-left languages (Arabic, Hebrew)**: ReportLab has limited RTL support; consider using `arabic-reshaper` + `python-bidi` for Arabic.
- **Font rendering**: Requires DejaVu fonts installed on the system for full Unicode support; falls back to Helvetica (ASCII-safe).

---

## Production Checklist

- [ ] Set `endpoint` and `api_key_ramesh` in `.env`
- [ ] Deploy Flask API with gunicorn (not the built-in development server)
- [ ] Set `FLASK_DEBUG=false` in production
- [ ] Restrict file upload size in Flask (`MAX_CONTENT_LENGTH`)
- [ ] Add authentication middleware to the Flask API
- [ ] Use HTTPS in production
- [ ] Monitor logs for LLM API errors and retry exhaustion
