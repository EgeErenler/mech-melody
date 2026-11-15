# mech.melody: kurulum ve kullanım

Bu repo, CHIMERA ses sentezleyicisinin (MIT lisanslı, [jamesondh/chimera-vocal-synth](https://github.com/jamesondh/chimera-vocal-synth)) üstüne kurulu bir Instagram otomasyonudur. Robot kafalar şarkıyı bir grup olarak söyler, bazıları enstrüman çalar, video İngilizce altyazıyla Reel olarak paylaşılır. Yorumlara otomatik cevap verilir, şarkı istekleri sıraya girer.

## Nasıl çalışır

```
songs/<şarkı>/song.yaml + .mid
        │  python3 -m mech build
        ▼
  her vokal için CHIMERA render (ses + yüz telemetrisi)
  enstrümanlar (gitar, bas, piyano, synth, yaylı, davul) MIDI'den sentez
  miks + İngilizce altyazı (Claude çevirir, songs/<şarkı>/subtitles.en.json'a kaydeder)
        │  bun mech/render_video.ts
        ▼
  out/<şarkı>/reel.mp4 (1080×1920, 30 fps) + cover.jpg
        │  python3 -m mech publish
        ▼
  Claude açıklama yazar → Instagram'a Reel olarak gider → data/published.json
```

GitHub Actions üç iş çalıştırır:

| İş | Ne zaman | Ne yapar |
|---|---|---|
| `publish a reel` | her gün 16:41 UTC | Sıradaki şarkıyı seçer (en çok istenen önce), videoyu ve kapağı üretir, paylaşır, isteyenlere yorumlarında haber verir |
| `answer comments` | her saat | Son 10 Reel'deki yeni yorumları okur, Claude ile cevaplar, şarkı isteklerini `data/queue.json` sırasına ekler |
| `refresh instagram token` | 3 haftada bir | Instagram token'ını yeniler (isteğe bağlı, `GH_PAT` gerekir) |

Repoda **"🎤 Song requests"** adında bir issue açılır ve her çalışmada güncellenir. Telefondan bakıp hangi şarkının istendiğini ve hangisinin MIDI beklediğini görürsün.

## 1. Instagram tarafı (bir kez)

1. Instagram hesabını **Profesyonel hesap** yap (Ayarlar → Hesap türü → Creator ya da Business).
2. [developers.facebook.com](https://developers.facebook.com) → **Create app** → kullanım alanı olarak **"Manage messaging & content on Instagram"** (Instagram API with Instagram Login).
3. Uygulamada **Instagram → API setup with Instagram login** bölümüne gir:
   - `mech.melody` hesabını ekle (uygulamada rolü olan hesap için App Review gerekmez).
   - İzinler: `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`.
   - **Generate token** ile uzun ömürlü token al (60 gün geçerli). Hesabın **Instagram user ID** değerini de not al.

## 2. GitHub tarafı (bir kez)

Repo → **Settings → Secrets and variables → Actions**:

| Secret | Değer |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API anahtarı ([console.anthropic.com](https://console.anthropic.com)) |
| `IG_ACCESS_TOKEN` | Instagram token'ı |
| `IG_USER_ID` | Instagram user ID |
| `GH_PAT` | (isteğe bağlı) Token'ın kendini yenilemesi için: bu repoya "Secrets: Read and write" izni olan fine-grained GitHub token |

| Variable (isteğe bağlı) | Varsayılan | Anlamı |
|---|---|---|
| `POSTS_PER_WEEK` | `7` | Haftada kaç gün paylaşılsın (`3` = Pzt, Sal, Çar) |
| `IG_API` | `instagram` | `facebook` yaparsan Facebook Login + doğrudan dosya yükleme kullanılır |
| `MEDIA_BASE_URL` | (boş) | Videoyu kendi sunucunda barındırırsan adresi. Boşsa video, Instagram'ın indirmesi için bu reponun bir GitHub release'ine konur |

Sonra **Actions → publish a reel → Run workflow → dry_run: true** ile bir deneme yap. Video `reel` artifact'ı olarak iner. İyiyse aynı işi `dry_run` kapalı çalıştır.

## 3. Şarkı ekleme

Her şarkı `songs/<kısa-ad>/` klasörüdür: bir MIDI dosyası ve bir `song.yaml`.

1. Şarkının MIDI'sini bul. Karaoke dosyaları (`.kar`) en iyisidir, çünkü sözler notalara zaten bağlıdır.
2. İskeleti oluştur:
   ```sh
   python3 -m mech new tarkan-simarik ~/Downloads/simarik.mid --lang tr --title "Şımarık" --artist "Tarkan"
   ```
   Bu komut MIDI'deki kanalları listeler ve vokal, davul, bas, gitar gibi parçaları tahmin ederek `song.yaml` yazar.
3. `song.yaml` dosyasını düzenle (örnek: `songs/frere-jacques/song.yaml`):
   - `start` / `end`: şarkının hangi saniyeleri kullanılsın (Reel için 30-75 sn iyidir).
   - `lyrics`: her satır bir müzikal cümle. `midi` yazarsan karaoke sözleri kullanılır. `from: Lead` başka bir sesin sözlerini alır. `vocalise: oo` sözsüz "uuu" söyletir.
   - `body`: `male`, `female`, `child`. `color`: `gold`, `blue`, `pink`, `green`, `violet`, `cyan`, `red`, `white`.
   - Enstrüman: `instrument: guitar | bass | piano | synth | strings | drums`.
   - `subtitles`: İngilizce altyazıyı kendin yazmak istersen. Yazmazsan Claude çevirir.
4. Önizle:
   ```sh
   python3 -m mech syllables tr "Şımarık, şımarık"   # robot sözleri nasıl okuyacak
   python3 -m mech video tarkan-simarik --seconds 15   # out/tarkan-simarik/reel.mp4
   ```
5. Commit + push. Sıradaki `publish a reel` çalışmasında, istek sayısına göre sıraya girer.

### Söz ipuçları

- Bir kelimeyi elle hecele: `ca|ri|ño`, `Frè|re Jac|que`. Fransızcadaki sessiz "e" böyle söylenir.
- `_` bir önceki heceyi bir nota daha uzatır: `bai _ la`.
- Uyarılar (`12 syllables for 10 notes` gibi) hangi satırın notalara oturmadığını söyler. O satırı kısalt ya da `_` ekle.

## Yerelde çalıştırma

Gerekenler: Rust (`wasm32-unknown-unknown` hedefiyle), Bun, Python 3.11+, `espeak-ng`, `ffmpeg`, Google Chrome.

```sh
pip install -r mech/requirements.txt
bun install && bun run wasm && cargo build --release -p chimera-cli
python3 -m mech video frere-jacques --seconds 10
bun run dev   # sonra http://127.0.0.1:5317/studio.html?id=frere-jacques ile tarayıcıda canlı izle
```

`python3 -m mech --help` tüm komutları listeler.

## Bilmen gerekenler

- **Telif:** Popüler şarkıların cover'ları için Instagram sesi kapatabilir ya da videoyu kaldırabilir. Bu repo şu an **public**. MIDI ve söz dosyaları herkese açık olur. Private yapmak istersen GitHub Actions'ın ücretsiz kotası (ayda 2.000 dk) her gün video üretmeye yetmeyebilir. Bir video yaklaşık 25-40 dk sürer.
- **Hız:** Robot kafalar GPU olmadan yazılımsal WebGL ile çizilir. 60 saniyelik bir Reel, GitHub'ın 4 çekirdekli makinesinde yaklaşık 25-40 dakikada hazır olur.
- **Token:** Instagram token'ı 60 günde biter. `GH_PAT` eklemezsen her 2 ayda bir yeni token alıp `IG_ACCESS_TOKEN` secret'ını elle güncelle.
- **Zamanlanmış işler:** Public repolarda 60 gün hiç commit olmazsa GitHub zamanlanmış işleri durdurur. Botun kendi commit'leri bunu genelde engeller.
