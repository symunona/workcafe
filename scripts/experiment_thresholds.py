#!/usr/bin/env python3
"""
Threshold experiment: run CLIP on 30 sampled images, output HTML report
showing raw scores + what each threshold (0.22 / 0.25 / 0.27) would label.

Run from project root:
    python scripts/experiment_thresholds.py
"""

import os, sqlite3, base64, random
from pathlib import Path
from io import BytesIO

DB       = "data/seoul/history/clean_image_tags_2026-04-23.db"
DATA_DIR = "data/seoul"
N        = 30   # images to sample
THRESHOLDS = [
    ("A", 0.22, "#e57373"),
    ("B", 0.25, "#ffb74d"),
    ("C", 0.27, "#81c784"),
]

TAGS = [
    "chairs", "large chairs", "tables", "large tables", "power plugs",
    "interior of a cafe", "exterior of a building",
    "food", "drinks", "windows", "laptops",
]
MODEL_NAME = "openai/clip-vit-base-patch32"
THUMB_SIZE = (224, 224)


def local_to_disk(p: str) -> str:
    return os.path.join(DATA_DIR, p.removeprefix("/images").lstrip("/"))


def img_to_b64(img) -> str:
    buf = BytesIO()
    img.thumbnail(THUMB_SIZE)
    img.save(buf, format="JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode()


def run():
    import torch
    from PIL import Image, UnidentifiedImageError
    from transformers import CLIPProcessor, CLIPModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")
    print(f"Loading {MODEL_NAME}...")
    model = CLIPModel.from_pretrained(MODEL_NAME).to(device)
    processor = CLIPProcessor.from_pretrained(MODEL_NAME)
    model.eval()

    text_inputs = processor(
        text=[f"a photo showing {t}" for t in TAGS],
        return_tensors="pt", padding=True
    ).to(device)
    with torch.no_grad():
        text_out = model.text_model(**text_inputs)
        text_features = model.text_projection(text_out.pooler_output)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT i.id, i.cafe_id, i.local_path
        FROM images i
        JOIN (
            SELECT cafe_id FROM images
            WHERE file_size > 0 AND local_path IS NOT NULL AND local_path != ''
            GROUP BY cafe_id ORDER BY COUNT(*) DESC LIMIT 100
        ) top ON i.cafe_id = top.cafe_id
        WHERE i.file_size > 0 AND i.local_path IS NOT NULL AND i.local_path != ''
    """).fetchall()
    conn.close()

    # Sample N images: 10 random + extras weighted toward boundary scores
    sampled = random.sample(rows, min(N, len(rows)))
    print(f"Sampled {len(sampled)} images, running CLIP...")

    results = []
    for image_id, cafe_id, local_path in sampled:
        disk = local_to_disk(local_path)
        try:
            img = Image.open(disk).convert("RGB")
        except Exception as e:
            print(f"  skip {disk}: {e}")
            continue

        inputs = processor(images=[img], return_tensors="pt").to(device)
        with torch.no_grad():
            image_out = model.vision_model(**inputs)
            image_features = model.visual_projection(image_out.pooler_output)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            scores = (image_features @ text_features.T).cpu().numpy()[0]

        tag_scores = {tag: float(s) for tag, s in zip(TAGS, scores)}
        results.append({
            "image_id": image_id,
            "cafe_id":  cafe_id,
            "path":     local_path,
            "b64":      img_to_b64(img),
            "scores":   tag_scores,
        })
        print(f"  [{len(results)}/{N}] {local_path}", end="\r")

    print(f"\nBuilding report for {len(results)} images...")
    html = _build_html(results)
    out = Path("scripts/experiment_thresholds_report.html")
    out.write_text(html)
    print(f"Report: {out.resolve()}")


def _build_html(results: list[dict]) -> str:
    threshold_headers = "".join(
        f'<th style="background:{color};padding:4px 8px">Exp {name}<br>≥{thr}</th>'
        for name, thr, color in THRESHOLDS
    )

    cards = []
    for r in results:
        scores = r["scores"]
        sorted_tags = sorted(scores.items(), key=lambda x: -x[1])

        # Score rows
        score_rows = ""
        for tag, sc in sorted_tags:
            bar_w = int(sc * 500)
            cells = "".join(
                f'<td style="text-align:center">'
                f'{"✓" if sc >= thr else "·"}'
                f'</td>'
                for _, thr, _ in THRESHOLDS
            )
            score_rows += (
                f'<tr>'
                f'<td style="padding:2px 6px;white-space:nowrap">{tag}</td>'
                f'<td style="padding:2px 6px">'
                f'<div style="background:#4fc3f7;width:{bar_w}px;height:10px;display:inline-block"></div>'
                f' {sc:.3f}</td>'
                f'{cells}'
                f'</tr>'
            )

        cards.append(f"""
<div style="display:flex;gap:16px;margin-bottom:24px;border:1px solid #ddd;padding:12px;border-radius:8px">
  <div>
    <img src="data:image/jpeg;base64,{r['b64']}" style="width:200px;height:200px;object-fit:cover;border-radius:4px">
    <div style="font-size:11px;color:#666;margin-top:4px;max-width:200px;word-break:break-all">{r['path']}</div>
    <div style="font-size:11px;color:#999">id={r['image_id']} cafe={r['cafe_id']}</div>
  </div>
  <div style="overflow-x:auto">
    <table style="border-collapse:collapse;font-size:13px">
      <thead>
        <tr>
          <th style="padding:4px 8px;text-align:left">Tag</th>
          <th style="padding:4px 8px">Score</th>
          {threshold_headers}
        </tr>
      </thead>
      <tbody>{score_rows}</tbody>
    </table>
  </div>
</div>""")

    legend = "".join(
        f'<span style="background:{color};padding:3px 10px;border-radius:4px;margin-right:8px">'
        f'Exp {name}: threshold ≥ {thr}</span>'
        for name, thr, color in THRESHOLDS
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CLIP Threshold Experiments</title>
<style>body{{font-family:sans-serif;padding:24px;max-width:1200px;margin:0 auto}}</style>
</head>
<body>
<h1>CLIP Threshold Experiments</h1>
<p>{legend}</p>
<p>Prompt template: <code>"a photo showing {{tag}}"</code> — {len(results)} images sampled from top-100 cafes</p>
<hr>
{"".join(cards)}
</body>
</html>"""


if __name__ == "__main__":
    run()
