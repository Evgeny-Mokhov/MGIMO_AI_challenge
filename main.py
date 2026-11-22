# main.py
import mimetypes
import os
import sqlite3

mimetypes.add_type("image/avif", ".avif")
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from fastapi.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(env_path: Path) -> None:
    """Загружает переменные из .env без внешних зависимостей."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

# === Настройки безопасности и JWT ===
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production-yamogu")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "24"))
DB_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "dekanat.db"))
_cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
CORS_ORIGINS = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]

# Точка отсчета чередования недель (синхронно с календарем на фронте)
EPOCH_MONDAY = date(2026, 1, 5)

# Периоды каникул (месяц, день) — работают для любого года
HOLIDAY_PERIODS = [
    ((7, 1), (8, 31), "Летние каникулы"),
    ((12, 31), (12, 31), "Новогодние каникулы"),
    ((1, 1), (1, 8), "Новогодние каникулы"),
    ((1, 25), (2, 8), "Зимние каникулы"),
]

# Маппинг статусов заявок под CHECK-ограничение таблицы requests
REQUEST_STATUS_MAP = {
    "в обработке": "в обработке",
    "готова": "готова",
    "готово": "готова",
    "отклонена": "отклонена",
    "отклонено": "отклонена",
}

REQUEST_STATUS_DISPLAY = {
    "в обработке": "В обработке",
    "готова": "Готово",
    "отклонена": "Отклонено",
}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")

app = FastAPI(title="ЯМОГУ: Цифровой деканат", version="2.0.0")

def ensure_notification_reads_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute('''
        CREATE TABLE IF NOT EXISTS notification_reads (
            user_id INTEGER NOT NULL,
            notification_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, notification_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()


ensure_notification_reads_table()


# === Настройка CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    # Ищем index.html в корне проекта или внутри папки static
    root_index = BASE_DIR / "index.html"
    static_index = STATIC_DIR / "index.html"
    if root_index.is_file():
        return FileResponse(str(root_index))
    if static_index.is_file():
        return FileResponse(str(static_index))
    raise HTTPException(status_code=404, detail="Файл index.html не найден")


# === Pydantic Модели ===

class RequestCreate(BaseModel):
    request_type: str = Field(..., min_length=2, max_length=200)


class ScheduleChangeUpsert(BaseModel):
    schedule_id: int
    change_date: str  # YYYY-MM-DD
    status: Literal["cancelled", "rescheduled"]
    new_room: Optional[str] = None
    new_start_time: Optional[str] = None
    new_end_time: Optional[str] = None
    comment: Optional[str] = None


class AdminRequestUpdate(BaseModel):
    status: str
    admin_comment: Optional[str] = None


class AdminNotificationCreate(BaseModel):
    target_type: Literal["all", "group", "student"]
    target_value: Optional[str] = None  # group_name для 'group', user_id для 'student'
    message: str = Field(..., min_length=1, max_length=1000)


class AdminStudentCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)
    full_name: str = Field(..., min_length=3, max_length=150)
    faculty: str = Field(..., min_length=2, max_length=100)
    group_name: str = Field(..., min_length=2, max_length=100)
    grade_book: str = Field(..., min_length=2, max_length=64)

class AdminGradeCreate(BaseModel):
    user_id: int
    semester: int = Field(..., ge=1, le=12)
    subject: str = Field(..., min_length=2, max_length=150)
    assessment_type: Literal["экзамен", "зачет"]
    grade: str = Field(..., min_length=1, max_length=50)


# === Вспомогательные функции ===

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_week_type_for_date(target_date: date) -> int:
    """Определяет week_type (1 или 2) для календарной даты синхронно с фронтом."""
    monday = target_date - timedelta(days=target_date.weekday())
    diff_days = (monday - EPOCH_MONDAY).days
    week_index = diff_days // 7
    return 1 if week_index % 2 == 0 else 2


def get_holiday_name(target_date: date) -> Optional[str]:
    md = (target_date.month, target_date.day)
    for start_md, end_md, title in HOLIDAY_PERIODS:
        if start_md <= md <= end_md:
            return title
    return None


def notify_group(db: sqlite3.Connection, group_name: str, message: str) -> int:
    """Отправляет уведомление всем студентам указанной группы."""
    cursor = db.cursor()
    cursor.execute("SELECT user_id FROM student_profiles WHERE group_name = ?", (group_name,))
    student_ids = [row["user_id"] for row in cursor.fetchall()]
    if not student_ids:
        return 0
    ts = now_str()
    cursor.executemany(
        "INSERT INTO notifications (user_id, message, created_at, is_read) VALUES (?, ?, ?, 0)",
        [(uid, message, ts) for uid in student_ids],
    )
    return len(student_ids)


# === Зависимости авторизации и ролей ===

def get_current_user(token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось проверить учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: Optional[str] = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    cursor = db.cursor()
    cursor.execute("SELECT id, username, role, full_name FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if user is None:
        raise credentials_exception
    return dict(user)


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Строгая проверка прав администратора для всех /api/admin/..."""
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещен: требуются права администратора",
        )
    return current_user


