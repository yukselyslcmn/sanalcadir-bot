"""
Sanal Çadır Botu Kapsamlı Test Paketi (test_suite.py)
Tüm sistem bileşenlerini (config, scraper, storage, notifier ve canlı bağlantı) test eder.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Proje dizinini sys.path'e ekle
PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import config
from scraper import SanalCadirScraper
from storage import ProductStorage, EVENT_PRICE_DROP, EVENT_NEW_STOCK, EVENT_BACK_IN_STOCK
from notifier import TelegramNotifier


class TestConfig(unittest.TestCase):
    def test_normalize_tr(self):
        self.assertEqual(config.normalize_tr("İSTANBUL"), "istanbul")
        self.assertEqual(config.normalize_tr("IŞIK"), "ışık")
        self.assertEqual(config.normalize_tr("ÇOCUK"), "çocuk")
        self.assertEqual(config.normalize_tr("Ördek"), "ördek")

    def test_parse_sizes_list(self):
        # Buçuklu ve tireli beden ayrıştırması
        parsed = config._parse_sizes_list("45, 45.5, 45-46, 46", [])
        self.assertEqual(parsed, ["45", "45.5", "45-46", "46"])

        # Virgüllü buçuklu yazım (39,5) tek parça kalmalı, 39 ve 5 olarak bölünmemeli
        parsed_comma = config._parse_sizes_list("39, 39,5, 39-40, 40", [])
        self.assertEqual(parsed_comma, ["39", "39.5", "39-40", "40"])

    def test_targets_structure(self):
        self.assertEqual(len(config.TARGETS), 2)
        erkek = next(t for t in config.TARGETS if t["key"] == "erkek")
        kadin = next(t for t in config.TARGETS if t["key"] == "kadin")
        self.assertIn("45", erkek["sizes"])
        self.assertIn("46", erkek["sizes"])
        self.assertIn("39", kadin["sizes"])
        self.assertIn("40", kadin["sizes"])


class TestScraperFilters(unittest.TestCase):
    def setUp(self):
        self.scraper = SanalCadirScraper()

    def test_normalize_size(self):
        self.assertEqual(self.scraper._normalize_size("45,5"), "45.5")
        self.assertEqual(self.scraper._normalize_size(" 40 "), "40")
        self.assertEqual(self.scraper._normalize_size(None), "")

    def test_keyword_exclusion(self):
        self.assertTrue(self.scraper.is_excluded_by_keyword("Lacoste Croco Terlik"))
        self.assertTrue(self.scraper.is_excluded_by_keyword("Yazlık Sandalet"))
        self.assertTrue(self.scraper.is_excluded_by_keyword("Bebek Patiği"))
        self.assertTrue(self.scraper.is_excluded_by_keyword("Çocuk Spor Ayakkabı"))
        self.assertFalse(self.scraper.is_excluded_by_keyword("Lacoste Court Master Sneaker"))

    def test_gender_and_age_compatibility(self):
        # Çocuk işareti olan elenmeli
        self.assertFalse(
            self.scraper.is_compatible_gender_and_age({"integration_cocuk": "1"}, "Nike Sneaker", "kadin")
        )
        # Kadın kategorisinde erkek ayakkabısı elenmeli (unisex değilse)
        self.assertFalse(
            self.scraper.is_compatible_gender_and_age({"integration_cinsiyet": "Erkek"}, "Erkek Koşu Ayakkabısı", "kadin")
        )
        # Unisex her iki kategoride de kabul edilmeli
        self.assertTrue(
            self.scraper.is_compatible_gender_and_age({"integration_cinsiyet": "Unisex"}, "Converse Chuck 70 Unisex Sneaker", "kadin")
        )
        self.assertTrue(
            self.scraper.is_compatible_gender_and_age({"integration_cinsiyet": "Unisex"}, "Converse Chuck 70 Unisex Sneaker", "erkek")
        )

    def test_basket_offer_extraction(self):
        raw_product = {
            "price": "2000",
            "basket_offers": [
                {
                    "label": "Sepette %20 İndirim",
                    "listing_kwargs": {
                        "discounted_total_price": 1600.0,
                    }
                }
            ]
        }
        price, label = self.scraper._extract_basket_offer(raw_product, 2000.0)
        self.assertEqual(price, 1600.0)
        self.assertEqual(label, "Sepette %20 İndirim")


class TestStorageLogic(unittest.TestCase):
    def setUp(self):
        self.test_state_file = PROJECT_DIR / "data" / "test_products_state.json"
        if self.test_state_file.exists():
            self.test_state_file.unlink()
        self.storage = ProductStorage(state_file=self.test_state_file)

    def tearDown(self):
        if self.test_state_file.exists():
            self.test_state_file.unlink()

    def test_new_stock_and_price_drop(self):
        sample_prod = {
            "id": "1001",
            "state_key": "erkek_1001",
            "pk": 1001,
            "sku": "SKU1001",
            "model_key": "erkek_M1001_C1",
            "name": "Test Sneaker Erkek",
            "brand": "TestBrand",
            "category": "Erkek",
            "category_key": "erkek",
            "category_badge": "🚹 Erkek (45-46)",
            "available_sizes": ["45"],
            "stock_count": 5,
            "retail_price": 5000.0,
            "sale_price": 3000.0,
            "basket_price": None,
            "basket_offer_label": None,
            "effective_price": 3000.0,
            "discount_rate": 40.0,
            "product_url": "https://www.sanalcadir.com/test-1001/",
            "image_url": "https://example.com/test.jpg",
            "max_price_limit": 10000.0,
        }

        # 1. İlk tarama (NOTIFY_ON_FIRST_RUN=False iken sessizce kaydedilmeli)
        config.NOTIFY_ON_FIRST_RUN = False
        alerts, stats = self.storage.process_scraped_products([sample_prod])
        self.assertEqual(len(alerts), 0)
        self.assertEqual(stats["silent_first_run_saved"], 1)
        self.assertIn("erkek_1001", self.storage.state)

        # 2. İkinci tarama: Fiyat aynı kaldı -> 0 bildirim
        alerts, stats = self.storage.process_scraped_products([sample_prod])
        self.assertEqual(len(alerts), 0)

        # 3. Üçüncü tarama: Fiyat %10 düştü (3000 -> 2700) -> 1 PRICE_DROP bildirimi ve lowest_price güncellenmeli
        dropped_prod = dict(sample_prod)
        dropped_prod["effective_price"] = 2700.0
        alerts, stats = self.storage.process_scraped_products([dropped_prod])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["event_type"], EVENT_PRICE_DROP)
        self.assertTrue(alerts[0]["is_all_time_low"])
        self.assertEqual(self.storage.state["erkek_1001"]["lowest_price"], 2700.0)

    def test_unisex_category_isolation(self):
        # Aynı ID'li unisex ürün hem Erkek hem Kadın'da birbirini ezmemeli
        erkek_prod = {
            "id": "2002",
            "state_key": "erkek_2002",
            "pk": 2002,
            "name": "Unisex Chuck 70",
            "category": "Erkek",
            "category_key": "erkek",
            "available_sizes": ["45"],
            "stock_count": 3,
            "retail_price": 3000.0,
            "sale_price": 1500.0,
            "effective_price": 1500.0,
            "discount_rate": 50.0,
            "product_url": "https://example.com/2002",
            "image_url": "https://example.com/2002.jpg",
            "max_price_limit": 10000.0,
        }
        kadin_prod = dict(erkek_prod)
        kadin_prod["state_key"] = "kadin_2002"
        kadin_prod["category"] = "Kadın"
        kadin_prod["category_key"] = "kadin"
        kadin_prod["available_sizes"] = ["39"]

        alerts, stats = self.storage.process_scraped_products([erkek_prod, kadin_prod])
        self.assertIn("erkek_2002", self.storage.state)
        self.assertIn("kadin_2002", self.storage.state)
        self.assertEqual(self.storage.state["erkek_2002"]["available_sizes"], ["45"])
        self.assertEqual(self.storage.state["kadin_2002"]["available_sizes"], ["39"])


class TestNotifierFormat(unittest.TestCase):
    def setUp(self):
        self.notifier = TelegramNotifier(bot_token="test_token", chat_id="12345")

    def test_format_caption_and_button(self):
        alert = {
            "event_type": EVENT_PRICE_DROP,
            "is_all_time_low": True,
            "old_price": 3000.0,
            "new_price": 2400.0,
            "extra_drop_rate": 20.0,
            "product": {
                "name": "Lacoste Test Ayakkabı",
                "brand": "Lacoste",
                "category_badge": "🚹 Erkek (45-46)",
                "available_sizes": ["45", "45.5"],
                "stock_count": 2,
                "retail_price": 4000.0,
                "sale_price": 3000.0,
                "basket_price": 2400.0,
                "basket_offer_label": "Sepette %20 İndirim",
                "discount_rate": 40.0,
                "product_url": "https://www.sanalcadir.com/product-test/",
            }
        }
        caption = self.notifier.format_caption(alert)
        self.assertIn("EKSTRA", caption)
        self.assertIn("En Düşük Fiyat", caption)
        self.assertIn("Kritik Stok", caption)
        self.assertIn("Son 2 Adet", caption)
        self.assertIn("Sepette", caption)
        self.assertLessEqual(len(caption), 1024)

        # Buton yapısı kontrolü
        markup = self.notifier._build_inline_keyboard("https://www.sanalcadir.com/product-test/")
        self.assertIn("inline_keyboard", markup)
        self.assertEqual(markup["inline_keyboard"][0][0]["text"], "🛒 Ürünü Sanal Çadır'da Aç")


class TestLiveScraper(unittest.TestCase):
    def test_live_first_page(self):
        scraper = SanalCadirScraper()
        erkek_target = config.TARGETS[0]
        products = scraper.scrape_target(erkek_target, max_pages=1)
        self.assertGreater(len(products), 0, "Canlı Sanal Çadır Erkek sayfasından ürün çekilemedi!")
        sample = products[0]
        self.assertIn("id", sample)
        self.assertIn("effective_price", sample)
        self.assertIn("available_sizes", sample)
        self.assertGreater(sample["effective_price"], 0)
        print(f"\n[CANLI TEST BAŞARILI] Çekilen Örnek Ürün: {sample['name']} | Numara: {sample['available_sizes']} | Fiyat: {sample['effective_price']} TL")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
