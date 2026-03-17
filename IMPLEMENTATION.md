# ArchiveTrail — Cold Data Tiering with Full Genealogy & Traceability

## Overview

ArchiveTrail is a VAST Data Platform-native solution for automated cold data tiering from local VAST S3/NFS/SMB views to remote AWS S3 buckets, with complete genealogy tracking and audit traceability. **Requires VAST Data Platform 5.4 or above.**

**Core problem:** Move data that hasn't been accessed in N days (user-configurable) from local VAST storage to AWS S3, while maintaining a full chain-of-custody record — where it came from, when it was created, when and why it was moved, where it lives now, and proof of data integrity throughout.

**Key design decisions:**

- **VAST Element Handle** is the immutable identity anchor (survives renames/moves)
- **Every state transition** produces an append-only lifecycle event
- **Three independent traceability layers** (application tables, VAST platform audit, AWS S3 metadata)
- **User-configurable threshold** stored in VAST DB, with its own change log
- **Protocol-agnostic** — works for NFS, SMB, and S3 via VAST Catalog polling

---

## Architecture

```
NFS / SMB / S3 clients ──► VAST Element Store
                                  │
                           VAST Catalog (periodic snapshot, e.g. every 30min)
                                  │  Columns: handle, parent_path, name, size,
                                  │           atime, mtime, ctime, extension...
                                  │
                      DataEngine Schedule Trigger (e.g. daily at 2 AM)
                                  │
                      DataEngine Pipeline:
                        ┌──────────────────┐
                        │  1. discover     │ Query Catalog for cold files
                        │  2. offload      │ Copy to AWS S3 with checksums
                        │  3. verify_purge │ Optional local deletion
                        └──────────────────┘
                                  │
                           VAST DB Tables:
                        ┌──────────────────┐
                        │ asset_registry   │ One row per file, ever
                        │ lifecycle_events │ Every state transition
                        │ offload_config   │ User-configurable params
                        │ config_change_log│ Config genealogy
                        └──────────────────┘
```

---

## Design Principles

1. **Element Handle as the immutable identity** — VAST assigns every element a unique handle that survives renames and moves. This is the genealogy anchor, not the file path.
2. **Every state transition is recorded** — no silent deletes, no overwrites without a log entry.
3. **Audit trail is queryable** — all lineage data lives in VAST DB tables accessible via SQL (VAST DB is built into the platform; no separate database required).
4. **VAST Protocol Auditing as independent witness** — a second, platform-level source of truth that corroborates application-level tracking.
5. **Catalog snapshots add the dimension of time** — query what the namespace looked like at any point in time.
6. **Config snapshot embedded in every event** — answer "what threshold was active when file X was offloaded?"

---

## VAST Platform Capabilities Used

| Capability | Purpose |
|-----------|---------|
| **VAST Catalog** | Periodic namespace snapshots with `atime`, `mtime`, `ctime` columns — the detection layer |
| **VAST Database** | SQL-queryable tables for registry, events, config — the tracking layer |
| **VAST DataEngine** | Serverless Python functions + schedule triggers — the execution layer |
| **VAST Protocol Auditing** | Independent platform-level log of every S3/NFS/SMB operation — the corroboration layer |
| **S3 Object Tagging** | Tags on local files (`offload_status`) indexed in Catalog |
| **Multiprotocol Views** | Same path exposed via NFS + SMB + S3 for protocol-agnostic access |

### DataEngine Element Trigger Limitation

DataEngine Element Triggers are **restricted to S3 objects** in the current version (source type is read-only "S3"). This is why ArchiveTrail uses a **Schedule Trigger + Catalog polling** pattern instead — it provides full coverage across NFS, SMB, and S3 protocols.

### VAST Catalog `atime` Column

The Catalog natively exposes `atime` (last access time) as a queryable timestamp column. VAST views have a configurable `atime_frequency` (default: 3600 seconds) — `atime` is updated on read operations only if the time since the last update exceeds this interval. For a 60+ day threshold, this granularity is more than sufficient.

