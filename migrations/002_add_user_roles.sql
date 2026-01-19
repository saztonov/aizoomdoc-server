-- Миграция: добавление ролей пользователей
-- Дата: 2026-01-19
-- Описание: Добавляет поле role в таблицу users с двумя возможными значениями: 'user', 'admin'

-- ===== ДОБАВЛЕНИЕ ПОЛЯ РОЛИ В ТАБЛИЦУ USERS =====
ALTER TABLE users 
    ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'user' 
    CHECK (role IN ('user', 'admin'));

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

COMMENT ON COLUMN users.role IS 'Роль пользователя: user (только свои чаты), admin (все чаты)';

-- ===== ОБНОВЛЕНИЕ СУЩЕСТВУЮЩИХ ПОЛЬЗОВАТЕЛЕЙ =====
-- Пользователь admin получает роль admin
UPDATE users SET role = 'admin' WHERE username = 'admin';

-- Остальные пользователи получают роль user (уже по умолчанию)
UPDATE users SET role = 'user' WHERE role IS NULL;

