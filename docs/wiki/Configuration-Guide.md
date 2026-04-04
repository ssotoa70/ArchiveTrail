# Configuration Guide

This guide covers all configuration options for ArchiveTrail, including environment variables, database parameters, AWS options, and multiprotocol considerations.

## Configuration Tiers

ArchiveTrail uses a two-tier configuration model:

### Tier 1: Bootstrap Configuration (Environment Variables)

Set at pipeline deployment time, before the application runs. Cannot be changed without redeploying the pipeline.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `S3_ENDPOINT` | Yes | N/A | VAST S3 endpoint (VIP or hostname) |
| `S3_ACCESS_KEY` | Yes | N/A | VAST S3 IAM access key |
| `S3_SECRET_KEY` | Yes | N/A | VAST S3 IAM secret |
| `VAST_DB_ENDPOINT` | Yes | Same as S3_ENDPOINT | VAST DB endpoint |
| `VAST_DB_ACCESS_KEY` | Yes | Same as S3_ACCESS_KEY | VAST DB access key |
| `VAST_DB_SECRET_KEY` | Yes | Same as S3_SECRET_KEY | VAST DB secret |
| `VAST_DB_BUCKET` | No | `archive-trail-db` | Bucket for ArchiveTrail tables |
| `VAST_DB_SCHEMA` | No | `archive/lineage` | Schema name for tables |
| `VAST_CATALOG_BUCKET` | No | `vast-big-catalog-bucket` | VAST Catalog bucket |
| `VAST_CATALOG_SCHEMA` | No | `catalog` | Catalog schema name |
| `VAST_CATALOG_TABLE` | No | `catalog_table` | Catalog table name |
| `VAST_CLUSTER_NAME` | No | `unknown-cluster` | Cluster identifier for metadata |
| `AWS_ACCESS_KEY_ID` | Yes | N/A | AWS IAM access key |
| `AWS_SECRET_ACCESS_KEY` | Yes | N/A | AWS IAM secret |
| `AWS_DEFAULT_REGION` | No | `us-east-1` | AWS region for cold bucket |

### Tier 2: Runtime Configuration (VAST DB Table)

Stored in the `offload_config` table, can be updated at any time without redeployment. Takes effect on next pipeline run.

| Key | Default | Type | Range/Options | Purpose |
|-----|---------|------|---------------|---------|
| `atime_threshold_days` | `60` | Integer | 1-36500 | Min age (days) for offload |
| `target_aws_bucket` | `corp-cold-tier` | String | S3 bucket name | AWS bucket for cold data |
| `target_aws_region` | `us-east-1` | String | AWS region | AWS region |
| `target_aws_storage_class` | `INTELLIGENT_TIERING` | String | See table below | S3 storage class |
| `source_paths` | `/tenant/projects,/tenant/media` | CSV | Comma-separated paths | Paths to scan |
| `auto_delete_local` | `false` | Boolean | true/false | Delete local after AWS verify |
| `dry_run` | `true` | Boolean | true/false | Log without executing |
| `batch_size` | `500` | Integer | 1-10000 | Max files per run |
| `verify_checksum` | `true` | Boolean | true/false | Verify copy integrity |

## Environment Variables

### VAST S3 (Source)

```bash
S3_ENDPOINT=https://<VAST_DATA_VIP>
S3_ACCESS_KEY=<IAM_ACCESS_KEY>
S3_SECRET_KEY=<IAM_SECRET_KEY>
```

The S3 endpoint should be a VAST S3 VIP or resolvable hostname. Example:
- `https://<VAST_DATA_VIP>`
- `https://s3.vast.internal`

Get credentials from VAST cluster admin (VAST UI > Cluster Settings > IAM > Generate Key).

### VAST Database

```bash
VAST_DB_ENDPOINT=https://<VAST_DATA_VIP>  # typically same as S3_ENDPOINT
VAST_DB_ACCESS_KEY=<IAM_ACCESS_KEY>       # typically same as S3_ACCESS_KEY
VAST_DB_SECRET_KEY=<IAM_SECRET_KEY>       # typically same as S3_SECRET_KEY
VAST_DB_BUCKET=archive-trail-db           # where to store tables
VAST_DB_SCHEMA=archive/lineage            # schema path
```