---

## Phase 1: VAST DB Schema

### Table 1: `offload_config` — User-Configurable Parameters

```sql
CREATE TABLE vast."archive/lineage".offload_config (
    config_key        VARCHAR,
    config_value      VARCHAR,
    updated_by        VARCHAR,
    updated_at        TIMESTAMP,
    change_reason     VARCHAR       -- why this config was changed
);

-- Seed values
INSERT INTO offload_config VALUES
  ('atime_threshold_days',  '60',             'admin', now(), 'Initial setup'),
  ('target_aws_bucket',     'corp-cold-tier', 'admin', now(), 'Initial setup'),
  ('target_aws_region',     'us-east-1',      'admin', now(), 'Initial setup'),
  ('source_paths',          '/tenant/projects,/tenant/media', 'admin', now(), 'Initial setup'),
  ('auto_delete_local',     'false',          'admin', now(), 'Start conservative'),
  ('dry_run',               'true',           'admin', now(), 'Initial setup'),
  ('batch_size',            '500',            'admin', now(), 'Initial setup'),
  ('verify_checksum',       'true',           'admin', now(), 'Data integrity enforcement');
```

An admin changes the threshold by simply running:

```sql
UPDATE offload_config SET config_value='90', updated_at=now()
WHERE config_key='atime_threshold_days';
```

### Table 2: `asset_registry` — The Genealogy Record (One Row Per Element, Ever)

This is the **master identity table**. Once an element enters this table, it never leaves.

```sql
CREATE TABLE vast."archive/lineage".asset_registry (
    -- IDENTITY (immutable)
    element_handle       VARCHAR,       -- VAST Element handle (survives renames/moves)
    registration_id      VARCHAR,       -- UUID: unique ID for this registry entry

    -- ORIGIN (captured at first discovery)
    original_path        VARCHAR,       -- full path when first seen
    original_bucket      VARCHAR,       -- S3 bucket name (if applicable)
    original_view        VARCHAR,       -- VAST view name
    file_name            VARCHAR,
    file_extension       VARCHAR,
    file_size_bytes      BIGINT,
    file_ctime           TIMESTAMP,     -- Element Store creation time
    file_mtime           TIMESTAMP,     -- last modification at registration
    file_atime           TIMESTAMP,     -- last access at registration
    owner_uid            VARCHAR,       -- POSIX UID / S3 owner
    owner_login          VARCHAR,       -- login name from Catalog
    nfs_mode_bits        INTEGER,       -- permissions at time of capture

    -- CURRENT STATE
    current_location     VARCHAR,       -- 'LOCAL' | 'AWS' | 'BOTH' | 'LOCAL_DELETED'
    current_aws_bucket   VARCHAR,
    current_aws_key      VARCHAR,
    current_aws_region   VARCHAR,

    -- TIMESTAMPS
    registered_at        TIMESTAMP,     -- when first entered into registry
    last_state_change    TIMESTAMP,     -- when current_location last changed

    -- INTEGRITY
    source_md5           VARCHAR,       -- checksum at source before copy
    destination_md5      VARCHAR        -- checksum at destination after copy
);
```

### Table 3: `lifecycle_events` — The Full Audit Trail (Append-Only)

Every action on every asset produces a row here. This is the **traceability chain**.

