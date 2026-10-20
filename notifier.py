"""
Sanal Çadır (sanalcadir.com) Görselli Telegram Bildirim Modülü (notifier.py)

Özellikler:
- Telegram Bot API `sendPhoto` uç noktasını kullanarak ürün görseliyle birlikte
  zengin HTML formatında bildirim gönderir.
- Görsel URL'si geçersizse veya Telegram sunucuları tarafından indirilemezse
  otomatik olarak `sendMessage` (metin + link önizleme) fallback moduna geçer.
- Rate-limit (flood) koruması için mesajlar arasında `time.sleep(1.5)` uygular
  ve HTTP 429 (Too Many Requests) durumunda `retry_after` süresini bekler.
"""

import html
import logging
import time
from typing import Any, Dict, List

import requests

import config
from storage import EVENT_PRICE_DROP

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram üzerinden fotoğraflı indirim ve stok bildirimi gönderen sınıf."""

    def __init__(
        self,
        bot_token: str = config.TELEGRAM_BOT_TOKEN,
        chat_id: str = config.TELEGRAM_CHAT_ID,
        rate_limit_delay: float = config.TELEGRAM_RATE_LIMIT_DELAY,
    ) -> None:
        self.bot_token: str = bot_token.strip()
        self.chat_id: str = chat_id.strip()
        self.rate_limit_delay: float = max(rate_limit_delay, 1.5)
        self.api_base: str = f"https://api.telegram.org/bot{self.bot_token}"
        self.session = requests.Session()

    def is_configured(self) -> bool:
        """Telegram token ve chat_id ayarlarının yapılıp yapılmadığını kontrol eder."""
        if not self.bot_token or not self.chat_id:
            return False
        if "ornek_token" in self.bot_token or self.bot_token.startswith("1234567890:"):
            return False
        return True

    @staticmethod
    def _format_tl(amount: float) -> str:
        """Fiyatı Türkçe para formatında (örn: 1.949,35 TL veya 2.999 TL) biçimlendirir."""
        if amount is None:
            return "0 TL"
        if float(amount).is_integer():
            formatted = f"{int(amount):,}".replace(",", ".")
            return f"{formatted} TL"
        formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{formatted} TL"

    def format_caption(self, alert: Dict[str, Any]) -> str:
        """
        Bildirim verisini Telegram HTML formatında şık bir mesaja dönüştürür.

        Format:
        🚨 Başlık: 🔥 EKSTRA FİYAT DÜŞÜŞÜ! veya 🆕 YENİ STOK GELDİ!
        🏷️ Kategori & Numara: 🚹 Erkek (45 - 45.5) veya 🚺 Kadın (39 - 40)
        👟 Marka & Ürün Adı
        💰 Fiyat Bilgisi: ~Eski Fiyat TL~ ➡️ Yeni Fiyat TL (Varsa: 🛒 Sepette: X TL) — (%XX İndirim!)
        🔗 Ürünü İncelemek ve Satın Almak İçin Tıklayın
        """
        event_type = alert.get("event_type", "")
        prod = alert["product"]

        # 1. Başlık
        if event_type == EVENT_PRICE_DROP:
            header = "🚨 <b>🔥 EKSTRA FİYAT DÜŞÜŞÜ!</b>"
        else:
            header = "🚨 <b>🆕 YENİ STOK GELDİ!</b>"

        # 2. Kategori & Numara Bilgisi
        category_badge = html.escape(str(prod.get("category_badge") or prod.get("category", "")))
        available_sizes = prod.get("available_sizes") or []
        sizes_str = ", ".join(str(s) for s in available_sizes) if available_sizes else "-"

        # 3. Marka & Ürün Adı
        brand = html.escape(str(prod.get("brand") or "").strip())
        name = html.escape(str(prod.get("name") or "").strip())
        if brand and brand.lower() not in name.lower():
            full_title = f"<b>{brand}</b> - {name}"
        else:
            full_title = f"<b>{name}</b>"

        # 4. Fiyat ve Sepet İndirimi Bilgisi
        retail_price = float(prod.get("retail_price") or 0.0)
        sale_price = float(prod.get("sale_price") or 0.0)
        basket_price = prod.get("basket_price")
        basket_label = prod.get("basket_offer_label")
        discount_rate = float(prod.get("discount_rate") or 0.0)

        old_price = float(alert.get("old_price") or retail_price)
        new_price = float(alert.get("new_price") or prod.get("effective_price") or sale_price)
        extra_drop = float(alert.get("extra_drop_rate") or 0.0)

        if event_type == EVENT_PRICE_DROP:
            # Ekstra fiyat düşüşünde: Önceki takip edilen fiyat -> Yeni net fiyat
            if basket_price and float(basket_price) < sale_price:
                basket_note = f" <i>(Etiket: {self._format_tl(sale_price)} | 🛒 Sepette: <b>{self._format_tl(float(basket_price))}</b>)</i>"
            else:
                basket_note = ""

            price_line = (
                f"💰 <s>{self._format_tl(old_price)}</s> ➡️ <b>{self._format_tl(new_price)}</b>"
                f"{basket_note} — <b>(%{extra_drop:.0f} Ekstra İndirim!)</b>"
            )
            if retail_price > old_price:
                price_line += f"\n🏷️ Liste Fiyatı: <s>{self._format_tl(retail_price)}</s> (Toplam %{discount_rate:.0f} İndirim)"
        else:
            # Yeni stok girişinde: Liste fiyatı -> Satış fiyatı (ve varsa Sepet fiyatı)
            if basket_price and float(basket_price) < sale_price:
                label_prefix = f"🛒 {html.escape(str(basket_label))}: " if basket_label else "🛒 Sepette: "
                if retail_price > sale_price:
                    price_line = (
                        f"💰 <s>{self._format_tl(retail_price)}</s> ➡️ {self._format_tl(sale_price)} "
                        f"<i>({label_prefix}<b>{self._format_tl(float(basket_price))}</b>)</i> — "
                        f"<b>(%{discount_rate:.0f} İndirim!)</b>"
                    )
                else:
                    price_line = (
                        f"💰 <s>{self._format_tl(sale_price)}</s> ➡️ "
                        f"<b>{self._format_tl(float(basket_price))}</b> <i>({label_prefix.strip(': ')})</i> — "
                        f"<b>(%{discount_rate:.0f} İndirim!)</b>"
                    )
            else:
                if retail_price > sale_price:
                    price_line = (
                        f"💰 <s>{self._format_tl(retail_price)}</s> ➡️ <b>{self._format_tl(sale_price)}</b> — "
                        f"<b>(%{discount_rate:.0f} İndirim!)</b>"
                    )
                else:
                    price_line = f"💰 <b>{self._format_tl(sale_price)}</b>"

        # 5. Ürün Linki
        product_url = html.escape(str(prod.get("product_url") or config.BASE_URL), quote=True)
        link_line = f'🔗 <a href="{product_url}"><b>Ürünü İncelemek ve Satın Almak İçin Tıklayın</b></a>'

        lines = [
            header,
            "",
            f"🏷️ <b>Kategori:</b> {category_badge}",
            f"📐 <b>Stoktaki Numara:</b> <code>{html.escape(sizes_str)}</code>",
            f"👟 {full_title}",
            price_line,
            "",
            link_line,
        ]
        return "\n".join(lines)

    def _post_telegram(self, endpoint: str, payload: Dict[str, Any]) -> bool:
        """Telegram API'sine POST isteği gönderir; HTTP 429 durumunda retry_after kadar bekler."""
        url = f"{self.api_base}/{endpoint}"
        for attempt in range(1, 3):
            try:
                resp = self.session.post(url, json=payload, timeout=20)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:
                    retry_after = 5
                    try:
                        retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                    except Exception:
                        pass
                    logger.warning(
                        "Telegram rate-limit (429) algılandı. %d saniye bekleniyor...", retry_after
                    )
                    time.sleep(retry_after + 1)
                    continue

                logger.warning(
                    "Telegram %s hatası (HTTP %d): %s",
                    endpoint,
                    resp.status_code,
                    resp.text[:250],
                )
                return False
            except Exception as exc:
                logger.warning("Telegram bağlantı hatası (%s, deneme %d): %s", endpoint, attempt, exc)
                time.sleep(2)
        return False

    def send_alert(self, alert: Dict[str, Any]) -> bool:
        """
        Tek bir ürün bildirimini önce `sendPhoto` ile göndermeyi dener.
        Fotoğraf gönderilemezse `sendMessage` fallback olarak devreye girer.
        """
        if not self.is_configured():
            logger.warning(
                "Telegram ayarları (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) yapılandırılmamış! "
                "Mesaj konsola yazdırılıyor:\n%s",
                self.format_caption(alert),
            )
            return False

        caption = self.format_caption(alert)
        prod = alert["product"]
        image_url = str(prod.get("image_url") or "").strip()

        # 1. Öncelik: sendPhoto ile ürün fotoğrafıyla birlikte gönder
        if image_url:
            photo_payload = {
                "chat_id": self.chat_id,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if self._post_telegram("sendPhoto", photo_payload):
                return True
            logger.info(
                "sendPhoto başarısız oldu (%s), sendMessage fallback devreye giriyor...",
                prod.get("name"),
            )

        # 2. Fallback: sendMessage ile gönder
        message_payload = {
            "chat_id": self.chat_id,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        return self._post_telegram("sendMessage", message_payload)

    def send_batch_alerts(self, alerts: List[Dict[str, Any]]) -> int:
        """
        Bildirim listesini sırayla gönderir.
        Telegram flood engeline takılmamak için her mesaj arasında `time.sleep(1.5)` bekler.
        """
        if not alerts:
            return 0

        sent_count = 0
        total = len(alerts)
        logger.info("Toplam %d adet Telegram bildirimi gönderiliyor...", total)

        for idx, alert in enumerate(alerts, start=1):
            success = self.send_alert(alert)
            if success:
                sent_count += 1
            if idx < total:
                time.sleep(self.rate_limit_delay)

        logger.info("Telegram bildirim gönderimi tamamlandı: %d/%d başarılı.", sent_count, total)
        return sent_count
