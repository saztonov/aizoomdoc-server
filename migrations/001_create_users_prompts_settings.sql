-- Миграция: создание таблиц для пользователей, промптов и настроек
-- Дата: 2026-01-13
-- Описание: Расширение схемы bd.json для поддержки многопользовательского режима

-- ===== ТАБЛИЦА ПОЛЬЗОВАТЕЛЕЙ =====
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR NOT NULL UNIQUE,
    static_token TEXT NOT NULL UNIQUE,  -- В MVP храним в открытом виде
    status VARCHAR DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'blocked')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_static_token ON users(static_token);
CREATE INDEX idx_users_status ON users(status);

COMMENT ON TABLE users IS 'Пользователи системы';
COMMENT ON COLUMN users.static_token IS 'Статичный токен для аутентификации (в MVP - открытый текст)';

-- ===== ТАБЛИЦА СИСТЕМНЫХ ПРОМПТОВ =====
CREATE TABLE IF NOT EXISTS prompts_system (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR NOT NULL UNIQUE,  -- llm_system, json_annotation, html_ocr, flash_extractor
    content TEXT NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX idx_prompts_system_name ON prompts_system(name);
CREATE INDEX idx_prompts_system_active ON prompts_system(is_active) WHERE is_active = true;

COMMENT ON TABLE prompts_system IS 'Системные промпты (управляются админами)';
COMMENT ON COLUMN prompts_system.name IS 'Уникальное имя промпта';

-- ===== ОБНОВЛЕНИЕ ТАБЛИЦЫ user_prompts (роли) =====
-- Таблица user_prompts уже существует в bd.json, но нужно убедиться в правильной структуре
-- Если нужны изменения:
ALTER TABLE user_prompts 
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_user_prompts_active ON user_prompts(is_active) WHERE is_active = true;

COMMENT ON TABLE user_prompts IS 'Пользовательские промпты-роли (создаются админами, выбираются пользователями)';

-- ===== ОБНОВЛЕНИЕ ТАБЛИЦЫ settings =====
-- Таблица settings уже существует, но нужно добавить поля для новой модели
ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS model_profile VARCHAR DEFAULT 'simple' 
        CHECK (model_profile IN ('simple', 'complex')),
    ADD COLUMN IF NOT EXISTS selected_role_prompt_id BIGINT REFERENCES user_prompts(id) ON DELETE SET NULL;

-- Меняем user_id на UUID если это еще VARCHAR
-- ALTER TABLE settings ALTER COLUMN user_id TYPE UUID USING user_id::uuid;
-- Для совместимости оставляем VARCHAR

CREATE INDEX IF NOT EXISTS idx_settings_user_id ON settings(user_id);
CREATE INDEX IF NOT EXISTS idx_settings_role ON settings(selected_role_prompt_id);

COMMENT ON COLUMN settings.model_profile IS 'Режим модели: simple (flash) или complex (flash+pro)';
COMMENT ON COLUMN settings.selected_role_prompt_id IS 'ID выбранной роли из user_prompts';

-- ===== ОБНОВЛЕНИЕ ТАБЛИЦЫ chats =====
-- Связь чата с пользователем
-- ALTER TABLE chats ALTER COLUMN user_id TYPE UUID USING user_id::uuid;
-- Для совместимости оставляем VARCHAR

-- ===== ТРИГГЕРЫ ДЛЯ АВТООБНОВЛЕНИЯ updated_at =====
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = timezone('utc'::text, now());
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Триггер для prompts_system
DROP TRIGGER IF EXISTS update_prompts_system_updated_at ON prompts_system;
CREATE TRIGGER update_prompts_system_updated_at
    BEFORE UPDATE ON prompts_system
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ===== НАЧАЛЬНЫЕ ДАННЫЕ =====

-- Создаем тестового пользователя для разработки
INSERT INTO users (username, static_token, status)
VALUES 
    ('admin', 'dev-static-token-admin-12345', 'active'),
    ('test_user', 'dev-static-token-test-67890', 'active')
ON CONFLICT (username) DO NOTHING;

-- Создаем системные промпты (контент загрузим позже из файлов)
INSERT INTO prompts_system (name, content, description, is_active, version)
VALUES 
    ('llm_system', '', 'Базовая системная инструкция анализа (ZOOM, шифры, квадранты)', true, 1),
    ('json_annotation', '', 'Правила интерпретации JSON-аннотаций', true, 1),
    ('html_ocr', '', 'Правила интерпретации HTML OCR', true, 1),
    ('flash_extractor', '', 'Промпт для этапа Flash (только в complex режиме)', true, 1)
ON CONFLICT (name) DO NOTHING;

-- Создаем примеры ролей в user_prompts
INSERT INTO user_prompts (user_id, name, content)
VALUES 
    ('default_user', 'Инженер', 'Ты опытный инженер-проектировщик с 10+ летним стажем. Твоя задача - детально анализировать техническую документацию с инженерной точки зрения.'),
    ('default_user', 'Экономист', 'Ты эксперт по сметам и экономике строительства. Фокусируйся на анализе спецификаций, объемов работ и стоимостных показателей.'),
    ('default_user', 'Инженер по гарантии', 'Ты специалист по гарантийному обслуживанию. Обращай особое внимание на дефекты, нестандартные решения и потенциальные проблемные зоны.')
ON CONFLICT DO NOTHING;

-- Создаем настройки по умолчанию для существующих пользователей
INSERT INTO settings (user_id, model_profile, page_settings)
SELECT username, 'simple', '{}'::jsonb
FROM users
WHERE NOT EXISTS (SELECT 1 FROM settings WHERE settings.user_id = users.username)
ON CONFLICT (user_id) DO NOTHING;

