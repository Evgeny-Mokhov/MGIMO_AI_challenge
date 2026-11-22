import os
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "dekanat.db"))

import sqlite3
import random

def generate_schedule():
    subjects_teachers = [
        {"subject": "Алгебра (Лекция)", "teacher": "Константинов А.В."},
        {"subject": "Алгебра (Практика)", "teacher": "Смирнов И.А."},
        {"subject": "Математический анализ (Лекция)", "teacher": "Орлов П.С."},
        {"subject": "Математический анализ (Практика)", "teacher": "Волкова Е.Д."},
        {"subject": "Аналитическая геометрия (Лекция)", "teacher": "Соколов М.М."},
        {"subject": "Аналитическая геометрия (Практика)", "teacher": "Лебедев Д.Н."},
        {"subject": "История", "teacher": "Романова А.И."},
        {"subject": "Основы Российской государственности", "teacher": "Воронцов В.В."},
        {"subject": "Основы программирования", "teacher": "Ильин С.П."},
        {"subject": "Английский язык", "teacher": "Попова М.А."}
    ]

    rooms = [f"Ауд. {i}" for i in range(101, 121)]
    groups = ["Группа 101", "Группа 202"]
    days_of_week = range(1, 7)
    week_types = [1, 2]

    time_slots = {
        1: ("09:30", "11:00"),
        2: ("11:10", "12:40"),
        3: ("13:40", "15:10"),
        4: ("15:20", "16:50"),
        5: ("17:00", "18:30")
    }

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Пересоздаем таблицу, чтобы очистить возможные лишние поля отмен/переносов
    cursor.execute("DROP TABLE IF EXISTS schedule_changes")
    cursor.execute("DROP TABLE IF EXISTS schedule")
    cursor.execute('''
    CREATE TABLE schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_name TEXT NOT NULL,
        week_type INTEGER NOT NULL CHECK(week_type IN (1, 2)),
        day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 1 AND 6),
        pair_number INTEGER NOT NULL CHECK(pair_number BETWEEN 1 AND 5),
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        subject TEXT NOT NULL,
        teacher TEXT NOT NULL,
        room TEXT NOT NULL
    )
    ''')

    occupied_rooms = {}
    schedule_records = []

    for week in week_types:
        for day in days_of_week:
            for group in groups:
                num_pairs = random.randint(2, 5)
                daily_subjects = random.sample(subjects_teachers, num_pairs)
                pair_slots = sorted(random.sample(range(1, 6), num_pairs))

                for slot_idx, pair_number in enumerate(pair_slots):
                    subject_info = daily_subjects[slot_idx]

                    time_key = (week, day, pair_number)
                    used_rooms = occupied_rooms.get(time_key, set())
                    available_rooms = list(set(rooms) - used_rooms)

                    if not available_rooms:
                        chosen_room = "Дистанционно"
                    else:
                        chosen_room = random.choice(available_rooms)
                        occupied_rooms.setdefault(time_key, set()).add(chosen_room)

                    start_time, end_time = time_slots[pair_number]

                    schedule_records.append((
                        group, week, day, pair_number,
                        start_time, end_time,
                        subject_info["subject"],
                        subject_info["teacher"],
                        chosen_room
                    ))

    cursor.executemany('''
    INSERT INTO schedule (
        group_name, week_type, day_of_week, pair_number,
        start_time, end_time, subject, teacher, room
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', schedule_records)

    conn.commit()
    conn.close()

    print(f"✅ Расписание перегенерировано. Записей: {len(schedule_records)}")

if __name__ == "__main__":
    generate_schedule()
