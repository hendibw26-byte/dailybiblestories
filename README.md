# Kisah Alkitab dalam 1 Menit: Automation Harian (gratis)

Setiap hari paket ini otomatis:
1. Memilih kisah berikutnya dari `data/stories.json` (117 kisah, Kejadian sampai Wahyu).
2. Menulis naskah 8 adegan dengan Gemini (parafrase, lengkap dengan referensi ayat).
3. Membuat dubbing dengan suara **Riko** (Gemini TTS).
4. Menggambar animasi siluet sinematik, lengkap dengan teks dan musik (maksimal 60 detik, format 9:16).
5. Tepat jam **20.00 WIB**, posting ke **Facebook Reels, Instagram Reels, dan YouTube Shorts** lewat API resmi (gratis).
6. Untuk **TikTok**, video dan caption-nya ditaruh di halaman *Releases* GitHub. Kamu tinggal download lalu upload manual.

Semuanya berjalan di GitHub Actions. Tidak ada layanan berbayar.

---

## A. Buat repository
1. Ekstrak zip. Tampilkan file tersembunyi supaya folder `.github` terlihat:
   - **Windows:** di File Explorer, menu View → Show → Hidden items
   - **Mac:** di Finder, tekan `Cmd + Shift + .`
2. Di github.com klik **+ → New repository**. Beri nama `kisah-alkitab` dan pilih **Private** (lebih aman, log tidak terlihat orang lain). Jangan centang *Add a README*. Klik **Create**.
3. Klik **uploading an existing file**. Drag **semua isi** folder `bible_shorts` (bukan folder-nya), termasuk `.github`, lalu klik **Commit changes**.
4. Cek: di halaman utama repo harus ada `.github/workflows/daily.yml`.
5. Buka **Settings → Actions → General**:
   - pilih *Allow all actions*
   - di *Workflow permissions* pilih **Read and write permissions**
   - klik **Save**

> Repo Private mendapat 2.000 menit GitHub Actions gratis per bulan. Paket ini memakai sekitar 25–45 menit per hari, karena termasuk menunggu sampai jam 20.00. Masih cukup.

## B. Gemini (naskah + suara Riko)
1. Di Google AI Studio → **Get API key**, buat key **baru**.
2. Di repo: **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `GEMINI_API_KEY`
   - Secret: isi API key tadi

## C. Facebook + Instagram (Meta Graph API)
**Persiapan**
- Akun Instagram harus **Professional** (Creator/Business) dan **terhubung ke Facebook Page** kamu.
- Sambungkan dari Meta Business Suite, atau dari pengaturan Page → *Linked accounts*.

**Buat aplikasi**
1. Buka **developers.facebook.com** → login → **My Apps → Create App**. Pilih tipe/use case yang menyediakan **Facebook Login for Business**, lalu tambahkan produk **Instagram** dan **Pages API**. Nama menu Meta bisa sedikit berbeda.

**Ambil token**
2. Buka **Graph API Explorer** (developers.facebook.com/tools/explorer):
   1. Pilih aplikasi kamu, lalu pilih **User Token**.
   2. Tambahkan permission: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `business_management`.
   3. Klik **Generate Access Token**. Login, lalu pilih Page dan akun Instagram kamu, dan izinkan.
3. Klik ikon **ⓘ** di samping token → **Open in Access Token Tool** → **Extend Access Token**. Copy token panjang yang muncul.
4. Kembali ke Graph API Explorer. Paste token panjang itu, lalu jalankan `GET me/accounts`. Di hasilnya, cari Page kamu:
   - `id` → simpan sebagai secret **`META_PAGE_ID`**
   - `access_token` → simpan sebagai secret **`META_PAGE_TOKEN`** (token Page ini tidak kedaluwarsa)

**Aktifkan aplikasi**
5. Di dashboard aplikasi, ubah **App Mode** ke **Live**. Langkah ini meminta URL kebijakan privasi. Buat halaman sederhana gratis, misalnya di Google Sites. Selama aplikasi masih Development, postingan bisa saja hanya terlihat oleh admin aplikasi.

## D. YouTube Shorts (YouTube Data API)
**Buat project dan aktifkan API**
1. Buka **console.cloud.google.com** → buat **New Project**.
2. Masuk ke **APIs & Services → Library**, cari **YouTube Data API v3**, lalu klik **Enable**.

**Atur layar persetujuan**
3. Buka **OAuth consent screen** (Google Auth Platform):
   1. Pilih **External**, isi nama aplikasi dan email kamu.
   2. Tambahkan email kamu sebagai *test user*.
   3. Klik **Publish app** supaya statusnya **In production**. Kalau tetap di mode *Testing*, token kedaluwarsa setelah 7 hari.

