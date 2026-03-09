"""Create documents table

Revision ID: 001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, UUID

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create enum type once; table column uses create_type=False to avoid duplicate DDL.
    document_status_type = PGEnum(
        "pending", "processing", "completed", "failed",
        name="document_status",
    )
    document_status_type.create(bind, checkfirst=True)

    if "documents" not in inspector.get_table_names():
        op.create_table(
            "documents",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("file_hash", sa.String(64), nullable=False),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("s3_path", sa.String(1024), nullable=True),
            sa.Column("result_path", sa.String(1024), nullable=True),
            sa.Column(
                "status",
                PGEnum(
                    "pending",
                    "processing",
                    "completed",
                    "failed",
                    name="document_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    if "documents" in inspector.get_table_names():
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("documents")}
        if "ix_documents_file_hash" not in existing_indexes:
            op.create_index("ix_documents_file_hash", "documents", ["file_hash"])
        if "ix_documents_filename_latest" not in existing_indexes:
            op.create_index("ix_documents_filename_latest", "documents", ["filename", "is_latest"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "documents" in inspector.get_table_names():
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("documents")}
        if "ix_documents_filename_latest" in existing_indexes:
            op.drop_index("ix_documents_filename_latest")
        if "ix_documents_file_hash" in existing_indexes:
            op.drop_index("ix_documents_file_hash")
        op.drop_table("documents")

    sa.Enum(name="document_status").drop(bind, checkfirst=True)
