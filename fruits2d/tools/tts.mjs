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

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const API = 'https://api.elevenlabs.io/v1';
const MODEL_ID = 'eleven_multilingual_v2';   // підтримує українську
const OUTPUT_FORMAT = 'mp3_44100_128';
const VOICE_SETTINGS = { stability: 0.6, similarity_boost: 0.8, style: 0.2, use_speaker_boost: true };

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

for (const c of cast) {
  const file = path.join(outDir, `${c.id}.mp3`);
  if (existsSync(file) && !args.has('--force')) {
    console.log(`skip ${c.id} (вже є)`);
    continue;
  }
  const url = `${API}/text-to-speech/${voiceId}?output_format=${OUTPUT_FORMAT}`;
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({ text: c.name, model_id: MODEL_ID, voice_settings: VOICE_SETTINGS }),
  });
  if (!res.ok) throw new Error(`${c.id}: HTTP ${res.status} ${await res.text()}`);
  await writeFile(file, Buffer.from(await res.arrayBuffer()));
  console.log(`ok ${c.id} "${c.name}"`);
}
