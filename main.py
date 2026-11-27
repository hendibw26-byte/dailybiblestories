"""Daily pipeline: pick story -> write script -> narrate (Riko) -> animate -> publish.

Usage:
  python main.py                 # full daily run (needs GEMINI_API_KEY, META_* and YT_* secrets)
  python main.py --dry-run       # everything except publishing
  python main.py --check         # verify Gemini key + voice, Meta and YouTube connections
  python main.py --mock          # offline test: sample script + silent/sample audio, no API calls
"""
import argparse, datetime as dt, json, os, sys, traceback
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state", "progress.json")
STORIES = os.path.join(ROOT, "data", "stories.json")


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"next_index": 0, "history": []}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=2)


def silent_voice(text, path, wps=2.4):
    import subprocess, re
    secs = max(2.0, len(re.sub(r"<[^>]+>", "", text).split()) / wps)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={secs:.2f}",
                    "-af", "volume=0.6", "-ar", "24000", "-ac", "1", path], check=True)
    return path


def check():
    import tts, publish
    ok = True
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("❌ GEMINI_API_KEY belum diisi"); ok = False
    else:
        try:
            v = tts.list_voices(key, search=os.environ.get("TTS_VOICE", "Riko"))
            names = [x.get("name") or x.get("displayName") or x.get("voice") for x in v.get("voices", [])]
            print("✅ Gemini API key valid. Suara yang cocok:", names[:10])
        except Exception as e:
            print("⚠️  Tidak bisa membaca daftar suara:", e)
        try:
            tts.synth("Halo, ini tes suara untuk kanal Kisah Alkitab.", "/tmp/check_voice.wav", key)
            print("✅ TTS berhasil dengan suara", os.environ.get("TTS_VOICE", "Riko"))
        except Exception as e:
            print("❌ TTS gagal:", e); ok = False
    ok &= publish.check_connections()
    return ok


def run(dry_run=False, mock=False, story_index=None, mock_audio_dir=None):
    import script_gen, assemble
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d")
    out_dir = os.path.join(ROOT, "output", today)
    os.makedirs(out_dir, exist_ok=True)
    st = load_state()
    stories = json.load(open(STORIES))
    idx = st["next_index"] if story_index is None else int(story_index)
    story = stories[idx % len(stories)]
    print(f"📖 Kisah #{idx + 1}: {story['title']} ({story['reference']})")
    if not dry_run and not mock and any(h.get("date") == today for h in st["history"]):
        print(f"✅ Hari ini ({today}) sudah ada video yang diposting. Dilewati supaya tidak dobel.")
        return None
    key = os.environ.get("GEMINI_API_KEY", "")
    result = None
    for attempt in range(3):
        pre = os.path.join(ROOT, "data", "scripts", f"{idx + 1:03d}.json")
        if attempt == 0 and not mock and os.path.exists(pre):
            script = script_gen.normalize(json.load(open(pre)))
            print("📄 Memakai naskah siap pakai:", os.path.relpath(pre, ROOT))
        elif mock:
            script = script_gen.normalize(json.load(open(os.environ.get("MOCK_SCRIPT", os.path.join(ROOT, "samples", "sample_script.json")))))
        else:
            script = script_gen.generate(story, key, [h["title"] for h in st["history"]],
                                         max_words=105 if attempt else 115)
        json.dump(script, open(os.path.join(out_dir, "script.json"), "w"), ensure_ascii=False, indent=2)
        print("📝 Naskah:", script["title"], "-", script_gen.word_count(script), "kata")
        if mock:
            voices = []
            for i, sc in enumerate(script["scenes"]):
                p = os.path.join(out_dir, f"voice_{i + 1}.wav")
                src = os.path.join(mock_audio_dir, f"s{i + 1}.wav") if mock_audio_dir else None
                voices.append(src if src and os.path.exists(src) else silent_voice(sc["narration"], p))
        else:
            import tts
            voices = tts.narrate_all(script, out_dir, key)
        print("🎙️  Narasi selesai")
        slug = f"kisah-{today}"
        try:
            result = assemble.build(script, voices, out_dir, final_name=f"{slug}.mp4", seed=idx)
            break
        except ValueError as e:
            if mock or os.path.exists(pre):
                # never throw away a hand-written script (and never spend TTS quota twice on it)
                raise RuntimeError(f"{e}. Naskah {os.path.basename(pre)} perlu dipersingkat, atau naikkan variable MAX_TEMPO.")
            print("⚠️ ", e, "- naskah akan dipersingkat")
    if result is None:
        raise RuntimeError("Video stays longer than 60 seconds after 3 attempts")
    print(f"🎬 Video: {result['path']} ({result['duration']} detik, tempo x{result['tempo']})")
    report = dict(date=today, story=story, script_title=script["title"], video=result)
    if not dry_run and not mock:
        import publish
        report["publish"] = publish.publish_all(script, result["path"], f"kisah-{today}")
        print("📤 Hasil posting:", json.dumps(report["publish"], ensure_ascii=False, indent=2))
        publish.post_report(publish.build_report(script, report["publish"], today, result["duration"]))
        st["next_index"] = idx + 1
        st["history"].append({"date": today, "title": script["title"], "reference": script["reference"]})
        st["history"] = st["history"][-120:]
        save_state(st)
        errors = [p for p, r in report["publish"].items() if isinstance(r, str) and r.startswith("ERROR")]
        if errors:
            json.dump(report, open(os.path.join(out_dir, "report.json"), "w"), ensure_ascii=False, indent=2)
            raise RuntimeError(f"Posting gagal di: {', '.join(errors)}")
    json.dump(report, open(os.path.join(out_dir, "report.json"), "w"), ensure_ascii=False, indent=2)
    return report


