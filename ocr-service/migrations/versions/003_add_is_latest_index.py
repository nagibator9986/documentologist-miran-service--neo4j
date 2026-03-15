"""Add composite index on (filename, is_latest) for version queries

The ix_documents_filename_latest index was defined in the ORM model from the
start but was added inline inside 001_initial.  This migration makes it an
explicit, idempotent step in the chain so tooling can detect and apply it
independently.  On fresh databases it is a no-op (001 already created it);
on databases that skipped 001's index DDL it will create it.

Revision ID: 003_add_is_latest_index
Revises: 002_file_hash_unique
Create Date: 2026-03-13 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_add_is_latest_index"
down_revision: Union[str, None] = "002_file_hash_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "documents" not in inspector.get_table_names():
        return

    existing = {idx["name"] for idx in inspector.get_indexes("documents")}
    if "ix_documents_filename_latest" not in existing:
        op.create_index(
            "ix_documents_filename_latest",
            "documents",
            ["filename", "is_latest"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "documents" not in inspector.get_table_names():
        return

    existing = {idx["name"] for idx in inspector.get_indexes("documents")}
    if "ix_documents_filename_latest" in existing:
        op.drop_index("ix_documents_filename_latest", table_name="documents")
