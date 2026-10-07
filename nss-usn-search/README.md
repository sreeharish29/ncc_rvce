# NSS USN search

Enter a USN, get every activity-points PDF that mentions it, with that PDF's first page next to the page that contains the USN (highlighted).

Unzip so the layout looks like this (next to `hf_model/` and the PDF folder):

```
NSS ACTIVITY POINTS LIST FROM 2021 to 2024-...-001/
├── backend/
├── frontend/
├── hf_model/output/<year>/<pdf_name>/page_XXXX.json   <- written by ocr_model.py
└── NSS ACTIVITY POINTS LIST FROM 2021 to 2024/<year>/<pdf files>
```

## Backend (FastAPI)

```bash
cd backend
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

- `GET http://localhost:8000/api/health` shows how many PDFs/pages were indexed.
- After the OCR script produces more output, run `curl -X POST localhost:8000/api/reindex` (or restart).
- Paths can be overridden with `PDF_ROOT`, `OCR_DIR`, `OCR_DPI` (default 200, must match the OCR run).

## Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. The frontend forwards `/api/*` to the backend; set `BACKEND_URL` if it isn't on `http://127.0.0.1:8000`.
