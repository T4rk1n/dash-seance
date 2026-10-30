"""The Seance: a multiplayer Ouija board on Dash streaming callbacks + shared storage.

Open it in several tabs or browsers. Anyone can ask the spirits a question and
the answer is spelled out on every board at once. Three AI players sit at the
table with you: they react to every answer and ask their own questions when the
room goes quiet.

With OPENROUTER_API_KEY set (in .env), the spirit and the players are LLMs on
OpenRouter and their words stream in token by token. Without it they use
canned lines. See .env.example for the other settings.
"""
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import time

import httpx
from dotenv import load_dotenv
from fastapi import Response

from dash import (
    ClientsideFunction, Dash, Input, Output, Patch, State, callback,
    clientside_callback, dcc, html, no_update, set_props,
)
from dash import LocalSharedStorage

load_dotenv()

app = Dash(
    __name__,
    backend="fastapi",
    shared_storage=LocalSharedStorage,
    update_title=None,
)
server = app.server
store = app.shared_storage

API_KEY = os.environ.get("OPENROUTER_API_KEY")
MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5")
IDLE = float(os.environ.get("SEANCE_IDLE", "25"))
TOPIC = "seance"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
TTS_MODEL = os.environ.get("OPENROUTER_TTS_MODEL", "elevenlabs/eleven-v3")
TTS_VOICE = os.environ.get("OPENROUTER_TTS_VOICE", "Callum")

BOARD_W, BOARD_H = 760, 440
PLANCHETTE = 70
LETTER_PACE = 0.55

PLAYERS = [
    {
        "name": "Madame Zelda",
        "avatar": "\U0001F52E",
        "persona": "a theatrical, overconfident psychic medium who claims to "
        "know every spirit personally and speaks in grand pronouncements",
        "lines": [
            "Ah, I sense a great disturbance. The spirit is old, very old.",
            "Silence, all of you! The veil is thin tonight.",
            "Just as I foretold. The spirits never lie to Zelda.",
        ],
        "questions": [
            "Spirit, do you remember me from the last full moon?",
            "Who among us will you visit tonight?",
        ],
    },
    {
        "name": "Skeptical Steve",
        "avatar": "\U0001F9D0",
        "persona": "a smug skeptic who insists someone is pushing the planchette, "
        "but who gets a little more rattled with every answer",
        "lines": [
            "Okay, who is pushing it? Hands where I can see them.",
            "That is just the ideomotor effect. Probably. Mostly.",
            "I am not scared. Why is it suddenly so cold in here?",
        ],
        "questions": [
            "If you are real, what is in my left pocket?",
            "Prove it. What is my middle name?",
        ],
    },
    {
        "name": "Nervous Nora",
        "avatar": "\U0001F628",
        "persona": "a jumpy, easily terrified guest who was dragged here by "
        "friends, talks fast and wants to leave",
        "lines": [
            "Nope. Nope nope nope. Can we turn the lights on now?",
            "Did anyone else hear that? Please tell me you heard that.",
            "I am sleeping with every light on tonight.",
        ],
        "questions": [
            "Are you... are you friendly? Please say yes.",
            "Is there something in this room with us right now?",
        ],
    },
]

CANNED_ANSWERS = [
    "BEHIND YOU",
    "YES",
    "NO",
    "SOON",
    "DONT LOOK UNDER THE BED",
    "13",
    "THE CELLAR",
    "WE ARE ALWAYS WATCHING",
    "IT IS TOO LATE",
    "ASK AGAIN AT MIDNIGHT",
    "GOODBYE",
]
WHOLE_WORDS = {"YES", "NO", "GOODBYE"}

SPIRIT_PROMPT = (
    "You are a restless spirit speaking through a Ouija board at a Halloween "
    "seance. Answer the question in 1 to 5 words, using only uppercase letters, "
    "digits and spaces. Be cryptic, ominous, sometimes darkly playful. You may "
    "answer exactly YES, NO or GOODBYE. Never explain, never use punctuation."
)


