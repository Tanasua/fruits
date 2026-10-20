"""Вирізає персонажів з картинок на чорному фоні й готує ролик: assets/, cast.json, check/cast.png.

Вхід у videos/<назва>/:
  input/      картинки (png/jpg/webp), беруться в природному порядку імен файлів (1, 2, 10)
  names.txt   назви українською в тому самому порядку: по одній на рядок або через кому

Порядок персонажів: картинки по черзі; у кожній — рядки зверху вниз, у рядку зліва направо.
Фон чорний, тож персонаж — це зв'язна область яскравіших за поріг пікселів. Закриття
морфологією склеює темні місця всередині персонажа; отвори не заливаються, щоб фон між
деталями (стебло й листок) лишався прозорим. Розмір нормалізується за sqrt(площа), щоб
персонажі однаково «важили» в кадрі.

Якщо кількість знайдених персонажів не збігається з кількістю назв, скрипт зупиняється
і малює check/detected_<картинка>.png з пронумерованими рамками — щоб побачити, що злиплось
або розпалось (тоді змінюйте --threshold / --close / --min-area).

Запуск: python3 studio/cut.py videos/<назва> [--threshold 12] [--close 2] [--min-area 3000]
"""
import argparse
import json
import re
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    from scipy import ndimage
except ImportError as e:
    raise SystemExit(f"бракує модуля: {e.name} (pip install numpy pillow scipy)")

TARGET_SQRT_AREA = 390  # px; ананас з першого ролика ~725 px заввишки при 1080p
PAD = 4
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
    "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "'": "", "’": "", "ʼ": "",
}


def slug(name: str) -> str:
    s = "".join(TRANSLIT.get(ch, ch) for ch in name.lower())
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "item"


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def read_names(path: Path):
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"[,\n;]+", text)
    return [re.sub(r"^\s*\d+[.)]\s*", "", p).strip() for p in parts if p.strip()]


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def detect(src: Image.Image, threshold, close, min_area):
    lum = np.asarray(src.convert("RGB")).astype(np.float32).max(axis=2)
    mask = ndimage.binary_closing(lum > threshold, iterations=close)
    mask = ndimage.binary_opening(mask, iterations=2)
    lab, n = ndimage.label(mask)
    comps = []
    for k, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        area = int((lab[sl] == k).sum())
        if area < min_area:
            continue
        y0, y1 = sl[0].start, sl[0].stop - 1
        x0, x1 = sl[1].start, sl[1].stop - 1
        comps.append({"label": k, "area": area, "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                      "cy": (y0 + y1) / 2, "cx": (x0 + x1) / 2})
    return lab, sort_rows(comps)


def sort_rows(comps):
    """Рядки: персонаж належить рядку, якщо його центр по вертикалі в межах висоти рядка."""
    rows = []
    for c in sorted(comps, key=lambda c: c["cy"]):
        for r in rows:
            if r["y0"] <= c["cy"] <= r["y1"]:
                r["items"].append(c)
                r["y0"], r["y1"] = min(r["y0"], c["y0"]), max(r["y1"], c["y1"])
                break
        else:
            rows.append({"y0": c["y0"], "y1": c["y1"], "items": [c]})
    return [c for r in rows for c in sorted(r["items"], key=lambda c: c["cx"])]


def draw_detected(src, comps, out: Path, start):
    im = src.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    f = font(max(24, im.height // 30))
    for i, c in enumerate(comps):
        d.rectangle((c["x0"], c["y0"], c["x1"], c["y1"]), outline=(255, 60, 60), width=4)
        d.text((c["x0"] + 6, c["y0"] + 4), str(start + i + 1), fill=(255, 255, 0), font=f)
    im.save(out)


def contact_sheet(cast, root: Path, out: Path):
    cell, cols = 240, 6
    rows = (len(cast) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 40)), (0, 0, 0))
    d = ImageDraw.Draw(sheet)
    f = font(22)
    for i, c in enumerate(cast):
        im = Image.open(root / c["file"])
        im.thumbnail((cell - 20, cell - 20))
        x, y = (i % cols) * cell, (i // cols) * (cell + 40)
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2), im)
        d.text((x + 10, y + cell + 6), f"{i + 1}. {c['name']}", fill=(255, 255, 255), font=f)
    sheet.save(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--threshold", type=float, default=12)
    ap.add_argument("--close", type=int, default=2)
    ap.add_argument("--min-area", type=int, default=3000)
    a = ap.parse_args()

    root = Path(a.video)
    images = sorted((p for p in (root / "input").iterdir() if p.suffix.lower() in IMAGE_EXT), key=natural_key)
    names = read_names(root / "names.txt")
    if not images:
        raise SystemExit(f"немає картинок у {root / 'input'}")
    (root / "assets").mkdir(exist_ok=True)
    (root / "check").mkdir(exist_ok=True)

    found = []
    for p in images:
        src = Image.open(p)
        lab, comps = detect(src, a.threshold, a.close, a.min_area)
        print(f"{p.name}: знайдено {len(comps)}")
        found.append((p, src, lab, comps))
    total = sum(len(f[3]) for f in found)
    if total != len(names):
        start = 0
        for p, src, lab, comps in found:
            draw_detected(src, comps, root / "check" / f"detected_{p.stem}.png", start)
            start += len(comps)
        raise SystemExit(f"знайдено {total} персонажів, а назв {len(names)}; дивіться {root / 'check'}/detected_*.png")

    cast, used = [], set()
    it = iter(names)
    for p, src, lab, comps in found:
        rgba = src.convert("RGBA")
        for c in comps:
            name = next(it)
            cid = base = slug(name)
            n = 2
            while cid in used:
                cid, n = f"{base}_{n}", n + 1
            used.add(cid)
            alpha = Image.fromarray(((lab == c["label"]) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
            cut = rgba.copy()
            cut.putalpha(alpha)
            cut = cut.crop((max(c["x0"] - PAD, 0), max(c["y0"] - PAD, 0),
                            min(c["x1"] + PAD + 1, src.width), min(c["y1"] + PAD + 1, src.height)))
            scale = TARGET_SQRT_AREA / np.sqrt(c["area"])
            cut = cut.resize((round(cut.width * scale), round(cut.height * scale)), Image.LANCZOS)
            cut.save(root / "assets" / f"{cid}.png")
            cast.append({"id": cid, "name": name, "file": f"assets/{cid}.png", "w": cut.width, "h": cut.height})
            print(f"  {len(cast):2d}. {name:14s} -> {cid} {cut.width}x{cut.height}")

    (root / "cast.json").write_text(json.dumps(cast, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contact_sheet(cast, root, root / "check" / "cast.png")
    print(f"персонажів: {len(cast)}; перевірка відповідності назв: {root / 'check' / 'cast.png'}")


if __name__ == "__main__":
    sys.exit(main())