**Buat kredensial**
4. Buka **Credentials → Create credentials → OAuth client ID**:
   1. Application type: **Web application**
   2. *Authorized redirect URIs*: `https://developers.google.com/oauthplayground`
   3. Copy **Client ID** → secret **`YT_CLIENT_ID`**
   4. Copy **Client secret** → secret **`YT_CLIENT_SECRET`**

**Ambil refresh token**
5. Buka **developers.google.com/oauthplayground**:
   1. Klik ikon ⚙️ → centang **Use your own OAuth credentials** → paste Client ID dan secret.
   2. Di kotak *Step 1*, ketik scope ini lalu klik **Authorize APIs**:
      `https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly`
   3. Login dengan akun yang memiliki channel YouTube. Kalau muncul peringatan "Google hasn't verified this app", klik **Advanced → Go to …**. Itu aplikasi milikmu sendiri.
   4. Di *Step 2*, klik **Exchange authorization code for tokens**. Copy **Refresh token** → secret **`YT_REFRESH_TOKEN`**.

**Ajukan audit (gratis)**
6. Project API yang belum diaudit Google selalu meng-upload video sebagai **Privat**. Ajukan audit lewat formulir *YouTube API Services – Audit and Quota Extension*. Selama belum lolos, video tetap ter-upload otomatis jam 20.00 sebagai privat. Kamu tinggal mengubahnya ke **Publik** di aplikasi YouTube Studio.

## E. TikTok (manual, sekitar 1 menit per hari)
1. Di halaman repo, klik **Watch → Custom → Releases → Apply**. GitHub akan mengirim email setiap video baru siap.
2. Setiap malam, buka **Releases**. Download file `.mp4`, upload di aplikasi TikTok, lalu paste caption yang sudah disediakan di catatan rilis.
3. Di TikTok, aktifkan label **konten AI** saat posting.

## F. Uji coba lalu aktifkan
1. Buka **Actions → Video Kisah Alkitab Harian → Run workflow → mode `check`**. Ulangi sampai semua baris bertanda ✅. Hasil yang diharapkan:
   - Gemini dan suara Riko OK
   - Facebook Page OK
   - Instagram @akunmu OK
   - Channel YouTube OK
2. Jalankan mode **`dry-run`**. Setelah selesai, download video di bagian **Artifacts** dan tonton.
3. Selesai. Jadwal harian jalan sendiri: video dibuat jam 19.15 WIB dan tayang jam 20.00 WIB. Untuk posting pertama sekarang juga, jalankan mode **`daily`**.
4. Untuk menghentikan sementara: Actions → workflow → **⋯ → Disable workflow**.

## Daftar secret
| Secret | Dari |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio |
| `META_PAGE_TOKEN`, `META_PAGE_ID` | Graph API Explorer (langkah C) |
| `META_IG_USER_ID` | Opsional. Otomatis dicari dari Page. |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | Google Cloud + OAuth Playground (langkah D) |

## Pengaturan opsional (tab *Variables*)
| Variable | Bawaan | Fungsi |
|---|---|---|
| `TTS_VOICE` | `Riko` | Suara narator |
| `POST_TIME_WIB` | `20:00` | Jam tayang. Jadwal `cron` di `daily.yml` juga perlu disesuaikan, sekitar 45 menit sebelumnya. |
| `PLATFORMS` | `facebook,instagram,youtube` | Hapus yang tidak dipakai |
| `YOUTUBE_PRIVACY` | `public` | `private` / `unlisted` |
| `GEMINI_TEXT_MODEL` | `gemini-flash-latest` | Model penulis naskah |
| `TTS_MODEL` | `gemini-3.8-flash-tts` | Model suara (cadangan otomatis: `gemini-3.1-flash-tts-preview`) |

## Kalau ada masalah
- GitHub mengirim email kalau run gagal. Buka run-nya, lalu baca pesan di langkah **Jalankan**.
- Kalau satu platform gagal, platform lain tetap diposting.
- Error Meta *"token expired/invalid"*: ulangi langkah C.2–C.4.
- Error YouTube *"invalid_grant"*: ulangi langkah D.5. Pastikan aplikasi sudah **In production**.
- Kisah berikutnya diatur oleh `next_index` di `state/progress.json`. Daftar kisah ada di `data/stories.json`.

Uji offline di komputer sendiri: `pip install -r requirements.txt && python main.py --mock`
