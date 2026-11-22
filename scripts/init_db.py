import os
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "dekanat.db"))

import sqlite3
from passlib.context import CryptContext

# Настройка контекста для хэширования паролей (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def init_db():
    # Подключение к локальной базе данных SQLite (файл создастся автоматически)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Включение поддержки внешних ключей
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Таблица пользователей (учетные данные и роли для авторизации)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin', 'student')),
        full_name TEXT NOT NULL
    )
    ''')

    # 2. Таблица профилей студентов (изолированные академические данные)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS student_profiles (
        user_id INTEGER PRIMARY KEY,
        faculty TEXT NOT NULL,
        group_name TEXT NOT NULL,
        grade_book TEXT UNIQUE NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    ''')
        # 5. Таблица успеваемости (grades)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS grades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        semester INTEGER NOT NULL,
        subject TEXT NOT NULL,
        assessment_type TEXT NOT NULL CHECK(assessment_type IN ('экзамен', 'зачет')),
        grade TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    ''')

    # 6. Таблица заявок на справки (requests)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        request_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'в обработке' CHECK(status IN ('в обработке', 'готова', 'отклонена')),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    ''')

    # 7. Таблица уведомлений (notifications)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,  -- Если NULL, считаем уведомление массовым (для всех)
        message TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        is_read INTEGER DEFAULT 0 CHECK(is_read IN (0, 1)),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    ''')


    # Очистка таблиц при повторном запуске скрипта (для чистого теста жюри)
    cursor.execute('DELETE FROM student_profiles')
    cursor.execute('DELETE FROM users')

    # 3. Данные тестовых аккаунтов для жюри (логин, пароль, роль, ФИО)
    test_users = [
        ("admin_jury", "admin2026", "admin", "Системный Администратор"),
        ("stud_pmpu", "stud1234", "student", "Иванов Иван Иванович"),
        ("stud_pmi", "stud5678", "student", "Смирнова Анна Алексеевна")
    ]

    # Добавление пользователей с хэшированием паролей
    for username, password, role, full_name in test_users:
        cursor.execute('''
        INSERT INTO users (username, password_hash, role, full_name)
        VALUES (?, ?, ?, ?)
        ''', (username, get_password_hash(password), role, full_name))

    # 4. Привязка специфичных данных к профилям студентов
    # Студент 1 (ПМ-ПУ)
    cursor.execute("SELECT id FROM users WHERE username = 'stud_pmpu'")
    stud1_id = cursor.fetchone()[0]
    cursor.execute('''
    INSERT INTO student_profiles (user_id, faculty, group_name, grade_book)
    VALUES (?, 'ПМ-ПУ', 'Группа 101', 'PMPU-26-001')
    ''', (stud1_id,))

    # Студент 2 (ПМИ)
    cursor.execute("SELECT id FROM users WHERE username = 'stud_pmi'")
    stud2_id = cursor.fetchone()[0]
    cursor.execute('''
    INSERT INTO student_profiles (user_id, faculty, group_name, grade_book)
    VALUES (?, 'ПМИ', 'Группа 202', 'PMI-26-042')
    ''', (stud2_id,))

    conn.commit()
    conn.close()
    
    print("✅ База данных 'dekanat.db' успешно инициализирована.")
    print("Тестовые аккаунты для жюри:")
    print("1. Админ: admin_jury / admin2026")
    print("2. Студент (ПМ-ПУ): stud_pmpu / stud1234")
    print("3. Студент (ПМИ): stud_pmi / stud5678")

if __name__ == "__main__":
    init_db()