def require_student(current_user: dict = Depends(get_current_user)) -> dict:
    """Проверка роли студента для изолированных студенческих маршрутов."""
    if current_user["role"] != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ разрешен только студентам",
        )
    return current_user


# === Общие эндпоинты авторизации ===

@app.post("/api/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, password_hash, role, full_name FROM users WHERE username = ?", (form_data.username,))
    user = cursor.fetchone()

    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": str(user["id"]), "role": user["role"]})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user["role"],
        "full_name": user["full_name"],
        "user_id": user["id"],
    }


@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    """Возвращает базовые данные текущего авторизованного пользователя любой роли."""
    return current_user


# === Защищенные эндпоинты студента (строгая изоляция по user_id) ===

@app.get("/api/profile")
def get_profile(current_user: dict = Depends(require_student), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute('''
        SELECT u.full_name, sp.faculty, sp.group_name, sp.grade_book
        FROM users u
        JOIN student_profiles sp ON u.id = sp.user_id
        WHERE u.id = ?
    ''', (current_user["id"],))
    profile = cursor.fetchone()
    if not profile:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return dict(profile)


@app.get("/api/schedule")
def get_student_schedule(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    week_type: Optional[int] = None,
    current_user: dict = Depends(require_student),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Расписание строго для группы текущего студента.
    В календарном режиме (?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD) вычисляет week_type,
    учитывает каникулы и накладывает точечные правки из schedule_changes строго по дате.
    """
    cursor = db.cursor()
    cursor.execute("SELECT group_name FROM student_profiles WHERE user_id = ?", (current_user["id"],))
    student_info = cursor.fetchone()
    if not student_info:
        raise HTTPException(status_code=404, detail="Группа студента не найдена")

    group_name = student_info["group_name"]

    # Обратная совместимость при запросе ?week_type=1|2 без дат
    if start_date is None and week_type is not None:
        if week_type not in (1, 2):
            raise HTTPException(status_code=400, detail="week_type должен быть 1 или 2")
        cursor.execute('''
            SELECT id AS schedule_id, day_of_week, pair_number, start_time, end_time,
                   subject, teacher, room
            FROM schedule
            WHERE group_name = ? AND week_type = ?
            ORDER BY day_of_week ASC, pair_number ASC
        ''', (group_name, week_type))
        rows = []
        for row in cursor.fetchall():
            item = dict(row)
            item.update({
                "date": None,
                "week_type": week_type,
                "original_start_time": item["start_time"],
                "original_end_time": item["end_time"],
                "original_room": item["room"],
                "status": "normal",
                "comment": None,
            })
            rows.append(item)
        return {"group": group_name, "week_type": week_type, "holidays": {}, "schedule": rows}

    # Календарный режим
    try:
        if start_date:
            start_dt = date.fromisoformat(start_date)
            end_dt = date.fromisoformat(end_date) if end_date else start_dt
        else:
            today = date.today()
            start_dt = today - timedelta(days=today.weekday())
            end_dt = start_dt + timedelta(days=5)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")

    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="end_date не может быть раньше start_date")
    if (end_dt - start_dt).days > 31:
        raise HTTPException(status_code=400, detail="Максимальный диапазон запроса — 31 день")

    cursor.execute('''
        SELECT id, week_type, day_of_week, pair_number, start_time, end_time, subject, teacher, room
        FROM schedule
        WHERE group_name = ?
        ORDER BY pair_number ASC
    ''', (group_name,))
    base_by_week_day = {}
    for row in cursor.fetchall():
        base_by_week_day.setdefault((row["week_type"], row["day_of_week"]), []).append(dict(row))

    cursor.execute('''
        SELECT sc.schedule_id, sc.change_date, sc.status,
               sc.new_room, sc.new_start_time, sc.new_end_time, sc.comment
        FROM schedule_changes sc
        JOIN schedule s ON s.id = sc.schedule_id
        WHERE s.group_name = ? AND sc.change_date BETWEEN ? AND ?
    ''', (group_name, start_dt.isoformat(), end_dt.isoformat()))
    changes_map = {
        (row["schedule_id"], row["change_date"]): dict(row)
        for row in cursor.fetchall()
    }

    holidays = {}
    schedule_result = []
    current_dt = start_dt

    while current_dt <= end_dt:
        date_str = current_dt.isoformat()
        holiday_title = get_holiday_name(current_dt)
        if holiday_title:
            holidays[date_str] = holiday_title
            current_dt += timedelta(days=1)
            continue

        iso_day = current_dt.isoweekday()
        if iso_day <= 6:
            w_type = get_week_type_for_date(current_dt)
            for lesson in base_by_week_day.get((w_type, iso_day), []):
                change = changes_map.get((lesson["id"], date_str))
                status_val = change["status"] if change else "normal"
                effective_room = change["new_room"] if (change and change["new_room"]) else lesson["room"]
                effective_start = change["new_start_time"] if (change and change["new_start_time"]) else lesson["start_time"]
                effective_end = change["new_end_time"] if (change and change["new_end_time"]) else lesson["end_time"]

                schedule_result.append({
                    "schedule_id": lesson["id"],
                    "date": date_str,
                    "week_type": w_type,
                    "day_of_week": iso_day,
                    "pair_number": lesson["pair_number"],
                    "subject": lesson["subject"],
                    "teacher": lesson["teacher"],
                    "start_time": effective_start,
                    "end_time": effective_end,
                    "room": effective_room,
                    "original_start_time": lesson["start_time"],
                    "original_end_time": lesson["end_time"],
                    "original_room": lesson["room"],
                    "status": status_val,
                    "comment": change["comment"] if change else None,
                })

        current_dt += timedelta(days=1)

    return {
        "group": group_name,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
        "holidays": holidays,
        "schedule": schedule_result,
    }
@app.get("/api/grades")
def get_grades(current_user: dict = Depends(require_student), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, semester, subject, assessment_type, grade
        FROM grades
        WHERE user_id = ?
        ORDER BY semester ASC, id ASC
    ''', (current_user["id"],))
    return [dict(row) for row in cursor.fetchall()]



@app.get("/api/notifications")
def get_notifications(current_user: dict = Depends(require_student), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute('''
        SELECT
            n.id,
            n.message,
            n.created_at,
            CASE
                WHEN n.user_id IS NOT NULL THEN n.is_read
                WHEN nr.notification_id IS NOT NULL THEN 1
                ELSE 0
            END AS is_read
        FROM notifications n
        LEFT JOIN notification_reads nr
            ON nr.notification_id = n.id AND nr.user_id = ?
        WHERE n.user_id = ? OR n.user_id IS NULL
        ORDER BY n.created_at DESC, n.id DESC
    ''', (current_user["id"], current_user["id"]))
    return [dict(row) for row in cursor.fetchall()]


@app.patch("/api/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    current_user: dict = Depends(require_student),
    db: sqlite3.Connection = Depends(get_db),
):
    """Отмечает уведомление прочитанным строго для текущего студента."""
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, user_id FROM notifications WHERE id = ? AND (user_id = ? OR user_id IS NULL)",
        (notification_id, current_user["id"]),
    )
    notif = cursor.fetchone()
    if not notif:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")

    if notif["user_id"] is not None:
        cursor.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, current_user["id"]),
        )
    else:
        cursor.execute(
            "INSERT OR IGNORE INTO notification_reads (user_id, notification_id) VALUES (?, ?)",
            (current_user["id"], notification_id),
        )

    db.commit()
    return {"id": notification_id, "is_read": 1}



@app.get("/api/requests")
def get_requests(current_user: dict = Depends(require_student), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute('''
        SELECT id, request_type, status, created_at, admin_comment
        FROM requests
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
    ''', (current_user["id"],))
    return [dict(row) for row in cursor.fetchall()]


@app.post("/api/requests", status_code=status.HTTP_201_CREATED)
def create_request(
    req_data: RequestCreate,
    current_user: dict = Depends(require_student),
    db: sqlite3.Connection = Depends(get_db),
):
    request_type = req_data.request_type.strip()
    if not request_type:
        raise HTTPException(status_code=400, detail="Укажите тип справки")

    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO requests (user_id, request_type, status, created_at)
        VALUES (?, ?, 'в обработке', ?)
    ''', (current_user["id"], request_type, now_str()))
    db.commit()
    return {"message": "Заявка успешно создана", "request_type": request_type}


# =====================================================================
# === АДМИН-ПАНЕЛЬ (/api/admin/...) — СТРОГО РОЛЬ 'admin' (иначе 403) ===
# =====================================================================

@app.get("/api/admin/groups")
def admin_get_groups(
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Возвращает список всех учебных групп (из расписания и профилей студентов)."""
    cursor = db.cursor()
    cursor.execute('''
        SELECT group_name FROM schedule
        UNION
        SELECT group_name FROM student_profiles
        ORDER BY group_name ASC
    ''')
    return {"groups": [row["group_name"] for row in cursor.fetchall()]}


@app.get("/api/admin/schedule")
def admin_get_schedule_for_date(
    group_name: str = Query(..., min_length=1),
    target_date: str = Query(..., alias="date"),
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Возвращает расписание указанной группы на конкретную дату YYYY-MM-DD:
    вычисляет week_type (1/2), учитывает каникулы и накладывает правки из schedule_changes.
    """
    try:
        dt = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")

    iso_day = dt.isoweekday()
    w_type = get_week_type_for_date(dt)
    holiday_title = get_holiday_name(dt)

    if holiday_title or iso_day == 7:
        return {
            "group": group_name,
            "date": target_date,
            "week_type": w_type,
            "day_of_week": iso_day,
            "is_sunday": iso_day == 7,
            "holiday": holiday_title,
            "schedule": [],
        }

    cursor = db.cursor()
    cursor.execute('''
        SELECT
            s.id AS schedule_id,
            s.day_of_week,
            s.pair_number,
            s.subject,
            s.teacher,
            s.start_time AS original_start_time,
            s.end_time AS original_end_time,
            s.room AS original_room,
            sc.id AS change_id,
            sc.status AS change_status,
            sc.new_room,
            sc.new_start_time,
            sc.new_end_time,
            sc.comment
        FROM schedule s
        LEFT JOIN schedule_changes sc
            ON sc.schedule_id = s.id AND sc.change_date = ?
        WHERE s.group_name = ? AND s.week_type = ? AND s.day_of_week = ?
        ORDER BY s.pair_number ASC
    ''', (target_date, group_name, w_type, iso_day))

    lessons = []
    for row in cursor.fetchall():
        status_val = row["change_status"] if row["change_status"] else "normal"
        lessons.append({
            "schedule_id": row["schedule_id"],
            "change_id": row["change_id"],
            "date": target_date,
            "week_type": w_type,
            "day_of_week": row["day_of_week"],
            "pair_number": row["pair_number"],
            "subject": row["subject"],
            "teacher": row["teacher"],
            "start_time": row["new_start_time"] or row["original_start_time"],
            "end_time": row["new_end_time"] or row["original_end_time"],
            "room": row["new_room"] or row["original_room"],
            "original_start_time": row["original_start_time"],
            "original_end_time": row["original_end_time"],
            "original_room": row["original_room"],
            "status": status_val,
            "comment": row["comment"],
        })

    return {
        "group": group_name,
        "date": target_date,
        "week_type": w_type,
        "day_of_week": iso_day,
        "is_sunday": False,
        "holiday": None,
        "schedule": lessons,
    }


@app.post("/api/admin/schedule/changes")
def admin_upsert_schedule_change(
    payload: ScheduleChangeUpsert,
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Отменяет или переносит пару на конкретную дату и шлёт уведомление всей группе."""
    try:
        dt = date.fromisoformat(payload.change_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")

    if get_holiday_name(dt):
        raise HTTPException(status_code=400, detail="На каникулярную дату нельзя вносить изменения пар")

    cursor = db.cursor()
    cursor.execute('''
        SELECT id, group_name, week_type, day_of_week, pair_number,
               start_time, end_time, subject, room
        FROM schedule
        WHERE id = ?
    ''', (payload.schedule_id,))
    lesson = cursor.fetchone()
    if not lesson:
        raise HTTPException(status_code=404, detail="Пара в базовом расписании не найдена")

    # Проверяем, что выбранная дата действительно соответствует этой паре
    if get_week_type_for_date(dt) != lesson["week_type"] or dt.isoweekday() != lesson["day_of_week"]:
        raise HTTPException(
            status_code=400,
            detail="Выбранная пара не проводится в указанную календарную дату",
        )

    new_room = payload.new_room.strip() if payload.new_room else None
    new_start = payload.new_start_time.strip() if payload.new_start_time else None
    new_end = payload.new_end_time.strip() if payload.new_end_time else None
    comment = payload.comment.strip() if payload.comment else None

    if payload.status == "cancelled":
        new_room, new_start, new_end = None, None, None
    else:
        if (new_start and not new_end) or (new_end and not new_start):
            raise HTTPException(status_code=400, detail="При изменении времени укажите и начало, и конец пары")
        if not new_room and not new_start:
            raise HTTPException(status_code=400, detail="Для переноса укажите новую аудиторию или новое время")

    # Upsert по уникальной паре (schedule_id, change_date)
    cursor.execute('''
        INSERT INTO schedule_changes (
            schedule_id, change_date, status, new_room, new_start_time, new_end_time, comment
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(schedule_id, change_date) DO UPDATE SET
            status = excluded.status,
            new_room = excluded.new_room,
            new_start_time = excluded.new_start_time,
            new_end_time = excluded.new_end_time,
            comment = excluded.comment
    ''', (payload.schedule_id, payload.change_date, payload.status, new_room, new_start, new_end, comment))

    # Формируем авто-уведомление для студентов группы
    short_date = dt.strftime("%d.%m")
    if payload.status == "cancelled":
        notif_text = f"Изменение на {short_date}: {lesson['subject']} отменена."
    else:
        details = []
        if new_room and new_room != lesson["room"]:
            details.append(f"в {new_room}")
        if new_start and new_end and (new_start != lesson["start_time"] or new_end != lesson["end_time"]):
            details.append(f"на {new_start}–{new_end}")
        details_str = " ".join(details) if details else "обновлены параметры занятия"
        notif_text = f"Изменение на {short_date}: {lesson['subject']} перенесена ({details_str})."

    if comment:
        notif_text += f" Причина: {comment}"

    notified_count = notify_group(db, lesson["group_name"], notif_text)
    db.commit()

    return {
        "message": "Изменение расписания сохранено",
        "status": payload.status,
        "group_name": lesson["group_name"],
        "notified_students": notified_count,
    }


@app.delete("/api/admin/schedule/changes/{schedule_id}")
def admin_delete_schedule_change(
    schedule_id: int,
    target_date: str = Query(..., alias="date"),
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Удаляет точечную правку на дату (возвращает пару к базовому расписанию) и уведомляет группу."""
    try:
        dt = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")

    cursor = db.cursor()
    cursor.execute('''
        SELECT sc.id, s.group_name, s.subject, s.start_time, s.end_time, s.room
        FROM schedule_changes sc
        JOIN schedule s ON s.id = sc.schedule_id
        WHERE sc.schedule_id = ? AND sc.change_date = ?
    ''', (schedule_id, target_date))
    existing = cursor.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Изменение на указанную дату не найдено")

    cursor.execute("DELETE FROM schedule_changes WHERE id = ?", (existing["id"],))

    short_date = dt.strftime("%d.%m")
    notif_text = (
        f"Изменение на {short_date}: {existing['subject']} возвращена в обычное расписание "
        f"({existing['start_time']}–{existing['end_time']}, {existing['room']})."
    )
    notified_count = notify_group(db, existing["group_name"], notif_text)
    db.commit()

    return {
        "message": "Пара возвращена к базовому расписанию",
        "group_name": existing["group_name"],
        "notified_students": notified_count,
    }


# --- Заявки на справки (админ) ---

@app.get("/api/admin/requests")
def admin_get_requests(
    filter_status: Optional[str] = Query(default="all"),
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Список всех заявок студентов с фильтрацией (all / pending / completed)."""
    cursor = db.cursor()
    base_query = '''
        SELECT
            r.id,
            r.user_id,
            u.full_name,
            COALESCE(sp.group_name, '—') AS group_name,
            COALESCE(sp.faculty, '—') AS faculty,
            r.request_type,
            r.status,
            r.created_at,
            r.admin_comment
        FROM requests r
        JOIN users u ON u.id = r.user_id
        LEFT JOIN student_profiles sp ON sp.user_id = r.user_id
    '''
    if filter_status == "pending":
        base_query += " WHERE r.status = 'в обработке'"
    elif filter_status == "completed":
        base_query += " WHERE r.status IN ('готова', 'отклонена')"

    base_query += " ORDER BY r.created_at DESC, r.id DESC"
    cursor.execute(base_query)
    return [dict(row) for row in cursor.fetchall()]


@app.patch("/api/admin/requests/{request_id}")
def admin_update_request(
    request_id: int,
    payload: AdminRequestUpdate,
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Меняет статус заявки ('Готово'/'Отклонено'), сохраняет комментарий и уведомляет студента."""
    normalized_status = REQUEST_STATUS_MAP.get(payload.status.strip().lower())
    if not normalized_status:
        raise HTTPException(
            status_code=400,
            detail="Недопустимый статус. Используйте 'Готово', 'Отклонено' или 'В обработке'",
        )

    cursor = db.cursor()
    cursor.execute("SELECT id, user_id, request_type FROM requests WHERE id = ?", (request_id,))
    req_row = cursor.fetchone()
    if not req_row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    comment = payload.admin_comment.strip() if payload.admin_comment else None
    cursor.execute(
        "UPDATE requests SET status = ?, admin_comment = ? WHERE id = ?",
        (normalized_status, comment, request_id),
    )

    pretty_status = REQUEST_STATUS_DISPLAY[normalized_status]
    notif_text = f"Заявка «{req_row['request_type']}»: статус изменён на «{pretty_status}»."
    if comment:
        notif_text += f" Комментарий деканата: {comment}"

    cursor.execute(
        "INSERT INTO notifications (user_id, message, created_at, is_read) VALUES (?, ?, ?, 0)",
        (req_row["user_id"], notif_text, now_str()),
    )
    db.commit()

    return {
        "message": "Статус заявки обновлён, студенту отправлено уведомление",
        "id": request_id,
        "status": normalized_status,
        "admin_comment": comment,
    }


# --- Рассылка уведомлений (админ) ---

@app.post("/api/admin/notifications", status_code=status.HTTP_201_CREATED)
def admin_send_notification(
    payload: AdminNotificationCreate,
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Отправляет объявление всем студентам, конкретной группе или одному студенту."""
    text = payload.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст уведомления не может быть пустым")

    cursor = db.cursor()
    ts = now_str()

    if payload.target_type == "all":
        cursor.execute(
            "INSERT INTO notifications (user_id, message, created_at, is_read) VALUES (NULL, ?, ?, 0)",
            (text, ts),
        )
        db.commit()
        return {"message": "Объявление отправлено всем студентам", "recipients": "all"}

    if payload.target_type == "group":
        group_name = (payload.target_value or "").strip()
        if not group_name:
            raise HTTPException(status_code=400, detail="Укажите учебную группу")
        sent_count = notify_group(db, group_name, text)
        if sent_count == 0:
            raise HTTPException(status_code=404, detail=f"В группе «{group_name}» нет зарегистрированных студентов")
        db.commit()
        return {"message": f"Уведомление отправлено группе {group_name}", "recipients": sent_count}

    # target_type == "student"
    try:
        student_id = int(payload.target_value or "")
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный ID студента")

    cursor.execute("SELECT id, full_name FROM users WHERE id = ? AND role = 'student'", (student_id,))
    student = cursor.fetchone()
    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")

    cursor.execute(
        "INSERT INTO notifications (user_id, message, created_at, is_read) VALUES (?, ?, ?, 0)",
        (student_id, text, ts),
    )
    db.commit()
    return {"message": f"Уведомление отправлено студенту {student['full_name']}", "recipients": 1}


# --- Управление студентами (админ) ---

@app.get("/api/admin/students")
def admin_get_students(
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Возвращает список всех зарегистрированных студентов."""
    cursor = db.cursor()
    cursor.execute('''
        SELECT u.id, u.username, u.full_name, sp.faculty, sp.group_name, sp.grade_book
        FROM users u
        JOIN student_profiles sp ON u.id = sp.user_id
        WHERE u.role = 'student'
        ORDER BY u.id DESC
    ''')
    return [dict(row) for row in cursor.fetchall()]


@app.post("/api/admin/students", status_code=status.HTTP_201_CREATED)
def admin_create_student(
    payload: AdminStudentCreate,
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Регистрирует нового студента с хэшированием пароля (bcrypt) и понятной обработкой дублей."""
    username = payload.username.strip()
    full_name = payload.full_name.strip()
    faculty = payload.faculty.strip()
    group_name = payload.group_name.strip()
    grade_book = payload.grade_book.strip()

    if not all([username, full_name, faculty, group_name, grade_book]):
        raise HTTPException(status_code=400, detail="Все поля студента должны быть заполнены")

    cursor = db.cursor()

    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail=f"Логин «{username}» уже занят")

    cursor.execute("SELECT user_id FROM student_profiles WHERE grade_book = ?", (grade_book,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail=f"Номер зачётной книжки «{grade_book}» уже используется")

    try:
        password_hash = get_password_hash(payload.password)
        cursor.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, 'student', ?)",
            (username, password_hash, full_name),
        )
        new_user_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO student_profiles (user_id, faculty, group_name, grade_book) VALUES (?, ?, ?, ?)",
            (new_user_id, faculty, group_name, grade_book),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Логин или номер зачётной книжки уже заняты")

    return {
        "id": new_user_id,
        "username": username,
        "full_name": full_name,
        "faculty": faculty,
        "group_name": group_name,
        "grade_book": grade_book,
    }
# --- Выставление оценок (админ) ---

ALLOWED_EXAM_GRADES = {"Отлично", "Хорошо", "Удовлетворительно", "Неудовлетворительно"}
ALLOWED_CREDIT_GRADES = {"Зачтено", "Незачет"}


@app.get("/api/admin/grades")
def admin_get_grades(
    student_id: Optional[int] = Query(default=None),
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Возвращает оценки выбранного студента (или всех студентов)."""
    cursor = db.cursor()
    query = '''
        SELECT g.id, g.user_id, u.full_name, COALESCE(sp.group_name, '—') AS group_name,
               g.semester, g.subject, g.assessment_type, g.grade
        FROM grades g
        JOIN users u ON u.id = g.user_id
        LEFT JOIN student_profiles sp ON sp.user_id = g.user_id
    '''
    params = []
    if student_id is not None:
        query += " WHERE g.user_id = ?"
        params.append(student_id)

    query += " ORDER BY g.semester DESC, g.id DESC"
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


@app.post("/api/admin/grades", status_code=status.HTTP_201_CREATED)
def admin_upsert_grade(
    payload: AdminGradeCreate,
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Выставляет или обновляет оценку студенту и отправляет ему уведомление."""
    subject = payload.subject.strip()
    grade = payload.grade.strip()

    if not subject:
        raise HTTPException(status_code=400, detail="Укажите название предмета")

    if payload.assessment_type == "экзамен" and grade not in ALLOWED_EXAM_GRADES:
        raise HTTPException(
            status_code=400,
            detail="Для экзамена допустимы: Отлично, Хорошо, Удовлетворительно, Неудовлетворительно",
        )
    if payload.assessment_type == "зачет" and grade not in ALLOWED_CREDIT_GRADES:
        raise HTTPException(status_code=400, detail="Для зачёта допустимы: Зачтено, Незачет")

    cursor = db.cursor()
    cursor.execute("SELECT id, full_name FROM users WHERE id = ? AND role = 'student'", (payload.user_id,))
    student = cursor.fetchone()
    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")

    # Если оценка по этому предмету в этом семестре уже есть — обновляем, иначе добавляем новую
    cursor.execute(
        "SELECT id FROM grades WHERE user_id = ? AND semester = ? AND LOWER(subject) = LOWER(?)",
        (payload.user_id, payload.semester, subject),
    )
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            "UPDATE grades SET subject = ?, assessment_type = ?, grade = ? WHERE id = ?",
            (subject, payload.assessment_type, grade, existing["id"]),
        )
        grade_id = existing["id"]
        action_text = "Обновлена оценка"
    else:
        cursor.execute(
            "INSERT INTO grades (user_id, semester, subject, assessment_type, grade) VALUES (?, ?, ?, ?, ?)",
            (payload.user_id, payload.semester, subject, payload.assessment_type, grade),
        )
        grade_id = cursor.lastrowid
        action_text = "Выставлена оценка"

    notif_text = f"{action_text} ({payload.semester} сем.): {subject} — {grade}."
    cursor.execute(
        "INSERT INTO notifications (user_id, message, created_at, is_read) VALUES (?, ?, ?, 0)",
        (payload.user_id, notif_text, now_str()),
    )
    db.commit()

    return {
        "id": grade_id,
        "updated": bool(existing),
        "message": f"{action_text} для {student['full_name']}, уведомление отправлено",
    }


@app.delete("/api/admin/grades/{grade_id}")
def admin_delete_grade(
    grade_id: int,
    admin_user: dict = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    """Удаляет ошибочно выставленную оценку."""
    cursor = db.cursor()
    cursor.execute("SELECT id FROM grades WHERE id = ?", (grade_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Запись об оценке не найдена")

    cursor.execute("DELETE FROM grades WHERE id = ?", (grade_id,))
    db.commit()
    return {"message": "Оценка удалена"}