VAST DB is built into VAST Data Platform; no separate database needed.

### VAST Catalog

```bash
VAST_CATALOG_BUCKET=vast-big-catalog-bucket
VAST_CATALOG_SCHEMA=catalog
VAST_CATALOG_TABLE=catalog_table
```

These defaults match standard VAST Catalog configuration. Change only if your cluster uses non-standard paths.

### VAST Cluster Identity

```bash
VAST_CLUSTER_NAME=prod-us-east-1
```

This name is embedded in AWS S3 metadata of offloaded objects, making it queryable and useful for multi-cluster deployments.

### AWS S3 (Destination)

```bash
AWS_ACCESS_KEY_ID=<AWS_ACCESS_KEY>
AWS_SECRET_ACCESS_KEY=<AWS_SECRET_KEY>
AWS_DEFAULT_REGION=us-east-1
```

Generate credentials from AWS IAM. Required permissions:
- `s3:GetObject`
- `s3:PutObject`
- `s3:DeleteObject`
- `s3:GetObjectTagging`
- `s3:PutObjectTagging`
- `s3:HeadObject`

On target bucket (e.g., `corp-cold-tier`).

## Runtime Configuration (VAST DB Table)

### Updating Parameters

All parameters in the `offload_config` table can be updated via SQL without restarting the pipeline:

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '<new_value>', updated_by = '<username>', updated_at = now(),
    change_reason = '<reason>'
WHERE config_key = '<key>';
```

Change is picked up on next pipeline run.

### Example: Adjust Threshold

```sql
-- Increase threshold from 60 to 90 days
UPDATE vast."archive/lineage".offload_config
SET config_value = '90', updated_by = 'admin', updated_at = now(),
    change_reason = 'Policy change: extend retention window'
WHERE config_key = 'atime_threshold_days';

-- View change log
SELECT * FROM vast."archive/lineage".config_change_log
WHERE config_key = 'atime_threshold_days'
ORDER BY changed_at DESC;
```

### Example: Change AWS Bucket

```sql
-- Switch to new cold tier bucket
UPDATE vast."archive/lineage".offload_config
SET config_value = 'corp-cold-tier-v2', updated_by = 'admin', updated_at = now(),
    change_reason = 'Migration to new bucket'
WHERE config_key = 'target_aws_bucket';
```

**Note:** Existing files remain in the old bucket. New files go to the new bucket. Track both locations.

### Example: Enable Auto-Purge

```sql
-- After confidence period in copy-only mode
UPDATE vast."archive/lineage".offload_config
SET config_value = 'false', updated_by = 'admin', updated_at = now(),
    change_reason = 'Phase 2→3: enable automatic purge'
WHERE config_key = 'dry_run';

UPDATE vast."archive/lineage".offload_config
SET config_value = 'true', updated_by = 'admin', updated_at = now(),
    change_reason = 'Phase 2→3: enable automatic purge'
WHERE config_key = 'auto_delete_local';
```

## AWS S3 Storage Classes

The `target_aws_storage_class` parameter controls how offloaded objects are stored:

| Class | Cost | Retrieval Latency | Min. Retention | Use Case |
|-------|------|-------------------|-----------------|----------|
| **STANDARD** | High | Immediate | None | Frequently accessed data (default AWS) |
| **INTELLIGENT_TIERING** | Medium (variable) | Immediate to hours | None | **Recommended** — Auto-transitions between tiers based on access |
| **STANDARD_IA** | Medium | Immediate | 30 days | Infrequent access, cost-sensitive |
| **ONEZONE_IA** | Medium | Immediate | 30 days | Infrequent, single-AZ OK |
| **GLACIER** | Low | 3-6 hours | 90 days | Cold archives, multi-hour retrieval OK |
| **GLACIER_IR** | Low | Instant | 90 days | Cold archives, instant retrieval needed |
| **DEEP_ARCHIVE** | Very low | 12 hours | 180 days | Long-term compliance archives |

### Recommended Configuration

**For most deployments:**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'INTELLIGENT_TIERING'
WHERE config_key = 'target_aws_storage_class';
```

