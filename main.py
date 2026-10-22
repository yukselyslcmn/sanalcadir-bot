"""
Sanal Çadır (sanalcadir.com) 7/24 Otomatik Ayakkabı İndirim & Stok Takip Botu
Ana Çalıştırma Modülü (main.py)

Kullanım:
  - Tek seferlik çalışma (GitHub Actions uyumlu):
      python main.py --once
  - Bilgisayar açıkken her 20 dakikada bir sürekli çalışma:
      python main.py --loop
"""

import argparse
import logging
import sys
import time

import config
from notifier import TelegramNotifier
from scraper import SanalCadirScraper
from storage import ProductStorage


def setup_logging() -> None:
    """Konsol çıktısı için UTF-8 uyumlu log yapılandırması."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


logger = logging.getLogger(__name__)


def run_check_cycle(dry_run: bool = False) -> None:
    """
    Tek bir tarama, karşılaştırma ve bildirim döngüsünü çalıştırır:
    1. Sanal Çadır Erkek (45-46) ve Kadın (39-40) kategorilerini tarar.
    2. `data/products_state.json` ile karşılaştırarak yeni stok ve ekstra indirimleri bulur.
    3. Tespit edilen fırsatları fotoğraflı olarak Telegram'a gönderir.
    """
    logger.info("=" * 72)
    logger.info("SANAL ÇADIR İNDİRİM & STOK TARAMASI BAŞLIYOR...")
    logger.info(
        "Filtreler -> Max Fiyat: %.0f TL | Min İndirim: %%%.0f | Ekstra Düşüş Eşiği: %%%.0f | Max Bildirim: %d",
        config.MAX_PRICE_LIMIT,
        config.MIN_DISCOUNT_RATE,
        config.PRICE_DROP_THRESHOLD,
        config.MAX_ALERTS_PER_RUN,
    )
    if config.ALLOWED_BRANDS:
        logger.info("İzin Verilen Markalar: %s", ", ".join(config.ALLOWED_BRANDS))
    else:
        logger.info("Marka Filtresi: Tüm Markalar (Hariç tutulan kelimeler aktif)")
    logger.info("=" * 72)

    scraper = SanalCadirScraper()
    storage = ProductStorage()
    notifier = TelegramNotifier()

    # 1. Ürünleri Çek
    scraped_products = scraper.scrape_all_targets()
    if not scraped_products:
        logger.warning("Tarama sonucunda hiçbir ürün alınamadı. İşlem sonlandırılıyor.")
        return

    # 2. Durum Karşılaştırması ve Kayıt
    alerts, stats = storage.process_scraped_products(
        scraped_products, completed_categories=scraper.completed_categories
    )

    logger.info("-" * 72)
    logger.info(
        "TARAMA ÖZETİ -> Stoktaki Geçerli Ürün: %d | Yeni Stok Bildirimi: %d | "
        "Ekstra Fiyat Düşüşü: %d | İlk Çalıştırma Sessiz Kayıt: %d | Toplam Takip Edilen: %d",
        stats["scraped_count"],
        stats["new_stock_alerts"],
        stats["price_drop_alerts"],
        stats["silent_first_run_saved"],
        stats["total_tracked"],
    )
    logger.info("-" * 72)

    # 3. Bildirim Gönderimi
    if not alerts:
        logger.info("Bildirim kriterlerini sağlayan yeni bir indirim veya stok bulunmadı.")
        return

    if dry_run:
        logger.info("--dry-run modu aktif: %d bildirim Telegram'a gönderilmedi.", len(alerts))
        for item in alerts[:10]:
            logger.info("\n%s", notifier.format_caption(item))
        return

    notifier.send_batch_alerts(alerts)


def run_loop_mode(interval_minutes: int, dry_run: bool = False) -> None:
    """`schedule` kütüphanesi ile belirtilen dakika aralığında sürekli çalışır."""
    try:
        import schedule  # type: ignore
    except ImportError:
        logger.warning(
            "'schedule' kütüphanesi bulunamadı, standart zamanlayıcı döngüsü kullanılacak."
        )
        schedule = None

    logger.info(
        "Sürekli çalışma modu (--loop) başlatıldı. Kontrol aralığı: Her %d dakikada bir.",
        interval_minutes,
    )

    # İlk taramayı hemen çalıştır
    run_check_cycle(dry_run=dry_run)

    if schedule is not None:
        schedule.every(interval_minutes).minutes.do(run_check_cycle, dry_run=dry_run)
        while True:
            schedule.run_pending()
            time.sleep(5)
    else:
        while True:
            time.sleep(interval_minutes * 60)
            run_check_cycle(dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sanal Çadır (sanalcadir.com) 7/24 Otomatik Ayakkabı İndirim & Stok Takip Botu"
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--once",
        action="store_true",
        help="Tek seferlik tarama yapar ve çıkar (GitHub Actions için varsayılan mod).",
    )
    mode_group.add_argument(
        "--loop",
        action="store_true",
        help=f"Bilgisayar açıkken her {config.CHECK_INTERVAL_MINUTES} dakikada bir tarama yapar.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Telegram'a mesaj göndermeden sadece tarama ve durum güncellemesi yapar.",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()

    try:
        if args.loop:
            run_loop_mode(config.CHECK_INTERVAL_MINUTES, dry_run=args.dry_run)
        else:
            # Varsayılan veya --once: Tek seferlik çalıştır
            run_check_cycle(dry_run=args.dry_run)
    except KeyboardInterrupt:
        logger.info("Bot kullanıcı tarafından durduruldu (Ctrl+C).")
        sys.exit(0)


if __name__ == "__main__":
    main()
