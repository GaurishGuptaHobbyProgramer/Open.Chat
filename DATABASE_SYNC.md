# Database synchronization design

## Status and safety boundary

Automatic database synchronization is **not implemented or enabled**. The
application currently uses raw SQL across `app.py`; the initial Alembic
baseline contains 16 application tables and the later developer-access
revision adds two more. The local SQLite database currently contains the
original 16 tables, but there is
no change log, stable cross-database identity, conflict resolver, sync worker,
or authenticated sync API. This document is a design proposal for review, not
an operational procedure. No production database was accessed or changed.

Production PostgreSQL remains authoritative. Local-to-production writes must
be explicitly promoted by an administrator; running the local app or worker
must never publish development changes automatically. Production-to-local
replication can be automatic after the design and security review. A conflict
must pause that record for review rather than silently choose a winner.

The selected data scope includes accounts and password hashes as well as
profiles, settings, statuses, conversations, memberships, messages, attachment
metadata, read state, and notifications. Password hashes are credential
material even though they are one-way hashes: they must only be sent over
verified TLS, stored with restrictive local file permissions, excluded from
logs and status APIs, and covered by the same access controls as passwords.
Session cookies and tokens are never synchronized. Volatile presence, typing,
call signaling, and operational application events should be excluded from
replication unless a later review explicitly changes the allowlist.

## Proposed architecture

### Transactional change capture

Add a reviewed, additive Alembic revision for PostgreSQL and an explicit
SQLite-side schema initializer. Each participating database gets:

- A change log/outbox containing an event UUID, instance ID, table name,
  operation, stable entity UUID, schema version, base revision, after-image or
  tombstone, creation time, delivery state, and retry details.
- A checkpoint and deduplication table for each peer.
- A conflict table retaining the local and remote versions and their event IDs.
- Stable UUID identity on each synchronized row. Existing integer primary keys
  remain the app's local keys; foreign keys are resolved by stable UUID when
  applying events so independently allocated integer IDs cannot collide.

Database triggers capture inserts, updates, and explicit deletes in the same
transaction as application writes. This is necessary because application
writes are issued from many raw-SQL call sites. Trigger capture is restricted
to a fixed reviewed table/column allowlist. Applying a remote event suppresses
local capture for that transaction and records the received event ID, avoiding
sync loops and duplicate application.

### Delivery and conflict handling

The local worker polls a fixed PostgreSQL synchronization API over TLS; the
production service does not initiate a network connection to a developer
machine. The production API accepts only authenticated event batches with
valid instance credentials, schema versions, entity IDs, and allowlisted
fields. It never accepts SQL, table names outside the allowlist, or arbitrary
database operations.

Delivery is at-least-once. Stable event UUIDs, per-peer checkpoints, and a
deduplication constraint make retries have exactly-once effects. A committed
batch advances the checkpoint in the same transaction as its changes. Failed
batches remain pending, are logged without payload secrets, and retry with
bounded exponential backoff.

Events carry the base revision the sender last observed. If the receiver has
advanced that entity since the base revision, it records a conflict and does
not apply the incoming change. This avoids unreliable wall-clock ordering and
last-write-wins data loss. An administrator reviews the versions and promotes
an explicit resolution. Successful promotion creates a new revision; it does
not erase the conflict history.

Deletes are represented only by explicit, reviewed tombstones. A missing row
or a missing event is never interpreted as a delete. Tombstones are retained
until all authorized peers have acknowledged them and the recovery window has
passed.

### Attachments

Database rows currently hold attachment names, MIME types, sizes, and URLs;
file bytes are stored under `static/uploads`. The SQLite importer does not
transfer those files. Render's service filesystem must not be assumed
durable. Before attachment replication is enabled, move file storage behind
an S3-compatible object-storage adapter with private buckets, TLS, checksums,
size/type limits, and narrowly scoped credentials. Replicate object keys and
checksums, not credentials or public bucket access. Existing files need a
separate verified copy and URL cutover plan.

## Configuration proposal (not currently read by the application)

Synchronization remains disabled until its implementation and deployment
have been separately reviewed. The following names are proposed for future
configuration; setting them now does not enable functionality:

| Variable | Purpose |
| --- | --- |
| `OPENCHAT_SYNC_ENABLED` | Explicit opt-in; default `false` |
| `OPENCHAT_SYNC_INSTANCE_ID` | Unique identifier for this installation |
| `OPENCHAT_SYNC_TOKEN_FILE` | Path to a per-instance secret outside the repository |
| `OPENCHAT_SYNC_POLL_SECONDS` | Bounded local worker polling interval |
| `OPENCHAT_SYNC_BATCH_SIZE` | Maximum event batch size |
| `OPENCHAT_SYNC_CONFLICT_POLICY` | Must remain `manual` |
| `OPENCHAT_OBJECT_STORAGE_ENDPOINT` | S3-compatible HTTPS endpoint |
| `OPENCHAT_OBJECT_STORAGE_BUCKET` | Private object bucket |
| `OPENCHAT_OBJECT_STORAGE_REGION` | Provider region |
| `OPENCHAT_OBJECT_STORAGE_ACCESS_KEY` | Secret-manager supplied access key |
| `OPENCHAT_OBJECT_STORAGE_SECRET_KEY` | Secret-manager supplied secret key |

Never put database URLs, sync tokens, or object-storage credentials in source
control, migration reports, application logs, or status responses. Production
credentials belong in the hosting provider's secret manager. Sync status, once
implemented, should be exposed only to authenticated administrators and show
last success, checkpoint, pending/retried events, and unresolved conflict
counts—not event payloads or credentials.

## Required review and rollout gates

1. Review and approve the table/column allowlist, account/hash handling,
   administrator promotion workflow, conflict UI, deletion/tombstone policy,
   object-storage provider, retention, and recovery plan.
2. Implement and test additive identity/outbox migrations against disposable
   SQLite and PostgreSQL databases. No schema changes should run automatically
   against production before review.
3. Test new rows, updates, explicit deletes, FK ordering, duplicate batches,
   integer-ID collisions, simultaneous updates, interrupted connections,
   restart recovery, credential rejection, and object checksum failures.
4. Restore the encrypted PostgreSQL backup to a separate test database and
   verify the SQLite import and attachment copy there. Do not start a sync
   worker against production during this test.
5. After a separate production approval, start with production-to-local pulls
   only. Enable local promotion only after conflict/status monitoring and
   rollback procedures have been exercised in a test deployment.

## Current limitation

The standalone one-time SQLite-to-PostgreSQL script preserves row IDs and
password hashes, reports identical rows already present, and aborts rather
than hiding conflicting primary or unique keys. It creates a verified SQLite
backup before a real import, validates schemas and relationships, and applies
changes in one PostgreSQL transaction. It supports read-only dry-run and
post-import verification modes. The Flask `import-sqlite` command delegates
to the same importer; neither command is invoked automatically or synchronizes
continuously. Database migration does not copy files under `static/uploads`.
