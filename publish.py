"""Free publishing through the official APIs.

- Facebook Reels  : Meta Graph API (/{page-id}/video_reels + rupload)
- Instagram Reels : Meta Graph API (resumable upload from file)
- YouTube Shorts  : YouTube Data API v3 (resumable upload, OAuth refresh token)
- TikTok          : manual. The video + ready-to-paste caption are put in a GitHub Release.
"""
import datetime as dt, json, os, subprocess, time
import requests

GRAPH_VER = os.environ.get("META_GRAPH_VERSION", "v27.0")
GRAPH = f"https://graph.facebook.com/{GRAPH_VER}"
RUPLOAD = "https://rupload.facebook.com"


def env(name, required=True):
    v = os.environ.get(name, "").strip()
    if required and not v:
        raise RuntimeError(f"Secret {name} belum diisi")
    return v


def _check(r, what):
    if r.status_code >= 400:
        # never echo tokens; Graph error bodies don't contain them
        raise RuntimeError(f"{what}: HTTP {r.status_code} {r.text[:400]}")
    return r.json() if r.text else {}


# ------------------------------------------------------------------ text
def build_texts(script):
    caption = script["caption"].strip()
    tags = " ".join(script["hashtags"])
    social = f"{caption}\n\n📖 {script['reference']}\n\n{tags}"
    return dict(
        facebook=social,
        instagram=social,
        tiktok=f"{caption} 📖 {script['reference']} {tags}",
        youtube_title=(script.get("youtube_title") or script["title"])[:95],
        youtube_desc=f"{caption}\n\n📖 {script['reference']}\n\n{tags} #Shorts",
    )


# ------------------------------------------------------------------ timing
def wait_until_post_time():
    """Sleep until POST_TIME_WIB today (default 20:00). Returns immediately if it has passed."""
    if os.environ.get("POST_NOW", "").lower() in ("1", "true", "yes"):
        return
    hh, mm = os.environ.get("POST_TIME_WIB", "20:00").split(":")
    now = dt.datetime.now(dt.timezone.utc)
    target = now.replace(hour=(int(hh) - 7) % 24, minute=int(mm), second=0, microsecond=0)
    secs = (target - now).total_seconds()
    if 0 < secs < 3 * 3600:
        print(f"⏳ Menunggu {int(secs // 60)} menit sampai {hh}:{mm} WIB…", flush=True)
        time.sleep(secs)


# ------------------------------------------------------------------ Facebook
def facebook_reel(video, text, title):
    token, page = env("META_PAGE_TOKEN"), env("META_PAGE_ID")
    st = _check(requests.post(f"{GRAPH}/{page}/video_reels", json={"upload_phase": "start", "access_token": token}, timeout=60), "FB start")
    vid = st["video_id"]
    size = os.path.getsize(video)
    with open(video, "rb") as f:
        _check(requests.post(f"{RUPLOAD}/video-upload/{GRAPH_VER}/{vid}",
                             headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                                      "Content-Type": "application/octet-stream"}, data=f, timeout=600), "FB upload")
    res = _check(requests.post(f"{GRAPH}/{page}/video_reels",
                               data={"access_token": token, "video_id": vid, "upload_phase": "finish",
                                     "video_state": "PUBLISHED", "title": title, "description": text,
                                     "is_ai_generated": "true"}, timeout=120), "FB publish")
    return {"video_id": vid, **res}


# ------------------------------------------------------------------ Instagram
def instagram_user_id():
    v = env("META_IG_USER_ID", required=False)
    if v:
        return v
    js = _check(requests.get(f"{GRAPH}/{env('META_PAGE_ID')}", params={"fields": "instagram_business_account",
                                                                      "access_token": env("META_PAGE_TOKEN")}, timeout=60), "IG lookup")
    if "instagram_business_account" not in js:
        raise RuntimeError("Facebook Page belum terhubung ke akun Instagram Professional")
    return js["instagram_business_account"]["id"]


