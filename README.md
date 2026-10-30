# The Seance

A multiplayer Ouija board for Halloween, built on Dash 4.5.0
streaming callbacks and shared storage.

Everyone who opens the app sits at the same table. Ask the spirit a question and its
answer is spelled out, letter by letter, on every open board at once. Three AI guests
(Madame Zelda, Skeptical Steve and Nervous Nora) react to each answer and ask their own
questions when the room goes quiet.

## Run it

```bash
cp .env.example .env   # add your OpenRouter key
uv run app.py
```

Open http://127.0.0.1:8050 in two or three tabs or browsers.

Without `uv`: `python -m venv .venv && .venv/bin/pip install "dash[fastapi,shared_storage]>=4.5.0" httpx python-dotenv && .venv/bin/python app.py`.

## How it works

- Asking a question runs a streaming callback. It streams the spirit's answer from
  OpenRouter and publishes each letter to a shared-storage topic.
- Every open tab runs a streaming callback that never ends: it subscribes to that topic
  and yields each letter, chat message and soul count to the page.
- The AI guests run in a background task that listens on the same topic.
- Sounds (a tolling bell, the planchette scraping, a ghostly wail, whispers) are
  synthesized in the browser with the Web Audio API in `assets/sounds.js`. The browser's
  speech synthesis whispers each letter, and the whole answer is spoken by an OpenRouter
  TTS model (ElevenLabs v3 by default), or by the browser without a key. The clip is kept
  in shared storage and served from `/spirit-voice/<id>.mp3`, since a whole mp3 is too
  big for a streaming frame. The speaker button in the corner mutes everything.
- Shared storage also holds the soul count and the recent conversation, so every LLM call
  sees what was said.

Runs as a single process (the soul count resets at startup).
