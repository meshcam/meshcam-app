import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MetaJSON = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    # int PK for cheap FK joins; public_id is what the API/UI sees (see refs.py)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    # Hidden = dropped from filter pickers, not deleted — rows auto-recreate on
    # the next ingest from that slug, so hiding is the only sane "remove".
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)

    cameras: Mapped[list[Camera]] = relationship(back_populates="site")


class Camera(Base):
    """A mesh node. Despite the table name this covers relays and the gateway
    too (kind) — a relay is just a node that never sends photos."""

    __tablename__ = "cameras"
    __table_args__ = (UniqueConstraint("site_id", "slug", name="uq_cameras_site_slug"),)

    # int PK for cheap FK joins; public_id is what the API/UI sees (see refs.py)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="camera")  # camera|relay|gateway
    notes: Mapped[str | None] = mapped_column(Text(), default=None)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_battery_v: Mapped[float | None] = mapped_column(Float, default=None)

    site: Mapped[Site] = relationship(back_populates="cameras")


class DeviceToken(Base):
    """Bearer tokens for the mesh gateways / ingest devices (sha256 stored, never the token)."""

    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Telemetry(Base):
    """Node heartbeat — health without a photo. The gateway forwards mesh
    beacons here; rows age out after settings.telemetry_ttl_days."""

    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_telemetry_camera_received", "camera_id", "received_at"),
        Index("ix_telemetry_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    battery_v: Mapped[float | None] = mapped_column(Float, default=None)
    temp_c: Mapped[float | None] = mapped_column(Float, default=None)
    pressure_hpa: Mapped[float | None] = mapped_column(Float, default=None)
    rssi: Mapped[float | None] = mapped_column(Float, default=None)
    snr: Mapped[float | None] = mapped_column(Float, default=None)
    uptime_s: Mapped[int | None] = mapped_column(Integer, default=None)
    boot_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    fw_version: Mapped[str | None] = mapped_column(String(64), default=None)
    extra: Mapped[dict | None] = mapped_column(MetaJSON, default=None)

    camera: Mapped[Camera] = relationship()


class Command(Base):
    """Work queued for a mesh node, pulled by the gateway (nodes sleep and sit
    behind the tunnel, so it's poll-based). Lifecycle: pending -> delivered
    (gateway fetched it) -> done (e.g. the full-res arrived via ingest) /
    failed (gateway ack) / expired (purge gave up)."""

    __tablename__ = "commands"
    __table_args__ = (
        Index("ix_commands_status", "status"),
        Index("ix_commands_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    kind: Mapped[str] = mapped_column(String(32), default="fetch_full")
    event_id: Mapped[str | None] = mapped_column(String(128), default=None)
    payload: Mapped[dict | None] = mapped_column(MetaJSON, default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    requested_by: Mapped[str | None] = mapped_column(String(255), default=None)
    detail: Mapped[str | None] = mapped_column(Text(), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    camera: Mapped[Camera] = relationship()


OUTSTANDING = ("pending", "delivered")


photo_tags = Table(
    "photo_tags",
    Base.metadata,
    Column("photo_id", Uuid, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_photo_tags_tag_id", "tag_id"),
)


class Tag(Base):
    """Species/content label ("Buck", "Turkey", …). slug is the stable filter
    key; name preserves the casing the user typed."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))


class Photo(Base):
    """One capture event. The thumbnail arrives over the mesh first; full-res may follow
    later (deferred transfer), landing on the same row via the shared event_id."""

    __tablename__ = "photos"
    __table_args__ = (
        Index("ix_photos_captured_at_id", "captured_at", "id"),
        Index("ix_photos_received_at_id", "received_at", "id"),
        Index("ix_photos_camera_captured", "camera_id", "captured_at"),
        Index("ix_photos_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    thumb_key: Mapped[str | None] = mapped_column(String(512), default=None)
    full_key: Mapped[str | None] = mapped_column(String(512), default=None)
    thumb_size: Mapped[int | None] = mapped_column(Integer, default=None)
    full_size: Mapped[int | None] = mapped_column(Integer, default=None)
    content_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    meta: Mapped[dict | None] = mapped_column(MetaJSON, default=None)
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    camera: Mapped[Camera] = relationship()
    # selectin: tags ride along on every photo query (feed pages, single gets,
    # SSE payload builds) without N+1s; tag lists are tiny.
    tags: Mapped[list[Tag]] = relationship(
        secondary=photo_tags, order_by=Tag.slug, lazy="selectin", passive_deletes=True
    )
