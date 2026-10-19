// Озвучка назв персонажів через ElevenLabs: одне слово на фрукт, чітко й привітно.
//
// Потрібні змінні середовища:
//   ELEVENLABS_API_KEY    ключ з правом на text-to-speech
//   ELEVENLABS_VOICE_ID   id голосу (список: node fruits2d/tools/tts.mjs --list-voices)
//
// Запуск:
//   node fruits2d/tools/tts.mjs            # генерує fruits2d/audio/<id>.mp3 для cast.json
//   node fruits2d/tools/tts.mjs --force    # перегенерувати наявні файли
//
// Моделі й параметри задаються тут, щоб їх легко було змінити.
//
// Початок слова: на одному слові модель іноді «з'їдає» перший звук — кліп стартує одразу
// гучно, з першої мілісекунди. Тому даємо контекст (PREVIOUS_TEXT), а кожен кліп перевіряємо
// (потрібен ffmpeg) і за обрізаного початку генеруємо знову з іншим seed.

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const API = 'https://api.elevenlabs.io/v1';
const MODEL_ID = 'eleven_multilingual_v2';   // підтримує українську
const OUTPUT_FORMAT = 'mp3_44100_128';
const VOICE_SETTINGS = { stability: 0.6, similarity_boost: 0.8, style: 0.2, use_speaker_boost: true };
const PREVIOUS_TEXT = 'Подивись, хто до нас прийшов!';
const ATTEMPTS = 5;
// Обрізаний початок: звук вище -40 dBFS починається раніше ніж за 10 мс від початку файлу,
// а перші 10 мс уже гучніші за -30 dB відносно піку (природний вступ наростає тихіше).
const MIN_LEAD_MS = 10;
const MAX_ONSET_DB = -30;

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

// Вступ кліпу: скільки мс до першого звуку вище -40 dBFS і гучність перших 10 мс після нього.
function onset(mp3) {
  const pcm = execFileSync('ffmpeg', ['-v', 'error', '-i', 'pipe:0', '-ac', '1', '-ar', '44100', '-f', 's16le', 'pipe:1'],
    { input: mp3, maxBuffer: 64 << 20 });
  const a = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.length >> 1);
  let peak = 1;
  for (const x of a) peak = Math.max(peak, Math.abs(x));
  const first = Math.max(0, a.findIndex((x) => Math.abs(x) > 328));
  const w = a.subarray(first, first + 441);
  const rms = Math.sqrt(w.reduce((sum, x) => sum + x * x, 0) / Math.max(1, w.length));
  return { leadMs: first / 44.1, db: 20 * Math.log10(Math.max(rms, 1) / peak) };
}

const clipped = (o) => o.leadMs < MIN_LEAD_MS && o.db > MAX_ONSET_DB;

let failed = 0;
for (const c of cast) {
  const file = path.join(outDir, `${c.id}.mp3`);
  if (existsSync(file) && !args.has('--force')) {
    console.log(`skip ${c.id} (вже є)`);
    continue;
  }
  const url = `${API}/text-to-speech/${voiceId}?output_format=${OUTPUT_FORMAT}`;
  let best = null;
  for (let seed = 1; seed <= ATTEMPTS; seed++) {
    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        text: c.name, model_id: MODEL_ID, voice_settings: VOICE_SETTINGS, previous_text: PREVIOUS_TEXT, seed,
      }),
    });
    if (!res.ok) throw new Error(`${c.id}: HTTP ${res.status} ${await res.text()}`);
    const mp3 = Buffer.from(await res.arrayBuffer());
    const o = onset(mp3);
    console.log(`  ${c.id} seed ${seed}: вступ ${o.leadMs.toFixed(1)} мс, ${o.db.toFixed(1)} dB${clipped(o) ? ' — обрізано' : ''}`);
    if (!best || o.leadMs > best.o.leadMs) best = { mp3, o };
    if (!clipped(o)) { best = { mp3, o }; break; }
  }
  await writeFile(file, best.mp3);
  if (clipped(best.o)) failed++;
  console.log(`${clipped(best.o) ? 'WARN' : 'ok'} ${c.id} "${c.name}"`);
}
if (failed) console.log(`::warning::${failed} кліп(ів) з обрізаним початком після ${ATTEMPTS} спроб`);
