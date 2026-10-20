"""
Sanal Çadır (sanalcadir.com) Ayakkabı İndirim & Stok Takip Botu
Yapılandırma ve Filtre Ayarları Modülü (config.py)
"""

import os
from pathlib import Path
from typing import Dict, List, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv yüklü değilse ortam değişkenlerinden devam et
    pass


# ==============================================================================
# DİZİN VE DOSYA YOLLARI
# ==============================================================================
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
STATE_FILE_PATH: Path = DATA_DIR / "products_state.json"


# ==============================================================================
# YARDIMCI FONKSİYONLAR (.env Okuma ve Türkçe Karakter Normalizasyonu)
# ==============================================================================
def normalize_tr(text: str) -> str:
    """Türkçe karakter duyarlı küçük harfe çevirme fonksiyonu."""
    if not text:
        return ""
    replacements = {
        "İ": "i",
        "I": "ı",
        "Ğ": "ğ",
        "Ü": "ü",
        "Ş": "ş",
        "Ö": "ö",
        "Ç": "ç",
    }
    for upper_char, lower_char in replacements.items():
        text = text.replace(upper_char, lower_char)
    return text.lower().strip()


def _parse_bool(value: str, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "evet", "on")


def _parse_list(value: str, default: List[str]) -> List[str]:
    if value is None:
        return default
    stripped = value.strip()
    if stripped == "":
        return []
    return [item.strip() for item in stripped.split(",") if item.strip()]


def _parse_float(value: str, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value.strip().replace(",", "."))
    except ValueError:
        return default