**Why?** 
- Automatically transitions from warm to cold tiers based on access patterns
- Cost-effective for unpredictable access patterns
- No minimum retention or retrieval fees for immediate access
- Easy to promote back to STANDARD if needed

**For compliance long-term archives:**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'DEEP_ARCHIVE'
WHERE config_key = 'target_aws_storage_class';
```

**For cost optimization (no recall expected):**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'GLACIER'
WHERE config_key = 'target_aws_storage_class';
```

## Source Path Configuration

The `source_paths` parameter controls which VAST paths are scanned for cold data.

### Format

Comma-separated list of absolute paths:
```
/tenant/projects,/tenant/media,/archive/logs
```

### Matching Logic

A file is included if its parent path:
1. Equals one of the configured paths exactly, OR
2. Starts with one of the paths followed by `/`

**Example:**
```
source_paths = /tenant/projects,/tenant/media
```

**Included:**
- `/tenant/projects/report.pdf` ✓
- `/tenant/projects/2026/report.pdf` ✓
- `/tenant/media/video.mp4` ✓
- `/tenant/media/uploads/image.jpg` ✓

**Excluded:**
- `/tenant/archived/old.pdf` ✗
- `/projects/report.pdf` ✗ (no /tenant prefix)

### Multi-Tenant Configuration

To scan multiple tenants:
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '/tenant-a/cold,/tenant-b/archive,/tenant-c/media'
WHERE config_key = 'source_paths';
```

Files under different tenants are tracked separately via element_handle and VAST Element Store, so there's no cross-contamination.

### Excluding Subdirectories

To scan `/data` but exclude `/data/keep`:
```sql
-- Configure to scan /data
UPDATE vast."archive/lineage".offload_config
SET config_value = '/data'
WHERE config_key = 'source_paths';

-- Then exclude /data/keep via a tag or naming convention
-- (future feature: path exclusion patterns)
```

## Multiprotocol Considerations

ArchiveTrail works transparently across VAST multiprotocol views (NFS, SMB, S3).

### View Configuration

Ensure source views have S3 enabled:

```
VAST UI → Element Store → Views → [Edit View]
  Protocols: NFSv3  ✓  NFSv4  ✓  SMB  ✓  S3 Bucket  ✓
```

**Why S3 required?**
- Discovery reads from VAST Catalog (works for any protocol)
- Offload copies via S3 (requires S3 on view)
- If only NFS/SMB enabled, offload will fail

### Path Representation

The same file is accessible via multiple paths depending on protocol:

**NFS:** `/tenant/projects/report.pdf`
**SMB:** `\\<server>\projects\report.pdf`
**S3:** `s3://<bucket>/tenant/projects/report.pdf`

ArchiveTrail uses:
- **NFS path** in Catalog queries and asset_registry
- **S3 path** for actual copy operations
- **Element Handle** for immutable identity across all protocols

### Cross-Protocol Queries

Query lifecycle events with the NFS path, even though the copy operation used S3:

```sql
SELECT event_timestamp, event_type, source_path, destination_path
FROM vast."archive/lineage".lifecycle_events
WHERE source_path = '/tenant/projects/report.pdf'
ORDER BY event_timestamp;
```

The element_handle links all operations together, regardless of protocol used.

## Batch Size Tuning

The `batch_size` parameter controls how many files are processed per pipeline run.

### Factors

**Increase batch_size if:**
- Large pipeline window (e.g., daily run has 8+ hours before next operation)
- High memory/CPU available in DataEngine pod
- Low per-file overhead (network fast, checksums disabled)
- Volume of cold files is very large

