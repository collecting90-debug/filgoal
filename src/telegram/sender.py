"""
src/telegram/sender.py
───────────────────────
Telegram notification — عرض واضح للمحررين.

الشكل النهائي لكل خبر:
━━━━━━━━━━━━━━━━━━━━━━━━
[صورة الخبر]

📌 العنوان

📝 الكونتنت الكامل (كامل — كل الفقرات)

🕐 موعد الخبر

🔗 [رابط المقال الأصلي]
━━━━━━━━━━━━━━━━━━━━━━━━

لو الكونتنت طويل جداً (> 900 حرف مع الصورة):
  → بتبعت الصورة أولاً بـ caption مختصر
  → بعدين message نصية تانية بالكونتنت الكامل
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from loguru import logger

from src.core.config import Settings
from src.core.models import Article

# Telegram limits
_CAPTION_HARD_LIMIT = 1024   # max for send_photo caption
_MESSAGE_HARD_LIMIT = 4096   # max for send_message
_CAPTION_SAFE        = 900   # leave margin for title + link
_CONTENT_SAFE        = 3500  # safe text message content limit


class TelegramSender:

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bot: Optional[Bot] = None
        self._last_sent: float = 0.0
        self._min_interval: float = 2.5   # seconds between sends

    async def start(self) -> None:
        self._bot = Bot(
            token=self._settings.effective_telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        logger.info("Telegram bot initialised")

    async def stop(self) -> None:
        if self._bot:
            await self._bot.session.close()
        self._bot = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def send(self, article: Article) -> bool:
        if not self._bot:
            logger.error("TelegramSender not started")
            return False

        await self._rate_limit()

        try:
            return await self._dispatch(article)
        except Exception as exc:
            logger.error(f"Telegram send failed for {article.url}: {exc}")
            return False

    # ── Dispatch logic ────────────────────────────────────────────────────────

    async def _dispatch(self, article: Article) -> bool:
        """
        استراتيجية الإرسال:
        1. لو في صورة وكونتنت قصير  → send_photo بـ caption كامل
        2. لو في صورة وكونتنت طويل → send_photo بـ caption مختصر + send_message بالكونتنت
        3. لو مفيش صورة             → send_message بكل شيء
        """
        content = self._get_full_content(article)
        has_image = bool(article.image_url)

        if has_image:
            header = self._build_header(article)   # العنوان + الوقت + الرابط
            content_block = self._escape(content) if content else ""
            caption_full = f"{header}\n\n{content_block}" if content_block else header

            if len(caption_full) <= _CAPTION_SAFE:
                # كل شيء يدخل في الـ caption — رسالة واحدة مع الصورة
                return await self._send_photo_with_caption(article, caption_full)
            else:
                # الصورة بـ caption مختصر + رسالة تانية بالكونتنت كامل
                short_caption = self._build_short_caption(article)
                ok = await self._send_photo_with_caption(article, short_caption)
                if ok and content:
                    await asyncio.sleep(0.5)
                    await self._rate_limit()
                    await self._send_content_message(article, content)
                return ok
        else:
            # نص فقط — كل شيء في رسالة واحدة
            full_text = self._build_full_text_message(article, content)
            return await self._send_text_message(full_text)

    # ── Builders ──────────────────────────────────────────────────────────────

    def _build_header(self, article: Article) -> str:
        """العنوان + الوقت + رابط — يظهر فوق الكونتنت دايماً."""
        lines = []
        lines.append(f"📌 <b>{self._escape(article.title)}</b>")

        date_str = self._format_date(article.publish_date)
        if date_str:
            lines.append(f"\n🕐 <i>{date_str}</i>")

        lines.append(f'\n🔗 <a href="{article.url}">رابط المقال</a>')
        return "\n".join(lines)

    def _build_short_caption(self, article: Article) -> str:
        """Caption مختصر للصورة لما الكونتنت هيجي في رسالة منفصلة."""
        lines = [f"📌 <b>{self._escape(article.title)}</b>"]
        date_str = self._format_date(article.publish_date)
        if date_str:
            lines.append(f"🕐 <i>{date_str}</i>")
        lines.append(f'🔗 <a href="{article.url}">رابط المقال</a>')
        lines.append("\n⬇️ <i>الكونتنت كامل في الرسالة التالية</i>")
        return "\n".join(lines)

    def _build_full_text_message(self, article: Article, content: str) -> str:
        """رسالة نصية كاملة لما مفيش صورة."""
        lines = [f"📌 <b>{self._escape(article.title)}</b>"]

        if content:
            lines.append(f"\n📝 {self._escape(content)}")

        date_str = self._format_date(article.publish_date)
        if date_str:
            lines.append(f"\n🕐 <i>{date_str}</i>")

        lines.append(f'\n🔗 <a href="{article.url}">رابط المقال</a>')

        text = "\n".join(lines)
        if len(text) > _CONTENT_SAFE:
            text = text[:_CONTENT_SAFE].rsplit("\n", 1)[0]
            text += f'\n\n🔗 <a href="{article.url}">رابط المقال</a>'
        return text

    # ── Send wrappers ─────────────────────────────────────────────────────────

    async def _send_photo_with_caption(self, article: Article, caption: str) -> bool:
        keyboard = self._build_keyboard(article)
        try:
            await self._bot.send_photo(  # type: ignore[union-attr]
                chat_id=self._settings.effective_telegram_chat_id,
                photo=article.image_url,
                caption=caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"Telegram photo sent: {article.title[:60]}")
            return True
        except Exception as exc:
            logger.warning(f"Photo send failed ({exc}), falling back to text")
            full_text = self._build_full_text_message(
                article, self._get_full_content(article)
            )
            return await self._send_text_message(full_text)

    async def _send_content_message(self, article: Article, content: str) -> None:
        """رسالة منفصلة بالكونتنت الكامل بعد الصورة."""
        # تقسيم الكونتنت لو طويل جداً
        chunks = self._split_content(content, _CONTENT_SAFE)
        for i, chunk in enumerate(chunks):
            prefix = "📝 <b>الكونتنت:</b>\n\n" if i == 0 else ""
            text = f"{prefix}{self._escape(chunk)}"
            await self._send_text_message(text, with_keyboard=False)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)

    async def _send_text_message(self, text: str, with_keyboard: bool = True) -> bool:
        keyboard = self._build_keyboard_from_url(
            self._extract_url_from_text(text)
        ) if with_keyboard else None

        await self._bot.send_message(  # type: ignore[union-attr]
            chat_id=self._settings.effective_telegram_chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return True

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def _build_keyboard(self, article: Article) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="رابط المقال الأصلي", url=article.url)
            ]]
        )

    def _build_keyboard_from_url(self, url: Optional[str]) -> Optional[InlineKeyboardMarkup]:
        if not url:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="رابط المقال الأصلي", url=url)
            ]]
        )

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _get_full_content(self, article: Article) -> str:
        """الكونتنت الكامل — content أولاً، summary كـ fallback."""
        return (article.content or article.summary or "").strip()

    @staticmethod
    def _split_content(text: str, chunk_size: int) -> list[str]:
        """تقسيم النص على حدود الجمل أو الفقرات."""
        if len(text) <= chunk_size:
            return [text]
        chunks = []
        while text:
            if len(text) <= chunk_size:
                chunks.append(text)
                break
            split_at = text.rfind("\n\n", 0, chunk_size)
            if split_at == -1:
                split_at = text.rfind(". ", 0, chunk_size)
            if split_at == -1:
                split_at = chunk_size
            chunks.append(text[:split_at].strip())
            text = text[split_at:].strip()
        return chunks

    @staticmethod
    def _format_date(dt: Optional[datetime]) -> str:
        if not dt:
            return ""
        # Format: "الثلاثاء، 11 يونيو 2026 - 14:30"
        arabic_months = {
            1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
            5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
            9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
        }
        month = arabic_months.get(dt.month, str(dt.month))
        return f"{dt.day} {month} {dt.year} — {dt.strftime('%H:%M')}"

    @staticmethod
    def _extract_url_from_text(text: str) -> Optional[str]:
        import re
        m = re.search(r'href="([^"]+)"', text)
        return m.group(1) if m else None

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )

    async def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_sent
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_sent = time.monotonic()