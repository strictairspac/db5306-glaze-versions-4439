"""已有演示库的幂等结构升级：为分段迁存补充档案列。

新建部署由 `schema.create_all` 直接建出完整结构；已存在的卷在启动时
补齐 `business_cases.archive_state` 并把 `payload` 放宽为可空（切换后原件清空）。
"""

from sqlalchemy import inspect, text

from src.config.settings import IS_MYSQL, engine


def upgrade():
    inspector = inspect(engine)
    if 'business_cases' not in inspector.get_table_names():
        return
    columns = {column['name']: column for column in inspector.get_columns('business_cases')}
    statements = []
    if 'archive_state' not in columns:
        statements.append(
            "ALTER TABLE business_cases ADD COLUMN archive_state VARCHAR(20) NOT NULL DEFAULT 'original'")
    if columns.get('payload') and not columns['payload']['nullable']:
        statements.append('ALTER TABLE business_cases MODIFY payload JSON NULL' if IS_MYSQL
                          else 'ALTER TABLE business_cases ALTER COLUMN payload DROP NOT NULL')
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
