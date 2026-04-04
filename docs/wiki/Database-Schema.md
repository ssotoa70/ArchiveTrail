# Database Schema

Complete reference for the four ArchiveTrail tables in VAST DB, including column descriptions, relationships, and example queries.

## Schema Location

All tables live in VAST DB under:

```
Bucket: archive-trail-db (default, configurable)
Schema: archive/lineage (default, configurable)
```

## Table 1: `offload_config`

**Purpose:** User-configurable operational parameters. Single-record table (one row per config key).

**Use Case:** Store threshold, target bucket, source paths, flags.

### Columns

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `config_key` | VARCHAR | No | Unique configuration parameter name (e.g., `atime_threshold_days`) |
| `config_value` | VARCHAR | No | Current value as string (e.g., `"60"`) |
| `updated_by` | VARCHAR | Yes | Username who last updated this parameter |
| `updated_at` | TIMESTAMP | Yes | When this parameter was last updated |
| `change_reason` | VARCHAR | Yes | Free-text reason for the change (e.g., "Policy update: increase retention") |

### Primary Key

Implicit: `config_key` is unique per row.

### Indexes

```sql
CREATE INDEX idx_offload_config_key ON vast."archive/lineage".offload_config(config_key);
```

### Example Rows

```
config_key               | config_value              | updated_by | updated_at
atime_threshold_days     | 60                        | admin      | 2026-03-17 14:00:00
target_aws_bucket        | corp-cold-tier            | admin      | 2026-03-15 10:30:00
target_aws_region        | us-east-1                 | admin      | 2026-03-01 08:00:00
target_aws_storage_class | INTELLIGENT_TIERING       | admin      | 2026-03-10 16:45:00
source_paths             | /tenant/projects,/tenant/media | admin  | 2026-02-28 09:00:00
auto_delete_local        | false                     | admin      | 2026-03-17 14:00:00
dry_run                  | true                      | admin      | 2026-03-17 14:00:00
batch_size               | 500                       | admin      | 2026-03-01 08:00:00
verify_checksum          | true                      | admin      | 2026-03-01 08:00:00
```

### Example Queries

**Get current config:**
```sql
SELECT config_key, config_value, updated_at, updated_by
FROM vast."archive/lineage".offload_config
ORDER BY config_key;
```

**Update threshold:**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '90', updated_by = 'admin', updated_at = now(),
    change_reason = 'Policy change: extend retention'
WHERE config_key = 'atime_threshold_days';
```

---

## Table 2: `asset_registry`

**Purpose:** Master identity table. One row per element, ever. Immutable once created. Current state of each asset.

**Use Case:** Locate files, track state (LOCAL/BOTH/LOCAL_DELETED), checksums.

### Columns

#### Identity (Immutable)

| Column | Type | Description |
|--------|------|-------------|
| `element_handle` | VARCHAR | VAST Element Handle (unique, survives renames/moves) |
| `registration_id` | VARCHAR | UUID for this registry entry |

#### Origin (Captured at First Discovery)

| Column | Type | Description |
|--------|------|-------------|
| `original_path` | VARCHAR | Full path when first seen (e.g., `/tenant/projects/report.pdf`) |
| `original_bucket` | VARCHAR | S3 bucket name at source |
| `original_view` | VARCHAR | VAST view name |
| `file_name` | VARCHAR | Filename only (e.g., `report.pdf`) |
| `file_extension` | VARCHAR | File extension (e.g., `pdf`) |
| `file_size_bytes` | BIGINT | File size in bytes |
| `file_ctime` | TIMESTAMP | Element creation time (immutable) |
| `file_mtime` | TIMESTAMP | Last modification time at registration |
| `file_atime` | TIMESTAMP | Last access time at registration |
| `owner_uid` | VARCHAR | POSIX UID or S3 owner ID |
| `owner_login` | VARCHAR | Login name from Catalog (human-readable owner) |
| `nfs_mode_bits` | INTEGER | File permissions (e.g., 0644) |

#### Current State (Mutable)

| Column | Type | Description |
|--------|------|-------------|
| `current_location` | VARCHAR | State: LOCAL \| BOTH \| LOCAL_DELETED \| RECALLED \| AWS (future) |
| `current_aws_bucket` | VARCHAR | AWS bucket (null if not offloaded) |
| `current_aws_key` | VARCHAR | AWS S3 key (null if not offloaded) |
| `current_aws_region` | VARCHAR | AWS region (null if not offloaded) |

#### Timestamps

| Column | Type | Description |
|--------|------|-------------|
| `registered_at` | TIMESTAMP | When first entered into registry |
| `last_state_change` | TIMESTAMP | When current_location last changed |

#### Integrity

| Column | Type | Description |
|--------|------|-------------|
| `source_md5` | VARCHAR | MD5 checksum of local file before copy |
| `destination_md5` | VARCHAR | MD5 checksum of AWS copy after verification |

### Primary Key

Implicit: `element_handle` is unique per row (one entry per element ever).

### Indexes

```sql
CREATE INDEX idx_asset_registry_handle
  ON vast."archive/lineage".asset_registry(element_handle);
