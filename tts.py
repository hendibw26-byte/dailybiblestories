"""Narration with Gemini TTS (Interactions API), one WAV per scene."""
import base64, os, subprocess, time, wave
import requests

API = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_STYLE = ("Warm, deep male storyteller voice. Calm and reverent, at a natural, moderate pace (not slow), "
                 "like narrating a Bible story for a short film. Speak in standard Indonesian, "
                 "the way an Indonesian Christian pastor reads the Bible in church. "
                 "Pronounce every Indonesian word with plain Indonesian vowels, never with an Arabic accent.")

PRON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pronunciation.json")


def fix_pronunciation(text):
    """Respell words only in what Riko reads (on-screen text is untouched). Keeps suffixes: Allahmu -> Alahmu."""
    import json, re
    try:
        table = json.load(open(PRON_FILE)).get("ganti", {})
    except Exception:
        table = {}
    for word, spoken in table.items():
        if word and spoken and word != spoken:
            text = re.sub(r"\b" + re.escape(word), spoken, text)
    return text


def _extract_audio(js):
    for step in reversed(js.get("steps", [])):
        for c in step.get("content", []) or []:
            if c.get("type") == "audio" and c.get("data"):
                return base64.b64decode(c["data"]), c.get("mime_type", "audio/wav")
    # fallback: generateContent-style response
    for cand in js.get("candidates", []):
        for p in cand.get("content", {}).get("parts", []):
            if "inlineData" in p:
                return base64.b64decode(p["inlineData"]["data"]), p["inlineData"].get("mimeType", "audio/L16")
    raise RuntimeError("No audio in TTS response: " + str(js)[:400])


def synth(text, out_path, api_key, voice=None, style=None, model=None):
    text = fix_pronunciation(text)
    voice = voice or os.environ.get("TTS_VOICE", "Riko")
    style = style or os.environ.get("TTS_STYLE", DEFAULT_STYLE)
    models = [model or os.environ.get("TTS_MODEL", "gemini-3.8-flash-tts"), "gemini-3.1-flash-tts-preview"]
    last = None
    for m in models:
        inline_style = False
        for attempt in range(6):
            if inline_style:   # models without speech_metadata: plain text only (a style prefix could be read aloud)
                content = [{"type": "text", "text": text}]
            else:
                content = [{"type": "text", "text": text, "annotations": [{"type": "speech_metadata", "style": style}]}]
            body = {"model": m, "input": [{"type": "user_input", "content": content}],
                    "response_format": {"type": "audio"},
                    "generation_config": {"speech_config": [{"voice": voice}]}}
            r = requests.post(API, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json=body, timeout=180)
            if r.status_code == 200:
                data, mime = _extract_audio(r.json())
                if data[:4] == b"RIFF":
                    open(out_path, "wb").write(data)
                else:  # headerless 16-bit PCM, 24 kHz mono
                    with wave.open(out_path, "wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(data)
                return out_path
            last = f"{m}: HTTP {r.status_code} {r.text[:400]}"
            if r.status_code == 400 and "speech metadata" in r.text.lower() and not inline_style:
                inline_style = True
                continue
            quota = r.status_code == 429 and ("quota" in r.text.lower() or "exceeded" in r.text.lower())
            print(f"   ⚠️ TTS {m}: HTTP {r.status_code}{' (kuota habis)' if quota else ''}", flush=True)
            if r.status_code in (400, 403, 404) or quota:
                break
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"TTS failed: {last}")


def duration(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]))


SCENE_BREAK = " <long pause> "


def _split_on_pauses(wav_path, weights, out_dir):
    """Cut one narration into len(weights) parts. Breaks are chosen among the pauses so that they land
    near where the word counts say each scene should end (TTS pause lengths alone are unreliable)."""
    import numpy as np
    n = len(weights)
    tmp = wav_path + ".mono.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, "-ac", "1", "-ar", "24000", tmp], check=True)
    with wave.open(tmp) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    os.remove(tmp)
    sr, win = 24000, 480
    k = len(a) // win
    rms = np.sqrt(np.mean(a[:k * win].reshape(k, win) ** 2, axis=1) + 1)
    quiet = 20 * np.log10(rms / 32768) < -38
    loud = np.where(~quiet)[0]
    if len(loud) == 0:
        return None
    s0, s1 = loud[0], loud[-1]
    cands, j = [], s0
    while j < s1:
        if quiet[j]:
            e = j
            while e < s1 and quiet[e]:
                e += 1
            if (e - j) * win / sr >= 0.3:
                cands.append(((j + e) / 2, (e - j) * win / sr))   # (centre frame, seconds)
            j = e
        else:
            j += 1
    m = len(cands)
    if m < n - 1:
        return None
    tot = float(sum(weights))
    span = s1 - s0
    exp, acc = [], 0.0
    for wgt in weights[:-1]:
        acc += wgt
        exp.append(s0 + span * acc / tot)
    fps = sr / win
    def cost(ci, bi):
        c, d = cands[ci]
        return ((c - exp[bi]) / fps / 3.0) ** 2 - 4.0 * min(d, 2.5)
    INF = 1e18
    dp = [[INF] * m for _ in range(n - 1)]
    back = [[-1] * m for _ in range(n - 1)]
    for ci in range(m):
        dp[0][ci] = cost(ci, 0)
    for bi in range(1, n - 1):
        best, arg = INF, -1
        for ci in range(m):
            if ci > 0 and dp[bi - 1][ci - 1] < best:
                best, arg = dp[bi - 1][ci - 1], ci - 1
            if arg >= 0:
                dp[bi][ci] = best + cost(ci, bi)
                back[bi][ci] = arg
    ci = min(range(m), key=lambda c: dp[n - 2][c])
    if dp[n - 2][ci] >= INF:
        return None
    chosen = [ci]
    for bi in range(n - 2, 0, -1):
        ci = back[bi][ci]
        chosen.append(ci)
    chosen.reverse()
    bounds = [0] + [int(cands[c][0]) * win for c in chosen] + [len(a)]
    paths = []
    for i in range(n):
        seg = a[bounds[i]:bounds[i + 1]]
        if len(seg) < sr * 0.8:
            return None
        p = os.path.join(out_dir, f"voice_{i + 1}.wav")
        with wave.open(p, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(seg.astype(np.int16).tobytes())
        paths.append(p)
    return paths


def narrate_all(script, out_dir, api_key):
    """One TTS request for the whole video (saves free quota); per-scene requests only if splitting fails."""
    scenes = script["scenes"]
    full = os.path.join(out_dir, "voice_full.wav")
    text = SCENE_BREAK.join(s["narration"].strip() for s in scenes)
    synth(text, full, api_key)
    import re
    weights = [max(1, len(re.sub(r"<[^>]+>", "", s["narration"]).split())) for s in scenes]
    parts = _split_on_pauses(full, weights, out_dir)
    if parts:
        print("   🎙️ 1 permintaan TTS, dipotong per adegan", flush=True)
        return parts
    print("   ⚠️ Pemotongan otomatis gagal, membuat suara per adegan", flush=True)
    return narrate_scenes(script, out_dir, api_key)


def narrate_scenes(script, out_dir, api_key):
    paths = []
    for i, sc in enumerate(script["scenes"]):
        p = os.path.join(out_dir, f"voice_{i + 1}.wav")
        synth(sc["narration"], p, api_key)
        paths.append(p)
        time.sleep(1.5)
    return paths


def list_voices(api_key, search=None):
    params = {"page_size": 200}
    if search:
        params["search"] = search
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/voices", headers={"x-goog-api-key": api_key},
                     params=params, timeout=60)
    r.raise_for_status()
    return r.json()
