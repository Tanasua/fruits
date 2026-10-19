"""Зводить озвучку назв в одну доріжку з тим самим таймінгом, що й parade.html.

Кожна назва звучить у момент приземлення фрукта в центрі: i * (SLOT + GAP) + T_ENTER.
Константи мають збігатися з parade.html (6 с вхід, 3 с пауза, 6 с вихід, 1 с чорного між фруктами).

Запуск: python3 fruits2d/tools/mix_audio.py <вихідний_файл.m4a>
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T_ENTER = 6.0
SLOT = 15.0
GAP = 1.0


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1]).resolve()
    cast = json.loads((ROOT / "cast.json").read_text(encoding="utf-8"))
    total = len(cast) * (SLOT + GAP) - GAP

    inputs, filters, labels = [], [], []
    for i, c in enumerate(cast):
        clip = ROOT / "audio" / f"{c['id']}.mp3"
        if not clip.exists():
            raise SystemExit(f"немає озвучки: {clip} (запустіть tts.mjs)")
        ms = round((i * (SLOT + GAP) + T_ENTER) * 1000)
        inputs += ["-i", str(clip)]
        filters.append(f"[{i}:a]adelay={ms}|{ms},aresample=44100[a{i}]")
        labels.append(f"[a{i}]")

    mix = "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad,atrim=0:{total}[out]"
    graph = ";".join(filters + [mix])
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", graph, "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)
    print(f"доріжка: {out} ({total} с, {len(cast)} назв)")


if __name__ == "__main__":
    main()
