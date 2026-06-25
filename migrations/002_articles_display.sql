-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: 002_articles_display.sql
-- Table مبسط للعرض — العنوان + الصورة + الكونتنت + الساموري + موعد الخبر
-- الهدف: يسهّل على المحررين أخذ المحتوى ونشره مباشرة
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS articles_display (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- ── المحتوى الأساسي فقط ──────────────────────────────────────────────────
    title           TEXT        NOT NULL,
    image_url       TEXT,
    content         TEXT,                   -- الكونتنت الكامل
    summary         TEXT,                   -- ملخص قصير (من صفحة الليستينج)
    publish_date    TIMESTAMPTZ,            -- موعد نزول الخبر الأصلي

    -- ── مرجع للجدول الأساسي ──────────────────────────────────────────────────
    article_id      UUID        REFERENCES articles(id) ON DELETE CASCADE,
    url             TEXT        NOT NULL UNIQUE,

    -- ── تاريخ الإضافة ────────────────────────────────────────────────────────
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index على publish_date للترتيب الزمني
CREATE INDEX IF NOT EXISTS idx_display_publish_date
    ON articles_display (publish_date DESC NULLS LAST);

-- Index على created_at للعرض الافتراضي (الأحدث أولاً)
CREATE INDEX IF NOT EXISTS idx_display_created_at
    ON articles_display (created_at DESC);

-- RLS — نفس سياسة الجدول الأساسي
ALTER TABLE articles_display ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE articles_display IS
    'جدول مبسط للمحررين — العنوان والصورة والكونتنت الكامل والساموري وموعد الخبر فقط.';
