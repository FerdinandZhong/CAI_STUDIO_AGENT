"""add native memory management

Revision ID: b1c2d3e4f5a6
Revises: ae5e1dd6afb6
Create Date: 2026-04-29 00:01:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "ae5e1dd6afb6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    def safe_add_column(table, column):
        try:
            op.add_column(table, column)
        except Exception as e:
            print(f"Skipping add_column for {table}.{column.name}: {e}")

    # Add memory_mode to agents
    safe_add_column("agents", sa.Column("memory_mode", sa.String(), nullable=True))
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE agents SET memory_mode = 'NONE' WHERE memory_mode IS NULL"))

    # Add is_memory_managed to mcp_instances
    safe_add_column("mcp_instances", sa.Column("is_memory_managed", sa.Boolean(), nullable=True))
    conn.execute(sa.text("UPDATE mcp_instances SET is_memory_managed = 0 WHERE is_memory_managed IS NULL"))

    # Create workflow_memory_config table
    try:
        op.create_table(
            "workflow_memory_config",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflows.id"), nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, default=False),
            sa.Column("collection_name", sa.String(), nullable=False),
            sa.Column("auto_inject", sa.Boolean(), nullable=False, default=True),
            sa.Column("session_scope", sa.String(), nullable=False, default="CROSS_SESSION"),
            sa.Column("store_mode", sa.String(), nullable=False, default="AGENT_CONTROLLED"),
            sa.Column("filter_config", sa.JSON(), nullable=True),
            sa.Column("backend_config", sa.JSON(), nullable=True),
            sa.Column("note_schema", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    except Exception as e:
        print(f"Skipping create_table workflow_memory_config: {e}")


def downgrade() -> None:
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"

    try:
        op.drop_table("workflow_memory_config")
    except Exception as e:
        print(f"Skipping drop_table workflow_memory_config: {e}")

    if is_sqlite:
        # SQLite doesn't support DROP COLUMN; skip silently
        print("Skipping DROP COLUMN on SQLite for agents.memory_mode and mcp_instances.is_memory_managed")
    else:

        def safe_drop_column(table, column):
            try:
                op.drop_column(table, column)
            except Exception as e:
                print(f"Skipping drop_column for {table}.{column}: {e}")

        safe_drop_column("agents", "memory_mode")
        safe_drop_column("mcp_instances", "is_memory_managed")
