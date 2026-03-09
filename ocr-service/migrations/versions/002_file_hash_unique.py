"""Add unique constraint for document file_hash

Revision ID: 002_file_hash_unique
Revises: 001_initial
Create Date: 2026-02-24 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_file_hash_unique"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "documents" not in inspector.get_table_names():
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("documents")}
    if "ix_documents_file_hash" in existing_indexes:
        op.drop_index("ix_documents_file_hash", table_name="documents")

    unique_constraints = inspector.get_unique_constraints("documents")
    has_file_hash_unique = any(
        set(uc.get("column_names") or []) == {"file_hash"} for uc in unique_constraints
    )
    if not has_file_hash_unique:
        op.create_unique_constraint(
            "uq_documents_file_hash",
            "documents",
            ["file_hash"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "documents" not in inspector.get_table_names():
        return

    unique_constraints = {uc["name"] for uc in inspector.get_unique_constraints("documents")}
    if "uq_documents_file_hash" in unique_constraints:
        op.drop_constraint("uq_documents_file_hash", "documents", type_="unique")

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("documents")}
    if "ix_documents_file_hash" not in existing_indexes:
        op.create_index("ix_documents_file_hash", "documents", ["file_hash"])