def _arc(chars, y_mid, sag, x0=70, x1=BOARD_W - 70):
    step = (x1 - x0) / (len(chars) - 1)
    spots = {}
    for i, ch in enumerate(chars):
        t = i / (len(chars) - 1) * 2 - 1
        spots[ch] = (x0 + i * step, y_mid + sag * t * t)
    return spots


SPOTS = {}
SPOTS.update(_arc("ABCDEFGHIJKLM", 170, -40))
SPOTS.update(_arc("NOPQRSTUVWXYZ", 250, -40))
SPOTS.update(_arc("1234567890", 320, 0, 160, BOARD_W - 160))
SPOTS.update(
    {"YES": (90, 55), "NO": (BOARD_W - 90, 55), "GOODBYE": (BOARD_W / 2, 395)}
)
REST = (BOARD_W / 2, 95)


def planchette_style(spot, glow=False):
    x, y = spot
    return {
        "left": f"{x - PLANCHETTE / 2}px",
        "top": f"{y - PLANCHETTE / 2}px",
        "boxShadow": "0 0 34px 14px rgba(255,120,40,0.75)" if glow else "none",
    }


# --- OpenRouter -------------------------------------------------------------


async def llm_stream(system, prompt, max_tokens):
    """Yield text deltas from an OpenRouter chat completion."""
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "POST",
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "X-Title": "Dash Seance Demo",
            },
            json={
                "model": MODEL,
                "stream": True,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
        ) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"OpenRouter {resp.status_code}: {await resp.aread()}")
            async for line in resp.aiter_lines():
                # SSE: ": OPENROUTER PROCESSING" keepalives are comments.
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                chunk = json.loads(data)
                if "error" in chunk:
                    raise RuntimeError(chunk["error"].get("message", "stream error"))
                delta = chunk["choices"][0]["delta"].get("content")
                if delta:
                    yield delta


