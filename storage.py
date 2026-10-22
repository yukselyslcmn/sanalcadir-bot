"""
Sanal Çadır (sanalcadir.com) Durum Takip ve Fiyat Karşılaştırma Modülü (storage.py)

Görevleri:
- `data/products_state.json` dosyasını okumak ve atomik/güvenli şekilde güncellemek.
- İlk çalıştırma spam korumasını (`NOTIFY_ON_FIRST_RUN`) yönetmek.
- Unisex ürünlerin Erkek ve Kadın kategorilerinde birbirini ezmesini önleyen `state_key`
  (`erkek_<pk>`, `kadin_<pk>`) mimarisini ve eski kayıtlardan kesintisiz geçişi yönetmek.
- Her ürünün bugüne kadar gördüğü en düşük fiyatı (`lowest_price`) takip ederek
  "🏆 Şimdiye Kadarki En Düşük Fiyat!" durumunu tespit etmek.
- Gerçek fiyat düşüşlerini (Durum 1: En az %5 ekstra indirim), yeni stok girişlerini (Durum 2)
  ve tükenip tekrar stoğa giren ürünleri (Durum 3: 🔄 TEKRAR STOKTA!) tespit etmek.
- Mükerrer bildirim koruması (`ALERT_COOLDOWN_HOURS`), gönderilemeyen bildirimlerin geri alınıp
  bir sonraki taramada yeniden denenmesi (`revert_alert`), site arızasında toplu sahte "stok tükendi"
  işaretlemesini önleyen güvenlik eşiği (`MIN_SCAN_COMPLETENESS`) ve eski kayıt temizliği (`STALE_RECORD_DAYS`).

Not: `process_scraped_products` artık dosyaya otomatik yazmaz; Telegram gönderiminden sonra
`main.py` tarafından `save_state()` çağrılır. Böylece gönderilemeyen bildirimler kaybolmaz.
"""

import copy
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import config

logger = logging.getLogger(__name__)

