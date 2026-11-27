"""Daily script writer: asks Gemini for one Bible-story short as structured JSON."""
import json, os, re, time
import requests

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SKIES = ["dawn", "golden", "day", "dusk", "night", "storm", "fire", "heaven"]
TERRAINS = ["hills", "valley", "desert", "sea", "river", "mountain", "city", "garden", "interior"]
ELEMENTS = ["sun", "moon", "rays", "palm_trees", "trees", "tents", "city_walls", "temple", "crowd", "army",
            "sheep", "camels", "boat", "ark", "stone_tablets", "cross", "tomb", "manger", "well",
            "fire", "burning_bush", "dove"]
ROLES = ["hero", "villain", "king", "woman", "child", "elder", "soldier", "angel", "jesus"]
SIZES = ["small", "medium", "large", "giant"]
POSITIONS = ["far_left", "left", "center_left", "center", "center_right", "right", "far_right"]
PROPS = ["staff", "spear", "shield", "crown", "sword", "scroll", "lamp", "helmet"]
POSES = ["stand", "walk", "raise_arm", "pray", "kneel", "arms_open"]
ACTIONS = ["none", "walk_in", "stone_throw", "fall", "lightning_strike", "fire_burst", "light_shine",
           "rain_falls", "waves_part", "dove_fly"]
CAMERAS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "push_up"]
TAGS = ["<pause>", "<slow>", "<serious>", "<warm>", "<hopeful>", "<tense>", "<determined>", "<reverent>", "<whispers>"]

SYSTEM = f"""Kamu penulis naskah video pendek (YouTube Shorts/TikTok/Reels) berbahasa Indonesia untuk kanal
"Kisah Alkitab dalam 1 Menit". Tujuan: mengedukasi orang tentang kisah dan ayat Alkitab dengan setia pada teks.

ATURAN ISI
- Setia pada kisah Alkitab. Jangan menambah peristiwa, nama, atau dialog yang tidak ada di Alkitab.
- Jangan mengutip Terjemahan Baru (LAI) kata per kata. Pakai parafrase dengan bahasamu sendiri dan cantumkan referensi ayat.
  Kalau berupa kutipan langsung, tulis "parafrase <kitab pasal:ayat>" di verse_ref.
- Netral antar-denominasi, hormat, tidak menghakimi, cocok untuk semua umur. Kekerasan diceritakan halus, tanpa detail sadis.
- Tuhan tidak digambarkan sebagai sosok manusia: pakai action "light_shine" atau elemen "rays".
- Bahasa Indonesia yang baku tapi hangat. Angka ditulis dengan huruf di narasi (contoh: "empat puluh hari").

ATURAN BENTUK
- Tepat 8 adegan. Adegan 1 = pembuka judul (narasi: judul + "Kisah dari ..."; on_screen_text kosong).
  Adegan 8 = pelajaran singkat yang relevan untuk hidup sehari-hari, diakhiri "Ikuti untuk kisah berikutnya."
- Total narasi 95-115 kata (maksimal 115) supaya video di bawah 60 detik. Tiap adegan 9-16 kata.
- Narasi boleh memakai tag suara bahasa Inggris dalam kurung sudut, maksimal 2 per adegan, dari daftar ini saja: {", ".join(TAGS)}.
  Tag tidak boleh berdempetan dan tidak boleh di awal adegan kecuali adegan 1. Contoh: "Goliat, <serious> raksasa dari Gat, menantang mereka."
- on_screen_text: versi ringkas narasi tanpa tag, maksimal 20 kata.
- highlight_words: 0-2 kata penting dari on_screen_text untuk diwarnai emas.

KOSAKATA VISUAL (wajib pakai nilai persis dari daftar)
- sky: {SKIES}
- terrain: {TERRAINS}
- elements (0-3): {ELEMENTS}
- characters (0-3 per adegan): role {ROLES}; size {SIZES}; position {POSITIONS}; props {PROPS}; pose {POSES}
  Tokoh utama pakai role "hero" (atau "jesus" untuk Yesus, "king" untuk raja). Lawan pakai "villain".
- action: {ACTIONS}  ("stone_throw" dan "fall" butuh satu hero dan satu villain di adegan yang sama;
  "waves_part" hanya untuk terrain "sea")
- camera: {CAMERAS}
Pilih visual yang sesuai suasana tiap adegan dan variasikan antar-adegan.

KELUARKAN HANYA JSON dengan bentuk:
{{"title": str (singkat, boleh pakai "&"), "reference": str, "youtube_title": str (<=90 karakter),
 "caption": str (1-2 kalimat untuk caption media sosial), "hashtags": [5-8 hashtag],
 "cta": "Ikuti untuk kisah Alkitab setiap hari",
 "scenes": [{{"narration": str, "on_screen_text": str, "verse_ref": str, "highlight_words": [str],
   "sky": str, "terrain": str, "elements": [str], "characters": [{{"role": str, "size": str, "position": str, "props": [str], "pose": str}}],
   "action": str, "camera": str}}]}}
"""


GROQ_API = "https://api.groq.com/openai/v1/chat/completions"


def _gemini_once(m, prompt, api_key, temperature):
    r = requests.post(API.format(model=m), headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                      json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"responseMimeType": "application/json", "temperature": temperature}},
                      timeout=180)
    if r.status_code == 200:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return r.status_code, "".join(p.get("text", "") for p in parts if not p.get("thought")), ""
    return r.status_code, None, r.text[:300]


