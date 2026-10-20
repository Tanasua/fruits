"""Збирає готовий ролик з videos/<назва>: персонажі (cut.py) + озвучка (tts.mjs) -> out/<назва>.mp4.

Кроки:
  1. sequence.json: усі персонажі по черзі; якщо ролик коротший за --min-sec, у кінець
     додаються повтори з початку списку, доки тривалість не перевищить --min-sec.
  2. Таймлайн сцени (studio/player.html): тривалість, момент кожної назви, момент появи в кадрі.
  3. Рендер кадрів паралельними частинами (render/render.mjs) і склейка без перекодування.
  4. Зведення звуку: кожен кліп ставиться так, щоб слово (після паузи з audio/onsets.json)
     почалося в момент приземлення персонажа в центрі.
  5. Перевірка: кількість кадрів, тривалість звуку, синхрон кожної назви, паузи в кліпах;
     out/check_frames.png — кадр кожного персонажа в центрі з підписом, для перевірки очима.
  6. out/timecodes.txt — таймкоди для опису (момент появи кожного персонажа).

Запуск: python3 studio/make_video.py videos/<назва> [--min-sec 480] [--fps 30] [--jobs N]
Результат: videos/<назва>/out/<назва>.mp4 (out/ не комітиться).
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYER = "studio/player.html"
PLAYWRIGHT = "/opt/node22/lib/node_modules/playwright/index.mjs"
SYNC_TOLERANCE = 0.06    # с: допустима різниця між приземленням і початком слова
MIN_LEAD_MS = 250        # кліп має починатися з паузи (див. tts.mjs)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def node_env():
    env = dict(os.environ)
    if "PLAYWRIGHT_MODULE" not in env and Path(PLAYWRIGHT).exists():
        env["PLAYWRIGHT_MODULE"] = PLAYWRIGHT
    return env


def timeline(video: Path, work: Path):
    out = work / "timeline.json"
    rel = video.relative_to(ROOT) if video.is_absolute() else video
    run(["node", "render/render.mjs", "--page", PLAYER, "--query", f"video={rel.as_posix()}",
         "--cues", str(out), "--still", "0", "--dir", str(work)],
        cwd=ROOT, env=node_env(), stdout=subprocess.DEVNULL)
    return json.loads(out.read_text(encoding="utf-8"))


def build_sequence(video: Path, work: Path, min_sec: float):
    ids = [c["id"] for c in json.loads((video / "cast.json").read_text(encoding="utf-8"))]
    seq_file = video / "sequence.json"

    def write(seq):
        seq_file.write_text(json.dumps(seq, ensure_ascii=False) + "\n", encoding="utf-8")

    seq = list(ids)
    write(seq)
    tl = timeline(video, work)
    if tl["duration"] <= min_sec:
        # оцінка за середньою довжиною слота, далі по одному, доки не перевищить min_sec
        avg = tl["duration"] / len(seq)
        extra = max(1, math.ceil((min_sec - tl["duration"]) / avg))
        while True:
            seq = ids + [ids[i % len(ids)] for i in range(extra)]
            write(seq)
            tl = timeline(video, work)
            if tl["duration"] > min_sec:
                break
            extra += 1
    print(f"послідовність: {len(seq)} появ ({len(ids)} персонажів), {fmt(tl['duration'])}")
    return tl


def render(video: Path, work: Path, tl, fps: int, jobs: int):
    total = round(tl["duration"] * fps)
    step = math.ceil(total / jobs)
    parts, procs = [], []
    for i in range(jobs):
        a, b = i * step, min(total, (i + 1) * step)
        if a >= b:
            break
        part = work / f"part{i}.mp4"
        parts.append(part)
        log = open(work / f"part{i}.log", "w")
        procs.append((subprocess.Popen(
            ["node", "render/render.mjs", "--page", PLAYER, "--query", f"video={video.as_posix()}",
             "--out", str(part), "--from", str(a), "--to", str(b), "--fps", str(fps), "--preset", "veryfast"],
            cwd=ROOT, env=node_env(), stdout=log, stderr=subprocess.STDOUT), log))
    print(f"рендер: {total} кадрів у {len(parts)} потоках...", flush=True)
    for p, log in procs:
        p.wait()
        log.close()
        if p.returncode:
            raise SystemExit(f"рендер впав, дивіться {log.name}")
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    return lst, total


def mix(video: Path, work: Path, tl):
    onsets_file = video / "audio" / "onsets.json"
    onsets = json.loads(onsets_file.read_text(encoding="utf-8")) if onsets_file.exists() else {}
    clips = sorted({c["id"] for c in tl["cues"]})
    missing = [i for i in clips if not (video / "audio" / f"{i}.mp3").exists()]
    if missing:
        raise SystemExit(f"немає озвучки: {', '.join(missing)} (studio/tts.mjs або workflow studio-voice)")
    inputs, filters, labels = [], [], []
    for i, c in enumerate(tl["cues"]):
        ms = max(0, round(c["t"] * 1000) - onsets.get(c["id"], 0))
        inputs += ["-i", str(video / "audio" / f"{c['id']}.mp3")]
        filters.append(f"[{i}:a]adelay={ms}|{ms},aresample=44100[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(filters + ["".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,"
                                f"apad,atrim=0:{tl['duration']}[out]"])
    # Сотні входів не влазять у командний рядок — граф з файлу
    (work / "mix.txt").write_text(graph)
    out = work / "voice.m4a"
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(work / "mix.txt"),
         "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)])
    return out, onsets


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-show_entries",
                        "stream=codec_type,duration,nb_read_packets", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return {s["codec_type"]: s for s in json.loads(r.stdout)["streams"]}


def sync_report(mp4: Path, tl):
    r = subprocess.run(["ffmpeg", "-i", str(mp4), "-vn", "-af", "silencedetect=n=-40dB:d=0.3", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    deltas = []
    for c in tl["cues"]:
        near = [s - c["t"] for s in starts if abs(s - c["t"]) < 1.0]
        deltas.append((c, min(near, key=abs) if near else None))
    return deltas


def check_frames(mp4: Path, tl, out: Path):
    """Кадр кожної появи через 1,5 с після приземлення (персонаж у центрі) з підписом."""
    from PIL import Image, ImageDraw, ImageFont
    tmp = out.parent / "frames"
    tmp.mkdir(exist_ok=True)
    cell_w, cell_h, cols = 320, 180, 6
    n = len(tl["cues"])
    sheet = Image.new("RGB", (cols * cell_w, math.ceil(n / cols) * (cell_h + 30)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(FONT, 18)
    except OSError:
        f = ImageFont.load_default()
    for i, c in enumerate(tl["cues"]):
        png = tmp / f"{i:03d}.png"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{c['t'] + 1.5:.3f}", "-i", str(mp4),
             "-frames:v", "1", "-vf", f"scale={cell_w}:{cell_h}", str(png)])
        x, y = (i % cols) * cell_w, (i // cols) * (cell_h + 30)
        sheet.paste(Image.open(png), (x, y))
        d.text((x + 6, y + cell_h + 4), f"{i + 1}. {c['name']}  {fmt(c['t'])}", fill=(255, 255, 255), font=f)
    sheet.save(out)
    shutil.rmtree(tmp)


def fmt(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--min-sec", type=float, default=480, help="мінімальна тривалість, с (за замовчуванням 8 хв)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()

    video = Path(a.video)
    if video.is_absolute():
        video = video.relative_to(ROOT)
    name = video.name
    out_dir = ROOT / video / "out"
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    for f in ("cast.json",):
        if not (ROOT / video / f).exists():
            raise SystemExit(f"немає {video / f} — спершу studio/cut.py")

    tl = build_sequence(ROOT / video, work, a.min_sec)
    voice, onsets = mix(ROOT / video, work, tl)
    parts, frames = render(video, work, tl, a.fps, a.jobs)
    mp4 = out_dir / f"{name}.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(parts), "-i", str(voice),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", str(mp4)])

    # Перевірка
    problems = []
    st = probe(mp4)
    if int(st["video"]["nb_read_packets"]) != frames:
        problems.append(f"кадрів {st['video']['nb_read_packets']}, очікувалось {frames}")
    if abs(float(st["audio"]["duration"]) - tl["duration"]) > 0.1:
        problems.append(f"звук {st['audio']['duration']} с, відео {tl['duration']:.2f} с")
    for c, dlt in sync_report(mp4, tl):
        if dlt is None or abs(dlt) > SYNC_TOLERANCE:
            problems.append(f"синхрон «{c['name']}» на {fmt(c['t'])}: {'слово не знайдено' if dlt is None else f'{dlt * 1000:+.0f} мс'}")
    for cid in sorted({c["id"] for c in tl["cues"]}):
        if onsets.get(cid, 0) < MIN_LEAD_MS:
            problems.append(f"кліп {cid}: пауза на початку {onsets.get(cid, 0)} мс < {MIN_LEAD_MS} — можливий обрізаний початок")
    check_frames(mp4, tl, out_dir / "check_frames.png")

    lines = [f"{fmt(i['visibleStart'])} {i['name'][0].upper()}{i['name'][1:]}" for i in tl["items"]]
    (out_dir / "timecodes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = {"video": str(mp4), "duration": round(tl["duration"], 2), "duration_mmss": fmt(tl["duration"]),
              "appearances": len(tl["cues"]), "characters": len({c["id"] for c in tl["cues"]}),
              "problems": problems}
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(work)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"кадри для перевірки: {out_dir / 'check_frames.png'}\nтаймкоди: {out_dir / 'timecodes.txt'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
