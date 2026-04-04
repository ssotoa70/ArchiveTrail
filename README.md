# ArchiveTrail

**Automated cold data tiering from VAST Data Platform to AWS S3 with complete genealogy tracking and audit traceability.**

ArchiveTrail is a VAST DataEngine-native solution that automatically identifies files that haven't been accessed in a configurable number of days, copies them to AWS S3, optionally deletes the local copies, and maintains a complete chain-of-custody record of every operation.

## Features

- **Automated Cold Data Detection** — Uses VAST Catalog to identify files exceeding access-time threshold (configurable, default 60 days)
- **Multi-Protocol Support** — Works with NFS, SMB, and S3 views transparently through VAST Catalog
- **Complete Genealogy Tracking** — Every file is immutably identified by VAST Element Handle and tracked through its entire lifecycle
- **Three-Layer Traceability**
  - Application-level tracking (ArchiveTrail tables in VAST DB)
  - Platform-level corroboration (VAST Protocol Auditing)
  - Destination metadata (AWS S3 object tags and metadata)
- **Data Integrity Verification** — MD5 checksums on source and destination with mismatch detection
- **Configuration Genealogy** — Every config change is logged; queries answer "what threshold was active when file X was offloaded?"
- **Phased Rollout** — Dry-run mode → copy-only → auto-purge for safe production deployment
- **No External Database** — Uses VAST DB built into the platform; no separate database required

## Architecture Overview

```
NFS / SMB / S3 Clients
        ↓
VAST Element Store
        ↓
VAST Catalog (30min snapshots, 7-day retention)
        ↓
DataEngine Schedule Trigger (configurable, e.g., daily 2 AM)
        ↓
Pipeline:
  [discover] → [offload_and_track] → [verify_and_purge]
        ↓
VAST DB Tables (traceability):
  • asset_registry (master identity)
  • lifecycle_events (append-only audit trail)
  • offload_config (user parameters + history)
  • config_change_log (config genealogy)
        ↓
AWS S3 (cold storage) + S3 metadata (genealogy anchor)
```

### Key Design Principles

1. **VAST Element Handle as Immutable Identity** — Each file is anchored by a unique handle that survives renames and moves
2. **Append-Only Lifecycle Events** — Every state transition produces an immutable record in VAST DB
3. **Config Snapshots in Every Event** — Know the exact configuration parameters that were active when a file was offloaded
4. **Three Independent Corroboration Layers** — Application tracking, VAST Platform Audit, and AWS S3 metadata each independently prove the chain of custody
5. **User-Configurable, Fully Auditable** — All operational decisions logged and queryable via SQL

## Quick Start

### Prerequisites

- **VAST Data Platform 5.4+** (required for Catalog and Protocol Auditing features)
- **VAST DataEngine** (for scheduling and pipeline orchestration)
- **AWS S3 Account** (destination for cold data)
- **VAST Catalog enabled** with at least 30-minute snapshot frequency and 7-day retention
- **VAST Protocol Auditing enabled** and configured to log to VAST DB
- **Multiprotocol views** (S3 enabled alongside NFS/SMB on source paths)

### 1. Deploy Schema and Configuration

Run these SQL scripts in VAST DB (via Query Editor or any Trino-compatible client):

```bash
# 1. Create schema
$ vastdb-sql < sql/001_create_schema.sql

# 2. Create tables
$ vastdb-sql < sql/002_create_tables.sql

# 3. Seed default configuration
$ vastdb-sql < sql/003_seed_config.sql
```

### 2. Build and Push Functions

```bash
# Build all three DataEngine functions and push to your registry
$ make build-all
$ make push-all
```

Functions:
- `archive-trail-discover` — Queries Catalog for cold files
- `archive-trail-offload` — Copies to AWS S3 with checksums
- `archive-trail-verify-purge` — Optionally deletes local copies

### 3. Create Pipeline in VMS UI

1. Go to **DataEngine → Pipelines**
2. Create a new pipeline with a **Schedule Trigger** (e.g., daily at 2 AM)
3. Connect the trigger to the three functions in sequence:
   ```
   Schedule Trigger → discover → offload_and_track → verify_and_purge
   ```
4. Set environment variables (see Configuration section below)
5. Start in **dry-run mode** to validate before enabling live offloads

### 4. Configure Parameters

Edit the `offload_config` table in VAST DB:

```sql
-- Start conservative: dry-run, no local deletion
UPDATE vast."archive/lineage".offload_config 
SET config_value='true' WHERE config_key='dry_run';

UPDATE vast."archive/lineage".offload_config 
SET config_value='false' WHERE config_key='auto_delete_local';

-- Adjust threshold and paths as needed
UPDATE vast."archive/lineage".offload_config 
SET config_value='60' WHERE config_key='atime_threshold_days';

UPDATE vast."archive/lineage".offload_config 
SET config_value='/my/cold/data' WHERE config_key='source_paths';
```

### 5. Monitor and Validate

Query the lifecycle events to verify the pipeline is working:

```sql
-- See all discovered files (dry-run or real)
SELECT event_type, event_timestamp, source_path, error_message
FROM vast."archive/lineage".lifecycle_events
WHERE event_type IN ('REGISTERED', 'SCANNED')
ORDER BY event_timestamp DESC
LIMIT 20;

-- Check for copy failures
SELECT source_path, error_message
FROM vast."archive/lineage".lifecycle_events
WHERE event_type = 'COPY_FAILED'
ORDER BY event_timestamp DESC;
```

### 6. Phased Rollout

