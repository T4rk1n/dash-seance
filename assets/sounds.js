// Spooky sound effects, synthesized with the Web Audio API (no audio files).
// Browsers only allow audio after a user gesture, so the context starts on the
// first click or key press. The speaker button in the corner mutes everything.
(function () {
  let ctx = null;
  let master = null;
  let reverb = null;
  let drone = null;
  let enabled = true;
  try {
    enabled = localStorage.getItem("seance-sound") !== "off";
  } catch (e) {}

  function noiseBuffer(seconds) {
    const buf = ctx.createBuffer(1, ctx.sampleRate * seconds, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    return buf;
  }

  function start() {
    if (!ctx) {
      ctx = new AudioContext();
      master = ctx.createGain();
      master.gain.value = 0.7;
      master.connect(ctx.destination);
      // A long, dark reverb: decaying noise as the impulse response.
      reverb = ctx.createConvolver();
      const len = ctx.sampleRate * 4;
      const ir = ctx.createBuffer(2, len, ctx.sampleRate);
      for (let c = 0; c < 2; c++) {
        const d = ir.getChannelData(c);
        for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3);
      }
      reverb.buffer = ir;
      const wet = ctx.createGain();
      wet.gain.value = 0.5;
      reverb.connect(wet).connect(master);
    }
    if (ctx.state !== "running") ctx.resume();
    startDrone();
  }

  function out(node, dry = 1, wet = 1) {
    const d = ctx.createGain();
    d.gain.value = dry;
    node.connect(d).connect(master);
    const w = ctx.createGain();
    w.gain.value = wet;
    node.connect(w).connect(reverb);
  }

  function envelope(peak, attack, release, at = ctx.currentTime) {
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak, at + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, at + attack + release);
    return g;
  }

  // Low, slowly breathing hum that sits under the whole seance.
  function startDrone() {
    if (drone) return;
    drone = ctx.createGain();
    drone.gain.setValueAtTime(0.0001, ctx.currentTime);
    drone.gain.exponentialRampToValueAtTime(0.05, ctx.currentTime + 4);
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 180;
    filter.Q.value = 6;
    const lfo = ctx.createOscillator();
    const depth = ctx.createGain();
    lfo.frequency.value = 0.07;
    depth.gain.value = 90;
    lfo.connect(depth).connect(filter.frequency);
    lfo.start();
    for (const f of [55, 55.4, 82.4]) {
      const osc = ctx.createOscillator();
      osc.type = "sawtooth";
      osc.frequency.value = f;
      osc.connect(filter);
      osc.start();
    }
    filter.connect(drone);
    out(drone, 1, 0.6);
  }

  // A distant church bell: a question has been asked.
  function toll() {
    const t = ctx.currentTime;
    [[0.5, 0.5], [1, 0.4], [2, 0.25], [2.4, 0.15], [3, 0.12], [4.2, 0.08]].forEach(([ratio, amp]) => {
      const osc = ctx.createOscillator();
      osc.frequency.value = 98 * ratio;
      const g = envelope(amp * 0.5, 0.01, 4.5 / ratio + 1, t);
      osc.connect(g);
      out(g, 0.6, 1);
      osc.start(t);
      osc.stop(t + 6);
    });
  }

  // The planchette scraping across the wood, then settling on the letter.
  function scrape() {
    const t = ctx.currentTime;
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer(0.5);
    src.playbackRate.value = 0.8 + Math.random() * 0.4;
    const band = ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.setValueAtTime(500 + Math.random() * 300, t);
    band.frequency.linearRampToValueAtTime(300, t + 0.45);
    band.Q.value = 1.5;
    const g = envelope(0.35, 0.08, 0.37, t);
    src.connect(band).connect(g);
    out(g, 1, 0.2);
    src.start(t);

    const knock = ctx.createOscillator();
    knock.frequency.setValueAtTime(140, t + 0.42);
    knock.frequency.exponentialRampToValueAtTime(60, t + 0.55);
    const kg = envelope(0.5, 0.005, 0.15, t + 0.42);
    knock.connect(kg);
    out(kg, 1, 0.4);
    knock.start(t + 0.42);
    knock.stop(t + 0.7);
  }

  // The spirit has spoken: a ghostly wail over a rumble as the board shakes.
  function wail() {
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    osc.frequency.setValueAtTime(330, t);
    osc.frequency.exponentialRampToValueAtTime(660, t + 0.9);
    osc.frequency.exponentialRampToValueAtTime(260, t + 2.6);
    const vib = ctx.createOscillator();
    const vibDepth = ctx.createGain();
    vib.frequency.value = 5.5;
    vibDepth.gain.value = 14;
    vib.connect(vibDepth).connect(osc.frequency);
    const g = envelope(0.18, 0.6, 2.2, t);
    osc.connect(g);
    out(g, 0.5, 1.2);
    osc.start(t);
    vib.start(t);
    osc.stop(t + 3);
    vib.stop(t + 3);

    const rumble = ctx.createBufferSource();
    rumble.buffer = noiseBuffer(1);
    const low = ctx.createBiquadFilter();
    low.type = "lowpass";
    low.frequency.value = 110;
    const rg = envelope(1.2, 0.05, 0.7, t);
    rumble.connect(low).connect(rg);
    out(rg, 1, 0.3);
    rumble.start(t);
  }

  // A guest starts talking: a breathy whisper.
  function whisper() {
    const t = ctx.currentTime;
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer(0.8);
    const band = ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.value = 2500 + Math.random() * 1500;
    band.Q.value = 3;
    const g = envelope(0.12, 0.2, 0.5, t);
    src.connect(band).connect(g);
    out(g, 0.6, 1);
    src.start(t);
  }

  // Another living soul joins the table.
  function chime() {
    const t = ctx.currentTime;
    [1318.5, 1568, 1975.5].forEach((f, i) => {
      const osc = ctx.createOscillator();
      osc.frequency.value = f;
      const g = envelope(0.06, 0.01, 2.5, t + i * 0.18);
      osc.connect(g);
      out(g, 0.3, 1);
      osc.start(t + i * 0.18);
      osc.stop(t + i * 0.18 + 3);
    });
  }

  // The spirit's answer from OpenRouter TTS, pitched down and drowned in reverb.
  async function spiritVoice(audio) {
    if (!audio) return;
    const clip = await fetch(audio).then((r) => r.arrayBuffer());
    const src = ctx.createBufferSource();
    src.buffer = await ctx.decodeAudioData(clip);
    src.playbackRate.value = 0.85;
    const g = ctx.createGain();
    g.gain.value = 1.4;
    src.connect(g);
    out(g, 1, 0.9);
    src.start();
  }

  const SOUNDS = { toll, scrape, wail, whisper, chime, voice: spiritVoice };

  // The spirit's voice: the browser's speech synthesis, pitched low and slow.
  // Single letters are whispered as the planchette lands; the full answer is
  // intoned once the wail has risen.
  let voice = null;
  function pickVoice() {
    const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
    voice = voices.find((v) => /male|daniel|fred|david|george/i.test(v.name)) || voices[0] || null;
  }
  if ("speechSynthesis" in window) {
    pickVoice();
    speechSynthesis.addEventListener("voiceschanged", pickVoice);
  }

  function speak(text, answer) {
    if (!("speechSynthesis" in window)) return;
    const say = () => {
      const u = new SpeechSynthesisUtterance(text.toLowerCase());
      if (voice) u.voice = voice;
      u.pitch = 0.1;
      u.rate = answer ? 0.55 : 0.8;
      u.volume = answer ? 1 : 0.6;
      speechSynthesis.speak(u);
    };
    if (answer) setTimeout(say, 900);
    else say();
  }

  function render() {
    const button = document.getElementById("sound");
    if (button) {
      button.textContent = enabled ? "\u{1F50A}" : "\u{1F507}";
      button.title = enabled ? "Mute the spirits" : "Unmute the spirits";
    }
  }

  function toggle() {
    enabled = !enabled;
    try {
      localStorage.setItem("seance-sound", enabled ? "on" : "off");
    } catch (e) {}
    if (enabled) start();
    else {
      if (ctx) ctx.suspend();
      if ("speechSynthesis" in window) speechSynthesis.cancel();
    }
    render();
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("#sound")) toggle();
    else if (enabled) start();
  });
  document.addEventListener("keydown", () => enabled && start());
  // Dash renders the button after this script runs; label it once it appears.
  const waiting = new MutationObserver(() => {
    if (document.getElementById("sound")) {
      render();
      waiting.disconnect();
    }
  });
  waiting.observe(document.documentElement, { childList: true, subtree: true });

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    seance: {
      play(sfx) {
        if (enabled && ctx && ctx.state === "running" && sfx && SOUNDS[sfx.kind]) {
          SOUNDS[sfx.kind](sfx.audio);
          if (sfx.say) speak(sfx.say, sfx.kind !== "scrape");
        }
        return window.dash_clientside.no_update;
      },
    },
  });
})();
