"""Зводить озвучку назв в одну доріжку з тим самим таймінгом, що й parade.html.

Таймінг береться зі сцени, а не рахується тут: parade.html вимірює видимі межі кожного
фрукта й будує змінний таймлайн (рівно 1 с чорного між видимими фруктами), тож моменти
назв і тривалість експортує render.mjs:

  node render/render.mjs --page fruits2d/parade.html --cues cues.json --still 0 --dir frames

Запуск: python3 fruits2d/tools/mix_audio.py <cues.json> <вихідний_файл.m4a>
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    timeline = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2]).resolve()
    total = timeline["duration"]
    cues = timeline["cues"]

    inputs, filters, labels = [], [], []
    for i, c in enumerate(cues):
        clip = ROOT / "audio" / f"{c['id']}.mp3"
        if not clip.exists():
            raise SystemExit(f"немає озвучки: {clip} (запустіть tts.mjs)")
        ms = round(c["t"] * 1000)
        inputs += ["-i", str(clip)]
        filters.append(f"[{i}:a]adelay={ms}|{ms},aresample=44100[a{i}]")
        labels.append(f"[a{i}]")

    mix = "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad,atrim=0:{total}[out]"
    graph = ";".join(filters + [mix])
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", graph, "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)
    print(f"доріжка: {out} ({total:.2f} с, {len(cues)} назв)")


if __name__ == "__main__":
    main()
