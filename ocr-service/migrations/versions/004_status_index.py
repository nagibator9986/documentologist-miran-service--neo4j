"""Add index on documents.status for polling worker

Revision ID: 004_status_index
Revises: 002_file_hash_unique
Create Date: 2026-02-27 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_status_index"
down_revision: Union[str, None] = "002_file_hash_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = [idx["name"] for idx in inspector.get_indexes("documents")]
    if "ix_documents_status" not in existing:
        op.create_index("ix_documents_status", "documents", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = [idx["name"] for idx in inspector.get_indexes("documents")]
    if "ix_documents_status" in existing:
        op.drop_index("ix_documents_status", table_name="documents")