**Decrease batch_size if:**
- Hits memory or timeout limits
- DataEngine pod constrained
- Network is slow
- Checksum verification is enabled (doubles I/O)

### Calculation

```
batch_size ≈ (available_memory_gb * 1000) / (avg_file_size_mb * 2 * checksum_multiplier)
           × memory_safety_factor
```

Where `checksum_multiplier = 2` if verify_checksum=true, else 1.

**Example:**
- Available: 4 GB
- Avg file: 50 MB
- Checksum verification: enabled
- Safety factor: 0.5 (conservative)

```
batch_size = (4 * 1000) / (50 * 2 * 2) * 0.5
           = 4000 / 200 * 0.5
           ≈ 10
```

### Recommended Defaults

| Scenario | batch_size |
|----------|-----------|
| Small files (<10 MB) | 1000 |
| Medium files (10-100 MB) | 500 |
| Large files (>100 MB) | 100 |
| Bandwidth constrained | 50-100 |
| Compliance/audit heavy | 100-200 |

## Dry-Run Mode

### Purpose

Test the pipeline without making any changes:

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'true' WHERE config_key = 'dry_run';
```

In dry-run mode:
- **discover** — Finds cold files, emits SCANNED instead of REGISTERED
- **offload** — Skips S3 copy, emits SCANNED instead of COPY_STARTED
- **verify_purge** — Does nothing (no files in BOTH state from dry-run)

### Workflow

1. Deploy pipeline with `dry_run=true`, `auto_delete_local=false`
2. Run pipeline (via schedule or manual trigger)
3. Query lifecycle_events for SCANNED records
4. Validate discovered files are correct
5. Set `dry_run=false` when confident

## Checksum Verification

### Overhead

Checksum verification doubles the I/O cost:
- Source: MD5 computed during GetObject from VAST
- Destination: Separate GetObject from AWS to verify

### Recommendation

Keep enabled (`verify_checksum=true`) unless:
- Network is severely constrained
- Compliance doesn't require copy verification
- File integrity already verified by other means

### Disable (Not Recommended)

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'false' WHERE config_key = 'verify_checksum';
```

If disabled:
- Source MD5 still computed and stored in AWS metadata
- No destination verification performed
- CHECKSUM_VERIFIED event not emitted
- Failures harder to detect

## Configuration File (Local Development)

For local testing, copy and fill `.env.example`:

```bash
cp .env.example .env
# Edit .env with your values
source .env
make install
make test
```

## Configuration Validation

Check current configuration:

```sql
SELECT config_key, config_value, updated_at, updated_by, change_reason
FROM vast."archive/lineage".offload_config
ORDER BY config_key;
```

View change history:

```sql
SELECT config_key, old_value, new_value, changed_at, changed_by, change_reason
FROM vast."archive/lineage".config_change_log
ORDER BY changed_at DESC
LIMIT 20;
```

## Troubleshooting Configuration Issues

### "Missing required config keys"

**Error:** Function crashes with "Missing required config keys: ..."

**Solution:** Run seed_config.sql to populate the offload_config table.

### "No bucket mapping found for path"

**Error:** Files not being discovered.

**Solution:** Check source_paths matches actual file locations. Remember the matching logic uses exact path or prefix with `/`.

### "AWS copy not found during purge"

**Error:** LOCAL_DELETE_FAILED events.

**Solution:** Verify target_aws_bucket is correct and accessible with current AWS credentials.

### "Checksum mismatch"

**Error:** CHECKSUM_MISMATCH events.

**Solution:** 
1. Check network stability (try disabling checksum verification temporarily)
2. Verify AWS S3 bucket permissions
3. Monitor VAST S3 and AWS S3 health
4. Re-run failed files manually

### Performance degradation

**Symptom:** Pipeline taking too long or hitting timeouts.

**Solutions:**
1. Decrease batch_size
2. Disable verify_checksum (if acceptable)
3. Extend pipeline timeout in VMS UI
4. Check network bandwidth to AWS
5. Monitor VAST S3 and AWS S3 latency
