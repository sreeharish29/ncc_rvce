import json
import shutil
from pathlib import Path
import fitz  # pip install pymupdf
from paddleocr import PaddleOCR

# ---------- paths ----------
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "NSS ACTIVITY POINTS LIST FROM 2021 to 2024"
OUT = HERE / "output10"
TMP = HERE / "_tmp_pages10"
DPI = 200

COMMON = dict(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)


def build_ocr():
    try:  # 1) ONNX engine
        return PaddleOCR(
            text_detection_model_name="PP-OCRv6_small_det",
            engine="onnxruntime",
            **COMMON,
        )
    except Exception as e:
        print(f"ONNX pipeline unavailable ({e}); falling back to Paddle runtime")
    return PaddleOCR(enable_mkldnn=False, **COMMON)  # 2) Paddle, oneDNN off


def group_rows(items, y_tol=15):
    items = sorted(items, key=lambda d: (d["y"], d["x"]))
    rows, cur, cur_y = [], [], None
    for it in items:
        if cur_y is None or abs(it["y"] - cur_y) <= y_tol:
            cur.append(it)
            cur_y = it["y"] if cur_y is None else (cur_y + it["y"]) / 2
        else:
            rows.append(sorted(cur, key=lambda d: d["x"]))
            cur, cur_y = [it], it["y"]
    if cur:
        rows.append(sorted(cur, key=lambda d: d["x"]))
    return [[c["text"] for c in r] for r in rows]


def find_pdfs(root):
    # also catches the oddly named "...quiz.pdf_" file
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".pdf", ".pdf_"))


def safe_name(p):
    stem = p.name
    for ext in (".pdf_", ".PDF_", ".pdf", ".PDF"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return "_".join(stem.split()).strip("_")


def process_pdf(ocr, pdf_path):
    year = pdf_path.parent.name
    out_dir = OUT / year / safe_name(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(stream=pdf_path.read_bytes(), filetype="pdf")
    pages = []

    for i, page in enumerate(doc, start=1):
        jp = out_dir / f"page_{i:04d}.json"
        if jp.exists():  # resume
            pages.append(json.loads(jp.read_text(encoding="utf-8")))
            continue

        img = TMP / "page.png"
        page.get_pixmap(dpi=DPI).save(img)
        res = ocr.predict(str(img))[0]

        items = []
        for t, s, p in zip(res["rec_texts"], res["rec_scores"], res["rec_polys"]):
            p = [[int(x), int(y)] for x, y in p]
            items.append({
                "text": t,
                "score": round(float(s), 4),
                "box": p,
                "x": min(pt[0] for pt in p),
                "y": min(pt[1] for pt in p),
            })

        data = {
            "source_pdf": str(pdf_path.relative_to(ROOT)),
            "page": i,
            "full_text": "\n".join(it["text"] for it in items),
            "rows": group_rows(items),
            "items": [{k: v for k, v in it.items() if k not in ("x", "y")} for it in items],
        }
        jp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        pages.append(data)

    # one combined file per PDF
    (out_dir / "_all_pages.json").write_text(
        json.dumps({"source_pdf": str(pdf_path.relative_to(ROOT)), "pages": pages},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(pages)


if __name__ == "__main__":
    pdfs = find_pdfs(ROOT)
    print(f"{len(pdfs)} PDFs found")
    ocr = build_ocr()

    summary = []
    for n, pdf in enumerate(pdfs, start=1):
        try:
            pages = process_pdf(ocr, pdf)
            print(f"[{n}/{len(pdfs)}] {pdf.parent.name}/{pdf.name}: {pages} pages")
            summary.append({"pdf": str(pdf.relative_to(ROOT)), "pages": pages, "status": "ok"})
        except Exception as e:
            print(f"[{n}/{len(pdfs)}] FAILED {pdf.name}: {e}")
            summary.append({"pdf": str(pdf.relative_to(ROOT)), "status": f"failed: {e}"})

    OUT.mkdir(exist_ok=True)
    (OUT / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    shutil.rmtree(TMP, ignore_errors=True)
    print("done")