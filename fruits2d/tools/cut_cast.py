"""Вирізає персонажів з фону (чорний) і зберігає їх у fruits2d/assets з прозорим фоном.

Фони чорні, тому персонажі виділяються порогом яскравості. Закриття морфологією
склеює темні місця (ожина, смородина), після чого кожна зв'язна компонента — один персонаж.
Порядок персонажів: рядки зверху вниз, у рядку зліва направо — як у списку назв.
Розмір нормалізується: sqrt(площа) кожного персонажа дорівнює TARGET_SQRT_AREA,
щоб фрукти однаково «важили» в кадрі.

Запуск: python3 fruits2d/tools/cut_cast.py <папка_з_зображеннями>
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

THRESHOLD = 12
MIN_AREA = 3000
TARGET_SQRT_AREA = 390  # px у вихідному кадрі, узгоджено з solo.html (ананас ~725 px заввишки)
PAD = 4

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

# Порядок з вашого списку: рядки зверху вниз, у рядку зліва направо.
# Файл, назва ukr, id (для імені PNG і аудіо).
SETS = [
    ("3.webp", [
        ("ананас", "pineapple"), ("банан", "banana"), ("яблучко", "apple"), ("грушка", "pear"),
    ]),
    ("5.webp", [
        ("апельсин", "orange"), ("полуниця", "strawberry"), ("виноград", "grape"), ("кавунчик", "watermelon"),
        ("персик", "peach"), ("вишенька", "cherry"), ("лимон", "lemon"), ("манго", "mango"),
    ]),
    ("6.webp", [
        ("кокос", "coconut"), ("ківі", "kiwi"), ("слива", "plum"), ("гранат", "pomegranate"),
        ("мандарин", "mandarin"), ("чорниця", "blueberry"), ("малинка", "raspberry"), ("ожина", "blackberry"),
    ]),
    ("7.webp", [
        ("абрикос", "apricot"), ("лайм", "lime"), ("грейпфрут", "grapefruit"), ("динька", "melon"),
        ("аґрус", "gooseberry"), ("смородина", "blackcurrant"), ("порічки", "redcurrant"), ("черешня", "sweet_cherry"),
    ]),
]


def cut_sheet(path: Path, expected: int):
    src = Image.open(path).convert("RGB")
    lum = np.asarray(src).astype(np.float32).max(axis=2)
    # Без заливки отворів: між стеблом і листком фон має лишитися прозорим.
    mask = ndimage.binary_closing(lum > THRESHOLD, iterations=2)
    mask = ndimage.binary_opening(mask, iterations=2)
    lab, n = ndimage.label(mask)
    comps = []
    for k in range(1, n + 1):
        comp = lab == k
        area = int(comp.sum())
        if area < MIN_AREA:
            continue
        ys, xs = np.nonzero(comp)
        comps.append({"label": k, "area": area, "x0": xs.min(), "x1": xs.max(), "y0": ys.min(), "y1": ys.max(),
                      "cy": (ys.min() + ys.max()) / 2, "cx": (xs.min() + xs.max()) / 2})
    if len(comps) != expected:
        raise SystemExit(f"{path.name}: очікувалось {expected} персонажів, знайдено {len(comps)}")
    rows_split = src.height / 2
    comps.sort(key=lambda c: (c["cy"] >= rows_split, c["cx"]))
    return src, lab, comps


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    src_dir = Path(sys.argv[1])
    ASSETS.mkdir(parents=True, exist_ok=True)
    cast = []
    for fname, items in SETS:
        src, lab, comps = cut_sheet(src_dir / fname, len(items))
        rgb_alpha_src = src.convert("RGBA")
        for (name, cid), c in zip(items, comps):
            comp = lab == c["label"]
            alpha = Image.fromarray((comp * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
            cut = rgb_alpha_src.copy()
            cut.putalpha(alpha)
            box = (max(c["x0"] - PAD, 0), max(c["y0"] - PAD, 0),
                   min(c["x1"] + PAD + 1, src.width), min(c["y1"] + PAD + 1, src.height))
            cut = cut.crop(box)
            scale = TARGET_SQRT_AREA / np.sqrt(c["area"])
            cut = cut.resize((round(cut.width * scale), round(cut.height * scale)), Image.LANCZOS)
            out = ASSETS / f"{cid}.png"
            cut.save(out)
            cast.append({"id": cid, "name": name, "file": f"assets/{cid}.png", "w": cut.width, "h": cut.height})
            print(f"{cid:14s} {name:10s} {cut.width}x{cut.height}")
    (ROOT / "cast.json").write_text(json.dumps(cast, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"всього персонажів: {len(cast)}")


if __name__ == "__main__":
    main()
