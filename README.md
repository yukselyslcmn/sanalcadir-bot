# 👟 Sanal Çadır (sanalcadir.com) 7/24 Otomatik Ayakkabı İndirim & Stok Takip Botu

`https://www.sanalcadir.com/` (Akinon altyapısı) üzerinde yer alan **Erkek (45 ve 45.5 numara)** ve **Kadın (39 ve 40 numara)** ayakkabıları her saat başı otomatik tarayan, **gerçek bir fiyat düşüşü** olduğunda veya **yeni indirimli stok** girdiğinde Telegram üzerinden **ürün fotoğrafı, fiyatı, sepette ekstra indirimi ve satın alma linkiyle** bildirim gönderen modüler Python projesi.

Bilgisayarınız kapalıyken bile **GitHub Actions** üzerinde 7/24 ücretsiz çalışır.

---

## ✨ Temel Özellikler

1. **Akinon JSON & Sayfalama (Pagination) Mimarisi (`scraper.py`):**
   - Kategori URL'lerine `?format=json` ekleyerek tüm sayfaları otomatik gezer.
   - Standart `requests` isteği WAF/Cloudflare engeline takılırsa sırasıyla `curl_cffi`, `cloudscraper` ve `playwright` katmanlarına otomatik geçiş yapar.
2. **Hayalet Stok Koruması (Ghost Stock Protection):**
   - Sadece üst ürün kartına değil, `extra_data.variants` (`integration_beden`) altındaki numara bazlı gerçek stoklara (`in_stock: true` ve `stock > 0`) bakar. Hedef numarası (`45`, `45.5` veya `39`, `40`) gerçekten stokta olmayan ürünleri kesin olarak eler.
3. **Sepette Ekstra İndirim Algılama:**
   - Ürün verisindeki liste fiyatını (`retail_price`), indirimli satış fiyatını (`price`) ve varsa **Sepette Ekstra İndirim** (`basket_offers` -> `discounted_total_price`) kampanyasını ayrıştırarak gerçek net fiyatı hesaplar.
4. **Akıllı Durum Takibi & Spam Koruması (`storage.py`):**
   - `data/products_state.json` içinde her ürünün son bilinen net fiyatını saklar.
   - **İlk Çalıştırma Koruması (`NOTIFY_ON_FIRST_RUN=False`):** İlk çalışmada mevcut yüzlerce ürünü mesaj olarak atmak yerine sessizce veritabanına kaydeder.
   - **🔥 EKSTRA FİYAT DÜŞÜŞÜ:** Kayıtlı bir ürünün net fiyatı en az **%5** daha düşerse bildirim gönderir.
   - **🆕 YENİ STOK GELDİ:** Daha önce veritabanında olmayan bir ürün hedef numaralarda stoğa girerse ve filtre kriterlerini (`MIN_DISCOUNT_RATE`, `MAX_PRICE_LIMIT`, `ALLOWED_BRANDS`) sağlıyorsa bildirim gönderir.
5. **Görselli Telegram Bildirimi (`notifier.py`):**
   - Telegram Bot API `sendPhoto` uç noktasını kullanarak ürün fotoğrafıyla birlikte zengin HTML mesajı gönderir (fotoğraf yüklenemezse `sendMessage` fallback olarak devreye girer).
   - Flood/rate-limit koruması için mesajlar arasında `1.5` saniye bekler.

---

## 📂 Proje Dosya Yapısı

```text
SanalCadir_Bot/
├── .github/
│   └── workflows/
│       └── hourly_tracker.yml   # Saatlik çalışan ve state dosyasını geri pushlayan GitHub Actions
├── data/
│   └── products_state.json      # Ürün fiyat ve stok hafızası (GitHub Actions tarafından güncellenir)
├── .env.example                 # Ortam değişkenleri ve filtre ayar şablonu
├── .gitignore                   # Git hariç tutma kuralları (.env gizlenir, state korunur)
├── config.py                    # Kategori URL'leri, bedenler ve filtre yapılandırması
├── scraper.py                   # Sanal Çadır (Akinon) JSON/HTML tarayıcı modülü
├── storage.py                   # Durum karşılaştırma ve gerçek indirim/yeni stok tespit modülü
├── notifier.py                  # Fotoğraflı Telegram bildirim modülü (sendPhoto + fallback)
├── main.py                      # Ana çalıştırma dosyası (--once ve --loop destekli)
├── test_scraper.py              # Telegram'a mesaj atmadan konsolda hızlı veri çekme testi
└── requirements.txt             # Gerekli Python kütüphaneleri
```

---

## 🚀 Hızlı Kurulum (Bilgisayarda Çalıştırma)

### 1. Kütüphaneleri Yükleyin
```bash
pip install -r requirements.txt
```

### 2. `.env` Dosyasını Oluşturun
`.env.example` dosyasının adını `.env` olarak kopyalayın ve Telegram bilgilerinizi girin:
```bash
copy .env.example .env
```
`.env` içerisindeki şu iki alanı doldurun:
- `TELEGRAM_BOT_TOKEN`: `@BotFather` üzerinden aldığınız bot token'ı.
- `TELEGRAM_CHAT_ID`: Bildirimlerin geleceği sohbet/grup ID'si (`@userinfobot` ile öğrenebilirsiniz).

### 3. Scraper'ı Test Edin (Telegram'a Mesaj Atmadan)
Verilerin doğru çekildiğini konsolda görmek için:
```bash
python test_scraper.py
```

### 4. Botu Çalıştırın
- **Tek Seferlik Tarama:**
  ```bash
  python main.py --once
  ```
- **Bilgisayar Açıkken Sürekli Tarama (Her 60 Dakikada Bir):**
  ```bash
  python main.py --loop
  ```

---

## ☁️ GitHub Actions ile 7/24 Ücretsiz Bulutta Çalıştırma

1. GitHub'da yeni bir depo (repository) oluşturun (**Private** yapmanız önerilir) ve bu klasördeki tüm dosyaları yükleyin.
2. GitHub deponuzda **Settings** ➡️ **Secrets and variables** ➡️ **Actions** menüsüne gidin.
3. **New repository secret** butonuna tıklayarak şu iki gizli değişkeni ekleyin:
   - `TELEGRAM_BOT_TOKEN` : Telegram Bot Token'ınız
   - `TELEGRAM_CHAT_ID` : Telegram Chat ID'niz
4. *(İsteğe Bağlı)* Filtreleri değiştirmek isterseniz **Variables** sekmesinden `MAX_PRICE_LIMIT`, `MIN_DISCOUNT_RATE`, `ALLOWED_BRANDS` değişkenlerini ekleyebilir veya doğrudan `config.py` dosyasından düzenleyebilirsiniz.
5. **Actions** sekmesine gidin, **"Sanal Çadır 7/24 Saatlik Ayakkabı İndirim & Stok Takip Botu"** iş akışını seçin ve sağdaki **Run workflow** butonuna tıklayarak ilk çalışmayı başlatın.
   - İlk çalışmada `data/products_state.json` otomatik olarak mevcut ürünlerle doldurulup deponuza geri kaydedilecek (`git commit & push`), sonraki her saat başı gerçek fiyat düşüşleri ve yeni stoklar Telegram'ınıza fotoğraflı olarak gelecektir!
