# 👟 Sanal Çadır (sanalcadir.com) 7/24 Otomatik Ayakkabı İndirim & Stok Takip Botu

`https://www.sanalcadir.com/` (Akinon altyapısı) üzerinde yer alan **Erkek (45, 45.5, 45-46 ve 46 numara)** ve **Kadın (39, 39.5, 39-40 ve 40 numara)** ayakkabıları **her 5 dakikada bir** otomatik tarayan, **gerçek bir fiyat düşüşü** olduğunda veya **yeni indirimli stok** girdiğinde Telegram üzerinden **ürün fotoğrafı, fiyatı, sepette ekstra indirimi ve satın alma linkiyle** bildirim gönderen modüler Python projesi.

Bilgisayarınız kapalıyken bile **GitHub Actions** üzerinde 7/24 ücretsiz çalışır.

---

## ✨ Temel Özellikler

1. **Akinon JSON & Sayfalama (Pagination) Mimarisi (`scraper.py`):**
   - Kategori URL'lerine `?format=json` ekleyerek tüm sayfaları otomatik gezer.
   - Standart `requests` isteği WAF/Cloudflare engeline takılırsa sırasıyla `curl_cffi`, `cloudscraper` ve `playwright` katmanlarına otomatik geçiş yapar.
2. **Hayalet Stok Koruması (`integration_beden` & `mp_size`):**
   - Sadece üst ürün kartına değil, `extra_data.variants` (`integration_beden` ve Marketplace/Superstep `mp_size`) altındaki numara bazlı gerçek stoklara (`in_stock: true` ve `stock > 0`) bakar. Hedef numarası (`45`, `45.5`, `45-46`, `46` veya `39`, `39.5`, `39-40`, `40`) gerçekten stokta olmayan ürünleri kesin olarak eler.
3. **Sepette Ekstra İndirim Algılama:**
   - Ürün verisindeki liste fiyatını (`retail_price`), indirimli satış fiyatını (`price`) ve varsa **Sepette Ekstra İndirim** (`basket_offers` -> `discounted_total_price`) kampanyasını ayrıştırarak gerçek net fiyatı hesaplar.
4. **Akıllı Durum Takibi & Spam Koruması (`storage.py`):**
   - `data/products_state.json` içinde her ürünün son bilinen net fiyatını ve şimdiye kadarki en düşük fiyatını (`lowest_price`) saklar.
   - **İlk Çalıştırma Koruması (`NOTIFY_ON_FIRST_RUN=False`):** İlk çalışmada mevcut binlerce ürünü mesaj olarak atmak yerine sessizce veritabanına kaydeder.
   - **🔥 EKSTRA FİYAT DÜŞÜŞÜ:** Kayıtlı bir ürünün net fiyatı en az **%5** daha düşerse bildirim gönderir.
   - **🔄 TEKRAR STOĞA GİRDİ:** Daha önce stoğu tükenen bir ürün tekrar stoğa girerse bildirim gönderir.
   - **🆕 YENİ STOK GELDİ:** Daha önce veritabanında olmayan bir ürün hedef numaralarda stoğa girerse ve filtre kriterlerini (`MIN_DISCOUNT_RATE`, `MAX_PRICE_LIMIT`, `ALLOWED_BRANDS`) sağlıyorsa bildirim gönderir.
5. **Görselli Telegram Bildirimi (`notifier.py`):**
   - Telegram Bot API `sendPhoto` uç noktasını kullanarak ürün fotoğrafıyla ve tıklanabilir satın alma butonuyla zengin HTML mesajı gönderir (fotoğraf yüklenemezse `sendMessage` fallback olarak devreye girer).
   - Flood/rate-limit koruması için mesajlar arasında `1.5` saniye bekler ve en yüksek indirimli fırsatları önceliklendirir (`MAX_ALERTS_PER_RUN=25`).

---

## 📂 Proje Dosya Yapısı

```text
SanalCadir_Bot/
├── .github/
│   └── workflows/
│       └── hourly_tracker.yml   # 5 dakikada bir çalışan (12x5dk) ve state dosyasını güncelleyen GitHub Actions
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
- **Bilgisayar Açıkken Sürekli Tarama (Her 5 Dakikada Bir):**
  ```bash
  python main.py --loop
  ```