```sql
CREATE TABLE vast."archive/lineage".lifecycle_events (
    -- IDENTITY
    event_id             VARCHAR,       -- UUID per event
    element_handle       VARCHAR,       -- FK to asset_registry
    registration_id      VARCHAR,       -- FK to asset_registry

    -- EVENT
    event_type           VARCHAR,       -- REGISTERED | SCANNED | COPY_STARTED |
                                        -- COPY_COMPLETED | COPY_FAILED |
                                        -- CHECKSUM_VERIFIED | CHECKSUM_MISMATCH |
                                        -- LOCAL_DELETE_REQUESTED | LOCAL_DELETED |
                                        -- LOCAL_DELETE_FAILED | RECALLED |
                                        -- CONFIG_CHANGED | THRESHOLD_EVALUATED
    event_timestamp      TIMESTAMP,

    -- CONTEXT
    source_path          VARCHAR,       -- where the file was at event time
    destination_path     VARCHAR,       -- where it went (if applicable)
    aws_bucket           VARCHAR,
    aws_key              VARCHAR,

    -- METADATA SNAPSHOT (state of the file at event time)
    file_size_bytes      BIGINT,
    file_atime           TIMESTAMP,
    file_mtime           TIMESTAMP,

    -- EXECUTION
    pipeline_run_id      VARCHAR,       -- DataEngine pipeline execution ID
    function_name        VARCHAR,       -- which DataEngine function emitted this
    triggered_by         VARCHAR,       -- 'SCHEDULE' | 'MANUAL' | 'RECALL_REQUEST'

    -- RESULT
    success              BOOLEAN,
    error_message        VARCHAR,
    checksum_value       VARCHAR,       -- MD5/SHA256 if relevant to this event

    -- TRACEABILITY
    config_snapshot      VARCHAR        -- JSON: threshold_days, auto_delete, etc.
                                        -- captures the config at time of decision
);
```

### Table 4: `config_change_log` — Config Genealogy

Tracks every change to `offload_config` so you can answer *"What threshold was active when file X was offloaded?"*

```sql
CREATE TABLE vast."archive/lineage".config_change_log (
    change_id            VARCHAR,
    config_key           VARCHAR,
    old_value            VARCHAR,
    new_value            VARCHAR,
    changed_by           VARCHAR,
    changed_at           TIMESTAMP,
    change_reason        VARCHAR
);
```

### Entity Relationship

```
┌──────────────────┐
│  offload_config   │───────────┐
└──────────────────┘            │ config snapshot embedded
         │                      │ in each lifecycle event
         ▼                      │
┌──────────────────┐            │
│ config_change_log │           │
└──────────────────┘            │
                                │
┌──────────────────┐       ┌────▼──────────────┐
│  asset_registry   │◄──────│  lifecycle_events  │
│  (1 row per file) │ handle│  (N rows per file) │
│                   │───────│                    │
│  element_handle   │  FK   │  element_handle    │
│  current_location │       │  event_type        │
│  source_md5       │       │  event_timestamp   │
└──────────────────┘       └────────────────────┘
         ▲                           ▲
         │                           │
         │  Corroborated by:         │
         │                           │
┌────────┴───────────┐    ┌──────────┴──────────┐
│   VAST Catalog      │    │ VAST Protocol Audit  │
│  (platform-level)   │    │   (platform-level)   │
│  atime/mtime/ctime  │    │  S3 GET/PUT/DELETE   │
│  per Catalog snap   │    │  NFS READ/WRITE      │
└────────────────────┘    └─────────────────────┘
```

---

## Phase 2: VAST Platform Configuration

### Step 2.1 — Enable VAST Catalog

```
Settings -> VAST Catalog -> Enable
  Save new catalog copies every: 30 minutes
  Keep catalog copies for:       7 days        <- enables historical queries
  Store filesystem snapshots:    Yes
  Keep filesystem snapshots for: 7 days
```

The 7-day retention on Catalog copies means you can query the Catalog *as it existed at any point in the last 7 days* — independent corroboration of application-level tracking.

### Step 2.2 — Enable Protocol Auditing

```
Settings -> Auditing -> Enable
  Protocols:  S3, NFSv3, NFSv4, SMB
  Operations: Create/Delete, Modify Data, Read Metadata
  Output:     VAST Database table          <- queryable via SQL
```

This gives a **platform-level witness** — every S3 GET (copy read), S3 DELETE (local purge), and NFS/SMB access is logged independently from the application. Cross-reference example:

```sql
-- Corroborate: was the file actually read by our function before deletion?
SELECT * FROM vast."audit/schema".audit_table
WHERE object_path = '/tenant/projects/old_report.pdf'
  AND operation IN ('GetObject', 'DeleteObject')
  AND timestamp BETWEEN '2026-03-15' AND '2026-03-16'
ORDER BY timestamp;
```