CREATE INDEX idx_asset_registry_path
  ON vast."archive/lineage".asset_registry(original_path);
CREATE INDEX idx_asset_registry_location
  ON vast."archive/lineage".asset_registry(current_location);
```

### States

```
Unknown → LOCAL → BOTH → LOCAL_DELETED → (future: RECALLED, AWS)
```

| State | Meaning | File Location |
|-------|---------|---------------|
| `LOCAL` | Registered, only on VAST | VAST only |
| `BOTH` | Copied to AWS, local copy exists | VAST + AWS |
| `LOCAL_DELETED` | Local deleted, only on AWS | AWS only |
| `RECALLED` | Re-downloaded from AWS | VAST + AWS (future) |
| `AWS` | Only on AWS, never accessed since recall (future) | AWS only (future) |

### Example Rows

```
element_handle | registration_id | original_path | file_size_bytes | current_location | current_aws_bucket | current_aws_key
0x1A2B3C4D    | uuid-123        | /tenant/proj... | 15728640       | BOTH             | corp-cold-tier    | tenant/proj...
0x5E6F7G8H    | uuid-456        | /tenant/media...| 104857600      | LOCAL_DELETED    | corp-cold-tier    | tenant/media...
0x9I0J1K2L    | uuid-789        | /tenant/proj... | 8388608        | LOCAL            | NULL              | NULL
```

### Example Queries

**Find file by path:**
```sql
SELECT element_handle, original_path, current_location, 
       current_aws_bucket, current_aws_key
