// Покадровий рендер index.html (Three.js у Chromium через Playwright) -> ffmpeg -> MP4.
//
// Використання:
//   node render/render.mjs --out fruits.mp4 [--fps 30] [--w 1920] [--h 1080] [--audio track.mp3]
//   node render/render.mjs --still 0,15,30 --dir frames      # PNG-кадри для перевірки
//
// Змінні середовища:
//   PLAYWRIGHT_MODULE  шлях до ESM-модуля playwright (за замовчуванням — пакет "playwright")
//   THREE_FILE         обов'язково: локальний three.module.js версії 0.169.0; запит до CDN перехоплюється
//
// Петля: 60 с (120 ударів при 120 BPM). Кадри беремо за t = f / fps, f = 0 .. 60*fps - 1,
// тож останній кадр і перший стикуються без шва.

import http from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : 'true']);
  return acc;
}, []));

const W = Number(args.w ?? 1920);
const H = Number(args.h ?? 1080);
const FPS = Number(args.fps ?? 30);
const OUT = args.out ?? 'fruits.mp4';
const THREE_FILE = process.env.THREE_FILE;
if (!THREE_FILE) throw new Error('Задайте THREE_FILE=/шлях/до/three/build/three.module.js (three@0.169.0)');

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript' };
const server = http.createServer(async (req, res) => {
  try {
    const file = path.join(ROOT, decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/+/, '') || 'index.html');
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

const browser = await chromium.launch({
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => console.error('pageerror:', e.message));
page.on('console', (m) => { if (m.type() === 'error') console.error('console:', m.text()); });

await page.route('https://unpkg.com/three@0.169.0/build/three.module.js', (r) =>
  r.fulfill({ path: THREE_FILE, contentType: 'text/javascript', headers: { 'access-control-allow-origin': '*' } }));

await page.goto(`http://127.0.0.1:${port}/index.html?frame&w=${W}&h=${H}`);
await page.waitForFunction(() => window.ready === true, null, { timeout: 60_000 });

const grab = (t) => page.evaluate((time) => {
  window.renderAt(time);
  return document.querySelector('canvas').toDataURL('image/png');
}, t);

if (args.still) {
  const dir = path.resolve(ROOT, args.dir ?? 'frames');
  await mkdir(dir, { recursive: true });
  for (const t of args.still.split(',').map(Number)) {
    const url = await grab(t);
    const file = path.join(dir, `still_${t}.png`);
    await writeFile(file, Buffer.from(url.split(',')[1], 'base64'));
    console.log(file);
  }
} else {
  const loopSec = await page.evaluate(() => window.LOOP_SEC);
  const frames = Math.round(loopSec * FPS);
  const audioArgs = args.audio ? ['-i', path.resolve(args.audio), '-map', '0:v', '-map', '1:a', '-c:a', 'aac', '-b:a', '192k', '-shortest'] : [];
  const ff = spawn('ffmpeg', [
    '-y', '-loglevel', 'error',
    '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
    ...audioArgs,
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
    OUT,
  ], { stdio: ['pipe', 'inherit', 'inherit'] });

  for (let f = 0; f < frames; f++) {
    const url = await grab(f / FPS);
    if (!ff.stdin.write(Buffer.from(url.split(',')[1], 'base64'))) await once(ff.stdin, 'drain');
    if (f % 150 === 0) console.log(`frame ${f}/${frames}`);
  }
  ff.stdin.end();
  const [code] = await once(ff, 'close');
  if (code !== 0) throw new Error(`ffmpeg exited with ${code}`);
  console.log(`done: ${path.resolve(OUT)} (${frames} frames @ ${FPS} fps)`);
}

await browser.close();
server.close();