def instagram_reel(video, text):
    token = env("META_PAGE_TOKEN")
    ig = instagram_user_id()
    auth = {"Authorization": f"OAuth {token}"}
    c = _check(requests.post(f"{GRAPH}/{ig}/media", params={"upload_type": "resumable"},
                             data={"media_type": "REELS", "caption": text, "access_token": token}, timeout=60), "IG container")
    cid = c["id"]
    up_url = c.get("uri") or f"{RUPLOAD}/ig-api-upload/{GRAPH_VER}/{cid}"   # use the upload address Meta returns
    size = os.path.getsize(video)
    data = open(video, "rb").read()
    _check(requests.post(up_url, headers={**auth, "offset": "0", "file_size": str(size),
                                          "Content-Type": "application/octet-stream"}, data=data, timeout=600), "IG upload")
    for _ in range(60):  # up to ~10 minutes of processing
        s = _check(requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=60), "IG status")
        code = s.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"IG processing {code}: {s.get('status')}")
        time.sleep(10)
    res = _check(requests.post(f"{GRAPH}/{ig}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=120), "IG publish")
    return {"container": cid, **res}


# ------------------------------------------------------------------ YouTube
def youtube_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=60)
    return _check(r, "YouTube token")["access_token"]


def youtube_short(video, title, desc, tags):
    tok = youtube_token()
    meta = {"snippet": {"title": title, "description": desc, "tags": [t.lstrip("#") for t in tags][:10],
                        "categoryId": os.environ.get("YOUTUBE_CATEGORY", "27"), "defaultLanguage": "id"},
            "status": {"privacyStatus": os.environ.get("YOUTUBE_PRIVACY", "public"), "selfDeclaredMadeForKids": False}}
    size = os.path.getsize(video)
    init = requests.post("https://www.googleapis.com/upload/youtube/v3/videos",
                         params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                         data=json.dumps(meta), timeout=60)
    if init.status_code >= 400:
        raise RuntimeError(f"YouTube init: HTTP {init.status_code} {init.text[:400]}")
    with open(video, "rb") as f:
        up = requests.put(init.headers["Location"], headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"},
                          data=f, timeout=900)
    js = _check(up, "YouTube upload")
    return {"id": js.get("id"), "url": f"https://youtube.com/shorts/{js.get('id')}",
            "privacy": js.get("status", {}).get("privacyStatus")}


# ------------------------------------------------------------------ TikTok (manual) via GitHub Release
def github_release(video, tag, title, tiktok_text):
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return "skipped: not running on GitHub"
    notes = ("**Untuk TikTok (upload manual)**\n\n1. Download video di bawah.\n2. Upload di aplikasi TikTok.\n"
             "3. Copy caption ini:\n\n```\n" + tiktok_text + "\n```\n")
    subprocess.run(["gh", "release", "create", tag, video, "--repo", repo, "--title", title,
                    "--notes", notes, "--latest=false"], check=True)
    return f"https://github.com/{repo}/releases/tag/{tag}"


# ------------------------------------------------------------------ all
def publish_all(script, video, tag):
    t = build_texts(script)
    platforms = [p.strip() for p in os.environ.get("PLATFORMS", "facebook,instagram,youtube").split(",") if p.strip()]
    results = {}
    if os.environ.get("SKIP_RELEASE"):
        pass
    else:
        try:
            results["tiktok_manual"] = github_release(video, tag, f"{script['title']} ({tag})", t["tiktok"])
        except Exception as e:
            results["tiktok_manual"] = f"ERROR: {e}"
    wait_until_post_time()
    jobs = {"facebook": lambda: facebook_reel(video, t["facebook"], script["title"]),
            "instagram": lambda: instagram_reel(video, t["instagram"]),
            "youtube": lambda: youtube_short(video, t["youtube_title"], t["youtube_desc"], script["hashtags"])}
    for p in platforms:
        if p not in jobs:
            continue
        try:
            results[p] = jobs[p]()
            print(f"✅ {p}: berhasil", flush=True)
        except Exception as e:  # one platform failing must not block the others
            results[p] = f"ERROR: {e}"
            print(f"❌ {p}: {e}", flush=True)
    return results


REPORT_TITLE = "📅 Laporan Harian Posting"


def _permalink_ig(media_id):
    try:
        js = requests.get(f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": env("META_PAGE_TOKEN")}, timeout=60).json()
        return js.get("permalink")
    except Exception:
        return None


def build_report(script, results, date, video_duration=None):
    def cell(p):
        r = results.get(p)
        if r is None:
            return "—", "tidak dijadwalkan"
        if isinstance(r, str):
            return ("❌", r[:180].replace("|", "/")) if r.startswith("ERROR") else ("➖", r[:120])
        if p == "facebook":
            return "✅", f"[Lihat Reel](https://www.facebook.com/reel/{r.get('video_id')})"
        if p == "instagram":
            link = _permalink_ig(r.get("id")) if r.get("id") else None
            return "✅", f"[Lihat Reel]({link})" if link else "terposting"
        if p == "youtube":
            extra = " (privat: ubah ke Publik di YouTube Studio)" if r.get("privacy") == "private" else ""
            return "✅", f"[Lihat Short]({r.get('url')}){extra}"
        return "✅", ""
    rows = []
    for p, name in (("facebook", "Facebook"), ("instagram", "Instagram"), ("youtube", "YouTube Shorts")):
        s, d = cell(p)
        rows.append(f"| {name} | {s} | {d} |")
    tk = results.get("tiktok_manual")
    rows.append(f"| TikTok (manual) | 📥 | [Download video & caption]({tk}) |" if isinstance(tk, str) and tk.startswith("http")
                else "| TikTok (manual) | ➖ | release tidak dibuat |")
    run = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run = f"\n\n[Detail proses]({os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']})"
    dur = f" · {video_duration:.0f} detik" if video_duration else ""
    return (f"### {date} · {script['title']} ({script['reference']}){dur}\n\n"
            "| Platform | Status | Link |\n|---|---|---|\n" + "\n".join(rows) + run)


def post_report(body):
    """Adds the day's report as a comment on one pinned issue: a daily log the user is notified about."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print(body)
        return None
    try:
        out = subprocess.run(["gh", "issue", "list", "--repo", repo, "--state", "open", "--search", REPORT_TITLE + " in:title",
                              "--json", "number,title"], check=True, capture_output=True, text=True).stdout
        found = [i for i in json.loads(out or "[]") if i["title"] == REPORT_TITLE]
        if found:
            num = str(found[0]["number"])
        else:
            url = subprocess.run(["gh", "issue", "create", "--repo", repo, "--title", REPORT_TITLE, "--body",
                                  "Setiap hari setelah posting, laporan hasilnya ditambahkan sebagai komentar di sini.\n"
                                  "Klik **Subscribe** (kanan) supaya mendapat email setiap ada laporan baru."],
                                 check=True, capture_output=True, text=True).stdout.strip()
            num = url.rstrip("/").split("/")[-1]
            subprocess.run(["gh", "issue", "pin", num, "--repo", repo], capture_output=True)
        subprocess.run(["gh", "issue", "comment", num, "--repo", repo, "--body", body], check=True)
        return num
    except Exception as e:
        print("⚠️  Gagal menulis laporan harian:", e)
        return None


def check_connections():
    ok = True
    try:
        js = _check(requests.get(f"{GRAPH}/{env('META_PAGE_ID')}", params={"fields": "name,instagram_business_account{username}",
                                                                          "access_token": env("META_PAGE_TOKEN")}, timeout=60), "Meta")
        print(f"✅ Facebook Page: {js.get('name')}")
        iga = js.get("instagram_business_account")
        print(f"✅ Instagram: @{iga.get('username')}" if iga else "❌ Instagram belum terhubung ke Page ini")
        ok &= bool(iga)
    except Exception as e:
        print("❌ Meta:", e); ok = False
    try:
        tok = youtube_token()
        r = _check(requests.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                                headers={"Authorization": f"Bearer {tok}"}, timeout=60), "YouTube channel")
        items = r.get("items", [])
        print(f"✅ YouTube: {items[0]['snippet']['title']}" if items else "❌ YouTube: tidak ada channel di akun ini")
        ok &= bool(items)
    except Exception as e:
        print("❌ YouTube:", e); ok = False
    return ok
