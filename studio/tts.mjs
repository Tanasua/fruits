// Озвучка назв персонажів ролика через ElevenLabs: одне слово на персонажа, привітно й весело.
//
// Змінні середовища:
//   ELEVENLABS_API_KEY    ключ з правом на text-to-speech
//   ELEVENLABS_VOICE_ID   id голосу
// Без них скрипт виходить з кодом 3 — тоді озвучку робить GitHub Actions (.github/workflows/voice.yml),
// де ці значення лежать у секретах репозиторію.
//
// Запуск:
//   node studio/tts.mjs videos/<назва>                    # кліпи, яких ще немає
//   node studio/tts.mjs videos/<назва> --force            # перегенерувати всі
//   node studio/tts.mjs videos/<назва> --force --only=banan --seed=100 --attempts=10
//
// Пише videos/<назва>/audio/<id>.mp3 і audio/onsets.json (пауза на початку кожного кліпу, мс).
//
// Початок слова: коли слово стоїть на самому початку генерації, модель «з'їдає» перший звук.
// Тому просимо паузу перед словом (<break>, підтримує multilingual v2; v3/v4 — ні), даємо
// веселий контекст (previous_text) і перевіряємо кожен кліп (потрібен ffmpeg): якщо пауза
// не вийшла, генеруємо знову з іншим seed. mix.py зсуває кліп на цю паузу, тож слово
// лягає точно на приземлення персонажа.
//
// Налаштування ролика (необов'язково) — videos/<назва>/video.json:
//   { "context": "фраза перед словом", "speed": 0.8 }

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const API = 'https://api.elevenlabs.io/v1';
const MODEL_ID = 'eleven_multilingual_v2';   // підтримує українську й <break>
const OUTPUT_FORMAT = 'mp3_44100_128';
const DEFAULT_CONTEXT = 'Ура! Привіт-привіт, малята! Дивіться, хто до нас весело стрибає!';
const DEFAULT_SPEED = 0.8;                   // 1 — звичайна швидкість, менше — повільніше
const PAUSE = '0.5s';
const MIN_LEAD_MS = 250;                     // пауза вийшла, якщо до першого звуку щонайменше стільки

const positional = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const flags = new Set(process.argv.slice(2));
const opt = (name) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split('=')[1];
const ATTEMPTS = Number(opt('attempts') || 5);
const SEED = Number(opt('seed') || 1);
const ONLY = (opt('only') || '').split(',').filter(Boolean);

const dir = positional[0];
if (!dir) throw new Error('вкажіть папку ролика: node studio/tts.mjs videos/<назва>');

const key = process.env.ELEVENLABS_API_KEY;
const voiceId = process.env.ELEVENLABS_VOICE_ID;
if (!key || !voiceId) {
  console.error('немає ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID — озвучте через GitHub Actions (див. CLAUDE.md)');
  process.exit(3);
}

const cfgFile = path.join(dir, 'video.json');
const cfg = existsSync(cfgFile) ? JSON.parse(await readFile(cfgFile, 'utf8')) : {};
const context = cfg.context ?? DEFAULT_CONTEXT;
const voiceSettings = {
  stability: 0.35, similarity_boost: 0.8, style: 0.6, use_speaker_boost: true, speed: cfg.speed ?? DEFAULT_SPEED,
};
const text = (c) => `<break time="${PAUSE}" />${c.name[0].toUpperCase()}${c.name.slice(1)}!`;

const cast = JSON.parse(await readFile(path.join(dir, 'cast.json'), 'utf8'));
const outDir = path.join(dir, 'audio');
await mkdir(outDir, { recursive: true });
const onsetsFile = path.join(outDir, 'onsets.json');
const onsets = existsSync(onsetsFile) ? JSON.parse(await readFile(onsetsFile, 'utf8')) : {};

// Скільки мс до першого звуку вище -40 dBFS.
function leadMs(mp3) {
  const pcm = execFileSync('ffmpeg', ['-v', 'error', '-i', 'pipe:0', '-ac', '1', '-ar', '44100', '-f', 's16le', 'pipe:1'],
    { input: mp3, maxBuffer: 64 << 20 });
  const a = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.length >> 1);
  return Math.max(0, a.findIndex((x) => Math.abs(x) > 328)) / 44.1;
}

const headers = { 'xi-api-key': key, 'content-type': 'application/json' };
const url = `${API}/text-to-speech/${voiceId}?output_format=${OUTPUT_FORMAT}`;
let failed = 0;
for (const c of cast) {
  if (ONLY.length && !ONLY.includes(c.id)) continue;
  const file = path.join(outDir, `${c.id}.mp3`);
  if (existsSync(file) && !flags.has('--force')) {
    console.log(`skip ${c.id} (вже є)`);
    continue;
  }
  let best = null;
  for (let seed = SEED; seed < SEED + ATTEMPTS; seed++) {
    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        text: text(c), model_id: MODEL_ID, voice_settings: voiceSettings, previous_text: context, seed,
      }),
    });
    if (!res.ok) throw new Error(`${c.id}: HTTP ${res.status} ${await res.text()}`);
    const mp3 = Buffer.from(await res.arrayBuffer());
    const lead = leadMs(mp3);
    console.log(`  ${c.id} seed ${seed}: пауза ${lead.toFixed(0)} мс${lead < MIN_LEAD_MS ? ' — замала' : ''}`);
    if (!best || lead > best.lead) best = { mp3, lead };
    if (lead >= MIN_LEAD_MS) break;
  }
  await writeFile(file, best.mp3);
  onsets[c.id] = Math.round(best.lead);
  if (best.lead < MIN_LEAD_MS) failed++;
  console.log(`${best.lead < MIN_LEAD_MS ? 'WARN' : 'ok'} ${c.id} "${c.name}"`);
}
await writeFile(onsetsFile, JSON.stringify(onsets, null, 2) + '\n');
if (failed) console.log(`::warning::${failed} кліп(ів) без паузи на початку після ${ATTEMPTS} спроб`);