### Step 2.3 — Add Custom S3 Tag to Catalog Index

Add a user-defined attribute for an `offload_status` tag so offload state is queryable directly from the Catalog:

```
Settings -> VAST Catalog -> User defined attributes -> Add
  Type: Tag
  Column Name: offload_status
```

This creates a `tag_offload_status` column in the Catalog table. The DataEngine function tags files via S3 `PutObjectTagging` with `offload_status=COPIED` or `offload_status=PURGED`, making the state visible in the Catalog.

### Step 2.4 — Create Multiprotocol Views

Ensure source views have **S3 enabled alongside NFS/SMB** so the DataEngine function can read files via S3:

```
Element Store -> Views -> Edit view
  Protocols: NFSv3  NFSv4  SMB  S3 Bucket  (all enabled)
```

---

## Phase 3: DataEngine Pipeline

### Pipeline Structure

```
┌─────────────────────────────────────────────────────────────┐
│                Pipeline: archive-trail-tiering                │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │   Schedule    │───>│   Discover   │───>│   Offload &   │  │
│  │   Trigger     │    │   Function   │    │   Track       │  │
│  │  (daily 2AM)  │    │              │    │   Function    │  │
│  └──────────────┘    └──────────────┘    └───────────────┘  │
│                                                │             │
│                                          ┌─────▼─────────┐  │
│                                          │   Verify &    │  │
│                                          │   Purge       │  │
│                                          │   Function    │  │
│                                          └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Function 1: `discover` — Find Cold Files, Register Them

```python
"""
discover.py - Queries VAST Catalog for cold files, registers them in asset_registry,
              emits lifecycle events, passes candidate list to next function.
"""
import uuid
import json
from datetime import datetime, timedelta
import vastdb

def handler(event, context):
    # 1. Load config
    config = load_config()
    threshold = int(config['atime_threshold_days'])
    source_paths = config['source_paths'].split(',')
    batch_size = int(config['batch_size'])
    config_json = json.dumps(config)  # snapshot for traceability

    # 2. Query Catalog for cold files not yet registered
    cold_files = vastdb.query(f"""
        SELECT handle, parent_path, name, extension, size,
               atime, mtime, ctime, login_name, nfs_mode_bits
        FROM vast."catalog/schema".catalog_table c
        WHERE c.atime < now() - INTERVAL '{threshold}' DAY
          AND c.element_type = 'FILE'
          AND c.parent_path IN ({path_list(source_paths)})
          AND c.handle NOT IN (
              SELECT element_handle FROM vast."archive/lineage".asset_registry
              WHERE current_location IN ('AWS', 'BOTH', 'LOCAL_DELETED')
          )
        ORDER BY c.atime ASC
        LIMIT {batch_size}
    """)

    # 3. Register each file and emit REGISTERED + THRESHOLD_EVALUATED events
    candidates = []
    for f in cold_files:
        reg_id = str(uuid.uuid4())

        vastdb.execute("""
            INSERT INTO vast."archive/lineage".asset_registry VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'LOCAL', NULL, NULL, NULL, now(), now(), NULL, NULL
            )
        """, [f.handle, reg_id, f.parent_path, bucket_for_path(f.parent_path),
              view_for_path(f.parent_path), f.name, f.extension, f.size,
              f.ctime, f.mtime, f.atime, f.login_name, f.login_name, f.nfs_mode_bits])

        # Emit two lifecycle events:
        # a) REGISTERED - file entered the system
        emit_event(f.handle, reg_id, 'REGISTERED',
                   source_path=full_path(f), pipeline_run=context.run_id,
                   config_snapshot=config_json, file=f)

        # b) THRESHOLD_EVALUATED - why this file was selected
        emit_event(f.handle, reg_id, 'THRESHOLD_EVALUATED',
                   source_path=full_path(f), pipeline_run=context.run_id,
                   config_snapshot=config_json, file=f,
                   details=f"atime={f.atime}, threshold={threshold}d, "
                           f"age={days_since(f.atime)}d")

        candidates.append({'handle': f.handle, 'reg_id': reg_id,
                          'path': full_path(f), 'size': f.size})

    context.output = candidates  # pass to next function
