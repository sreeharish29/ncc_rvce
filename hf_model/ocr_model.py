import json
from pathlib import Path
import fitz  # pip install pymupdf
from paddleocr import PaddleOCR

PDF = Path("/home/sreeharishtj/Downloads/activity_points/nss_extract/NSS ACTIVITY POINTS LIST FROM 2021 to 2024-20261007T071543Z-1-001/NSS ACTIVITY POINTS LIST FROM 2021 to 2024/2023-24/4. July to Aug 2024 .pdf")
OUT = Path("./output")
IMG_DIR = OUT / "pages"
JSON_DIR = OUT / "text_json"
DPI = 200

IMG_DIR.mkdir(parents=True, exist_ok=True)
JSON_DIR.mkdir(parents=True, exist_ok=True)

COMMON = dict(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)


def build_ocr():
    # 1) ONNX engine (what worked for you with TextDetection)
    try:
        return PaddleOCR(
            text_detection_model_name="PP-OCRv6_small_det",
            engine="onnxruntime",
            **COMMON,
        )
    except Exception as e:
        print(f"ONNX pipeline unavailable ({e}); falling back to Paddle runtime")
    # 2) Paddle runtime with oneDNN disabled (avoids the PIR/oneDNN crash)
    return PaddleOCR(enable_mkldnn=False, **COMMON)


def group_rows(items, y_tol=15):
    """Group text boxes into visual rows, sorted left to right."""
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


ocr = build_ocr()
doc = fitz.open(PDF)
print(f"{len(doc)} pages")

for i, page in enumerate(doc, start=1):
    json_path = JSON_DIR / f"page_{i:04d}.json"
    if json_path.exists():  # resume after a crash
        continue

    img_path = IMG_DIR / f"page_{i:04d}.png"
    page.get_pixmap(dpi=DPI).save(img_path)

    res = ocr.predict(str(img_path))[0]
    texts, scores, polys = res["rec_texts"], res["rec_scores"], res["rec_polys"]

    items = []
    for t, s, p in zip(texts, scores, polys):
        p = [[int(x), int(y)] for x, y in p]
        items.append({
            "text": t,
            "score": round(float(s), 4),
            "box": p,
            "x": min(pt[0] for pt in p),
            "y": min(pt[1] for pt in p),
        })

    data = {
        "page": i,
        "image": str(img_path),
        "full_text": "\n".join(texts),
        "rows": group_rows(items),
        "items": [{k: v for k, v in it.items() if k not in ("x", "y")} for it in items],
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"page {i}: {len(items)} text boxes")