FROM vast."archive/lineage".asset_registry
WHERE original_path LIKE '%report.pdf';
```

**Count files by location:**
```sql
SELECT current_location, COUNT(*) as count
FROM vast."archive/lineage".asset_registry
GROUP BY current_location;
```

**Total size offloaded:**
```sql
SELECT ROUND(SUM(file_size_bytes) / 1024.0 / 1024.0 / 1024.0, 2) as gb_offloaded
FROM vast."archive/lineage".asset_registry
WHERE current_location IN ('BOTH', 'LOCAL_DELETED');
```

**Find recently offloaded files:**
```sql
SELECT file_name, current_location, last_state_change
FROM vast."archive/lineage".asset_registry
WHERE current_location = 'BOTH'
ORDER BY last_state_change DESC
LIMIT 20;
```

---

## Table 3: `lifecycle_events`

**Purpose:** Append-only audit trail. Every state transition produces one or more rows. Immutable once written.

**Use Case:** Complete genealogy of file, troubleshooting, compliance audit.

### Columns

#### Identity

| Column | Type | Description |
|--------|------|-------------|
| `event_id` | VARCHAR | UUID unique per event |
| `element_handle` | VARCHAR | FK to asset_registry (links to file) |
| `registration_id` | VARCHAR | FK to asset_registry (matches registry entry) |

#### Event Details

| Column | Type | Description |
|--------|------|-------------|
| `event_type` | VARCHAR | Type of event (see table below) |
| `event_timestamp` | TIMESTAMP | When this event occurred |

#### Context

| Column | Type | Description |
|--------|------|-------------|
| `source_path` | VARCHAR | Where file was during event (NFS path) |
| `destination_path` | VARCHAR | Where file went (if applicable, S3 URI) |
| `aws_bucket` | VARCHAR | AWS bucket involved (if applicable) |
| `aws_key` | VARCHAR | AWS S3 key (if applicable) |

#### Metadata Snapshot

| Column | Type | Description |
|--------|------|-------------|
| `file_size_bytes` | BIGINT | File size at event time |
| `file_atime` | TIMESTAMP | Access time at event time |
| `file_mtime` | TIMESTAMP | Modification time at event time |

#### Execution Context

| Column | Type | Description |
|--------|------|-------------|
| `pipeline_run_id` | VARCHAR | Schedule run ID (links events from same run) |
| `function_name` | VARCHAR | Which function emitted this event |
| `triggered_by` | VARCHAR | SCHEDULE \| MANUAL \| RECALL_REQUEST |

#### Result

| Column | Type | Description |
|--------|------|-------------|
| `success` | BOOLEAN | Did this operation succeed? |
| `error_message` | VARCHAR | Error details if failed |
| `checksum_value` | VARCHAR | MD5/SHA256 if relevant to event |

#### Traceability

| Column | Type | Description |
|--------|------|-------------|
| `config_snapshot` | VARCHAR | JSON of all config at event time |

### Event Types

| Type | When Emitted | Meaning |
|------|--------------|---------|
| **REGISTERED** | discover | File entered registry (state → LOCAL) |
| **SCANNED** | discover (dry-run) | Would register this file (dry-run only) |
| **THRESHOLD_EVALUATED** | discover | File age exceeds threshold (context event) |
| **COPY_STARTED** | offload | Beginning S3 copy operation |
| **COPY_COMPLETED** | offload | S3 copy succeeded (state → BOTH) |
| **COPY_FAILED** | offload | S3 copy failed, local retained |
| **CHECKSUM_VERIFIED** | offload | MD5 verification passed |
| **CHECKSUM_MISMATCH** | offload | MD5 verification failed, copy aborted |
| **LOCAL_DELETE_REQUESTED** | verify_purge | About to delete local copy |
| **LOCAL_DELETED** | verify_purge | Local copy deleted (state → LOCAL_DELETED) |
| **LOCAL_DELETE_FAILED** | verify_purge | Local delete failed, retained for safety |
| **RECALLED** | recall (future) | Downloaded back from AWS |
| **CONFIG_CHANGED** | (manual update) | Config parameter changed (future) |

### Primary Key

Implicit: `event_id` is unique per row.

### Indexes

```sql
CREATE INDEX idx_lifecycle_handle
  ON vast."archive/lineage".lifecycle_events(element_handle);
CREATE INDEX idx_lifecycle_type_ts
  ON vast."archive/lineage".lifecycle_events(event_type, event_timestamp);
CREATE INDEX idx_lifecycle_pipeline
  ON vast."archive/lineage".lifecycle_events(pipeline_run_id);
CREATE INDEX idx_lifecycle_success
  ON vast."archive/lineage".lifecycle_events(success);
```

### Example Rows

One file's complete lifecycle:

```
event_type          | event_timestamp       | element_handle | source_path     | success | error_message
REGISTERED          | 2026-03-17 02:01:12  | 0x1A2B3C4D    | /tenant/proj... | true    | NULL
THRESHOLD_EVALUATED | 2026-03-17 02:01:12  | 0x1A2B3C4D    | /tenant/proj... | true    | atime=..., age=71d
COPY_STARTED        | 2026-03-17 02:01:13  | 0x1A2B3C4D    | /tenant/proj... | true    | NULL
CHECKSUM_VERIFIED   | 2026-03-17 02:03:45  | 0x1A2B3C4D    | /tenant/proj... | true    | a1b2c3d4e5...
COPY_COMPLETED      | 2026-03-17 02:03:46  | 0x1A2B3C4D    | /tenant/proj... | true    | NULL
LOCAL_DELETE_REQ... | 2026-03-18 02:00:01  | 0x1A2B3C4D    | /tenant/proj... | true    | NULL
LOCAL_DELETED       | 2026-03-18 02:00:03  | 0x1A2B3C4D    | /tenant/proj... | true    | NULL
```

### Example Queries

**Show complete lifecycle of one file:**
```sql
SELECT event_type, event_timestamp, success, error_message, checksum_value
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x1A2B3C4D'
ORDER BY event_timestamp ASC;
```

**Find all failures in last 24 hours:**
```sql
SELECT event_type, source_path, error_message, event_timestamp
FROM vast."archive/lineage".lifecycle_events
WHERE success = false
  AND event_timestamp > now() - INTERVAL '24' HOUR
