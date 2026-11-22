import os
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "dekanat.db"))

import sqlite3
import random
from datetime import datetime, timedelta

def seed_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Очистка таблиц перед новой генерацией
    cursor.execute("DELETE FROM grades")
    cursor.execute("DELETE FROM requests")
    cursor.execute("DELETE FROM notifications")

    # Получаем ID всех студентов из базы
    cursor.execute("SELECT id FROM users WHERE role = 'student'")
    students = cursor.fetchall()

    if not students:
        print("Студенты не найдены. Убедитесь, что init_db.py был запущен.")
        conn.close()
        return

    # Пулы тестовых данных
    subjects_pool = [
        ("Алгебра", "экзамен", ["Отлично", "Хорошо", "Удовлетворительно"]),
        ("Математический анализ", "экзамен", ["Хорошо", "Удовлетворительно", "Неудовлетворительно"]),
        ("Аналитическая геометрия", "экзамен", ["Удовлетворительно", "Неудовлетворительно"]),
        ("История", "зачет", ["Зачтено"]),
        ("Основы Российской государственности", "зачет", ["Зачтено"]),
        ("Основы программирования", "экзамен", ["Отлично", "Хорошо"]),
        ("Английский язык", "зачет", ["Зачтено"]),
        ("Математический анализ", "зачет", ["Зачтено"]),
        ("История", "экзамен", ["Отлично", "Удовлетворительно"]),
        ("Основы программирования", "зачет", ["Зачтено"]),
        ("Английский язык", "экзамен", ["Отлично", "Хорошо"])
    ]

    request_types = [
        "Справка об обучении", 
        "Справка в военкомат (Приложение №4)", 
        "Справка-вызов на сессию", 
        "Характеристика с места учебы"
    ]
    request_statuses = ["в обработке", "готова", "отклонена"]
    
    notification_msgs = [
        "Дедлайн по практике перенесен на 3 дня.",
        "Завтра отменена лекция по Истории.",
        "Ваша справка готова, можете забрать в деканате.",
        "Срочно зайдите в деканат для уточнения документов!",
        "Опубликовано расписание промежуточной аттестации.",
        "Напоминание: срок оплаты обучения до 10 числа."
    ]

    grades_data = []
    requests_data = []
    notifications_data = []
    
    now = datetime.now()

    for (student_id,) in students:
        # 1. Успеваемость (5-6 предметов, 2-й семестр - весенний)
        student_subjects = random.sample(subjects_pool, random.randint(8, 10))
        for subj, assess_type, grades_list in student_subjects:
            grade = random.choice(grades_list)
            grades_data.append((student_id, random.randint(1, 2), subj, assess_type, grade))

        # 2. Заявки на справки (1-2 штуки)
        for _ in range(random.randint(1, 2)):
            req_type = random.choice(request_types)
            status = random.choice(request_statuses)
            # Дата от 1 до 5 дней назад
            req_date = now - timedelta(days=random.randint(1, 5), hours=random.randint(1, 12))
            requests_data.append((student_id, req_type, status, req_date.strftime("%Y-%m-%d %H:%M:%S")))

        # 3. Уведомления (ровно 3 штуки)
        student_notifs = random.sample(notification_msgs, 3)
        for msg in student_notifs:
            # Дата от 0 до 2 дней назад
            notif_date = now - timedelta(days=random.randint(0, 2), hours=random.randint(0, 23))
            is_read = random.choice([0, 1])
            notifications_data.append((student_id, msg, notif_date.strftime("%Y-%m-%d %H:%M:%S"), is_read))

    # Запись сгенерированных данных в таблицы
    cursor.executemany('''
        INSERT INTO grades (user_id, semester, subject, assessment_type, grade) 
        VALUES (?, ?, ?, ?, ?)
    ''', grades_data)

    cursor.executemany('''
        INSERT INTO requests (user_id, request_type, status, created_at) 
        VALUES (?, ?, ?, ?)
    ''', requests_data)

    cursor.executemany('''
        INSERT INTO notifications (user_id, message, created_at, is_read) 
        VALUES (?, ?, ?, ?)
    ''', notifications_data)

    conn.commit()
    conn.close()

    print("✅ Тестовые данные успешно загружены в базу:")
    print(f"   • Оценок сгенерировано: {len(grades_data)}")
    print(f"   • Заявок создано: {len(requests_data)}")
    print(f"   • Уведомлений отправлено: {len(notifications_data)}")

if __name__ == "__main__":
    seed_data()