def _groq_once(m, prompt, key, temperature):
    r = requests.post(GROQ_API, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": m, "temperature": temperature, "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": SYSTEM},
                                         {"role": "user", "content": prompt + "\n\nKeluarkan hanya JSON."}]},
                      timeout=180)
    if r.status_code == 200:
        return r.status_code, r.json()["choices"][0]["message"]["content"], ""
    return r.status_code, None, r.text[:300]


def call_gemini(prompt, api_key, model=None, temperature=0.8):
    """Writes the script. Gemini first; if it is busy or unavailable, Groq (free) as backup."""
    first = model or os.environ.get("GEMINI_TEXT_MODEL", "gemini-3.8-flash")
    extra = os.environ.get("GEMINI_TEXT_FALLBACKS", "gemini-flash-latest")
    providers = [("Gemini", m, _gemini_once, api_key)
                 for m in [first] + [x.strip() for x in extra.split(",") if x.strip() and x.strip() != first]]
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        for m in os.environ.get("GROQ_MODELS", "llama-3.3-70b-versatile,openai/gpt-oss-120b").split(","):
            if m.strip():
                providers.append(("Groq", m.strip(), _groq_once, groq_key))
    dead, last = set(), None
    for rnd in range(4):                       # up to 4 rounds over all providers
        for name, m, fn, key in providers:
            if m in dead:
                continue
            for attempt in range(2):
                try:
                    code, text, err = fn(m, prompt, key, temperature)
                except requests.RequestException as e:
                    code, text, err = 0, None, str(e)[:300]
                if code == 200 and text:
                    print(f"   naskah ditulis oleh {name} ({m})", flush=True)
                    return text
                last = f"{name} {m}: HTTP {code} {err}"
                print(f"   ⚠️ {name} {m}: HTTP {code}, mencoba berikutnya…", flush=True)
                if code in (400, 401, 403, 404):
                    dead.add(m)                # not usable with this key: skip from now on
                    break
                time.sleep(10)
        if len(dead) == len(providers):
            break
        time.sleep(45)                          # everyone busy: wait, then another round
    raise RuntimeError(f"Script generation failed (Gemini{' + Groq' if groq_key else ''}): {last}")


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).replace("  ", " ").strip()


def word_count(script):
    return sum(len(strip_tags(s["narration"]).split()) for s in script["scenes"])


def _pick(v, allowed, default):
    return v if v in allowed else default


def normalize(script):
    sc = script.get("scenes", [])
    if len(sc) < 6:
        raise ValueError("too few scenes")
    sc = sc[:9]
    out = []
    for i, s in enumerate(sc):
        narr = s.get("narration", "").strip()
        narr = re.sub(r"<(?!/?(?:%s)>)[^>]*>" % "|".join(t.strip("<>") for t in TAGS), "", narr)  # drop unknown tags
        chars = []
        for c in (s.get("characters") or [])[:3]:
            chars.append(dict(role=_pick(c.get("role"), ROLES, "hero"), size=_pick(c.get("size"), SIZES, "medium"),
                              position=_pick(c.get("position"), POSITIONS, "center"),
                              props=[p for p in c.get("props", []) if p in PROPS][:3], pose=_pick(c.get("pose"), POSES, "stand")))
        terrain = _pick(s.get("terrain"), TERRAINS, "hills")
        action = _pick(s.get("action"), ACTIONS, "none")
        roles = {c["role"] for c in chars}
        if action in ("stone_throw", "fall") and not ("villain" in roles and roles & {"hero", "king", "jesus"}):
            action = "none"
        if action == "waves_part" and terrain != "sea":
            action = "none"
        out.append(dict(narration=narr, on_screen_text=strip_tags(s.get("on_screen_text", "")) if i > 0 else "",
                        verse_ref=s.get("verse_ref", ""), highlight_words=(s.get("highlight_words") or [])[:2],
                        sky=_pick(s.get("sky"), SKIES, "golden"), terrain=terrain,
                        elements=[e for e in s.get("elements", []) if e in ELEMENTS][:3],
                        characters=chars, action=action, camera=_pick(s.get("camera"), CAMERAS, "zoom_in")))
    script["scenes"] = out
    script.setdefault("cta", "Ikuti untuk kisah Alkitab setiap hari")
    script["hashtags"] = [h if h.startswith("#") else "#" + h for h in script.get("hashtags", [])][:8]
    for k in ("title", "reference", "youtube_title", "caption"):
        script[k] = str(script.get(k, "")).strip()
    if not script["title"] or not script["reference"]:
        raise ValueError("missing title/reference")
    return script


def generate(story, api_key, history_titles=(), max_words=115):
    prompt = (f"Buat naskah untuk kisah: {story['title']} ({story['reference']}).\n"
              f"Fokus pesan: {story.get('focus', 'pilih satu pelajaran utama yang paling jelas dari kisah ini')}.\n"
              f"Kisah yang sudah pernah dibuat (jangan diulang): {', '.join(history_titles[-15:]) or '-'}")
    for attempt in range(3):
        raw = call_gemini(prompt, api_key)
        try:
            data = normalize(json.loads(raw))
        except Exception as e:
            prompt += f"\n\nPercobaan sebelumnya tidak valid ({e}). Keluarkan JSON yang valid sesuai format."
            continue
        wc = word_count(data)
        if wc <= max_words + 10:
            return data
        prompt += f"\n\nNaskah sebelumnya {wc} kata, terlalu panjang. Persingkat jadi maksimal {max_words} kata total."
    raise RuntimeError("Could not get a valid script after 3 attempts")