def post_existing(src_dir):
    """Publish a video that an earlier run (e.g. a dry-run) already made: no new script, no TTS quota used."""
    import glob, publish, script_gen
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d")
    st = load_state()
    if any(h.get("date") == today for h in st["history"]):
        print(f"✅ Hari ini ({today}) sudah ada video yang diposting. Dilewati.")
        return None
    vids = sorted(glob.glob(os.path.join(src_dir, "**", "*.mp4"), recursive=True))
    scripts = sorted(glob.glob(os.path.join(src_dir, "**", "script.json"), recursive=True))
    if not vids or not scripts:
        raise RuntimeError(f"Video atau script.json tidak ditemukan di hasil run tersebut ({src_dir}).")
    video, script = vids[-1], script_gen.normalize(json.load(open(scripts[-1])))
    print(f"🎬 Memakai video yang sudah jadi: {os.path.basename(video)} ({script['title']})")
    res = publish.publish_all(script, video, f"kisah-{today}")
    print("📤 Hasil posting:", json.dumps(res, ensure_ascii=False, indent=2))
    import subprocess
    d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video]))
    publish.post_report(publish.build_report(script, res, today, d))
    st["next_index"] = st["next_index"] + 1
    st["history"].append({"date": today, "title": script["title"], "reference": script["reference"]})
    st["history"] = st["history"][-120:]
    save_state(st)
    errors = [p for p, r in res.items() if isinstance(r, str) and r.startswith("ERROR")]
    if errors:
        raise RuntimeError(f"Posting gagal di: {', '.join(errors)}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--story-index", default=None)
    ap.add_argument("--mock-audio-dir", default=None)
    ap.add_argument("--from-dir", default=None)
    a = ap.parse_args()
    if a.from_dir:
        post_existing(a.from_dir)
        sys.exit(0)
    if a.check:
        sys.exit(0 if check() else 1)
    dry = a.dry_run or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    si = a.story_index if a.story_index not in (None, "") else None
    run(dry_run=dry, mock=a.mock, story_index=si, mock_audio_dir=a.mock_audio_dir)