```

### Function 2: `offload_and_track` — Copy to AWS with Checksum Verification

```python
"""
offload_and_track.py - Copies files to AWS S3 with integrity verification,
                       updates registry, emits full event chain.
"""
import boto3
import hashlib
import json
from datetime import datetime

def handler(event, context):
    config = load_config()
    dry_run = config['dry_run'] == 'true'
    verify = config['verify_checksum'] == 'true'
    config_json = json.dumps(config)

    s3_vast = boto3.client('s3', endpoint_url=VAST_S3_ENDPOINT)
    s3_aws  = boto3.client('s3', region_name=config['target_aws_region'])
    aws_bucket = config['target_aws_bucket']

    for candidate in event.candidates:
        handle = candidate['handle']
        reg_id = candidate['reg_id']
        src_path = candidate['path']
        aws_key = src_path.lstrip('/')  # preserve full path as key

        if dry_run:
            emit_event(handle, reg_id, 'SCANNED', source_path=src_path,
                       config_snapshot=config_json, details="DRY_RUN: would copy")
            continue

        try:
            # -- COPY_STARTED --
            emit_event(handle, reg_id, 'COPY_STARTED',
                       source_path=src_path,
                       destination_path=f"s3://{aws_bucket}/{aws_key}",
                       aws_bucket=aws_bucket, aws_key=aws_key,
                       pipeline_run=context.run_id, config_snapshot=config_json)

            # Read from VAST S3, compute checksum during transfer
            obj = s3_vast.get_object(
                Bucket=vast_bucket(src_path), Key=s3_key(src_path))
            body = obj['Body'].read()
            source_md5 = hashlib.md5(body).hexdigest()

            # Write to AWS S3 with genealogy metadata embedded in the object
            s3_aws.put_object(
                Bucket=aws_bucket, Key=aws_key, Body=body,
                Metadata={
                    'x-amz-meta-vast-element-handle': handle,
                    'x-amz-meta-vast-registration-id': reg_id,
                    'x-amz-meta-vast-original-path': src_path,
                    'x-amz-meta-vast-source-cluster': VAST_CLUSTER_NAME,
                    'x-amz-meta-vast-offload-timestamp': datetime.utcnow().isoformat(),
                    'x-amz-meta-vast-source-md5': source_md5
                }
            )

            # -- CHECKSUM VERIFICATION --
            if verify:
                verify_obj = s3_aws.get_object(Bucket=aws_bucket, Key=aws_key)
                dest_md5 = hashlib.md5(verify_obj['Body'].read()).hexdigest()

                if source_md5 != dest_md5:
                    emit_event(handle, reg_id, 'CHECKSUM_MISMATCH',
                               source_path=src_path,
                               aws_bucket=aws_bucket, aws_key=aws_key,
                               checksum=f"src={source_md5} dst={dest_md5}",
                               success=False, pipeline_run=context.run_id,
                               config_snapshot=config_json)
                    update_registry(handle, 'LOCAL')  # don't change state
                    continue

                emit_event(handle, reg_id, 'CHECKSUM_VERIFIED',
                           source_path=src_path,
                           aws_bucket=aws_bucket, aws_key=aws_key,
                           checksum=source_md5, success=True,
                           pipeline_run=context.run_id, config_snapshot=config_json)

            # -- COPY_COMPLETED --
            emit_event(handle, reg_id, 'COPY_COMPLETED',
                       source_path=src_path,
                       aws_bucket=aws_bucket, aws_key=aws_key,
                       checksum=source_md5, success=True,
                       pipeline_run=context.run_id, config_snapshot=config_json)

            # Update registry
            vastdb.execute("""
                UPDATE vast."archive/lineage".asset_registry
                SET current_location = 'BOTH',
                    current_aws_bucket = ?, current_aws_key = ?,
                    current_aws_region = ?, source_md5 = ?,
                    destination_md5 = ?, last_state_change = now()
                WHERE element_handle = ?
            """, [aws_bucket, aws_key, config['target_aws_region'],
                  source_md5, source_md5, handle])

            # Tag the local file via S3 for Catalog visibility
            s3_vast.put_object_tagging(
                Bucket=vast_bucket(src_path), Key=s3_key(src_path),
                Tagging={'TagSet': [
                    {'Key': 'offload_status', 'Value': 'COPIED'},
                    {'Key': 'offload_destination',
                     'Value': f"s3://{aws_bucket}/{aws_key}"},
                    {'Key': 'offload_timestamp',
                     'Value': datetime.utcnow().isoformat()}
                ]}
            )

        except Exception as e:
            emit_event(handle, reg_id, 'COPY_FAILED',
                       source_path=src_path,
                       aws_bucket=aws_bucket, aws_key=aws_key,
                       success=False, error_message=str(e),
                       pipeline_run=context.run_id, config_snapshot=config_json)