ORDER BY event_timestamp DESC;
```

**Pipeline run summary:**
```sql
SELECT
    pipeline_run_id,
    COUNT(*) as total_events,
    COUNT(CASE WHEN event_type = 'REGISTERED' THEN 1 END) as discovered,
    COUNT(CASE WHEN event_type = 'COPY_COMPLETED' THEN 1 END) as copied,
    COUNT(CASE WHEN success = false THEN 1 END) as failures
FROM vast."archive/lineage".lifecycle_events
WHERE pipeline_run_id = 'schedule-20260317-020000'
GROUP BY pipeline_run_id;
```

**Extract config from event:**
```sql
SELECT
    event_timestamp,
    json_extract_scalar(config_snapshot, '$.atime_threshold_days') as threshold,
    json_extract_scalar(config_snapshot, '$.target_aws_bucket') as bucket
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x1A2B3C4D'
  AND event_type = 'COPY_COMPLETED'
LIMIT 1;
```

---

## Table 4: `config_change_log`

**Purpose:** Genealogy of configuration changes. Answer "what threshold was active when?" questions.

**Use Case:** Audit config changes, compliance, policy change tracking.

### Columns

| Column | Type | Description |
|--------|------|-------------|
| `change_id` | VARCHAR | UUID unique per change |
| `config_key` | VARCHAR | Which config parameter changed |
| `old_value` | VARCHAR | Previous value |
| `new_value` | VARCHAR | New value |
| `changed_by` | VARCHAR | Username who made the change |
| `changed_at` | TIMESTAMP | When the change was made |
| `change_reason` | VARCHAR | Free-text reason for change |

### Primary Key

Implicit: `change_id` is unique per row.

### Indexes

```sql
CREATE INDEX idx_config_change_log_key_ts
  ON vast."archive/lineage".config_change_log(config_key, changed_at);
CREATE INDEX idx_config_change_log_ts
  ON vast."archive/lineage".config_change_log(changed_at);
```

### Example Rows

```
change_id          | config_key              | old_value | new_value | changed_by | changed_at        | change_reason
uuid-change-001    | atime_threshold_days    | 60        | 90        | admin      | 2026-03-15 14:00  | Extend retention window
uuid-change-002    | target_aws_storage_class| STANDARD  | GLACIER   | ops        | 2026-03-10 09:30  | Cost reduction initiative
uuid-change-003    | auto_delete_local       | false     | true      | admin      | 2026-03-01 08:00  | Enable auto-purge
```

### Example Queries

**Show all threshold changes:**
```sql
SELECT changed_at, old_value, new_value, changed_by, change_reason
FROM vast."archive/lineage".config_change_log
WHERE config_key = 'atime_threshold_days'
ORDER BY changed_at DESC;
```

**Find what threshold was active on a specific date:**
```sql
SELECT config_key, new_value
FROM vast."archive/lineage".config_change_log
WHERE config_key = 'atime_threshold_days'
  AND changed_at <= '2026-03-17'
ORDER BY changed_at DESC
LIMIT 1;
```

**Audit all changes by a user:**
```sql
SELECT config_key, old_value, new_value, changed_at, change_reason
FROM vast."archive/lineage".config_change_log
WHERE changed_by = 'admin'
ORDER BY changed_at DESC;
```

---

## Entity Relationship Diagram

```
┌──────────────────────────────────────────────┐
│         offload_config                       │
│         (current parameters)                 │
│                                              │
│  config_key ─ (PK)                           │
│  config_value                                │
│  updated_by                                  │
│  updated_at                                  │
│  change_reason                               │
└──────────────────┬───────────────────────────┘
                   │ snapshot embedded in
                   │ every event
                   │
┌──────────────────▼───────────────────────────┐
│    asset_registry                            │
│    (master identity)                         │
│                                              │
│  element_handle ─ (PK)                       │
│  registration_id                             │
│  original_path                               │
│  original_bucket                             │
│  original_view                               │
│  file_name, file_extension                   │
│  file_size_bytes                             │
│  file_ctime, file_mtime, file_atime          │
│  owner_uid, owner_login                      │
│  nfs_mode_bits                               │
│  current_location                            │
│  current_aws_bucket                          │
│  current_aws_key                             │
│  current_aws_region                          │
│  registered_at                               │
│  last_state_change                           │
│  source_md5                                  │
│  destination_md5                             │
└──────────────────┬───────────────────────────┘
                   │ FK: element_handle
                   │
