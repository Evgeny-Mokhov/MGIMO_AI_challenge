import os
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "dekanat.db"))

# migrate_admin.py
import sqlite3


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Проверяем таблицу schedule_changes
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedule_changes'")
    has_changes_table = cursor.fetchone() is not None

    if has_changes_table:
        cursor.execute("PRAGMA table_info(schedule_changes)")
        columns = {row[1] for row in cursor.fetchall()}
        # Если осталась прошлая версия таблицы (с полем reason), пересоздаем пустую под новую схему
        if "comment" not in columns:
            cursor.execute("DROP TABLE schedule_changes")
            has_changes_table = False

    if not has_changes_table:
        cursor.execute('''
        CREATE TABLE schedule_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            change_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('cancelled', 'rescheduled')),
            new_room TEXT,
            new_start_time TEXT,
            new_end_time TEXT,
            comment TEXT,
            UNIQUE(schedule_id, change_date),
            FOREIGN KEY(schedule_id) REFERENCES schedule(id) ON DELETE CASCADE
        )
        ''')
        print("✅ Таблица schedule_changes создана (пустая).")
    else:
        print("ℹ️ Таблица schedule_changes уже актуальна.")

    # 2. Проверяем колонку admin_comment в таблице requests
    cursor.execute("PRAGMA table_info(requests)")
    request_columns = {row[1] for row in cursor.fetchall()}
    if "admin_comment" not in request_columns:
        cursor.execute("ALTER TABLE requests ADD COLUMN admin_comment TEXT")
        print("✅ В таблицу requests добавлена колонка admin_comment.")
    else:
        print("ℹ️ Колонка admin_comment в таблице requests уже существует.")


    # 3. Таблица отметок прочтения массовых уведомлений
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notification_reads (
        user_id INTEGER NOT NULL,
        notification_id INTEGER NOT NULL,
        PRIMARY KEY (user_id, notification_id),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE CASCADE
    )
    """)

    conn.commit()
    conn.close()
    print("✅ Миграция завершена, все существующие данные сохранены.")


if __name__ == "__main__":
    migrate()
