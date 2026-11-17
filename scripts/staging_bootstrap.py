"""Initialize an empty, explicitly named Sprint 5 staging database."""
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.db import get_engine
from app.models.orm import Base


def main():
    engine = get_engine()
    try:
        with engine.begin() as connection:
            if connection.dialect.name != "postgresql" or connection.exec_driver_sql(
                    "SELECT current_database()").scalar_one() != "marketpredict_sprint5_staging":
                raise RuntimeError("Se requiere la base aislada marketpredict_sprint5_staging.")
            if inspect(connection).get_table_names():
                raise RuntimeError("La inicialización requiere una base vacía.")
            Base.metadata.create_all(connection)
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "20261123_enterprise_commercial")
            for name in Base.metadata.tables:
                # Table names come only from static metadata.
                connection.exec_driver_sql(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
