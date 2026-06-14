"""add rag_api_key to agent_configs

Revision ID: a1b2c3d4e5f6
Revises: 08fc8d728449
Create Date: 2026-06-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '08fc8d728449'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'agent_configs',
        sa.Column('rag_api_key', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('agent_configs', 'rag_api_key')