# Bildirim Türü Sabitleri
EVENT_PRICE_DROP = "PRICE_DROP"
EVENT_NEW_STOCK = "NEW_STOCK"
EVENT_BACK_IN_STOCK = "BACK_IN_STOCK"

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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
        """Güncel ürün durumlarını `data/products_state.json` dosyasına atomik olarak yazar."""
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

    # ------------------------------------------------------------------
    # Zaman yardımcıları
    # ------------------------------------------------------------------
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _now_iso(cls) -> str:
        return cls._now().strftime(ISO_FORMAT)

    @staticmethod
    def _parse_iso(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(str(value), ISO_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Kayıt oluşturma
    # ------------------------------------------------------------------
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

        state_key = str(product.get("state_key") or f"{product.get('category_key', 'genel')}_{product['id']}")

        # NOT: Her satışta değişen anlık stok adedi (stock_count) bilinçli olarak kaydedilmez.
        # Mantıkta kullanılmıyordu ve her 5 dakikada bir binlerce satırlık gereksiz git commit'ine yol açıyordu.
        record: Dict[str, Any] = {
            "id": str(product["id"]),
            "state_key": state_key,
            "pk": product.get("pk"),
            "sku": product.get("sku", ""),
            "model_key": product.get("model_key", ""),
            "name": product["name"],
            "brand": product.get("brand", ""),
            "category": product["category"],
            "category_key": product.get("category_key", ""),
            "category_badge": product.get("category_badge", product["category"]),
            "available_sizes": product.get("available_sizes", []),
            "in_stock": True,
            "missing_scans": 0,
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

        # Son bildirim bilgisi (mükerrer bildirim koruması için) önceki kayıttan devralınır
        if previous_record:
            for key in ("last_alert_price", "last_alert_at"):
                if previous_record.get(key) is not None:
                    record[key] = previous_record[key]

        # Ürün verisinde gerçek bir değişim yoksa gereksiz git commit oluşmaması için last_updated koru
        if previous_record:
            unchanged = all(
                record.get(k) == previous_record.get(k)
                for k in record
                if k != "last_updated"
            )
            if unchanged and previous_record.get("last_updated"):
                record["last_updated"] = previous_record["last_updated"]

        return record

    # ------------------------------------------------------------------
    # Mükerrer bildirim koruması
    # ------------------------------------------------------------------
    def _in_cooldown(self, previous_record: Optional[Dict[str, Any]], new_price: float) -> bool:
        """
        Ürün son `ALERT_COOLDOWN_HOURS` saat içinde aynı veya daha düşük bir fiyatla zaten bildirildiyse True.
        (Daha düşük yeni bir fiyat her zaman bildirilir.)
        """
        if not previous_record or config.ALERT_COOLDOWN_HOURS <= 0:
            return False
        last_alert_at = self._parse_iso(previous_record.get("last_alert_at"))
        last_alert_price = previous_record.get("last_alert_price")
        if last_alert_at is None or last_alert_price is None:
            return False
        if self._now() - last_alert_at > timedelta(hours=config.ALERT_COOLDOWN_HOURS):
            return False
        return new_price >= float(last_alert_price) - 0.01

    # ------------------------------------------------------------------
    # Gönderilemeyen bildirimi geri alma
    # ------------------------------------------------------------------
    def revert_alert(self, alert: Dict[str, Any]) -> None:
        """
        Telegram'a gönderilemeyen bir bildirimin durum değişikliğini geri alır.
        Böylece ürün bir sonraki taramada tekrar "fırsat" olarak algılanır ve bildirim kaybolmaz.
        """
        s_key = alert.get("_state_key")
        if not s_key:
            return
        prev_record = alert.get("_prev_record")
        prev_key = alert.get("_prev_key")
        if prev_record is None:
            self.state.pop(s_key, None)
        else:
            if prev_key and prev_key != s_key:
                self.state.pop(s_key, None)
                self.state[prev_key] = prev_record
            else:
                self.state[s_key] = prev_record

    # ------------------------------------------------------------------
    # Ana karşılaştırma akışı
    # ------------------------------------------------------------------
    def process_scraped_products(
        self,
        scraped_products: List[Dict[str, Any]],
        completed_categories: Optional[Set[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """
        Taranan ürünleri mevcut `products_state.json` ile karşılaştırır.
        (Dosyaya yazmaz; gönderimden sonra `save_state()` çağrılmalıdır.)

        Dönüş:
            (notifications, stats)
        """
        is_first_run = len(self.state) == 0
        notifications: List[Dict[str, Any]] = []
        stats = {
            "scraped_count": len(scraped_products),
            "new_stock_alerts": 0,
            "price_drop_alerts": 0,
            "silent_first_run_saved": 0,
            "suppressed_duplicates": 0,
            "marked_out_of_stock": 0,
            "pruned_stale": 0,
            "total_tracked": 0,
        }

        # İkincil indeksler (model_key ve eski şema id uyumluluğu için)
        model_key_index: Dict[str, str] = {}
        legacy_id_records: Dict[str, Dict[str, Any]] = {}
        legacy_keys_to_clean: Set[str] = set()
        tracked_in_stock_by_cat: Dict[str, int] = {}

        for rec_key, rec in self.state.items():
            m_key = rec.get("model_key")
            if m_key:
                model_key_index[m_key] = rec_key
            raw_id = str(rec.get("id") or rec_key)
            if "_" not in rec_key:
                legacy_id_records[raw_id] = rec
                legacy_keys_to_clean.add(rec_key)
            if rec.get("in_stock", True):
                cat = rec.get("category_key") or ("erkek" if rec.get("category") == "Erkek" else "kadin")
                tracked_in_stock_by_cat[cat] = tracked_in_stock_by_cat.get(cat, 0) + 1

        # İlk Çalıştırma Koruması (Spam Önleme)
        if is_first_run and not config.NOTIFY_ON_FIRST_RUN:
            logger.info(
                "İlk çalıştırma tespit edildi (products_state.json boş ve NOTIFY_ON_FIRST_RUN=False). "
                "%d ürün bildirim gönderilmeden sessizce veritabanına kaydediliyor...",
                len(scraped_products),
            )
            for prod in scraped_products:
                s_key = str(prod.get("state_key") or f"{prod.get('category_key', 'genel')}_{prod['id']}")
                self.state[s_key] = self._build_record(prod)
                stats["silent_first_run_saved"] += 1

            stats["total_tracked"] = len(self.state)
            return [], stats

        seen_state_keys: Set[str] = set()
        seen_by_cat: Dict[str, int] = {}
        now_iso = self._now_iso()

        # Normal Karşılaştırma Akışı
        for prod in scraped_products:
            uid = str(prod["id"])
            s_key = str(prod.get("state_key") or f"{prod.get('category_key', 'genel')}_{uid}")
            m_key = prod.get("model_key", "")
            seen_state_keys.add(s_key)
            cat_key_seen = str(prod.get("category_key") or "")
            seen_by_cat[cat_key_seen] = seen_by_cat.get(cat_key_seen, 0) + 1

            # 1. Doğrudan state_key ile ara
            # 2. Bulunamazsa model_key ile ara
            # 3. Bulunamazsa eski şema (kategori öneksiz pk) kaydından miras al
            existing_key: Optional[str] = None
            existing_rec: Optional[Dict[str, Any]] = None

            if s_key in self.state:
                existing_key = s_key
                existing_rec = self.state[s_key]
            elif m_key and m_key in model_key_index:
                existing_key = model_key_index[m_key]
                existing_rec = self.state.get(existing_key)
            elif uid in legacy_id_records:
                existing_rec = legacy_id_records[uid]

            new_effective_price = float(prod["effective_price"])
            max_price_limit = float(prod.get("max_price_limit", config.MAX_PRICE_LIMIT))
            discount_rate = float(prod.get("discount_rate", 0.0))
            alert_item: Optional[Dict[str, Any]] = None

            if existing_rec is not None:
                old_price = float(existing_rec.get("last_price", new_effective_price))
                prev_lowest = float(existing_rec.get("lowest_price", old_price))
                was_in_stock = bool(existing_rec.get("in_stock", True))

                if old_price > 0 and new_effective_price < old_price:
                    drop_ratio = ((old_price - new_effective_price) / old_price) * 100.0
                else:
                    drop_ratio = 0.0

                # ==============================================================
                # DURUM 1: KAYITLI ÜRÜNDE EKSTRA FİYAT DÜŞÜŞÜ (%5 ve üzeri)
                # ==============================================================
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

                # ==============================================================
                # DURUM 3: DAHA ÖNCE STOĞU TÜKENMİŞ ÜRÜN TEKRAR STOĞA GİRDİYSE
                # ==============================================================
                elif (
                    not was_in_stock
                    and new_effective_price <= max_price_limit
                    and discount_rate >= config.MIN_DISCOUNT_RATE
                ):
                    is_all_time_low = new_effective_price <= prev_lowest
                    alert_item = {
                        "event_type": EVENT_BACK_IN_STOCK,
                        "product": prod,
                        "old_price": round(float(prod["retail_price"]), 2),
                        "new_price": round(new_effective_price, 2),
                        "extra_drop_rate": round(discount_rate, 1),
                        "is_all_time_low": is_all_time_low,
                    }

                # Mükerrer bildirim koruması (fiyat dalgalanması / kampanya aç-kapa / stok gidip gelme)
                if alert_item is not None and self._in_cooldown(existing_rec, new_effective_price):
                    stats["suppressed_duplicates"] += 1
                    logger.info(
                        "⏸️ Mükerrer bildirim engellendi (son %.0f saatte aynı/daha düşük fiyatla bildirildi): [%s] %s | %.2f TL",
                        config.ALERT_COOLDOWN_HOURS,
                        prod["category"],
                        prod["name"],
                        new_effective_price,
                    )
                    alert_item = None

                prev_snapshot = copy.deepcopy(existing_rec)
                if existing_key and existing_key != s_key and existing_key in self.state:
                    del self.state[existing_key]
                new_record = self._build_record(prod, previous_record=existing_rec)
                if m_key:
                    model_key_index[m_key] = s_key

                if alert_item is not None:
                    new_record["last_alert_price"] = round(new_effective_price, 2)
                    new_record["last_alert_at"] = now_iso
                    alert_item["_state_key"] = s_key
                    alert_item["_prev_record"] = prev_snapshot
                    alert_item["_prev_key"] = existing_key
                    notifications.append(alert_item)
                    if alert_item["event_type"] == EVENT_PRICE_DROP:
                        stats["price_drop_alerts"] += 1
                        logger.info(
                            "🔥 EKSTRA FİYAT DÜŞÜŞÜ: [%s] %s | %.2f TL -> %.2f TL (%%%.1f düşüş)%s",
                            prod["category"],
                            prod["name"],
                            alert_item["old_price"],
                            new_effective_price,
                            alert_item["extra_drop_rate"],
                            " [🏆 EN DÜŞÜK FİYAT!]" if alert_item["is_all_time_low"] else "",
                        )
                    else:
                        stats["new_stock_alerts"] += 1
                        logger.info(
                            "🔄 TEKRAR STOKTA: [%s] %s | Numara: %s | Net Fiyat: %.2f TL (%%%.1f İndirim)",
                            prod["category"],
                            prod["name"],
                            ", ".join(prod.get("available_sizes", [])),
                            new_effective_price,
                            discount_rate,
                        )

                self.state[s_key] = new_record

            else:
                # ==============================================================
                # DURUM 2: YENİ STOK / NUMARA GİRİŞİ KONTROLÜ
                # ==============================================================
                meets_price_limit = new_effective_price <= max_price_limit
                meets_min_discount = discount_rate >= config.MIN_DISCOUNT_RATE
                new_record = self._build_record(prod)

                if meets_price_limit and meets_min_discount:
                    alert_item = {
                        "event_type": EVENT_NEW_STOCK,
                        "product": prod,
                        "old_price": round(float(prod["retail_price"]), 2),
                        "new_price": round(new_effective_price, 2),
                        "extra_drop_rate": round(discount_rate, 1),
                        "is_all_time_low": False,
                        "_state_key": s_key,
                        "_prev_record": None,
                        "_prev_key": None,
                    }
                    new_record["last_alert_price"] = round(new_effective_price, 2)
                    new_record["last_alert_at"] = now_iso
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

                self.state[s_key] = new_record
                if m_key:
                    model_key_index[m_key] = s_key

        # Eski şema (kategori öneksiz) anahtarları temizle
        for old_k in legacy_keys_to_clean:
            if old_k in self.state:
                del self.state[old_k]

        # Eksiksiz taranan kategorilerde stoktan düşen ürünleri takip et
        if completed_categories:
            safe_categories: Set[str] = set()
            for cat in completed_categories:
                tracked = tracked_in_stock_by_cat.get(cat, 0)
                seen = seen_by_cat.get(cat, 0)
                if tracked >= 20 and seen < tracked * config.MIN_SCAN_COMPLETENESS:
                    logger.warning(
                        "⚠️ [%s] Güvenlik eşiği: Bu taramada %d ürün bulundu, takipte %d stoklu ürün var "
                        "(< %%%.0f). Site/API arızası ihtimaline karşı hiçbir ürün 'stok tükendi' işaretlenmedi.",
                        cat, seen, tracked, config.MIN_SCAN_COMPLETENESS * 100,
                    )
                    continue
                safe_categories.add(cat)

            now_iso_oos = self._now_iso()
            for rec_key, rec in list(self.state.items()):
                if rec_key in seen_state_keys:
                    continue
                cat_key = rec.get("category_key") or (
                    "erkek" if rec.get("category") == "Erkek" else "kadin"
                )
                if cat_key in safe_categories:
                    current_missing = int(rec.get("missing_scans", 0) or 0)
                    if rec.get("in_stock", True) or current_missing < config.OUT_OF_STOCK_CONFIRM_SCANS:
                        missing = current_missing + 1
                        rec["missing_scans"] = missing
                        if missing >= config.OUT_OF_STOCK_CONFIRM_SCANS and rec.get("in_stock", True):
                            rec["in_stock"] = False
                            rec["last_updated"] = now_iso_oos
                            stats["marked_out_of_stock"] += 1

        # Uzun süredir stoğu olmayan eski kayıtları temizle (dosya şişmesini önler)
        if config.STALE_RECORD_DAYS > 0:
            cutoff = self._now() - timedelta(days=config.STALE_RECORD_DAYS)
            for rec_key, rec in list(self.state.items()):
                if rec.get("in_stock", True):
                    continue
                last_upd = self._parse_iso(rec.get("last_updated"))
                if last_upd is not None and last_upd < cutoff:
                    del self.state[rec_key]
                    stats["pruned_stale"] += 1

        # Bildirimleri öncelik (PRICE_DROP -> BACK_IN_STOCK -> NEW_STOCK) ve indirim oranına göre sırala
        priority_map = {
            EVENT_PRICE_DROP: 0,
            EVENT_BACK_IN_STOCK: 1,
            EVENT_NEW_STOCK: 2,
        }
        notifications.sort(
            key=lambda x: (
                priority_map.get(x.get("event_type", ""), 3),
                -float(x.get("extra_drop_rate", 0.0)),
            )
        )

        stats["total_tracked"] = len(self.state)
        return notifications, stats