```

### Function 3: `verify_and_purge` — Optional Local Deletion with Full Traceability

```python
"""
verify_and_purge.py - Optionally deletes local copies after verification.
                      Every delete is preceded by a re-verification.
"""
import boto3
from datetime import datetime

def handler(event, context):
    config = load_config()
    if config['auto_delete_local'] != 'true':
        return  # nothing to do

    config_json = json.dumps(config)

    s3_vast = boto3.client('s3', endpoint_url=VAST_S3_ENDPOINT)
    s3_aws  = boto3.client('s3', region_name=config['target_aws_region'])

    # Find files in BOTH state (local + AWS copies exist)
    candidates = vastdb.query("""
        SELECT element_handle, registration_id, original_path,
               current_aws_bucket, current_aws_key, source_md5
        FROM vast."archive/lineage".asset_registry
        WHERE current_location = 'BOTH'
    """)

    for c in candidates:
        try:
            # Re-verify AWS copy exists and is intact before deleting local
            aws_obj = s3_aws.head_object(
                Bucket=c.current_aws_bucket, Key=c.current_aws_key)

            emit_event(c.element_handle, c.registration_id,
                       'LOCAL_DELETE_REQUESTED',
                       source_path=c.original_path,
                       aws_bucket=c.current_aws_bucket,
                       aws_key=c.current_aws_key,
                       pipeline_run=context.run_id,
                       config_snapshot=config_json)

            # Delete local copy
            s3_vast.delete_object(
                Bucket=vast_bucket(c.original_path),
                Key=s3_key(c.original_path)
            )

            emit_event(c.element_handle, c.registration_id,
                       'LOCAL_DELETED',
                       source_path=c.original_path,
                       aws_bucket=c.current_aws_bucket,
                       aws_key=c.current_aws_key,
                       success=True, pipeline_run=context.run_id,
                       config_snapshot=config_json)

            # Update registry
            vastdb.execute("""
                UPDATE vast."archive/lineage".asset_registry
                SET current_location = 'LOCAL_DELETED',
                    last_state_change = now()
                WHERE element_handle = ?
            """, [c.element_handle])

            # Update S3 tags on AWS copy
            s3_aws.put_object_tagging(
                Bucket=c.current_aws_bucket, Key=c.current_aws_key,
                Tagging={'TagSet': [
                    {'Key': 'vast-source-purged', 'Value': 'true'},
                    {'Key': 'vast-purge-timestamp',
                     'Value': datetime.utcnow().isoformat()}
                ]}
            )

        except Exception as e:
            emit_event(c.element_handle, c.registration_id,
                       'LOCAL_DELETE_FAILED',
                       source_path=c.original_path, success=False,
                       error_message=str(e), pipeline_run=context.run_id,
                       config_snapshot=config_json)