async def spirit_voice(answer):
    """Speak the answer with OpenRouter TTS. Returns the clip's URL, None on failure.

    The clip goes in shared storage and is served by /spirit-voice: a whole mp3
    is too big to send through a streaming callback's frames.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                SPEECH_URL,
                headers={"Authorization": f"Bearer {API_KEY}", "X-Title": "Dash Seance Demo"},
                json={
                    "model": TTS_MODEL,
                    "voice": TTS_VOICE,
                    "response_format": "mp3",
                    # Audio tags steer ElevenLabs v3; other models may read them aloud.
                    "input": f"[slow, sinister whisper] {answer.lower()}",
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text}")
        key = hashlib.sha256(resp.content).hexdigest()[:16]
        store.set(f"voice:{key}", base64.b64encode(resp.content).decode(), ttl=300)
        return f"/spirit-voice/{key}.mp3"
    except Exception as err:  # pylint: disable=broad-exception-caught
        print("spirit lost its voice:", err)
        return None


@server.get("/spirit-voice/{key}.mp3")
def serve_voice(key: str):
    clip = store.get(f"voice:{key}")
    if clip is None:
        return Response(status_code=404)
    return Response(base64.b64decode(clip), media_type="audio/mpeg")


async def canned_stream(text):
    for word in text.split(" "):
        await asyncio.sleep(0.12)
        yield word + " "


def remember(line):
    history = (store.get("history") or [])[-11:]
    store.set("history", history + [line])


def transcript():
    return "\n".join(store.get("history") or []) or "(the seance has just begun)"


# --- The spirit -------------------------------------------------------------


def _canned_answer(question):
    seed = int(hashlib.sha256(question.lower().encode()).hexdigest(), 16)
    return random.Random(seed).choice(CANNED_ANSWERS)


async def channel(question, who):
    """Ask the spirit and spell its answer on every board. False if busy."""
    if store.get("busy"):
        return False
    store.set("busy", who, ttl=60)
    try:
        store.publish(TOPIC, {"t": "question", "who": who, "q": question})
        remember(f"{who} asks: {question}")
        await asyncio.sleep(1.2)

        if API_KEY:
            prompt = f"Seance so far:\n{transcript()}\n\n{who} asks: {question}"
            source = llm_stream(SPIRIT_PROMPT, prompt, 30)
        else:
            source = canned_stream(_canned_answer(question))

        answer = ""
        try:
            async for delta in source:
                for ch in re.sub(r"[^A-Z0-9 ]", "", delta.upper()):
                    if len(answer) >= 40 or (ch == " " and answer.endswith(" ")):
                        continue
                    answer += ch
                    if ch == " ":
                        continue
                    store.publish(TOPIC, {"t": "letter", "spot": SPOTS[ch], "text": answer})
                    await asyncio.sleep(LETTER_PACE)
        except Exception as err:  # pylint: disable=broad-exception-caught
            print("spirit lost:", err)
        answer = answer.strip() or "GOODBYE"
        voice = asyncio.create_task(spirit_voice(answer)) if API_KEY else None
        if answer in WHOLE_WORDS:
            store.publish(TOPIC, {"t": "letter", "spot": SPOTS[answer], "text": answer})
            await asyncio.sleep(1.0)
        await asyncio.sleep(0.6)
        store.publish(TOPIC, {"t": "done", "answer": answer, "voiced": bool(voice)})
        remember(f"The spirit answers: {answer}")
        if voice:
            store.publish(TOPIC, {"t": "voice", "answer": answer, "audio": await voice})
        return True
    finally:
        store.delete("busy")


# --- The AI players ---------------------------------------------------------


async def speak(seat, instruction, fallback):
    player = PLAYERS[seat]
    if API_KEY:
        system = (
            f"You are {player['name']}, {player['persona']}, at a Halloween seance "
            "around a Ouija board with other guests. Stay in character. Reply with "
            "one or two short spoken sentences, no stage directions, no quotes."
        )
        source = llm_stream(system, f"Seance so far:\n{transcript()}\n\n{instruction}", 80)
    else:
        source = canned_stream(fallback)
    text = ""
    try:
        async for delta in source:
            text += delta
            store.publish(TOPIC, {"t": "say", "seat": seat, "text": text})
    except Exception as err:  # pylint: disable=broad-exception-caught
        print(f"{player['name']} lost their voice:", err)
    text = text.strip() or fallback
    store.publish(TOPIC, {"t": "said", "seat": seat, "text": text})
    remember(f"{player['name']}: {text}")
    return text


async def react(answer):
    for seat in random.sample(range(len(PLAYERS)), k=random.choice([1, 2])):
        await asyncio.sleep(random.uniform(0.6, 1.5))
        await speak(
            seat,
            f"The board just spelled: {answer}. React out loud.",
            random.choice(PLAYERS[seat]["lines"]),
        )


async def ask_as_player(seat):
    player = PLAYERS[seat]
    if API_KEY:
        question = await speak(
            seat,
            "The room has gone quiet. Ask the spirit one short question, "
            "addressed to the spirit, ending with a question mark.",
            random.choice(player["questions"]),
        )
    else:
        question = random.choice(player["questions"])
    await channel(question, player["name"])


async def director():
    """Runs the AI players: reacts to answers, fills silences."""
    last_activity = time.monotonic()
    reacting = None

    async def fill_silences():
        nonlocal last_activity
        while True:
            await asyncio.sleep(1)
            quiet = time.monotonic() - last_activity > IDLE
            if quiet and store.get("souls", 0) > 0 and not store.get("busy"):
                last_activity = time.monotonic()
                await ask_as_player(random.randrange(len(PLAYERS)))
                last_activity = time.monotonic()

    asyncio.create_task(fill_silences())
    while True:
        try:
            async with store.subscribe(TOPIC) as seance:
                async for event in seance:
                    last_activity = time.monotonic()
                    if event["t"] == "done" and (reacting is None or reacting.done()):
                        reacting = asyncio.create_task(react(event["answer"]))
        except Exception as err:  # pylint: disable=broad-exception-caught
            print("director resubscribing:", err)
            await asyncio.sleep(1)


_director = None


def ensure_director():
    """Start the AI players on the server's loop when the first tab joins.

    Not at import: `python app.py` re-imports this module in a uvicorn
    subprocess, and a director in each process would make every guest speak twice.
    """
    global _director  # pylint: disable=global-statement
    if _director is None:
        store.set("souls", 0)
        store.set("history", [])
        store.delete("busy")
    if _director is None or _director.done():
        _director = asyncio.get_running_loop().create_task(director())


# --- Layout -----------------------------------------------------------------


def board():
    marks = [
        html.Span(
            label,
            className="mark word" if len(label) > 1 else "mark",
            style={"left": f"{x}px", "top": f"{y}px"},
        )
        for label, (x, y) in SPOTS.items()
    ]
    planchette = html.Div(
        html.Div(className="lens"),
        id="planchette",
        className="planchette",
        style=planchette_style(REST),
    )
    return html.Div(
        marks + [planchette],
        id="board",
        className="board",
        style={"width": f"{BOARD_W}px", "height": f"{BOARD_H}px"},
    )


def seat(i, player):
    return html.Div(
        [
            html.Div(player["avatar"], className="avatar"),
            html.Div(player["name"], className="seat-name"),
            html.Div(id=f"bubble-{i}", className="bubble"),
        ],
        className="seat",
    )


def candles():
    return html.Div([html.Div(html.Div(className="flame"), className="candle")] * 3,
                    className="candles")


app.layout = html.Div(
    [
        html.Button(id="sound", className="sound"),
        html.H1(["The Seance ", html.Span("\U0001F47B", className="ghost")]),
        html.Div(id="souls", className="souls"),
        html.Div("The spirits are listening...", id="message", className="message"),
        html.Div([seat(i, p) for i, p in enumerate(PLAYERS)], className="seats"),
        html.Div([candles(), board(), candles()], className="table"),
        html.Div(
            [
                dcc.Input(id="name", placeholder="Your name", className="name",
                          persistence=True, maxLength=24),
                dcc.Input(id="question", placeholder="Ask the spirits a question...",
                          className="question", n_submit=0, maxLength=120),
                html.Button("Ask", id="ask", className="ask"),
            ],
            className="controls",
        ),
        html.Div(id="log", className="log", children=[]),
        dcc.Store(id="join", data=0),
        dcc.Store(id="sfx"),
    ],
    className="room",
)


def frame(**changes):
    """One yield of the listener: only the named outputs change."""
    order = ["style", "message", "souls", "log", "board"] + [
        f"bubble{i}" for i in range(len(PLAYERS))
    ]
    return tuple(changes.get(name, no_update) for name in order)


def log_line(children, cls):
    log = Patch()
    log.prepend(html.Div(children, className=cls))
    return log


@callback(
    Output("planchette", "style"),
    Output("message", "children"),
    Output("souls", "children"),
    Output("log", "children"),
    Output("board", "className"),
    *[Output(f"bubble-{i}", "children") for i in range(len(PLAYERS))],
    Input("join", "data"),
)
async def sit_at_the_table(_):
    """One per open tab, for as long as the tab stays open."""
    ensure_director()
    souls = store.get("souls", 0) + 1
    store.set("souls", souls)
    seen, speaking, cues = souls, set(), 0

    def sfx(kind, say=None, audio=None):
        # Not an Output: this stream never ends, and Dash holds back callbacks
        # that depend on a running callback's outputs. The nonce makes the
        # store change even when the same sound repeats.
        nonlocal cues
        cues += 1
        set_props("sfx", {"data": {"kind": kind, "n": cues, "say": say, "audio": audio}})

    try:
        async with store.subscribe(TOPIC) as seance:
            store.publish(TOPIC, {"t": "souls", "n": souls})
            async for event in seance:
                kind = event["t"]
                if kind == "souls":
                    n = event["n"]
                    plural = "s" if n != 1 else ""
                    label = f"{n} living soul{plural} and {len(PLAYERS)} AI guests at the table"
                    if n > seen:
                        sfx("chime")
                    yield frame(souls=label)
                    seen = n
                elif kind == "question":
                    sfx("toll")
                    yield frame(
                        style=planchette_style(REST),
                        message="...",
                        board="board",
                        log=log_line([html.B(event["who"]), f' asks "{event["q"]}"'], "asked"),
                    )
                elif kind == "letter":
                    text = event["text"]
                    word = text in WHOLE_WORDS and tuple(event["spot"]) == SPOTS[text]
                    sfx("scrape", say=None if word else text[-1])
                    yield frame(style=planchette_style(event["spot"], glow=True),
                                message=event["text"])
                elif kind == "done":
                    sfx("wail", say=None if event.get("voiced") else event["answer"])
                    yield frame(
                        style=planchette_style(REST),
                        message=event["answer"],
                        board="board shake",
                        log=log_line(["The spirit: ", event["answer"]], "answered"),
                    )
                elif kind == "voice":
                    # Fall back to the browser's voice if OpenRouter TTS failed.
                    audio = event["audio"]
                    sfx("voice", say=None if audio else event["answer"], audio=audio)
                    yield frame()  # set_props only goes out with a yield
                elif kind == "say":
                    if event["seat"] not in speaking:
                        speaking.add(event["seat"])
                        sfx("whisper")
                    yield frame(**{f"bubble{event['seat']}": event["text"]})
                elif kind == "said":
                    speaking.discard(event["seat"])
                    name = PLAYERS[event["seat"]]["name"]
                    yield frame(
                        **{f"bubble{event['seat']}": event["text"]},
                        log=log_line([html.B(name), ": ", event["text"]], "said"),
                    )
    finally:
        # Runs when the tab closes: the stream is cancelled at its await.
        souls = max(store.get("souls", 1) - 1, 0)
        store.set("souls", souls)
        store.publish(TOPIC, {"t": "souls", "n": souls})


clientside_callback(
    ClientsideFunction("seance", "play"),
    Output("sfx", "clear_data"),  # unused
    Input("sfx", "data"),
    prevent_initial_call=True,
)


@callback(
    Output("question", "value"),
    Output("ask", "disabled"),
    Input("ask", "n_clicks"),
    Input("question", "n_submit"),
    State("question", "value"),
    State("name", "value"),
    prevent_initial_call=True,
)
async def ask_the_spirits(_clicks, _submits, question, name):
    question = (question or "").strip()
    if not question or store.get("busy"):
        yield no_update, no_update
        return
    yield "", True
    await channel(question, (name or "").strip() or "A living soul")
    yield no_update, False


app.index_string = app.index_string.replace(
    "</head>",
    """<style>
