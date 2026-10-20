"""
Sanal Çadır (sanalcadir.com) Durum Takip ve Fiyat Karşılaştırma Modülü (storage.py)

Görevleri:
- `data/products_state.json` dosyasını okumak ve atomik/güvenli şekilde güncellemek.
- İlk çalıştırma spam korumasını (`NOTIFY_ON_FIRST_RUN`) yönetmek.
- Her ürünün bugüne kadar gördüğü en düşük fiyatı (`lowest_price`) takip ederek
  "🏆 Şimdiye Kadarki En Düşük Fiyat!" durumunu tespit etmek.
- Gerçek fiyat düşüşlerini (Durum 1: En az %5 ekstra indirim) ve
  yeni stok girişlerini (Durum 2: İlk kez stoğa giren ve kriterleri sağlayan ürünler) tespit etmek.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

# Bildirim Türü Sabitleri
EVENT_PRICE_DROP = "PRICE_DROP"
EVENT_NEW_STOCK = "NEW_STOCK"


class ProductStorage:
    """Ürün durumlarını JSON dosyasında saklayan ve fiyat/stok değişimlerini analiz eden sınıf."""

    def __init__(self, state_file: Path = config.STATE_FILE_PATH) -> None:
        self.state_file: Path = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state: Dict[str, Dict[str, Any]] = self.load_state()

    def load_state(self) -> Dict[str, Dict[str, Any]]:
        """`data/products_state.json` dosyasını okur. Dosya yoksa veya bozuksa boş sözlük döner."""
        if not self.state_file.exists():
            return {}
        try:
            content = self.state_file.read_text(encoding="utf-8").strip()
            if not content:
                return {}
            data = json.loads(content)
            if isinstance(data, dict):
                return data
            return {}
        except Exception as exc:
            logger.error("Durum dosyası okunurken hata oluştu (%s): %s", self.state_file, exc)
            return {}

    def save_state(self) -> None:
        """Güncel ürün durumlarını `data/products_state.json` dosyasına güvenli şekilde yazar."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.state_file.with_suffix(".json.tmp")
            temp_file.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temp_file.replace(self.state_file)
            logger.info(
                "Durum dosyası güncellendi: %s (Toplam kayıtlı ürün: %d)",
                self.state_file,
                len(self.state),
            )
        except Exception as exc:
            logger.error("Durum dosyası kaydedilirken hata oluştu: %s", exc)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _build_record(
        self, product: Dict[str, Any], previous_record: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """`products_state.json` içine kaydedilecek standart ürün kaydını oluşturur."""
        now_str = self._now_iso()
        first_seen = (
            previous_record.get("first_seen", now_str) if previous_record else now_str
        )
        current_effective = float(product["effective_price"])

        # En düşük fiyat (lowest_price) takibi
        if previous_record and previous_record.get("lowest_price") is not None:
            prev_lowest = float(previous_record["lowest_price"])
            lowest_price = round(min(prev_lowest, current_effective), 2)
        elif previous_record and previous_record.get("last_price") is not None:
            prev_last = float(previous_record["last_price"])
            lowest_price = round(min(prev_last, current_effective), 2)
        else:
            lowest_price = round(current_effective, 2)

        return {
            "id": str(product["id"]),
            "pk": product.get("pk"),
            "sku": product.get("sku", ""),
            "model_key": product.get("model_key", ""),
            "name": product["name"],
            "brand": product.get("brand", ""),
            "category": product["category"],
            "category_badge": product.get("category_badge", product["category"]),
            "available_sizes": product.get("available_sizes", []),
            "stock_count": int(product.get("stock_count", 1) or 1),
            "retail_price": product["retail_price"],
            "sale_price": product["sale_price"],
            "basket_price": product.get("basket_price"),
            "basket_offer_label": product.get("basket_offer_label"),
            "last_price": current_effective,
            "lowest_price": lowest_price,
            "discount_rate": product["discount_rate"],
            "image_url": product.get("image_url", ""),
            "product_url": product["product_url"],
            "first_seen": first_seen,
            "last_updated": now_str,
        }

    def process_scraped_products(
        self, scraped_products: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """
        Taranan ürünleri mevcut `products_state.json` ile karşılaştırır.

        Dönüş:
            (notifications, stats)
            - notifications: Bildirim gönderilecek olay listesi
            - stats: İstatistik özeti
        """
        is_first_run = len(self.state) == 0
        notifications: List[Dict[str, Any]] = []
        stats = {
            "scraped_count": len(scraped_products),
            "new_stock_alerts": 0,
            "price_drop_alerts": 0,
            "silent_first_run_saved": 0,
            "total_tracked": 0,
        }

        # Model anahtarı (kategori + base_code + listing_code) üzerinden ikincil indeks:
        model_key_index: Dict[str, str] = {}
        for rec_id, rec in self.state.items():
            m_key = rec.get("model_key")
            if m_key:
                model_key_index[m_key] = rec_id

        # İlk Çalıştırma Koruması (Spam Önleme)
        if is_first_run and not config.NOTIFY_ON_FIRST_RUN:
            logger.info(
                "İlk çalıştırma tespit edildi (products_state.json boş ve NOTIFY_ON_FIRST_RUN=False). "
                "%d ürün bildirim gönderilmeden sessizce veritabanına kaydediliyor...",
                len(scraped_products),
            )
            for prod in scraped_products:
                uid = str(prod["id"])
                self.state[uid] = self._build_record(prod)
                stats["silent_first_run_saved"] += 1

            self.save_state()
            stats["total_tracked"] = len(self.state)
            return [], stats

        # Normal Karşılaştırma Akışı
        for prod in scraped_products:
            uid = str(prod["id"])
            m_key = prod.get("model_key", "")

            existing_id = uid if uid in self.state else model_key_index.get(m_key)
            existing_rec = self.state.get(existing_id) if existing_id else None

            new_effective_price = float(prod["effective_price"])
            max_price_limit = float(prod.get("max_price_limit", config.MAX_PRICE_LIMIT))
            discount_rate = float(prod.get("discount_rate", 0.0))

            if existing_rec is not None:
                # ==============================================================
                # DURUM 1: ÜRÜN ZATEN KAYITLI -> EKSTRA FİYAT DÜŞÜŞÜ KONTROLÜ
                # ==============================================================
                old_price = float(existing_rec.get("last_price", new_effective_price))
                prev_lowest = float(existing_rec.get("lowest_price", old_price))

                if old_price > 0 and new_effective_price < old_price:
                    drop_ratio = ((old_price - new_effective_price) / old_price) * 100.0
                else:
                    drop_ratio = 0.0

                # Kayıtlı fiyattan en az %5 (PRICE_DROP_THRESHOLD) düştüyse VE tavan fiyat altındaysa
                if (
                    drop_ratio >= config.PRICE_DROP_THRESHOLD
                    and new_effective_price <= max_price_limit
                ):
                    is_all_time_low = new_effective_price <= prev_lowest
                    alert_item = {
                        "event_type": EVENT_PRICE_DROP,
                        "product": prod,
                        "old_price": round(old_price, 2),
                        "new_price": round(new_effective_price, 2),
                        "extra_drop_rate": round(drop_ratio, 1),
                        "is_all_time_low": is_all_time_low,
                        "previous_lowest_price": round(prev_lowest, 2),
                    }
                    notifications.append(alert_item)
                    stats["price_drop_alerts"] += 1
                    logger.info(
                        "🔥 EKSTRA FİYAT DÜŞÜŞÜ: [%s] %s | %.2f TL -> %.2f TL (%%%.1f düşüş)%s",
                        prod["category"],
                        prod["name"],
                        old_price,
                        new_effective_price,
                        drop_ratio,
                        " [🏆 EN DÜŞÜK FİYAT!]" if is_all_time_low else "",
                    )

                if existing_id and existing_id != uid and existing_id in self.state:
                    del self.state[existing_id]
                self.state[uid] = self._build_record(prod, previous_record=existing_rec)
                if m_key:
                    model_key_index[m_key] = uid

            else:
                # ==============================================================
                # DURUM 2: YENİ STOK / NUMARA GİRİŞİ KONTROLÜ
                # ==============================================================
                meets_price_limit = new_effective_price <= max_price_limit
                meets_min_discount = discount_rate >= config.MIN_DISCOUNT_RATE

                if meets_price_limit and meets_min_discount:
                    alert_item = {
                        "event_type": EVENT_NEW_STOCK,
                        "product": prod,
                        "old_price": round(float(prod["retail_price"]), 2),
                        "new_price": round(new_effective_price, 2),
                        "extra_drop_rate": round(discount_rate, 1),
                        "is_all_time_low": False,
                    }
                    notifications.append(alert_item)
                    stats["new_stock_alerts"] += 1
                    logger.info(
                        "🆕 YENİ STOK GİRİŞİ: [%s] %s | Numara: %s | Net Fiyat: %.2f TL (%%%.1f İndirim)",
                        prod["category"],
                        prod["name"],
                        ", ".join(prod.get("available_sizes", [])),
                        new_effective_price,
                        discount_rate,
                    )

                self.state[uid] = self._build_record(prod)
                if m_key:
                    model_key_index[m_key] = uid

        self.save_state()
        stats["total_tracked"] = len(self.state)
        return notifications, stats
