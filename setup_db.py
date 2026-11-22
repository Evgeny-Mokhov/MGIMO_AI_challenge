"""Единый скрипт полной инициализации базы данных dekanat.db для запуска проекта."""

from scripts.init_db import init_db
from scripts.generate_schedule import generate_schedule
from scripts.seed_data import seed_data
from scripts.migrate_admin import migrate


def setup_all() -> None:
    print("🚀 Запуск полной инициализации базы данных ЯМОГУ...")
    init_db()
    generate_schedule()
    seed_data()
    migrate()
    print("\n🎉 База данных dekanat.db полностью готова к работе!")


if __name__ == "__main__":
    setup_all()