body { margin: 0; color: #e9d8c0; font-family: Georgia, serif; min-height: 100vh;
       background: radial-gradient(ellipse at 50% 30%, #3a0d05, #12030a 55%, #050103 85%); }
.room { display: flex; flex-direction: column; align-items: center; padding: 20px 16px; }
h1 { font-size: 48px; letter-spacing: 6px; margin: 0; color: #ffe2b8;
     text-shadow: 0 0 12px #ff7a1a, 0 0 32px #ff2e00; animation: flicker 4s infinite; }
.ghost { display: inline-block; animation: float 3s ease-in-out infinite; }
.souls { color: #ffb37a; margin: 4px 0 8px; font-style: italic; min-height: 20px; }
.message { font-size: 32px; letter-spacing: 8px; min-height: 44px; margin-bottom: 10px;
           color: #ffd27a; text-shadow: 0 0 10px #ff8a1a, 0 0 28px #ff3c00; }
.seats { display: flex; gap: 16px; margin-bottom: 14px; }
.seat { width: 250px; background: rgba(30,6,4,0.75); border: 1px solid #6b2410;
        border-radius: 12px; padding: 8px 12px; box-shadow: 0 0 18px rgba(255,80,0,0.15); }
.avatar { font-size: 30px; float: left; margin-right: 8px; }
.seat-name { font-weight: bold; color: #ffb37a; }
.bubble { clear: both; min-height: 44px; font-size: 14px; font-style: italic; color: #f3e1c8;
          margin-top: 6px; }
.table { display: flex; align-items: center; gap: 18px; }
.board { position: relative; border-radius: 40px; border: 6px solid #3b1608;
         background: radial-gradient(ellipse, #d7a565, #8f5528 75%, #4f230c);
         box-shadow: 0 0 70px rgba(255,90,0,0.45), inset 0 0 50px rgba(0,0,0,0.6); }
.board.shake { animation: shake 0.6s; }
.mark { position: absolute; transform: translate(-50%, -50%); font-size: 34px;
        color: #2a1006; font-weight: bold; text-shadow: 1px 1px 0 rgba(255,220,170,0.4); }
.mark.word { font-size: 28px; letter-spacing: 4px; }
.planchette { position: absolute; width: 70px; height: 70px; border-radius: 50% 50% 45% 45%;
              background: rgba(250,228,195,0.88); border: 3px solid #3b1608;
              transition: left 0.45s ease-in-out, top 0.45s ease-in-out, box-shadow 0.3s; }
.lens { width: 24px; height: 24px; border-radius: 50%; margin: 20px auto;
        border: 3px solid #3b1608; background: rgba(255,255,255,0.25); }
.candles { display: flex; flex-direction: column; gap: 60px; }
.candle { position: relative; width: 22px; height: 70px; border-radius: 4px;
          background: linear-gradient(#f4ecd8, #c9b994); }
.flame { position: absolute; left: 3px; top: -30px; width: 16px; height: 28px;
         border-radius: 50% 50% 35% 35%; animation: burn 0.15s infinite alternate;
         background: radial-gradient(ellipse at 50% 70%, #fff6c4, #ffb21a 45%, #ff3c00 80%);
         box-shadow: 0 0 22px 8px rgba(255,120,20,0.55); }
.controls { display: flex; gap: 8px; margin-top: 18px; }
.controls .dash-input-container { height: 52px; font-size: 18px; border-radius: 6px;
                                  color: #f3e1c8; background: #1c0805; border: 1px solid #7a2a10; }
.controls .dash-input-container:focus-within { border-color: #ff6a1a; outline: 1px solid #ff6a1a; }
.controls .dash-input-element { padding: 0 16px; }
.name { width: 150px; } .question { width: 400px; }
.ask { padding: 0 24px; font-size: 18px; border-radius: 6px; cursor: pointer;
       background: #7a1e05; color: #ffe2b8; border: 1px solid #ff6a1a; font-family: inherit; }
.sound { position: fixed; top: 14px; right: 16px; font-size: 24px; cursor: pointer;
         background: rgba(30,6,4,0.75); border: 1px solid #6b2410; border-radius: 50%;
         width: 46px; height: 46px; padding: 0; }
.ask:disabled { opacity: 0.4; cursor: wait; }
.log { margin-top: 16px; width: 640px; max-height: 260px; overflow: hidden; font-size: 15px; }
.log > div { margin-top: 6px; }
.asked { color: #d6b8a0; } .said { color: #c9a98f; font-style: italic; }
.answered { color: #ffcf6a; letter-spacing: 3px; }
@keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
@keyframes flicker { 0%,19%,22%,62%,64%,100% { opacity: 1; } 20%,21%,63% { opacity: 0.45; } }
@keyframes burn { from { transform: scale(1, 1) rotate(-2deg); }
                  to { transform: scale(0.92, 1.08) rotate(2deg); } }
@keyframes shake { 0%,100% { transform: translate(0,0); } 20% { transform: translate(-6px,2px); }
                   40% { transform: translate(5px,-3px); } 60% { transform: translate(-4px,3px); }
                   80% { transform: translate(3px,-2px); } }
</style>
</head>""",
)

if __name__ == "__main__":
    app.run(debug=False)
