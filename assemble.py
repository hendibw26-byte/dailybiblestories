"""Turns a script + per-scene narration into the final vertical video."""
import json, os, random, subprocess, wave
from multiprocessing import Pool
import numpy as np
import renderer

SR = 44100
LEAD, TAIL, END_HOLD = 0.2, 0.2, 0.5
MAX_TOTAL = float(os.environ.get("MAX_VIDEO_SECONDS", "59.3"))


def _read_mono(path):
    tmp = path + ".24k.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "24000", tmp], check=True)
    with wave.open(tmp) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    os.remove(tmp)
    return a, 24000


def tighten(path, out_wav, max_pause=0.42):
    """Trim edges and shorten long internal pauses. Returns seconds."""
    a, sr = _read_mono(path)
    win = int(0.02 * sr)
    n = max(1, len(a) // win)
    rms = np.sqrt(np.mean(a[:n * win].reshape(n, win) ** 2, axis=1) + 1)
    quiet = 20 * np.log10(rms / 32768) < -38
    idx = np.where(~quiet)[0]
    if len(idx) == 0:
        y = np.zeros(int(0.5 * sr), np.float32)
    else:
        f0, f1 = max(idx[0] - 3, 0), min(idx[-1] + 6, n)
        keep, j, m = [], f0, int(max_pause / 0.02)
        while j < f1:
            if quiet[j]:
                k = j
                while k < f1 and quiet[k]:
                    k += 1
                keep += list(range(j, k)) if k - j <= m else list(range(j, j + m // 2)) + list(range(k - m // 2, k))
                j = k
            else:
                keep.append(j); j += 1
        y = np.concatenate([a[f * win:(f + 1) * win] for f in keep])
        fd = int(0.01 * sr); y[:fd] *= np.linspace(0, 1, fd); y[-fd:] *= np.linspace(1, 0, fd)
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(y.astype(np.int16).tobytes())
    return len(y) / sr


def dur(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]))


def music(path, total, impacts, seed):
    rnd = random.Random(seed)
    t = np.arange(int(SR * total)) / SR
    out = np.zeros((len(t), 2))
    base = 146.83 * (2 ** (rnd.choice([-2, 0, 1, 3]) / 12))
    progs = [[0, -4, 3, -2], [0, 3, -4, -2], [0, -2, -4, -5], [0, 5, 3, -2]]
    prog = rnd.choice(progs)

    def chord(root_semi, minor):
        r = base * 2 ** (root_semi / 12)
        return [r, r * 2 ** ((3 if minor else 4) / 12), r * 2 ** (7 / 12)]

    def note(f, start, length, amp, pan=0.0):
        i0, i1 = int(start * SR), min(len(t), int((start + length) * SR))
        if i1 <= i0:
            return
        tt = t[i0:i1] - start
        env = np.clip(np.minimum(1, tt / 1.6) * np.minimum(1, (length - tt) / 1.8), 0, 1)
        w_ = np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(4 * np.pi * f * tt + 0.3) + 0.12 * np.sin(6 * np.pi * f * tt)
        s = w_ * env * amp * (1 + 0.15 * np.sin(2 * np.pi * 0.25 * tt))
        out[i0:i1, 0] += s * (1 - pan); out[i0:i1, 1] += s * (1 + pan)

    st, k = 0.0, 0
    while st < total:
        semi = prog[k % 4]
        ch = chord(semi, minor=(semi in (0, 5)))
        for j, f in enumerate(ch):
            note(f, st, 5.5, 0.05, (j - 1) * 0.4)
        note(ch[0] / 2, st, 5.5, 0.06)
        note(ch[k % 3] * 2, st + 1.0, 2.8, 0.018, 0.3 * (-1) ** k)
        st += 4.0; k += 1
    out[:, 0] += 0.035 * np.sin(2 * np.pi * base / 2 * t); out[:, 1] += 0.035 * np.sin(2 * np.pi * base / 2 * t + 0.2)
    for dl, g in [(0.113, 0.35), (0.197, 0.28), (0.293, 0.22), (0.41, 0.16)]:
        n = int(dl * SR)
        out[n:, 0] += out[:-n, 1] * g; out[n:, 1] += out[:-n, 0] * g
    nrnd = np.random.default_rng(seed)
    for at, kind in impacts:
        if kind == "whoosh":
            i0, i1 = int(max(0, at - 1.1) * SR), int(at * SR)
            nz = np.convolve(nrnd.standard_normal(i1 - i0), np.ones(30) / 30, "same") * np.linspace(0, 1, i1 - i0) ** 2 * 0.5
            out[i0:i1] += nz[:, None]
        else:
            i0 = int(at * SR); n = int(2.5 * SR); tt = np.arange(n) / SR
            ph = 2 * np.pi * np.cumsum(30 + 60 * np.exp(-tt * 6)) / SR
            s = np.sin(ph) * np.exp(-tt * 2.2) * 0.7
            s += np.convolve(nrnd.standard_normal(n), np.ones(60) / 60, "same") * np.exp(-tt * 9) * 0.5
            e = min(len(t), i0 + n)
            if e > i0:
                out[i0:e] += s[:e - i0, None]
    fi, fo = int(1.0 * SR), int(2.5 * SR)
    out[:fi] *= np.linspace(0, 1, fi)[:, None]; out[-fo:] *= np.linspace(1, 0, fo)[:, None]
    out /= max(np.max(np.abs(out)), 1e-6) / 0.89
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((out * 32767).astype(np.int16).tobytes())


def build(script, voice_paths, out_dir, final_name="final.mp4", seed=1):
    os.makedirs(out_dir, exist_ok=True)
    n = len(script["scenes"])
    # 1. tighten narration
    tight = []
    for i, p in enumerate(voice_paths):
        o = os.path.join(out_dir, f"tight_{i + 1}.wav")
        tighten(p, o); tight.append(o)
    speech = sum(dur(p) for p in tight)
    budget = MAX_TOTAL - n * (LEAD + TAIL) - END_HOLD
    tempo = max(1.0, speech / budget)
    if tempo > 1.2:
        raise ValueError(f"narration too long ({speech:.1f}s for a {budget:.1f}s budget)")
    final_voice = []
    for i, p in enumerate(tight):
        o = os.path.join(out_dir, f"scene_voice_{i + 1}.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", p, "-af", f"atempo={tempo:.4f}", "-ar", str(SR), "-ac", "2", o], check=True)
        final_voice.append(o)
    durs = [dur(p) + LEAD + TAIL for p in final_voice]
    durs[-1] += END_HOLD
    durs = [round(d * renderer.FPS) / renderer.FPS for d in durs]
    total = sum(durs)
    # 2. render scenes in parallel
    clips = [os.path.join(out_dir, f"scene_{i + 1}.mp4") for i in range(n)]
    jobs = [(script, i, durs[i], clips[i]) for i in range(n)]
    with Pool(max(1, min(n, os.cpu_count() or 2))) as pool:
        pool.map(renderer.render_scene, jobs)
    lst = os.path.join(out_dir, "clips.txt")
    open(lst, "w").write("".join(f"file '{os.path.abspath(c)}'\n" for c in clips))
    video_only = os.path.join(out_dir, "video_only.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video_only], check=True)
    # 3. music with sound effects
    impacts, off = [], 0.0
    for i, sc in enumerate(script["scenes"]):
        if sc["action"] == "stone_throw":
            impacts += [(off + 1.1, "whoosh"), (off + 1.1, "boom"), (off + 3.0, "boom")]
        elif sc["action"] == "fall":
            impacts += [(off + 2.0, "boom")]
        elif sc["action"] == "lightning_strike":
            impacts += [(off + 1.0, "boom"), (off + 2.6, "boom")]
        off += durs[i]
    mus = os.path.join(out_dir, "music.wav")
    music(mus, total, impacts, seed)
    # 4. mix: voice on top, music ducked under it
    inputs, filt, off = [], [], 0.0
    for i, p in enumerate(final_voice):
        inputs += ["-i", p]
        ms = int((off + LEAD) * 1000)
        filt.append(f"[{i + 2}:a]aformat=sample_rates={SR}:channel_layouts=stereo,adelay={ms}|{ms}[v{i}]")
        off += durs[i]
    filt.append("".join(f"[v{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,volume=1.6,apad=whole_dur={total:.3f},asplit=2[voice][sc]")
    filt.append("[1:a]volume=0.55[mus]")
    filt.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
    filt.append("[duck][voice]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")
    final = os.path.join(out_dir, final_name)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_only, "-i", mus] + inputs +
                   ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{total:.3f}", "-movflags", "+faststart", final], check=True)
    for f in tight + final_voice + clips + [video_only, mus, lst]:
        try:
            os.remove(f)
        except OSError:
            pass
    return dict(path=final, duration=round(total, 2), tempo=round(tempo, 3), scene_durations=durs)