**Phase 1: Dry-Run (Safe Validation)**
- Set `dry_run=true`, `auto_delete_local=false`
- Review discovered files and copy events
- Verify checksums match

**Phase 2: Copy-Only (Build Confidence)**
- Set `dry_run=false`, `auto_delete_local=false`
- Files are copied to AWS but local copies retained
- Validate files are retrievable from AWS
- Review costs and offload volume

**Phase 3: Auto-Purge (Production)**
- Set `auto_delete_local=true`
- Local copies are deleted after AWS verification
- Monitor storage savings and re-call patterns

## Configuration Reference

### Environment Variables

Set these in the DataEngine pipeline deployment (VMS UI > Pipelines > Edit > Environment):

| Variable | Required | Purpose |
|----------|----------|---------|
| `S3_ENDPOINT` | Yes | VAST S3 VIP or endpoint (e.g., `https://<VAST_DATA_VIP>`) |
| `S3_ACCESS_KEY` | Yes | VAST S3 access key |
| `S3_SECRET_KEY` | Yes | VAST S3 secret key |
| `VAST_DB_ENDPOINT` | Yes | VAST DB endpoint (same as S3_ENDPOINT typically) |
| `VAST_DB_ACCESS_KEY` | Yes | VAST DB access key (same as S3_ACCESS_KEY typically) |
| `VAST_DB_SECRET_KEY` | Yes | VAST DB secret key (same as S3_SECRET_KEY typically) |
| `VAST_DB_BUCKET` | No | VAST DB bucket for ArchiveTrail tables (default: `archive-trail-db`) |
| `VAST_DB_SCHEMA` | No | Schema name (default: `archive/lineage`) |
| `VAST_CATALOG_BUCKET` | No | Bucket containing VAST Catalog (default: `vast-big-catalog-bucket`) |
| `VAST_CATALOG_SCHEMA` | No | Catalog schema name (default: `catalog`) |
| `VAST_CATALOG_TABLE` | No | Catalog table name (default: `catalog_table`) |
| `VAST_CLUSTER_NAME` | No | Cluster identifier for metadata (default: `unknown-cluster`) |
| `AWS_ACCESS_KEY_ID` | Yes | AWS IAM credentials for target bucket |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS IAM secret |
| `AWS_DEFAULT_REGION` | No | AWS region (default: `us-east-1`) |

### Configuration Table Parameters

All operational parameters are stored in the `offload_config` table. Update via SQL:

| Key | Default | Type | Purpose |
|-----|---------|------|---------|
| `atime_threshold_days` | 60 | Integer | Min age (days) for file to be offloaded |
| `target_aws_bucket` | corp-cold-tier | String | AWS S3 bucket for offloaded files |
| `target_aws_region` | us-east-1 | String | AWS region |
| `target_aws_storage_class` | INTELLIGENT_TIERING | String | S3 storage class (see below) |
| `source_paths` | /tenant/projects,/tenant/media | CSV | Paths to scan (comma-separated) |
| `auto_delete_local` | false | Boolean | Delete local copies after AWS verification |
| `dry_run` | true | Boolean | Log actions without executing |
| `batch_size` | 500 | Integer | Max files per pipeline run |
| `verify_checksum` | true | Boolean | Verify copy integrity with MD5 |

### AWS S3 Storage Class Options

- `STANDARD` — Frequently accessed data (default AWS behavior)
- `INTELLIGENT_TIERING` — **Recommended** — Auto-transitions between access tiers
- `STANDARD_IA` — Infrequent access, 30-day minimum, retrieval fee
- `ONEZONE_IA` — Single-AZ infrequent access
- `GLACIER` — Archive, 3-6 hour retrieval latency
- `GLACIER_IR` — Instant retrieval (90-day minimum)
- `DEEP_ARCHIVE` — Long-term archive, 12-hour retrieval

## Documentation Map

- **[Architecture Guide](wiki/Architecture.md)** — Detailed system design, state machines, and traceability layers
- **[Configuration Guide](wiki/Configuration-Guide.md)** — Complete parameter reference and tuning
- **[Deployment Guide](wiki/Deployment-Guide.md)** — Step-by-step production deployment
- **[Operations Guide](wiki/Operations-Guide.md)** — Day-to-day operations, monitoring, CLI reference
- **[Database Schema](wiki/Database-Schema.md)** — Table definitions and example queries
- **[VAST Platform Setup](wiki/VAST-Platform-Setup.md)** — Prerequisites and platform configuration

## Common Queries

### "Where is file X now?"

```sql
SELECT element_handle, original_path, current_location,
       current_aws_bucket, current_aws_key, source_md5
FROM vast."archive/lineage".asset_registry
WHERE original_path LIKE '%filename.ext';
```

### "Show complete lifecycle of file X"

```sql
SELECT event_type, event_timestamp, source_path, destination_path,
       success, error_message
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x...'
ORDER BY event_timestamp ASC;
```

### "What config was active when file X was offloaded?"

```sql
SELECT event_timestamp,
       json_extract_scalar(config_snapshot, '$.atime_threshold_days') AS threshold,
       json_extract_scalar(config_snapshot, '$.auto_delete_local') AS auto_delete
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x...' AND event_type = 'COPY_COMPLETED';
```

## Support and Troubleshooting

See **[Operations Guide](wiki/Operations-Guide.md#Troubleshooting)** for:
- Common issues and solutions
- Log analysis
- Performance tuning
- Health checks

## License

[Your License Here]

## Contributing

Contributions welcome. Please follow conventional commits and include tests.