def _parse_int(value: str, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


# ==============================================================================
# 1. TELEGRAM BOT AYARLARI
# ==============================================================================
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_RATE_LIMIT_DELAY: float = _parse_float(
    os.getenv("TELEGRAM_RATE_LIMIT_DELAY"), 1.5
)


# ==============================================================================
# 2. HEDEF SİTE VE KATEGORİ / NUMARA AYARLARI (TARGETS)
# ==============================================================================
BASE_URL: str = "https://www.sanalcadir.com"

# Akinon altyapısında beden filtresi `attributes_filterable_size` parametresiyle çalışır.
# Buçuklu numaralar sitede "45,5" (virgüllü) olarak yer aldığı için URL'de 45 ve 45,5 kullanılır.
ERKEK_CATEGORY_URL: str = os.getenv(
    "ERKEK_CATEGORY_URL",
    "https://www.sanalcadir.com/erkek-ayakkabi/?attributes_filterable_size=45&attributes_filterable_size=45,5",
).strip()

KADIN_CATEGORY_URL: str = os.getenv(
    "KADIN_CATEGORY_URL",
    "https://www.sanalcadir.com/kadin-ayakkabi/?attributes_filterable_size=39&attributes_filterable_size=40",
).strip()

ERKEK_TARGET_SIZES: List[str] = _parse_list(
    os.getenv("ERKEK_TARGET_SIZES"), ["45", "45.5"]
)
KADIN_TARGET_SIZES: List[str] = _parse_list(
    os.getenv("KADIN_TARGET_SIZES"), ["39", "40"]
)


# ==============================================================================
# 3. FİLTRELEME VE KALİTE AYARLARI
# ==============================================================================
# Ürün adında bu kelimelerden herhangi biri geçiyorsa ürün tarama dışı bırakılır:
DEFAULT_EXCLUDE_KEYWORDS: List[str] = [
    "terlik",
    "sandalet",
    "panduf",
    "deniz",
    "çorap",
    "bakım",
    "bağcık",
]
EXCLUDE_KEYWORDS: List[str] = (
    _parse_list(os.getenv("EXCLUDE_KEYWORDS"), DEFAULT_EXCLUDE_KEYWORDS)
    if os.getenv("EXCLUDE_KEYWORDS") is not None
    else DEFAULT_EXCLUDE_KEYWORDS
)

# Marka Filtresi:
# Boş liste [] bırakılırsa tüm markalar kabul edilir.
# Sadece belirli markaları takip etmek isterseniz .env içinde ALLOWED_BRANDS tanımlayabilir
# veya aşağıdaki listeye ekleyebilirsiniz:
# Örn: ["Nike", "Adidas", "New Balance", "Puma", "Lacoste", "Skechers", "Vans", "Timberland", "Converse"]
DEFAULT_ALLOWED_BRANDS: List[str] = []
ALLOWED_BRANDS: List[str] = (
    _parse_list(os.getenv("ALLOWED_BRANDS"), DEFAULT_ALLOWED_BRANDS)
    if os.getenv("ALLOWED_BRANDS") is not None
    else DEFAULT_ALLOWED_BRANDS
)

# Tavan Fiyat Sınırı (TL) - Net (indirimli/sepet) fiyatı bu sınırın üzerinde olanlar bildirilmez
MAX_PRICE_LIMIT: float = _parse_float(os.getenv("MAX_PRICE_LIMIT"), 10000.0)
ERKEK_MAX_PRICE_LIMIT: float = _parse_float(
    os.getenv("ERKEK_MAX_PRICE_LIMIT"), MAX_PRICE_LIMIT
)
KADIN_MAX_PRICE_LIMIT: float = _parse_float(
    os.getenv("KADIN_MAX_PRICE_LIMIT"), MAX_PRICE_LIMIT
)

# İlk kez stoğa giren ürünlerde aranacak minimum indirim oranı (%)
MIN_DISCOUNT_RATE: float = _parse_float(os.getenv("MIN_DISCOUNT_RATE"), 20.0)

# Kayıtlı ürünlerde "EKSTRA FİYAT DÜŞÜŞÜ" bildirimi için gereken minimum düşüş yüzdesi (%)
PRICE_DROP_THRESHOLD: float = _parse_float(os.getenv("PRICE_DROP_THRESHOLD"), 5.0)

# Kritik stok uyarı eşiği (Stok bu sayı ve altındaysa "Son X Adet Kaldı!" uyarısı eklenir)
CRITICAL_STOCK_LIMIT: int = _parse_int(os.getenv("CRITICAL_STOCK_LIMIT"), 3)


# ==============================================================================
# 4. ÇALIŞMA VE DURUM TAKİP AYARLARI
# ==============================================================================
# İlk çalıştırmada (products_state.json boşken) bildirim atılsın mı? (Spam koruması için False)
NOTIFY_ON_FIRST_RUN: bool = _parse_bool(os.getenv("NOTIFY_ON_FIRST_RUN"), default=False)

# --loop modunda kontrol aralığı (dakika)
CHECK_INTERVAL_MINUTES: int = _parse_int(os.getenv("CHECK_INTERVAL_MINUTES"), 20)

# Kategori başına maksimum sayfa sınırı (0 = tüm sayfaları tara)
MAX_PAGES_PER_CATEGORY: int = _parse_int(os.getenv("MAX_PAGES_PER_CATEGORY"), 0)

# HTTP İstek Ayarları (GitHub Actions'ta 60 saniyenin altında bitip 1 dk yazması için 0.2 sn)
REQUEST_TIMEOUT: int = _parse_int(os.getenv("REQUEST_TIMEOUT"), 25)
PAGE_DELAY_SECONDS: float = _parse_float(os.getenv("PAGE_DELAY_SECONDS"), 0.2)


# ==============================================================================
# 5. TAKİP EDİLECEK HEDEF GRUPLAR (TARGETS)
# ==============================================================================
TARGETS: List[Dict[str, Any]] = [
    {
        "key": "erkek",
        "name": "Erkek",
        "badge": "🚹 Erkek (45 - 45.5)",
        "url": ERKEK_CATEGORY_URL,
        "sizes": ERKEK_TARGET_SIZES,
        "max_price": ERKEK_MAX_PRICE_LIMIT,
    },
    {
        "key": "kadin",
        "name": "Kadın",
        "badge": "🚺 Kadın (39 - 40)",
        "url": KADIN_CATEGORY_URL,
        "sizes": KADIN_TARGET_SIZES,
        "max_price": KADIN_MAX_PRICE_LIMIT,
    },
]
