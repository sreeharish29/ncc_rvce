"""NSS USN search backend.

Reads the per-page OCR JSON written by ocr_model.py, builds an in-memory index,
and serves search results plus rendered page images with the USN highlighted.

Expected layout (this folder sits next to hf_model/ and the PDF folder):

    <BASE>/
      backend/main.py            <- this file
      hf_model/output/<year>/<pdf_name>/page_0001.json ...
      NSS ACTIVITY POINTS LIST FROM 2021 to 2024/<year>/<pdf file>

Override with the PDF_ROOT, OCR_DIR, OCR_DPI, CACHE_DIR and CORS_ORIGINS env vars.
"""
import hashlib
import io
import json
import os
import re
import shutil
import threading
import zipfile
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import fitz  # PyMuPDF
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageDraw
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
PDF_ROOT = Path(os.getenv("PDF_ROOT", BASE / "NSS ACTIVITY POINTS LIST FROM 2021 to 2024"))
OCR_DIR = Path(os.getenv("OCR_DIR", BASE / "hf_model" / "output"))
OCR_DPI = int(os.getenv("OCR_DPI", "200"))  # must match the DPI used when the OCR was run
CACHE_DIR = Path(os.getenv("CACHE_DIR", HERE / ".cache"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

THUMB_W, FULL_W = 700, 1800
MIN_QUERY_LEN = 6


# --------------------------------------------------------------------------- text matching

# OCR often reads "1" as "I"/"l", "0" as "O", and so on. Both the query and the OCR text go
# through the same mapping, so a USN still matches when a character was misread.
_CONFUSABLE = str.maketrans(
    {"I": "1", "L": "1", "|": "1", "!": "1", "O": "0", "Q": "0", "S": "5", "B": "8", "Z": "2"}
)


def canon(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9|!]", "", text).upper()
    return text.translate(_CONFUSABLE)


# --------------------------------------------------------------------------- index

INDEX: dict[str, dict] = {}


def display_name(rel: str) -> str:
    return Path(rel).stem.strip(" _")


def _norm_name(name: str) -> str:
    """Compare file names ignoring case, punctuation, spacing and the .pdf / .pdf_ extension."""
    stem = re.sub(r"\.pdf_?$", "", name.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", stem.lower())


def resolve_pdf(rel: str) -> Path | None:
    """Find the PDF for an OCR record even if the file was renamed since the OCR run."""
    exact = PDF_ROOT / rel
    if exact.is_file():
        return exact
    folder = exact.parent
    if not folder.is_dir():
        return None
    want = _norm_name(Path(rel).name)
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in (".pdf", ".pdf_") and _norm_name(f.name) == want:
            return f
    return None


def build_index() -> dict[str, dict]:
    pdfs: dict[str, dict] = {}
    for jf in sorted(OCR_DIR.glob("*/*/page_*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rel = data.get("source_pdf") or f"{jf.parent.parent.name}/{jf.parent.name}.pdf"
        pdf_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
        if pdf_id not in pdfs:
            pdfs[pdf_id] = {
                "id": pdf_id,
                "rel": rel,
                "name": display_name(rel),
                "year": Path(rel).parent.name or jf.parent.parent.name,
                "path": resolve_pdf(rel),  # once per PDF, not once per page
                "pages": [],
            }
        entry = pdfs[pdf_id]
        items = [
            {"text": it["text"], "box": it["box"], "canon": canon(it["text"])}
            for it in data.get("items", [])
        ]
        entry["pages"].append(
            {"page": int(data["page"]), "items": items, "rows": data.get("rows", [])}
        )
    for e in pdfs.values():
        e["pages"].sort(key=lambda p: p["page"])
    return pdfs


def row_for(page: dict, q: str) -> list[str]:
    for row in page["rows"]:
        if any(q in canon(cell) for cell in row):
            return row
    return []


def img_url(pdf_id: str, page: int, width: int, q: str | None = None) -> str:
    url = f"/api/pdfs/{pdf_id}/pages/{page}/image?w={width}"
    return url + (f"&usn={quote(q)}" if q else "")


# --------------------------------------------------------------------------- rendering

_doc_lock = threading.Lock()  # PyMuPDF documents are not thread-safe


@lru_cache(maxsize=6)
def _open_doc(path: str):
    # read bytes + filetype so odd extensions such as ".pdf_" still open
    return fitz.open(stream=Path(path).read_bytes(), filetype="pdf")


def _render(path: Path, page_no: int, width: int, boxes: list, quality: int = 85) -> bytes:
    with _doc_lock:
        doc = _open_doc(str(path))
        if not 1 <= page_no <= len(doc):
            raise HTTPException(404, "Page out of range")
        pix = doc[page_no - 1].get_pixmap(dpi=OCR_DPI)  # same DPI as the OCR run
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    if boxes:
        img = img.convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        pad = 8
        for b in boxes:
            xs, ys = [p[0] for p in b], [p[1] for p in b]
            x0, y0, x1, y1 = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
            d.rectangle([0, y0, img.width, y1], fill=(255, 230, 0, 70))  # whole row
            d.rectangle([x0, y0, x1, y1], outline=(255, 70, 0, 255), width=6)  # the USN
        img = Image.alpha_composite(img, overlay).convert("RGB")

    if width < img.width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# --------------------------------------------------------------------------- app


@asynccontextmanager
async def lifespan(_: FastAPI):
    INDEX.clear()
    INDEX.update(build_index())
    if not INDEX:
        print(f"WARNING: no OCR JSON found under {OCR_DIR}")
    yield


app = FastAPI(title="NSS USN search", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "pdfs": len(INDEX),
        "pages": sum(len(p["pages"]) for p in INDEX.values()),
        "ocr_dir": str(OCR_DIR),
        "pdf_root": str(PDF_ROOT),
        "pdfs_missing_on_disk": [p["rel"] for p in INDEX.values() if p["path"] is None],
    }


@app.post("/api/reindex")
def reindex():
    """Call this after the OCR script has produced more output."""
    INDEX.clear()
    INDEX.update(build_index())
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    return health()


def matching_pdfs(q: str) -> list[tuple[dict, list[dict]]]:
    """(pdf, pages containing q) for every PDF that mentions the normalized USN."""
    found = []
    for pdf in sorted(INDEX.values(), key=lambda p: (p["year"], p["name"])):
        hits = [pg for pg in pdf["pages"] if any(q in it["canon"] for it in pg["items"])]
        if hits:
            found.append((pdf, hits))
    return found


@app.get("/api/search")
def search(usn: str = Query(..., min_length=1)):
    q = canon(usn)
    if len(q) < MIN_QUERY_LEN:
        raise HTTPException(400, "Enter a full USN, e.g. 1RV23AI104")

    results = []
    for pdf, hits in matching_pdfs(q):
        results.append(
            {
                "pdf_id": pdf["id"],
                "name": pdf["name"],
                "year": pdf["year"],
                "n_pages": len(pdf["pages"]),
                "available": pdf["path"] is not None,
                "file": f"/api/pdfs/{pdf['id']}/file",
                "first_page_image": img_url(pdf["id"], 1, THUMB_W),
                "first_page_image_full": img_url(pdf["id"], 1, FULL_W),
                "matches": [
                    {
                        "page": pg["page"],
                        "row": row_for(pg, q),
                        "image": img_url(pdf["id"], pg["page"], THUMB_W, q),
                        "image_full": img_url(pdf["id"], pg["page"], FULL_W, q),
                    }
                    for pg in hits
                ],
            }
        )
    return {"query": usn.strip(), "normalized": q, "total": len(results), "results": results}


@app.get("/api/pdfs/{pdf_id}/pages/{page}/image")
def page_image(
    pdf_id: str,
    page: int,
    w: int = Query(THUMB_W, ge=200, le=2400),
    usn: str | None = None,
):
    pdf = INDEX.get(pdf_id)
    if not pdf:
        raise HTTPException(404, "Unknown PDF")
    if pdf["path"] is None:
        raise HTTPException(404, "Source PDF not found on disk")

    q = canon(usn) if usn else ""
    key = hashlib.sha1(f"{pdf_id}|{page}|{w}|{q}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.jpg"
    if not cached.exists():
        boxes: list = []
        if q:
            for pg in pdf["pages"]:
                if pg["page"] == page:
                    boxes = [it["box"] for it in pg["items"] if q in it["canon"]]
        data = _render(pdf["path"], page, w, boxes)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return FileResponse(cached, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def _safe_folder(name: str, used: set[str], year: str) -> str:
    """Folder name = PDF name, made filesystem-safe and unique inside the zip."""
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .") or "pdf"
    folder, n = base, 1
    while folder in used:
        n += 1
        folder = f"{base} ({year})" if n == 2 else f"{base} ({year}) {n - 1}"
    used.add(folder)
    return folder


def _single_page_pdf(path: Path, page_no: int) -> bytes:
    with _doc_lock:
        src = _open_doc(str(path))
        if not 1 <= page_no <= len(src):
            raise HTTPException(404, "Page out of range")
        out = fitz.open()
        out.insert_pdf(src, from_page=page_no - 1, to_page=page_no - 1)
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
    return data


@app.get("/api/download")
def download(
    usn: str = Query(..., min_length=1),
    fmt: str = Query("images", alias="format", pattern="^(images|pdf)$"),
):
    """Zip with one folder per matching PDF: first page + the page(s) that contain the USN."""
    q = canon(usn)
    if len(q) < MIN_QUERY_LEN:
        raise HTTPException(400, "Enter a full USN, e.g. 1RV23AI104")
    found = matching_pdfs(q)
    if not found:
        raise HTTPException(404, "No PDF mentions that USN")

    buf = io.BytesIO()
    used: set[str] = set()
    skipped: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pdf, hits in found:
            if pdf["path"] is None:
                skipped.append(pdf["rel"])
                continue
            folder = _safe_folder(pdf["name"], used, pdf["year"])
            nums = [pg["page"] for pg in hits]
            files = [("first_page_has_usn" if 1 in nums else "first_page", 1)]
            files += [(f"page_{n}", n) for n in nums if n != 1]
            for stem, n in files:
                if fmt == "pdf":
                    zf.writestr(f"{folder}/{stem}.pdf", _single_page_pdf(pdf["path"], n))
                else:
                    zf.writestr(f"{folder}/{stem}.jpg", _render(pdf["path"], n, 100_000, [], quality=90))
        if skipped:
            zf.writestr("_skipped.txt", "Source PDF not found for:\n" + "\n".join(skipped) + "\n")

    label = re.sub(r"[^A-Za-z0-9_-]", "", usn) or "usn"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{label}_activity_points_{fmt}.zip"'},
    )


@app.get("/api/pdfs/{pdf_id}/file")
def pdf_file(pdf_id: str):
    pdf = INDEX.get(pdf_id)
    if not pdf or pdf["path"] is None:
        raise HTTPException(404, "PDF not found")
    return FileResponse(
        pdf["path"],
        media_type="application/pdf",
        filename=f"{pdf['name']}.pdf",
        content_disposition_type="inline",
    )

STATIC_DIR = Path(os.getenv("STATIC_DIR", BASE / "frontend" / "out"))
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="site")