# STT runner protocol

`stt_runner.py` is a separate long-lived process that turns a voice message into text. The bot itself stays
stdlib-only: it sends the path of an audio file to the runner and gets the text back.

## Transport

Same as the SDK runner (`docs/sdk-protocol.md`): Unix socket (default `~/.local/state/cc-tgbot/stt.sock`, mode
`0600`, directory `0700`), newline-delimited JSON, one request per connection.

- Request: `{"id": "1", "method": "stt.transcribe", "params": {...}}`
- Reply: `{"id": "1", "result": {...}}` or `{"id": "1", "error": {"code": "...", "message": "..."}}`

Error codes: `bad_request` (no such file, not audio, longer than `CC_STT_MAX_SECONDS`, bad `lang`), `unavailable`
(no engine could do it; the message lists `engine:reason` pairs), `busy` (more than 4 requests waiting),
`too_big`, `internal`.

## Methods

`stt.info` -> `{"version": "1", "engines": [{"name": "local", "ready": true, "model": "small"}, {"name": "groq",
"ready": false}], "loaded": false}`. `ready` for a cloud engine means that its key file holds a key; for `local`
that faster-whisper is importable. `loaded` tells whether the local model is in memory.

`stt.transcribe` `{"path": "/abs/file.ogg", "lang": "ru"}` -> `{"text", "lang", "seconds", "took", "engine"}`.
`lang` is an optional two or three letter hint (`ru`, `en`); without it the language is detected. `seconds` is the
audio duration, `took` the processing time. `lang` in the reply is the hint, or the detected language for the
local engine (cloud replies may carry `null`). The runner never deletes the input file: that is the caller's job.

Accepted inputs: anything `ffmpeg` can decode (OGG/Opus, webm/opus, mp4/AAC, mp3, wav).

## Behaviour

- Engines are tried in the order of `CC_STT_ENGINES`; an error or `CC_STT_TIMEOUT` seconds without an answer
  moves on to the next one. Requests are processed one at a time.
- The local model is loaded on the first request and unloaded after `CC_STT_IDLE` minutes without requests.
  Decoding uses the system `ffmpeg` (the PyAV decoder bundled with faster-whisper is not used).
- Cloud engines upload an ogg/opus 16 kHz re-encoding (temporary file, deleted afterwards): Groq
  `POST https://api.groq.com/openai/v1/audio/transcriptions`, model `whisper-large-v3-turbo`; OpenAI
  `POST https://api.openai.com/v1/audio/transcriptions`, model `gpt-4o-mini-transcribe`. Keys come from the files
  named by `CC_STT_GROQ_KEY_FILE` / `CC_STT_OPENAI_KEY_FILE` and are read on every call.
- Privacy: the log holds only engine names, durations, times and error codes. Keys, recognised text and file
  paths are never logged, and keys are never returned.

## Models and the first start

Models come from the Hugging Face cache (`~/.cache/huggingface/hub`). `large-v3-turbo` (what `CC_STT_MODEL=auto`
picks on a machine with at least 8 cores and 8 GB RAM) is about 1.6 GB: if it is not cached, the first request
downloads it, which can exceed `CC_STT_TIMEOUT`. Warm it up beforehand, or set `HF_HUB_OFFLINE=1` to forbid
downloads (then a missing model is an engine error and the next engine is tried). On a heavily loaded machine local
recognition can be slow: raise `CC_STT_TIMEOUT` or put a cloud engine after `local`.

## Use by the bot

The bot (`CC_STT=auto|off`, `CC_STT_SOCKET`, `CC_STT_LANG`) considers the service ready when `stt.info` lists at least
one engine with `ready: true` (the answer is remembered for 20 seconds). It sends the path of a temporary file and
the language hint (`CC_STT_LANG`, else the bot language) with a 600 second timeout, and deletes the file itself.
Telegram `voice` and `audio` messages and `POST /api/voice` of the mini-app both go through `stt.transcribe`;
only durations, engine names and times are logged, never the text.
