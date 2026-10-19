// Озвучка назв персонажів через ElevenLabs: одне слово на фрукт, чітко й привітно.
//
// Потрібні змінні середовища:
//   ELEVENLABS_API_KEY    ключ з правом на text-to-speech
//   ELEVENLABS_VOICE_ID   id голосу (список: node fruits2d/tools/tts.mjs --list-voices)
//
// Запуск:
//   node fruits2d/tools/tts.mjs            # генерує fruits2d/audio/<id>.mp3 для cast.json
//   node fruits2d/tools/tts.mjs --force    # перегенерувати наявні файли
//   node fruits2d/tools/tts.mjs --force --only=pear --attempts=20   # один кліп, більше спроб
//   node fruits2d/tools/tts.mjs --force --only=banana --seed=100     # інший дубль
//
// Моделі й параметри задаються тут, щоб їх легко було змінити.
//
// Початок слова: коли слово стоїть на самому початку генерації, модель «з'їдає» перший звук.
// Тому просимо паузу перед словом (<break>, підтримує multilingual v2; v3/v4 — ні), даємо
// веселий контекст (PREVIOUS_TEXT) і перевіряємо кожен кліп (потрібен ffmpeg): якщо пауза
// не вийшла, генеруємо знову з іншим seed. Тривалість паузи кожного кліпу пишемо
// в audio/onsets.json — mix_audio.py зсуває кліп, щоб слово лягало на приземлення.

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const API = 'https://api.elevenlabs.io/v1';
const MODEL_ID = 'eleven_multilingual_v2';   // підтримує українську
const OUTPUT_FORMAT = 'mp3_44100_128';
// Нижча стабільність і вищий style — жвавіша, емоційніша подача; speed < 1 — повільніше (1 — звичайна)
const VOICE_SETTINGS = { stability: 0.35, similarity_boost: 0.8, style: 0.6, use_speaker_boost: true, speed: 0.8 };
const PREVIOUS_TEXT = 'Ура! Привіт-привіт, малята! Дивіться, хто до нас весело стрибає!';
const PAUSE = '0.5s';
// Репліка з cast.json (line), а без неї — сама назва з окликом
const text = (c) => `<break time="${PAUSE}" />${c.line ?? `${c.name[0].toUpperCase()}${c.name.slice(1)}!`}`;
const opt = (name) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split('=')[1];
const ATTEMPTS = Number(opt('attempts') ?? 5);
const SEED = Number(opt('seed') ?? 1);   // інший seed — інший дубль тієї самої репліки
const ONLY = opt('only')?.split(',').filter(Boolean);
// Пауза вийшла, якщо до першого звуку вище -40 dBFS щонайменше стільки мс
const MIN_LEAD_MS = 250;

const key = process.env.ELEVENLABS_API_KEY;
if (!key) throw new Error('Задайте ELEVENLABS_API_KEY');

const headers = { 'xi-api-key': key, 'content-type': 'application/json' };
const args = new Set(process.argv.slice(2));

if (args.has('--list-voices')) {
  const res = await fetch(`${API}/voices`, { headers });
  if (!res.ok) throw new Error(`voices: HTTP ${res.status} ${await res.text()}`);
  const { voices } = await res.json();
  for (const v of voices) console.log(`${v.voice_id}\t${v.name}\t${v.labels?.language ?? ''}`);
  process.exit(0);
}

const voiceId = process.env.ELEVENLABS_VOICE_ID;
if (!voiceId) throw new Error('Задайте ELEVENLABS_VOICE_ID (список голосів: --list-voices)');

const cast = JSON.parse(await readFile(path.join(ROOT, 'cast.json'), 'utf8'));
const outDir = path.join(ROOT, 'audio');
await mkdir(outDir, { recursive: true });

// Вступ кліпу: скільки мс до першого звуку вище -40 dBFS.
function onset(mp3) {
  const pcm = execFileSync('ffmpeg', ['-v', 'error', '-i', 'pipe:0', '-ac', '1', '-ar', '44100', '-f', 's16le', 'pipe:1'],
    { input: mp3, maxBuffer: 64 << 20 });
  const a = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.length >> 1);
  return { leadMs: Math.max(0, a.findIndex((x) => Math.abs(x) > 328)) / 44.1 };
}

const clipped = (o) => o.leadMs < MIN_LEAD_MS;
const onsetsFile = path.join(outDir, 'onsets.json');
const onsets = existsSync(onsetsFile) ? JSON.parse(await readFile(onsetsFile, 'utf8')) : {};

let failed = 0;
for (const c of cast) {
  if (ONLY?.length && !ONLY.includes(c.id)) continue;
  const file = path.join(outDir, `${c.id}.mp3`);
  if (existsSync(file) && !args.has('--force')) {
    console.log(`skip ${c.id} (вже є)`);
    continue;
  }
  const url = `${API}/text-to-speech/${voiceId}?output_format=${OUTPUT_FORMAT}`;
  let best = null;
  for (let seed = SEED; seed < SEED + ATTEMPTS; seed++) {
    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        text: text(c), model_id: MODEL_ID, voice_settings: VOICE_SETTINGS, previous_text: PREVIOUS_TEXT, seed,
      }),
    });
    if (!res.ok) throw new Error(`${c.id}: HTTP ${res.status} ${await res.text()}`);
    const mp3 = Buffer.from(await res.arrayBuffer());
    const o = onset(mp3);
    console.log(`  ${c.id} seed ${seed}: пауза ${o.leadMs.toFixed(0)} мс${clipped(o) ? ' — замала' : ''}`);
    if (!best || o.leadMs > best.o.leadMs) best = { mp3, o };
    if (!clipped(o)) { best = { mp3, o }; break; }
  }
  await writeFile(file, best.mp3);
  onsets[c.id] = Math.round(best.o.leadMs);
  if (clipped(best.o)) failed++;
  console.log(`${clipped(best.o) ? 'WARN' : 'ok'} ${c.id} "${c.name}"`);
}
await writeFile(onsetsFile, JSON.stringify(onsets, null, 2) + '\n');
if (failed) console.log(`::warning::${failed} кліп(ів) без паузи на початку після ${ATTEMPTS} спроб`);
