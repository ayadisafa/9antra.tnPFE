import logging
from sqlalchemy import inspect, text
from app.database import engine, Base
import app.models  # Ensures model classes are loaded into Base metadata

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_database():
    """Inspects SQLite database tables and adds missing columns defined in SQLAlchemy models."""
    inspector = inspect(engine)

    with engine.connect() as conn:
        for table_name, table in Base.metadata.tables.items():
            if not inspector.has_table(table_name):
                logger.info(f"Creating missing table: {table_name}")
                table.create(bind=engine)
                continue

            # Fetch existing column names in SQLite table
            existing_columns = {
                col["name"] for col in inspector.get_columns(table_name)
            }

            # Compare against SQLAlchemy model columns
            for column in table.columns:
                if column.name not in existing_columns:
                    col_type = column.type.compile(engine.dialect)
                    
                    alter_stmt = (
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN {column.name} {col_type}"
                    )
                    
                    logger.info(
                        f"Adding missing column '{column.name}' to table '{table_name}'..."
                    )
                    conn.execute(text(alter_stmt))
                    conn.commit()

    logger.info("✅ Database migration completed successfully.")


if __name__ == "__main__":
    migrate_database()