```

---

## Phase 4: Genealogy Queries

### "Where is file X now?"

```sql
SELECT element_handle, original_path, current_location,
       current_aws_bucket, current_aws_key, source_md5
FROM vast."archive/lineage".asset_registry
WHERE original_path LIKE '%quarterly_report_2025.xlsx';
```

### "Show the complete lifecycle of file X"

```sql
SELECT event_type, event_timestamp, source_path, destination_path,
       aws_bucket, aws_key, success, checksum_value, error_message,
       pipeline_run_id, config_snapshot
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x1A2B3C4D'
ORDER BY event_timestamp ASC;
```

Example output:

```
REGISTERED            2026-03-17 02:01:12  /tenant/projects/report.xlsx
THRESHOLD_EVALUATED   2026-03-17 02:01:12  atime=2026-01-05, threshold=60d, age=71d
COPY_STARTED          2026-03-17 02:01:13  -> s3://corp-cold-tier/tenant/projects/report.xlsx
CHECKSUM_VERIFIED     2026-03-17 02:03:45  md5=a1b2c3d4...
COPY_COMPLETED        2026-03-17 02:03:45  success=true
LOCAL_DELETE_REQUESTED 2026-03-18 02:00:01
LOCAL_DELETED          2026-03-18 02:00:03  success=true
```

### "Cross-reference with VAST Protocol Audit (independent witness)"

```sql
-- Did our function actually read this file before claiming it was copied?
SELECT timestamp, protocol, operation, object_path, user_name, bytes
FROM vast."audit/schema".audit_table
WHERE object_path LIKE '%report.xlsx%'
  AND timestamp BETWEEN '2026-03-17 02:00:00' AND '2026-03-17 02:05:00'
ORDER BY timestamp;
```

### "What config was active when file X was offloaded?"

```sql
SELECT event_type, event_timestamp,
       json_extract_scalar(config_snapshot, '$.atime_threshold_days') AS threshold,
       json_extract_scalar(config_snapshot, '$.auto_delete_local') AS auto_delete
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x1A2B3C4D'
  AND event_type = 'THRESHOLD_EVALUATED';
```

### "Who changed the threshold and when?"

```sql
SELECT * FROM vast."archive/lineage".config_change_log
WHERE config_key = 'atime_threshold_days'
ORDER BY changed_at DESC;
```

### "Show all files offloaded under a threshold that was later changed"

```sql
SELECT a.element_handle, a.original_path, e.event_timestamp,
       json_extract_scalar(e.config_snapshot, '$.atime_threshold_days')
           AS threshold_at_offload,
       c.new_value AS current_threshold
FROM vast."archive/lineage".asset_registry a
JOIN vast."archive/lineage".lifecycle_events e
  ON a.element_handle = e.element_handle
  AND e.event_type = 'COPY_COMPLETED'
CROSS JOIN (
    SELECT config_value AS new_value
    FROM vast."archive/lineage".offload_config
    WHERE config_key = 'atime_threshold_days'
) c
WHERE json_extract_scalar(e.config_snapshot, '$.atime_threshold_days')
      != c.new_value;
```

### "List all files from a specific original path that have been purged locally"

```sql
SELECT a.element_handle, a.file_name, a.file_size_bytes,
       a.current_aws_bucket, a.current_aws_key,
       a.registered_at, a.last_state_change,
       a.source_md5
FROM vast."archive/lineage".asset_registry a
WHERE a.original_path LIKE '/tenant/projects/2024/%'
  AND a.current_location = 'LOCAL_DELETED'
ORDER BY a.last_state_change DESC;
```

---

## Phase 5: Traceability Layers

```
Layer 1: APPLICATION TRACKING (ArchiveTrail tables in VAST DB)
+-- asset_registry        - master identity, current state, checksums
+-- lifecycle_events      - every state transition with full context
+-- config_change_log     - config genealogy
+-- offload_config        - current parameters

