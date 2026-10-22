"""
Sanal Çadır (sanalcadir.com - Akinon) Veri Çekme (Scraper) Modülü (scraper.py)

Özellikler:
- Akinon `?format=json` uç noktasından sayfalama (pagination) desteğiyle tüm ürünleri çeker.
- WAF/Cloudflare engeline karşı `requests` -> `curl_cffi` -> `cloudscraper` -> `playwright`
  katmanlı (fallback) esnek mimari sunar.
- Hayalet Stok Koruması: `extra_data.variants` içindeki beden bazlı gerçek stokları (`integration_beden` ve
  `mp_size` altında `in_stock` ve `stock > 0`) kontrol ederek sadece hedef numaraları
  (Erkek: 45, 45.5, 45-46, 46 | Kadın: 39, 39.5, 39-40, 40) gerçekten stokta olan ürünleri alır.
- Çocuk / Yanlış Cinsiyet Filtresi: Kadın kategorisine sızan Çocuk (`integration_cocuk == "1"`) veya Erkek
  ürünlerini otomatik ayıklar (Unisex ürünleri her iki kategoride de destekler).
- Sepette Ekstra İndirim Algılama: `retail_price`, `price` ve `basket_offers` (`discounted_total_price`)
  verilerini ayrıştırarak gerçek net fiyatı hesaplar.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from config import normalize_tr

logger = logging.getLogger(__name__)


class SanalCadirScraper:
    """Sanal Çadır (Akinon) kategori ve ürün tarayıcı sınıfı."""

    DEFAULT_HEADERS: Dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.sanalcadir.com/",
        "Connection": "keep-alive",
    }

    def __init__(self) -> None:
        self.base_url: str = config.BASE_URL
        self.timeout: int = config.REQUEST_TIMEOUT
        self.page_delay: float = config.PAGE_DELAY_SECONDS

        # Otomatik yeniden deneme (Retry) destekli standart requests oturumu
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        retry_strategy = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # WAF / Cloudflare aşımı için opsiyonel istemciler (lazy-init)
        self._curl_session: Any = None
        self._cloudscraper_session: Any = None

        # Son taramada tüm sayfaları eksiksiz çekilen kategorilerin anahtarları
        self.completed_categories: Set[str] = set()

    # ==========================================================================
    # HTTP İSTEK VE WAF / CLOUDFLARE BYPASS KATMANI
    # ==========================================================================
    @staticmethod
    def _is_waf_blocked(status_code: int, text: str) -> bool:
        """Yanıtın Cloudflare / WAF engeline takılıp takılmadığını tespit eder."""
        if status_code in (403, 429, 503):
            return True
        lowered = (text or "")[:2000].lower()
        waf_markers = (
            "cloudflare",
            "cf-browser-verification",
            "just a moment...",
            "attention required!",
            "access denied",
            "captcha",
        )
        if any(marker in lowered for marker in waf_markers) and not lowered.strip().startswith("{"):
            return True
        return False

    def _fetch_with_requests(self, url: str) -> Optional[Tuple[int, str]]:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            return resp.status_code, resp.text
        except Exception as exc:
            logger.debug("requests hatası (%s): %s", url, exc)
            return None

    def _fetch_with_curl_cffi(self, url: str) -> Optional[Tuple[int, str]]:
        try:
            from curl_cffi import requests as curl_requests  # type: ignore

            if self._curl_session is None:
                self._curl_session = curl_requests.Session(impersonate="chrome120")
                self._curl_session.headers.update(self.DEFAULT_HEADERS)
            resp = self._curl_session.get(url, timeout=self.timeout)
            return resp.status_code, resp.text
        except ImportError:
            logger.debug("curl_cffi kütüphanesi yüklü değil, atlanıyor.")
            return None
        except Exception as exc:
            logger.debug("curl_cffi hatası (%s): %s", url, exc)
            return None

    def _fetch_with_cloudscraper(self, url: str) -> Optional[Tuple[int, str]]:
        try:
            import cloudscraper  # type: ignore

            if self._cloudscraper_session is None:
                self._cloudscraper_session = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False}
                )
                self._cloudscraper_session.headers.update(self.DEFAULT_HEADERS)
            resp = self._cloudscraper_session.get(url, timeout=self.timeout)
            return resp.status_code, resp.text
        except ImportError:
            logger.debug("cloudscraper kütüphanesi yüklü değil, atlanıyor.")
            return None
        except Exception as exc:
            logger.debug("cloudscraper hatası (%s): %s", url, exc)
            return None

    def _fetch_with_playwright(self, url: str) -> Optional[Tuple[int, str]]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=self.DEFAULT_HEADERS["User-Agent"],
                    extra_http_headers={"Accept": "application/json"},
                )
                page = context.new_page()
                response = page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                status = response.status if response else 200
                body = page.evaluate("() => document.body.innerText") or page.content()
                browser.close()
                return status, body
        except ImportError:
            logger.debug("playwright yüklü değil, atlanıyor.")
            return None
        except Exception as exc:
            logger.debug("playwright hatası (%s): %s", url, exc)
            return None

    def fetch_raw(self, url: str, retries: int = 3) -> Optional[str]:
        """
        Verilen URL'den ham yanıtı çeker.
        Standart `requests` WAF/Cloudflare engeline takılırsa sırasıyla
        `curl_cffi`, `cloudscraper` ve `playwright` katmanlarını dener.
        """
        for attempt in range(1, retries + 1):
            # 1. Standart requests
            res = self._fetch_with_requests(url)
            if res is not None:
                status, text = res
                if status == 200 and not self._is_waf_blocked(status, text):
                    return text
                logger.warning(
                    "Standart istek WAF/HTTP engeline takıldı (Status: %s, Deneme: %d/%d). Alternatif çözücüler deneniyor...",
                    status,
                    attempt,
                    retries,
                )

            # 2. curl_cffi (TLS Impersonation)
            res = self._fetch_with_curl_cffi(url)
            if res is not None:
                status, text = res
                if status == 200 and not self._is_waf_blocked(status, text):
                    logger.info("curl_cffi ile WAF aşımı başarılı.")
                    return text

            # 3. cloudscraper
            res = self._fetch_with_cloudscraper(url)
            if res is not None:
                status, text = res
                if status == 200 and not self._is_waf_blocked(status, text):
                    logger.info("cloudscraper ile WAF aşımı başarılı.")
                    return text

            # 4. Son çare: Playwright Headless
            if attempt == retries:
                res = self._fetch_with_playwright(url)
                if res is not None:
                    status, text = res
                    if status == 200:
                        logger.info("Playwright ile veri çekimi başarılı.")
                        return text

            time.sleep(1.5 * attempt)

        logger.error("URL çekilemedi (tüm katmanlar başarısız oldu): %s", url)
        return None

    # ==========================================================================
    # URL VE SAYFALAMA (PAGINATION) YARDIMCILARI
    # ==========================================================================
    @staticmethod
    def build_json_page_url(category_url: str, page: int = 1) -> str:
        """
        Kategori URL'sindeki çoklu filtre parametrelerini (örn: birden fazla
        `attributes_filterable_size`) koruyarak `?format=json&page=N` ekler.
        Ayrıca 45.5 gibi noktalı bedenleri Akinon'un kullandığı 45,5 formatıyla da destekler.
        """
        parts = urlsplit(category_url)
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)

        filtered_pairs: List[Tuple[str, str]] = []
        seen_sizes = set()

        for k, v in query_pairs:
            if k.lower() in ("format", "page"):
                continue
            filtered_pairs.append((k, v))
            if k == "attributes_filterable_size":
                seen_sizes.add(v)

        extra_size_pairs: List[Tuple[str, str]] = []
        for k, v in filtered_pairs:
            if k == "attributes_filterable_size" and "." in v:
                comma_val = v.replace(".", ",")
                if comma_val not in seen_sizes:
                    extra_size_pairs.append(("attributes_filterable_size", comma_val))
                    seen_sizes.add(comma_val)

        filtered_pairs.extend(extra_size_pairs)
        filtered_pairs.append(("format", "json"))
        if page > 1:
            filtered_pairs.append(("page", str(page)))

        new_query = urlencode(filtered_pairs, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))

    # ==========================================================================
    # FİYAT, SEPET İNDİRİMİ VE STOK AYRIŞTIRMA
    # ==========================================================================
    @staticmethod
    def _to_float(val: Any, default: float = 0.0) -> float:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        text = str(val).strip()
        if not text:
            return default
        text = re.sub(r"[^\d.,]", "", text)
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return default

    @staticmethod
    def _normalize_size(size_val: Any) -> str:
        """Beden bilgisini karşılaştırma için standartlaştırır ('45,5' -> '45.5')."""
        if size_val is None:
            return ""
        return str(size_val).strip().replace(",", ".")

    def _extract_basket_offer(
        self, raw_product: Dict[str, Any], sale_price: float
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Akinon ürün JSON'unda yer alan 'Sepette Ekstra İndirim' (`basket_offers`,
        `promotions`, `basket_price`) alanlarını ayrıştırır.
        Varyant satış fiyatı ana kart fiyatından farklıysa sepet indirim oranını
        varyant fiyatına doğru şekilde oranlar.
        """
        basket_price: Optional[float] = None
        offer_label: Optional[str] = None

        # 1. Akinon standart `basket_offers` dizisi
        basket_offers = raw_product.get("basket_offers")
        if isinstance(basket_offers, list) and basket_offers:
            for offer in basket_offers:
                if not isinstance(offer, dict):
                    continue
                label = offer.get("label") or offer.get("name") or offer.get("title")
                listing_kwargs = offer.get("listing_kwargs") or {}
                discounted_total = listing_kwargs.get("discounted_total_price")
                listing_base_price = self._to_float(listing_kwargs.get("price"))

                if discounted_total is not None:
                    raw_disc_total = self._to_float(discounted_total)
                    if (
                        listing_base_price > 0
                        and abs(listing_base_price - sale_price) > 0.05
                        and 0 < raw_disc_total < listing_base_price
                    ):
                        ratio = raw_disc_total / listing_base_price
                        candidate = round(sale_price * ratio, 2)
                    else:
                        candidate = round(raw_disc_total, 2)

                    if 0 < candidate < sale_price and (basket_price is None or candidate < basket_price):
                        basket_price = candidate
                        offer_label = str(label).strip() if label else "Sepette Ekstra İndirim"
                elif label:
                    match = re.search(r"%\s*(\d+(?:[.,]\d+)?)", str(label))
                    if match:
                        pct = self._to_float(match.group(1))
                        if 0 < pct < 90:
                            candidate = round(sale_price * (1.0 - pct / 100.0), 2)
                            if 0 < candidate < sale_price and (basket_price is None or candidate < basket_price):
                                basket_price = candidate
                                offer_label = str(label).strip()

        # 2. Doğrudan `basket_price` veya `campaign_price` alanı varsa
        for key in ("basket_price", "discounted_price", "campaign_price", "special_price"):
            if raw_product.get(key) is not None:
                candidate = self._to_float(raw_product.get(key))
                if 0 < candidate < sale_price and (basket_price is None or candidate < basket_price):
                    basket_price = round(candidate, 2)
                    if not offer_label:
                        offer_label = "Sepette Ekstra İndirim"

        # 3. `promotions` veya `data_source_offers` listesi
        for promo_key in ("promotions", "data_source_offers"):
            promos = raw_product.get(promo_key)
            if isinstance(promos, list):
                for promo in promos:
                    if isinstance(promo, dict):
                        p_price = promo.get("price") or promo.get("discounted_price")
                        if p_price is not None:
                            candidate = self._to_float(p_price)
                            if 0 < candidate < sale_price and (basket_price is None or candidate < basket_price):
                                basket_price = round(candidate, 2)
                                offer_label = str(promo.get("label") or promo.get("name") or "Sepette Ekstra İndirim")

        return basket_price, offer_label

    def _extract_in_stock_target_sizes(
        self, raw_product: Dict[str, Any], target_sizes: List[str]
    ) -> Tuple[List[str], int, Optional[float], Optional[float]]:
        """
        HAYALET STOK KORUMASI & VARYANT FİYAT DOĞRULAMASI:
        Ürünün `extra_data.variants` (`integration_beden` ve Marketplace/Superstep `mp_size`)
        altındaki beden seçeneklerini kontrol ederek, hedef numaralardan (`target_sizes`)
        hangilerinin GERÇEKTEN stokta (`in_stock: True` ve `stock > 0`) olduğunu ve bu
        numaralara ait varyant fiyatlarını tespit eder.
        """
        normalized_targets = {self._normalize_size(s) for s in target_sizes if s}
        available_sizes: List[str] = []
        total_target_stock: int = 0
        variant_sale_price: Optional[float] = None
        variant_retail_price: Optional[float] = None

        extra_data = raw_product.get("extra_data") or {}
        variants = extra_data.get("variants") if isinstance(extra_data, dict) else None

        has_beden_variants = False
        seen_variant_ids: Set[str] = set()
        if isinstance(variants, list):
            for var_group in variants:
                if not isinstance(var_group, dict):
                    continue
                attr_key = str(var_group.get("attribute_key") or "").strip().lower()
                if (
                    attr_key in ("integration_beden", "mp_size", "filterable_size", "size", "beden")
                    or "beden" in attr_key
                    or "size" in attr_key
                ):
                    options = var_group.get("options") or []
                    if options:
                        has_beden_variants = True
                    for opt in options:
                        if not isinstance(opt, dict):
                            continue
                        opt_in_stock = bool(opt.get("in_stock", False))
                        opt_selectable = bool(opt.get("is_selectable", True))
                        sub_prod = opt.get("product") or {}
                        sub_in_stock = bool(sub_prod.get("in_stock", opt_in_stock))
                        try:
                            sub_stock_qty = int(sub_prod.get("stock", 1 if sub_in_stock else 0) or 0)
                        except (TypeError, ValueError):
                            sub_stock_qty = 1 if sub_in_stock else 0

                        if not (opt_in_stock and opt_selectable and sub_in_stock and sub_stock_qty > 0):
                            continue

                        # Aynı alt ürün (varyant) birden fazla beden grubunda listelenmişse stoğu çift sayma
                        variant_id = str(sub_prod.get("pk") or sub_prod.get("sku") or "")
                        if variant_id:
                            if variant_id in seen_variant_ids:
                                continue
                            seen_variant_ids.add(variant_id)

                        attrs = sub_prod.get("attributes") or {}
                        raw_size = (
                            attrs.get("filterable_size")
                            or attrs.get("integration_trBeden")
                            or attrs.get("mp_size")
                            or opt.get("label")
                            or ""
                        )
                        norm_size = self._normalize_size(raw_size)

                        if norm_size in normalized_targets:
                            if norm_size not in available_sizes:
                                available_sizes.append(norm_size)
                            total_target_stock += sub_stock_qty

                            v_price = self._to_float(sub_prod.get("price"))
                            v_retail = self._to_float(sub_prod.get("retail_price"))
                            if v_price > 0 and (variant_sale_price is None or v_price < variant_sale_price):
                                variant_sale_price = v_price
                                if v_retail >= v_price:
                                    variant_retail_price = v_retail

        if has_beden_variants:
            return sorted(available_sizes), total_target_stock, variant_sale_price, variant_retail_price

        top_in_stock = bool(raw_product.get("in_stock", False))
        try:
            top_stock = int(raw_product.get("stock", 1 if top_in_stock else 0) or 0)
        except (TypeError, ValueError):
            top_stock = 1 if top_in_stock else 0
        if not (top_in_stock and top_stock > 0):
            return [], 0, None, None

        attrs = raw_product.get("attributes") or {}
        top_size = self._normalize_size(
            attrs.get("filterable_size")
            or attrs.get("integration_trBeden")
            or attrs.get("mp_size")
            or ""
        )
        if top_size and top_size in normalized_targets:
            return [top_size], top_stock, None, None
        elif not top_size:
            return sorted(list(normalized_targets)), top_stock, None, None

        return [], 0, None, None

    @staticmethod
    def _extract_image_url(raw_product: Dict[str, Any]) -> str:
        """Ürünün en yüksek öncelikli aktif fotoğraf URL'sini döndürür."""
        image_set = raw_product.get("productimage_set")
        if isinstance(image_set, list) and image_set:
            active_images = [
                img for img in image_set
                if isinstance(img, dict) and img.get("image") and img.get("status", "active") == "active"
            ]
            if not active_images:
                active_images = [img for img in image_set if isinstance(img, dict) and img.get("image")]
            if active_images:
                active_images.sort(key=lambda x: int(x.get("order", 999) or 999))
                img_url = str(active_images[0]["image"]).strip()
                if img_url.startswith("//"):
                    return "https:" + img_url
                return img_url

        if raw_product.get("image"):
            img_url = str(raw_product["image"]).strip()
            if img_url.startswith("//"):
                return "https:" + img_url
            return img_url

        return ""

    # ==========================================================================
    # FİLTRELEME KONTROLLERİ (Kelime, Çocuk/Cinsiyet, Marka)
    # ==========================================================================
    @staticmethod
    def is_excluded_by_keyword(name: str, sub_category: str = "") -> bool:
        """Ürün adında veya alt kategorisinde hariç tutulan kelime var mı kontrol eder."""
        combined = f"{normalize_tr(name)} {normalize_tr(sub_category)}"
        for kw in config.EXCLUDE_KEYWORDS:
            norm_kw = normalize_tr(kw)
            if norm_kw and norm_kw in combined:
                return True
        return False

    @staticmethod
    def is_compatible_gender_and_age(
        attrs: Dict[str, Any], name: str, target_key: str
    ) -> bool:
        """
        Çocuk ayakkabılarını (`integration_cocuk == '1'`) ve yanlış cinsiyet
        etiketli ürünleri eler (Unisex ürünler her iki kategoride de kabul edilir).
        """
        if str(attrs.get("integration_cocuk") or "0").strip() == "1":
            return False

        prod_gender = normalize_tr(
            str(attrs.get("integration_cinsiyet") or attrs.get("emarsys_cinsiyet") or "")
        )
        norm_name = normalize_tr(name)

        if "çocuk" in prod_gender or "bebek" in prod_gender or "cocuk" in prod_gender:
            return False

        # Erkek kategorisinde (45-46) sadece Kadın olarak işaretlenmiş ürünleri ele
        if target_key == "erkek":
            if prod_gender == "kadın" or prod_gender == "kadin":
                return False
            if " kadın " in f" {norm_name} " and "unisex" not in norm_name:
                return False

        # Kadın kategorisinde (39-40) sadece Erkek olarak işaretlenmiş ürünleri ele
        elif target_key == "kadin":
            if prod_gender == "erkek":
                return False
            if " erkek " in f" {norm_name} " and "unisex" not in norm_name:
                return False

        return True

    @staticmethod
    def is_allowed_brand(brand: str, name: str) -> bool:
        """
        ALLOWED_BRANDS boşsa tüm markaları kabul eder.
        Doluysa ürün markasının veya ürün adının izin verilen markalardan biriyle eşleşmesini şart koşar.
        """
        if not config.ALLOWED_BRANDS:
            return True
        norm_brand = normalize_tr(brand)
        norm_name = normalize_tr(name)
        for allowed in config.ALLOWED_BRANDS:
            norm_allowed = normalize_tr(allowed)
            if not norm_allowed:
                continue
            if norm_allowed == norm_brand or norm_allowed in norm_brand or norm_allowed in norm_name:
                return True
        return False

    @staticmethod
    def _extract_brand(raw_product: Dict[str, Any], attrs: Dict[str, Any], name: str) -> str:
        """
        Hem Marketplace (`integration_alt_marka`) hem de Eren Perakende (`attributes_kwargs.integration_marka.label`)
        ürünlerinden doğru marka adını çıkarır.
        """
        attrs_kwargs = raw_product.get("attributes_kwargs") or {}
        kw_brand = ""
        if isinstance(attrs_kwargs, dict):
            for key in ("integration_alt_marka", "integration_marka", "brand"):
                entry = attrs_kwargs.get(key)
                if isinstance(entry, dict) and entry.get("label"):
                    candidate = str(entry["label"]).strip()
                    if candidate and candidate != "-" and not candidate.isdigit():
                        kw_brand = candidate
                        break

        alt_marka = str(attrs.get("integration_alt_marka") or "").strip()
        if alt_marka and alt_marka != "-" and not alt_marka.isdigit():
            return alt_marka
        if kw_brand:
            return kw_brand
        if raw_product.get("brand"):
            return str(raw_product["brand"]).strip()
        return name.split()[0] if name else ""

    # ==========================================================================
    # ÜRÜN NORMALİZASYONU VE PARSE İŞLEMİ
    # ==========================================================================
    def parse_product(
        self, raw_product: Dict[str, Any], target_cfg: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Ham Akinon ürün JSON nesnesini doğrular, hayalet stok, çocuk/cinsiyet ve kelime/marka
        filtrelerinden geçirir ve standart ürün sözlüğüne dönüştürür.
        """
        if not isinstance(raw_product, dict):
            return None

        pk = raw_product.get("pk")
        sku = str(raw_product.get("sku") or "").strip()
        if not pk and not sku:
            return None

        name = str(raw_product.get("name") or "").strip()
        if not name:
            return None

        attrs = raw_product.get("attributes") or {}
        sub_category = str(attrs.get("cadir_kategori") or attrs.get("integration_tanim") or "").strip()
        brand = self._extract_brand(raw_product, attrs, name)

        target_key = str(target_cfg.get("key") or "genel")

        # 1. Hariç Tutulan Kelime Filtresi (terlik, sandalet, panduf, deniz, çorap, bakım, bağcık, çocuk, bebek)
        if self.is_excluded_by_keyword(name, sub_category):
            return None

        # 2. Çocuk ve Cinsiyet Uyumsuzluğu Filtresi
        if not self.is_compatible_gender_and_age(attrs, name, target_key):
            return None

        # 3. Marka Filtresi (ALLOWED_BRANDS)
        if not self.is_allowed_brand(brand, name):
            return None

        # 4. Hayalet Stok Koruması (Hedef numaralar gerçekten stokta mı?)
        target_sizes = target_cfg.get("sizes", [])
        (
            available_sizes,
            target_stock_count,
            variant_sale_price,
            variant_retail_price,
        ) = self._extract_in_stock_target_sizes(raw_product, target_sizes)
        if not available_sizes or target_stock_count <= 0:
            return None

        # 5. Fiyat ve Sepette Ekstra İndirim Hesaplaması
        top_sale_price = self._to_float(raw_product.get("price"))
        sale_price = variant_sale_price if (variant_sale_price and variant_sale_price > 0) else top_sale_price
        retail_price = (
            variant_retail_price
            if (variant_retail_price and variant_retail_price >= sale_price)
            else self._to_float(raw_product.get("retail_price"), default=sale_price)
        )
        if sale_price <= 0:
            return None
        if retail_price < sale_price:
            retail_price = sale_price

        basket_price, basket_offer_label = self._extract_basket_offer(raw_product, sale_price)
        effective_price = basket_price if (basket_price and 0 < basket_price < sale_price) else sale_price

        # Toplam İndirim Oranı (%)
        if retail_price > effective_price and retail_price > 0:
            discount_rate = round(((retail_price - effective_price) / retail_price) * 100.0, 1)
        else:
            discount_rate = round(self._to_float(raw_product.get("discount_ratio"), 0.0), 1)

        # Ürün URL ve Görsel URL
        abs_url = str(raw_product.get("absolute_url") or f"/product/{pk}/").strip()
        product_url = urljoin(self.base_url, abs_url)
        image_url = self._extract_image_url(raw_product)

        base_code = str(raw_product.get("base_code") or "").strip()
        listing_code = str(raw_product.get("listing_code") or "").strip()
        product_id = str(pk) if pk is not None else sku

        # Kategori bazlı benzersiz anahtar: Unisex bir ayakkabı hem Erkek (45-46) hem Kadın (39-40)
        # kategorisinde aynı pk ile yer aldığında birbirinin kaydını ezmesini önler.
        state_key = f"{target_key}_{product_id}"
        model_key = (
            f"{target_key}_{base_code}_{listing_code}"
            if (base_code and listing_code)
            else state_key
        )

        return {
            "id": product_id,
            "state_key": state_key,
            "pk": pk,
            "sku": sku,
            "model_key": model_key,
            "base_code": base_code,
            "listing_code": listing_code,
            "name": name,
            "brand": brand,
            "category": target_cfg["name"],
            "category_key": target_key,
            "category_badge": target_cfg["badge"],
            "available_sizes": available_sizes,
            "stock_count": target_stock_count,
            "retail_price": round(retail_price, 2),
            "sale_price": round(sale_price, 2),
            "basket_price": round(basket_price, 2) if basket_price else None,
            "basket_offer_label": basket_offer_label,
            "effective_price": round(effective_price, 2),
            "discount_rate": discount_rate,
            "product_url": product_url,
            "image_url": image_url,
            "max_price_limit": float(target_cfg.get("max_price", config.MAX_PRICE_LIMIT)),
        }

    # ==========================================================================
    # HTML FALLBACK PARSER (JSON Uç Noktası Yanıt Vermezse Devreye Girer)
    # ==========================================================================
    def _parse_html_fallback(self, html_text: str) -> List[Dict[str, Any]]:
        """Sayfa kaynağındaki gömülü JSON veya HTML kartlarından ürünleri çıkarır."""
        products: List[Dict[str, Any]] = []
        try:
            match = re.search(
                r'"products"\s*:\s*(\[\s*\{.*?\}\s*\])\s*,\s*"(?:facets|pagination|category)"',
                html_text,
                re.DOTALL,
            )
            if match:
                return json.loads(match.group(1))
        except Exception as exc:
            logger.debug("HTML gömülü JSON ayrıştırma hatası: %s", exc)

        try:
            from bs4 import BeautifulSoup  # type: ignore

            soup = BeautifulSoup(html_text, "html.parser")
            for item in soup.select("[data-product], .js-product-wrapper, .product-item"):
                raw_attr = item.get("data-product") or item.get("data-gtm-product")
                if raw_attr:
                    try:
                        products.append(json.loads(raw_attr))
                    except Exception:
                        pass
        except ImportError:
            pass
        return products

    # ==========================================================================
    # KATEGORİ TARAMA (TÜM SAYFALAR)
    # ==========================================================================
    def scrape_target(
        self, target_cfg: Dict[str, Any], max_pages: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Belirtilen hedef kategorinin (`Erkek` veya `Kadın`) tüm sayfalarını gezer,
        aktif stoklu ve filtrelere uygun ürünleri liste olarak döndürür.
        """
        category_name = target_cfg["name"]
        category_key = target_cfg["key"]
        category_url = target_cfg["url"]
        effective_max_pages = (
            max_pages if max_pages is not None else config.MAX_PAGES_PER_CATEGORY
        )

        logger.info(
            "[%s] Kategorisi taranıyor (Hedef Numaralar: %s)...",
            category_name,
            ", ".join(target_cfg.get("sizes", [])),
        )

        collected: Dict[str, Dict[str, Any]] = {}
        model_to_skey: Dict[str, str] = {}
        current_page = 1
        num_pages = 1
        raw_total_seen = 0
        had_page_error = False

        while current_page <= num_pages:
            page_url = self.build_json_page_url(category_url, page=current_page)
            raw_body = self.fetch_raw(page_url)
            if not raw_body:
                logger.warning("[%s] Sayfa %d alınamadı, tarama sonlandırılıyor.", category_name, current_page)
                had_page_error = True
                break

            raw_products: List[Dict[str, Any]] = []
            try:
                data = json.loads(raw_body)
                if isinstance(data, dict):
                    pagination = data.get("pagination") or {}
                    num_pages = int(pagination.get("num_pages", 1) or 1)
                    if effective_max_pages and effective_max_pages > 0:
                        num_pages = min(num_pages, effective_max_pages)
                    raw_products = data.get("products") or []
            except json.JSONDecodeError:
                logger.warning("[%s] Sayfa %d JSON formatında değil, HTML fallback deneniyor...", category_name, current_page)
                raw_products = self._parse_html_fallback(raw_body)

            if not raw_products:
                logger.info("[%s] Sayfa %d üzerinde ürün bulunamadı.", category_name, current_page)
                break

            raw_total_seen += len(raw_products)
            page_valid_count = 0

            for raw_prod in raw_products:
                try:
                    parsed = self.parse_product(raw_prod, target_cfg)
                except Exception as exc:  # Tek bir bozuk ürün kaydı tüm taramayı durdurmasın
                    logger.debug("[%s] Ürün ayrıştırma hatası (pk=%s): %s", category_name,
                                 raw_prod.get("pk") if isinstance(raw_prod, dict) else "?", exc)
                    parsed = None
                if parsed:
                    s_key = parsed["state_key"]
                    m_key = parsed.get("model_key") or s_key
                    existing_skey = s_key if s_key in collected else model_to_skey.get(m_key)
                    existing = collected.get(existing_skey) if existing_skey else None

                    if existing:
                        merged_sizes = sorted(
                            set(existing.get("available_sizes", [])) | set(parsed.get("available_sizes", []))
                        )
                        if parsed["effective_price"] < existing["effective_price"]:
                            parsed["available_sizes"] = merged_sizes
                            if existing_skey and existing_skey != s_key:
                                del collected[existing_skey]
                            collected[s_key] = parsed
                            model_to_skey[m_key] = s_key
                        else:
                            existing["available_sizes"] = merged_sizes
                    else:
                        collected[s_key] = parsed
                        model_to_skey[m_key] = s_key
                    page_valid_count += 1

            logger.info(
                "[%s] Sayfa %d/%d tamamlandı -> Ham ürün: %d | Stokta & Filtreye Uygun: %d",
                category_name,
                current_page,
                num_pages,
                len(raw_products),
                page_valid_count,
            )

            current_page += 1
            if current_page <= num_pages and self.page_delay > 0:
                time.sleep(self.page_delay)

        if not had_page_error and raw_total_seen > 0 and (not effective_max_pages or effective_max_pages == 0):
            self.completed_categories.add(category_key)

        results = list(collected.values())
        logger.info(
            "[%s] Tarama tamamlandı. Toplam incelenen: %d | Geçerli & Stokta ürün: %d",
            category_name,
            raw_total_seen,
            len(results),
        )
        return results

    def scrape_all_targets(self, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        """`config.TARGETS` içindeki tüm hedef grupları sırasıyla tarar."""
        self.completed_categories.clear()
        all_products: List[Dict[str, Any]] = []
        for target in config.TARGETS:
            target_products = self.scrape_target(target, max_pages=max_pages)
            all_products.extend(target_products)
        return all_products