┌──────────────────▼───────────────────────────┐
│    lifecycle_events                          │
│    (append-only audit trail)                 │
│                                              │
│  event_id ─ (PK)                             │
│  element_handle ─ (FK to asset_registry)     │
│  registration_id ─ (FK to asset_registry)    │
│  event_type                                  │
│  event_timestamp                             │
│  source_path                                 │
│  destination_path                            │
│  aws_bucket                                  │
│  aws_key                                     │
│  file_size_bytes                             │
│  file_atime, file_mtime                      │
│  pipeline_run_id                             │
│  function_name                               │
│  triggered_by                                │
│  success                                     │
│  error_message                               │
│  checksum_value                              │
│  config_snapshot ─ (JSON)                    │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│    config_change_log                         │
│    (config genealogy)                        │
│                                              │
│  change_id ─ (PK)                            │
│  config_key                                  │
│  old_value                                   │
│  new_value                                   │
│  changed_by                                  │
│  changed_at                                  │
│  change_reason                               │
└──────────────────────────────────────────────┘
```

---

## Corroboration with VAST Platform

ArchiveTrail tables are corroborated by two VAST platform features:

### VAST Catalog

Located in: `vast."catalog/schema".catalog_table` (configurable)

**Relevant columns:**
- `handle` — Element Handle (same as asset_registry.element_handle)
- `parent_path`, `name` — Path (reconstructable as asset_registry.original_path)
- `atime`, `mtime`, `ctime` — File times (compare with asset_registry.file_* columns)
- `tag_offload_status` — Custom indexed tag (values: COPIED, PURGED)

**Cross-reference example:**
```sql
-- Verify Catalog snapshot matches our asset_registry
SELECT
    a.element_handle,
    a.original_path,
    c.atime as catalog_atime,
    a.file_atime as registry_atime
FROM vast."archive/lineage".asset_registry a
JOIN vast."catalog/schema".catalog_table c
  ON a.element_handle = c.handle
WHERE a.element_handle = '0x1A2B3C4D';
```

### VAST Protocol Audit

Located in: `vast."audit/schema".audit_table` (configurable)

**Relevant columns:**
- `timestamp` — When operation occurred
- `protocol` — S3, NFSv3, NFSv4, SMB
- `operation` — GetObject, PutObject, DeleteObject, etc.
- `object_path` — Path of object
- `user_name` — User who performed operation
- `bytes` — Data transferred

**Cross-reference example:**
```sql
-- Verify our function actually performed the copy
SELECT
    timestamp,
    protocol,
    operation,
    bytes
FROM vast."audit/schema".audit_table
WHERE object_path = '/tenant/projects/report.pdf'
  AND timestamp BETWEEN '2026-03-17 02:00:00' AND '2026-03-17 02:05:00'
ORDER BY timestamp;
```

---

## Data Types

All ArchiveTrail tables use standard VAST DB types:

| Type | Example | Notes |
|------|---------|-------|
| `VARCHAR` | `"hello"` | Strings, unbounded length |
| `BIGINT` | `15728640` | Large integers (file sizes) |
| `INTEGER` | `644` | Regular integers (permissions) |
| `TIMESTAMP` | `2026-03-17 02:01:12` | Date + time with microsecond precision |
| `BOOLEAN` | `true`, `false` | Success/failure flags |

---

## Constraints & Guarantees

### Immutability

Once a row is written:
- **asset_registry** — Cannot be deleted; only current_location and last_state_change can be updated
- **lifecycle_events** — Completely immutable (append-only)
- **config_change_log** — Completely immutable (audit trail)

### Referential Integrity

- `lifecycle_events.element_handle` references `asset_registry.element_handle`
- `lifecycle_events.registration_id` references `asset_registry.registration_id`
- Both must match for integrity

### Data Freshness

- **offload_config** — Updated immediately when parameter changes
- **asset_registry** — Updated at end of each offload/purge stage
- **lifecycle_events** — Written immediately per event (ordered by event_timestamp)
- **config_change_log** — Written immediately per config change

---

## Backup & Restore

All tables should be backed up as part of VAST DB backup:

```bash
# Backup (VAST UI or CLI)
vastdb backup --schema "archive/lineage"

# Restore from backup
vastdb restore --backup-id <backup_id>
```

For point-in-time recovery, VAST Catalog retains 7-day snapshots, allowing reconstruction of the state at any point in the last week.
