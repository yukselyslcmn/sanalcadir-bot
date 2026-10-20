"""
Sanal Çadır (sanalcadir.com) Hızlı Scraper Test Betiği (test_scraper.py)

Telegram'a mesaj göndermeden ve `products_state.json` dosyasını değiştirmeden,
Sanal Çadır üzerinden Erkek (45 - 45.5) ve Kadın (39 - 40) kategorilerinden
veri çekilip çekilmediğini, hayalet stok korumasını ve sepette ekstra indirim
algılamasını konsolda hızlıca test eder.

Kullanım:
    python test_scraper.py
    python test_scraper.py --pages 2
"""

import argparse
import sys

import config
from main import setup_logging
from notifier import TelegramNotifier
from scraper import SanalCadirScraper
from storage import EVENT_NEW_STOCK


def run_test(pages_to_test: int = 1, sample_count: int = 5) -> None:
    setup_logging()
    scraper = SanalCadirScraper()
    notifier = TelegramNotifier()

    print("\n" + "=" * 78)
    print("🧪 SANAL ÇADIR (sanalcadir.com) SCRAPER BAĞLANTI & VERİ ÇEKME TESTİ")
    print("=" * 78)
    print(f"• Hedef Site           : {config.BASE_URL}")
    print(f"• Hariç Kelimeler      : {', '.join(config.EXCLUDE_KEYWORDS)}")
    print(
        f"• Marka Filtresi       : {', '.join(config.ALLOWED_BRANDS) if config.ALLOWED_BRANDS else 'Tüm Markalar'}"
    )
    print(f"• Tavan Fiyat Sınırı   : {config.MAX_PRICE_LIMIT:.0f} TL")
    print(f"• Min. İndirim Oranı   : %{config.MIN_DISCOUNT_RATE:.0f}")
    print(f"• Test Sayfa Limiti    : Kategori başına ilk {pages_to_test} sayfa")
    print("=" * 78 + "\n")

    total_found = 0

    for target in config.TARGETS:
        products = scraper.scrape_target(target, max_pages=pages_to_test)
        total_found += len(products)

        # Bütçe ve minimum indirim filtresine de uyanları ayrıca filtrele
        qualifying = [
            p
            for p in products
            if p["effective_price"] <= p["max_price_limit"]
            and p["discount_rate"] >= config.MIN_DISCOUNT_RATE
        ]

        print("\n" + "-" * 78)
        print(
            f"📌 KATEGORİ: {target['badge']} | "
            f"Stokta & Geçerli: {len(products)} ürün | "
            f"Bütçe & İndirim Kriterine Uyan: {len(qualifying)} ürün"
        )
        print("-" * 78)

        display_list = qualifying[:sample_count] if qualifying else products[:sample_count]
        if not display_list:
            print("  ⚠️ Bu kategoride kriterlere uygun ürün bulunamadı.")
            continue

        for idx, prod in enumerate(display_list, start=1):
            basket_info = (
                f" | 🛒 Sepette: {prod['basket_price']:.2f} TL ({prod['basket_offer_label']})"
                if prod.get("basket_price")
                else ""
            )
            print(
                f"  [{idx}] ID: {prod['id']} | Marka: {prod['brand']} | {prod['name']}\n"
                f"      📐 Stoktaki Numara : {', '.join(prod['available_sizes'])} (Stok: {prod['stock_count']} adet)\n"
                f"      💰 Fiyat           : Liste: {prod['retail_price']:.0f} TL -> "
                f"Satış: {prod['sale_price']:.0f} TL{basket_info}\n"
                f"      🏷️ Net Fiyat       : {prod['effective_price']:.2f} TL (%{prod['discount_rate']:.1f} İndirim)\n"
                f"      🖼️ Görsel          : {prod['image_url']}\n"
                f"      🔗 Ürün Linki      : {prod['product_url']}\n"
            )

        # İlk ürün için örnek Telegram mesaj formatını göster
        sample_alert = {
            "event_type": EVENT_NEW_STOCK,
            "product": display_list[0],
            "old_price": display_list[0]["retail_price"],
            "new_price": display_list[0]["effective_price"],
            "extra_drop_rate": display_list[0]["discount_rate"],
        }
        print("  📨 ÖRNEK TELEGRAM BİLDİRİM METNİ (HTML):")
        print("  " + "~" * 50)
        for line in notifier.format_caption(sample_alert).splitlines():
            print(f"  {line}")
        print("  " + "~" * 50)

    print("\n" + "=" * 78)
    if total_found > 0:
        print(
            f"✅ TEST BAŞARILI! Toplam {total_found} adet gerçek stoklu ürün başarıyla çekildi ve ayrıştırıldı."
        )
    else:
        print(
            "❌ TEST UYARISI: Hiçbir ürün çekilemedi. İnternet bağlantınızı veya kategori URL'lerini kontrol edin."
        )
        sys.exit(1)
    print("=" * 78 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sanal Çadır Scraper Hızlı Test Betiği")
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Test sırasında her kategoriden taranacak sayfa sayısı (Varsayılan: 1)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Konsolda gösterilecek örnek ürün sayısı (Varsayılan: 5)",
    )
    args = parser.parse_args()
    run_test(pages_to_test=args.pages, sample_count=args.samples)
