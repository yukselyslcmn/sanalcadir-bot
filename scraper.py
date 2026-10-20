"""
Sanal Çadır (sanalcadir.com - Akinon) Veri Çekme (Scraper) Modülü (scraper.py)

Özellikler:
- Akinon `?format=json` uç noktasından sayfalama (pagination) desteğiyle tüm ürünleri çeker.
- WAF/Cloudflare engeline karşı `requests` -> `curl_cffi` -> `cloudscraper` -> `playwright`
  katmanlı (fallback) esnek mimari sunar.
- Hayalet Stok Koruması: `extra_data.variants` içindeki beden bazlı gerçek stokları (`in_stock` ve `stock > 0`)
  kontrol ederek sadece hedef numaraları (Erkek: 45, 45.5 | Kadın: 39, 40) gerçekten stokta olan ürünleri alır.
- Sepette Ekstra İndirim Algılama: `retail_price`, `price` ve `basket_offers` (`discounted_total_price`)
  verilerini ayrıştırarak gerçek net fiyatı hesaplar.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

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

        # Standart requests oturumu
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

        # WAF / Cloudflare aşımı için opsiyonel istemciler (lazy-init)
        self._curl_session: Any = None
        self._cloudscraper_session: Any = None

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

        # Mevcut format ve page parametrelerini temizle (yeniden eklenecek)
        filtered_pairs: List[Tuple[str, str]] = []
        seen_sizes = set()

        for k, v in query_pairs:
            if k.lower() in ("format", "page"):
                continue
            filtered_pairs.append((k, v))
            if k == "attributes_filterable_size":
                seen_sizes.add(v)

        # Eğer kullanıcı URL'de "45.5" yazdıysa Akinon "45,5" beklediği için virgüllü karşılığını da ekle
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
        # "1.949,35 TL" vs "1949.35" ayrımı
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

    def _extract_basket_offer(self, raw_product: Dict[str, Any], sale_price: float) -> Tuple[Optional[float], Optional[str]]:
        """
        Akinon ürün JSON'unda yer alan 'Sepette Ekstra İndirim' (`basket_offers`,
        `promotions`, `basket_price`) alanlarını ayrıştırır.
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
                if discounted_total is not None:
                    candidate = self._to_float(discounted_total)
                    if 0 < candidate < sale_price and (basket_price is None or candidate < basket_price):
                        basket_price = round(candidate, 2)
                        offer_label = str(label).strip() if label else "Sepette Ekstra İndirim"
                elif label:
                    # Etikette "%XX" varsa ve listing_kwargs boşsa yüzdeden hesapla
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
    ) -> Tuple[List[str], int]:
        """
        HAYALET STOK KORUMASI:
        Ürünün `extra_data.variants` (integration_beden) altındaki beden seçeneklerini
        kontrol ederek, hedef numaralardan (`target_sizes`) hangilerinin GERÇEKTEN stokta
        (`in_stock: True` ve `stock > 0`) olduğunu tespit eder.
        """
        normalized_targets = {self._normalize_size(s) for s in target_sizes if s}
        available_sizes: List[str] = []
        total_target_stock: int = 0

        extra_data = raw_product.get("extra_data") or {}
        variants = extra_data.get("variants") if isinstance(extra_data, dict) else None

        has_beden_variants = False
        if isinstance(variants, list):
            for var_group in variants:
                if not isinstance(var_group, dict):
                    continue
                attr_key = var_group.get("attribute_key", "")
                if attr_key in ("integration_beden", "filterable_size", "size", "beden"):
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
                        sub_stock_qty = int(sub_prod.get("stock", 1 if sub_in_stock else 0) or 0)

                        if not (opt_in_stock and opt_selectable and sub_in_stock and sub_stock_qty > 0):
                            continue

                        attrs = sub_prod.get("attributes") or {}
                        raw_size = (
                            attrs.get("filterable_size")
                            or attrs.get("integration_trBeden")
                            or opt.get("label")
                            or ""
                        )
                        norm_size = self._normalize_size(raw_size)

                        if norm_size in normalized_targets:
                            if norm_size not in available_sizes:
                                available_sizes.append(norm_size)
                            total_target_stock += sub_stock_qty

        # Eğer ürün JSON'unda `extra_data.variants` yer alıyorsa sadece hedef numarası
        # aktif stokta olanları kabul et (Hayalet stokları kesin olarak ele)
        if has_beden_variants:
            return sorted(available_sizes), total_target_stock

        # Eğer `extra_data.variants` yoksa üst düzey ürünün stok ve beden bilgisine bak
        top_in_stock = bool(raw_product.get("in_stock", False))
        top_stock = int(raw_product.get("stock", 1 if top_in_stock else 0) or 0)
        if not (top_in_stock and top_stock > 0):
            return [], 0

        attrs = raw_product.get("attributes") or {}
        top_size = self._normalize_size(
            attrs.get("filterable_size") or attrs.get("integration_trBeden") or ""
        )
        if top_size and top_size in normalized_targets:
            return [top_size], top_stock
        elif not top_size:
            # Kategori zaten beden filtreli çekildiği için ve üst stok aktifse
            return sorted(list(normalized_targets)), top_stock

        return [], 0

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
    # FİLTRELEME KONTROLLERİ (Kelime, Marka, Fiyat)
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

    # ==========================================================================
    # ÜRÜN NORMALİZASYONU VE PARSE İŞLEMİ
    # ==========================================================================
    def parse_product(
        self, raw_product: Dict[str, Any], target_cfg: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Ham Akinon ürün JSON nesnesini doğrular, hayalet stok ve kelime/marka filtrelerinden
        geçirir ve standart ürün sözlüğüne dönüştürür.
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
        brand = str(
            attrs.get("integration_alt_marka")
            or raw_product.get("brand")
            or name.split()[0]
        ).strip()

        # 1. Hariç Tutulan Kelime Filtresi (terlik, sandalet, panduf, deniz, çorap, bakım, bağcık)
        if self.is_excluded_by_keyword(name, sub_category):
            return None

        # 2. Marka Filtresi (ALLOWED_BRANDS)
        if not self.is_allowed_brand(brand, name):
            return None

        # 3. Hayalet Stok Koruması (Hedef numaralar gerçekten stokta mı?)
        target_sizes = target_cfg.get("sizes", [])
        available_sizes, target_stock_count = self._extract_in_stock_target_sizes(
            raw_product, target_sizes
        )
        if not available_sizes or target_stock_count <= 0:
            return None

        # 4. Fiyat ve Sepette Ekstra İndirim Hesaplaması
        sale_price = self._to_float(raw_product.get("price"))
        retail_price = self._to_float(raw_product.get("retail_price"), default=sale_price)
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
        model_key = (
            f"{target_cfg['key']}_{base_code}_{listing_code}"
            if (base_code and listing_code)
            else f"{target_cfg['key']}_{product_id}"
        )

        return {
            "id": product_id,
            "pk": pk,
            "sku": sku,
            "model_key": model_key,
            "base_code": base_code,
            "listing_code": listing_code,
            "name": name,
            "brand": brand,
            "category": target_cfg["name"],
            "category_key": target_cfg["key"],
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
            # 1. Gömülü JSON değişkeni kontrolü
            match = re.search(r'"products"\s*:\s*(\[\s*\{.*?\}\s*\])\s*,\s*"(?:facets|pagination|category)"', html_text, re.DOTALL)
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
        current_page = 1
        num_pages = 1
        raw_total_seen = 0

        while current_page <= num_pages:
            page_url = self.build_json_page_url(category_url, page=current_page)
            raw_body = self.fetch_raw(page_url)
            if not raw_body:
                logger.warning("[%s] Sayfa %d alınamadı, tarama sonlandırılıyor.", category_name, current_page)
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
                parsed = self.parse_product(raw_prod, target_cfg)
                if parsed:
                    # Aynı model_key tekrar gelirse en düşük fiyatlı olanı tut
                    uid = parsed["id"]
                    existing = collected.get(uid)
                    if not existing or parsed["effective_price"] < existing["effective_price"]:
                        collected[uid] = parsed
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
        all_products: List[Dict[str, Any]] = []
        for target in config.TARGETS:
            target_products = self.scrape_target(target, max_pages=max_pages)
            all_products.extend(target_products)
        return all_products
