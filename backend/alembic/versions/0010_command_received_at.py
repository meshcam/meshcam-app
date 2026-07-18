"""commands.received_at: leaf-confirmed receipt timestamp (bug 8, 2026-07-16).

"delivered" only ever meant "the gateway fetched it from the server" — commands
sat delivered for hours while the gateway TXed them onto a radio profile the
leaf never listened on (office-leaf outage, 07-15). leaf-0.12.0 acks received
command ids in its next announce; the gateway relays them to
POST /commands/{id}/ack {"status": "received"}. received stops redelivery but
stays expirable and completable (a received fetch_full still finishes via the
kind=full ingest).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "commands",
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("commands", "received_at")
