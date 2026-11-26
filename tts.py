"""Narration with Gemini TTS (Interactions API), one WAV per scene."""
import base64, os, subprocess, time, wave
import requests

API = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_STYLE = ("Warm, deep male storyteller voice. Calm and reverent, slightly slow pace, "
                 "like narrating a Bible story for a short film. Speak in standard Indonesian.")


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
    voice = voice or os.environ.get("TTS_VOICE", "Riko")
    style = style or os.environ.get("TTS_STYLE", DEFAULT_STYLE)
    models = [model or os.environ.get("TTS_MODEL", "gemini-3.8-flash-tts"), "gemini-3.1-flash-tts-preview"]
    last = None
    for m in models:
        body = {"model": m,
                "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                                                               "annotations": [{"type": "speech_metadata", "style": style}]}]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": voice}]}}
        for attempt in range(4):
            r = requests.post(API, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json=body, timeout=180)
            if r.status_code == 200:
                data, mime = _extract_audio(r.json())
                raw = out_path + ".raw"
                if data[:4] == b"RIFF":
                    open(out_path, "wb").write(data)
                else:  # headerless 16-bit PCM, 24 kHz mono
                    with wave.open(out_path, "wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(data)
                return out_path
            last = f"{m}: HTTP {r.status_code} {r.text[:400]}"
            if r.status_code in (400, 403, 404):
                break
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"TTS failed: {last}")


def duration(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]))


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
