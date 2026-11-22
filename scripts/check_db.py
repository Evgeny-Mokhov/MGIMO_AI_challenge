import os
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "dekanat.db"))

import sqlite3

def check_tables():
    # Подключаемся к нашей базе
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Запрашиваем список всех таблиц из системного каталога SQLite
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()

    print("📊 АНАЛИЗ БАЗЫ ДАННЫХ DEKANAT.DB 📊\n" + "="*40)

    for table in tables:
        table_name = table[0]
        
        # Пропускаем внутренние служебные таблицы SQLite
        if table_name.startswith("sqlite_"):
            continue

        print(f"\n📁 ТАБЛИЦА: {table_name}")
        
        # Запрашиваем структуру таблицы (PRAGMA table_info)
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = cursor.fetchall()
        
        # columns - это список кортежей. Название колонки находится под индексом 1
        col_names = [col[1] for col in columns]
        print(f"   Колонки: {', '.join(col_names)}")

        # Запрашиваем первые 3 записи, чтобы убедиться, что данные на месте
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 3;")
        rows = cursor.fetchall()
        
        if rows:
            print("   Данные (первые 3 строки):")
            for row in rows:
                print(f"   > {row}")
        else:
            print("   > Таблица пуста.")
            
    print("\n" + "="*40 + "\n✅ Проверка завершена.")
    
    conn.close()

if __name__ == "__main__":
    check_tables()