Layer 2: PLATFORM CORROBORATION (VAST native features)
+-- VAST Catalog          - periodic namespace snapshots (7-day retention)
|   +-- tag_offload_status column - visible in Catalog queries
+-- VAST Protocol Audit   - independent log of every S3/NFS/SMB operation
+-- Catalog Snapshots     - point-in-time queries of file metadata

Layer 3: DESTINATION METADATA (AWS S3)
+-- S3 Object Metadata    - embedded in each offloaded object:
    +-- x-amz-meta-vast-element-handle
    +-- x-amz-meta-vast-registration-id
    +-- x-amz-meta-vast-original-path
    +-- x-amz-meta-vast-source-cluster
    +-- x-amz-meta-vast-offload-timestamp
    +-- x-amz-meta-vast-source-md5
```

The three layers are **independent** — even if one is corrupted or lost, the other two can reconstruct the chain. The AWS S3 object metadata means that even someone with *only* access to the AWS bucket can trace any object back to its VAST origin.

---

## Implementation Sequence

| Step | What | Depends On |
|------|------|-----------|
| 1 | Enable VAST Catalog (30min interval, 7-day retention) | -- |
| 2 | Enable Protocol Auditing to VAST DB | -- |
| 3 | Add `offload_status` as Catalog indexed tag | Step 1 |
| 4 | Enable S3 on NFS/SMB views (multiprotocol) | -- |
| 5 | Create VAST DB schema and tables (`asset_registry`, `lifecycle_events`, `offload_config`, `config_change_log`) | -- |
| 6 | Seed `offload_config` with initial values | Step 5 |
| 7 | Build and push DataEngine function containers (`discover`, `offload_and_track`, `verify_and_purge`) | -- |
| 8 | Create DataEngine Schedule Trigger (daily 2AM) | -- |
| 9 | Build pipeline: trigger -> discover -> offload_and_track -> verify_and_purge | Steps 7-8 |
| 10 | Deploy pipeline in `dry_run=true` mode | Step 9 |
| 11 | Validate: review lifecycle_events, cross-check with Audit table | Step 10 |
| 12 | Set `dry_run=false`, `auto_delete_local=false` (copy only) | Step 11 |
| 13 | After confidence period: set `auto_delete_local=true` | Step 12 |

---

## State Machine

```
                    ┌───────────┐
                    │  Unknown  │  (file exists in Element Store,
                    │           │   not yet discovered by ArchiveTrail)
                    └─────┬─────┘
                          │ discover function finds atime > threshold
                          ▼
                    ┌───────────┐
                    │   LOCAL   │  REGISTERED + THRESHOLD_EVALUATED
                    │           │
                    └─────┬─────┘
                          │ offload_and_track copies to AWS
                          ▼
                    ┌───────────┐
                    │   BOTH    │  COPY_STARTED -> CHECKSUM_VERIFIED -> COPY_COMPLETED
                    │           │
                    └─────┬─────┘
                          │ verify_and_purge deletes local (if auto_delete=true)
                          ▼
                    ┌───────────┐
                    │  LOCAL_   │  LOCAL_DELETE_REQUESTED -> LOCAL_DELETED
                    │  DELETED  │
                    └─────┬─────┘
                          │ (future: recall from AWS)
                          ▼
                    ┌───────────┐
                    │ RECALLED  │  (re-downloaded from AWS to local)
                    └───────────┘
```

Each arrow produces one or more rows in `lifecycle_events` with full context.

---

## Future Extensions

- **Recall function**: Download from AWS back to VAST, update registry to `LOCAL` or `BOTH`, emit `RECALLED` event
- **Cost tracking**: Add AWS storage cost estimates to `asset_registry` based on size and storage class
- **Retention policies**: Auto-delete from AWS after N years, with full lifecycle event
- **Multi-destination**: Support multiple AWS buckets/regions, or Azure Blob, with destination tracking per copy
- **Dashboard**: VAST DB + Apache Superset visualization of offload activity, cost savings, and genealogy queries
- **Alerting**: DataEngine function that queries for `COPY_FAILED` or `CHECKSUM_MISMATCH` events and sends notifications
