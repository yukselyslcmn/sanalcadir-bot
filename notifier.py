"""
Sanal Çadır (sanalcadir.com) Görselli Telegram Bildirim Modülü (notifier.py)

Özellikler:
- Telegram Bot API `sendPhoto` uç noktasını kullanarak ürün görseliyle birlikte
  zengin HTML formatında bildirim gönderir.
- 🏆 "Şimdiye Kadarki En Düşük Fiyat!" rozeti ve ⚠️ "Son X Adet Kaldı!" kritik stok uyarısı içerir.
- 🔘 Mesaj altına tıklanabilir `[🛒 Ürünü Sanal Çadır'da Aç]` inline butonu ekler.
- Çoklu `TELEGRAM_CHAT_ID` (virgülle ayrılmış birden fazla alıcı) destekler.
- Görsel URL'si geçersizse otomatik olarak `sendMessage` fallback moduna geçer.
- Rate-limit (flood) koruması için mesajlar arasında `time.sleep(1.5)` uygular.
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
    """Telegram üzerinden fotoğraflı ve butonlu indirim/stok bildirimi gönderen sınıf."""

    def __init__(
        self,
        bot_token: str = config.TELEGRAM_BOT_TOKEN,
        chat_id: str = config.TELEGRAM_CHAT_ID,
        rate_limit_delay: float = config.TELEGRAM_RATE_LIMIT_DELAY,
    ) -> None:
        self.bot_token: str = bot_token.strip()
        self.chat_id: str = chat_id.strip()
        self.chat_ids: List[str] = [
            cid.strip() for cid in self.chat_id.split(",") if cid.strip()
        ]
        self.rate_limit_delay: float = max(rate_limit_delay, 1.5)
        self.api_base: str = f"https://api.telegram.org/bot{self.bot_token}"
        self.session = requests.Session()

    def is_configured(self) -> bool:
        """Telegram token ve chat_id ayarlarının yapılıp yapılmadığını kontrol eder."""
        if not self.bot_token or not self.chat_ids:
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

    @staticmethod
    def _build_inline_keyboard(product_url: str) -> Dict[str, Any]:
        """Mesaj altına tıklanabilir '🛒 Ürünü Sanal Çadır'da Aç' butonu oluşturur."""
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "🛒 Ürünü Sanal Çadır'da Aç",
                        "url": product_url,
                    }
                ]
            ]
        }

    def format_caption(self, alert: Dict[str, Any]) -> str:
        """
        Bildirim verisini Telegram HTML formatında şık bir mesaja dönüştürür.
        """
        event_type = alert.get("event_type", "")
        is_all_time_low = bool(alert.get("is_all_time_low", False))
        prod = alert["product"]

        # 1. Başlık ve En Düşük Fiyat Rozeti
        if event_type == EVENT_PRICE_DROP:
            header = "🚨 <b>🔥 EKSTRA FİYAT DÜŞÜŞÜ!</b>"
            if is_all_time_low:
                header += "\n🏆 <b>Şimdiye Kadarki En Düşük Fiyat!</b>"
        else:
            header = "🚨 <b>🆕 YENİ STOK GELDİ!</b>"

        # 2. Kategori, Numara ve Kritik Stok Bilgisi
        category_badge = html.escape(str(prod.get("category_badge") or prod.get("category", "")))
        available_sizes = prod.get("available_sizes") or []
        sizes_str = ", ".join(str(s) for s in available_sizes) if available_sizes else "-"
        stock_count = int(prod.get("stock_count") or 0)

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
            if basket_price and float(basket_price) < sale_price:
                basket_note = (
                    f" <i>(Etiket: {self._format_tl(sale_price)} | "
                    f"🛒 Sepette: <b>{self._format_tl(float(basket_price))}</b>)</i>"
                )
            else:
                basket_note = ""

            price_line = (
                f"💰 <s>{self._format_tl(old_price)}</s> ➡️ <b>{self._format_tl(new_price)}</b>"
                f"{basket_note} — <b>(%{extra_drop:.0f} Ekstra İndirim!)</b>"
            )
            if retail_price > old_price:
                price_line += (
                    f"\n🏷️ Liste Fiyatı: <s>{self._format_tl(retail_price)}</s> "
                    f"(Toplam %{discount_rate:.0f} İndirim)"
                )
        else:
            if basket_price and float(basket_price) < sale_price:
                label_prefix = (
                    f"🛒 {html.escape(str(basket_label))}: " if basket_label else "🛒 Sepette: "
                )
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
        ]

        # Kritik Stok Uyarısı ("Son X Adet Kaldı!")
        if 0 < stock_count <= config.CRITICAL_STOCK_LIMIT:
            lines.append(f"⚠️ <b>Kritik Stok:</b> Bu numarada <b>Son {stock_count} Adet</b> kaldı!")

        lines.extend(
            [
                f"👟 {full_title}",
                price_line,
                "",
                link_line,
            ]
        )
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
        Tek bir ürün bildirimini önce `sendPhoto` (+ Inline Keyboard butonu) ile göndermeyi dener.
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
        raw_product_url = str(prod.get("product_url") or config.BASE_URL).strip()
        reply_markup = self._build_inline_keyboard(raw_product_url)

        any_sent = False
        for target_chat_id in self.chat_ids:
            sent_for_chat = False
            # 1. Öncelik: sendPhoto ile ürün fotoğrafı ve tıklanabilir butonla gönder
            if image_url:
                photo_payload = {
                    "chat_id": target_chat_id,
                    "photo": image_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup,
                }
                if self._post_telegram("sendPhoto", photo_payload):
                    sent_for_chat = True

            # 2. Fallback: sendMessage ile gönder
            if not sent_for_chat:
                if image_url:
                    logger.info(
                        "sendPhoto başarısız oldu (%s), sendMessage fallback devreye giriyor...",
                        prod.get("name"),
                    )
                message_payload = {
                    "chat_id": target_chat_id,
                    "text": caption,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                    "reply_markup": reply_markup,
                }
                sent_for_chat = self._post_telegram("sendMessage", message_payload)

            if sent_for_chat:
                any_sent = True

        return any_sent

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
