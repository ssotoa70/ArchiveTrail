# Deployment Guide

Step-by-step guide to deploy ArchiveTrail in production on VAST Data Platform.

## Prerequisites Checklist

Before starting deployment, verify:

- [ ] **VAST Data Platform 5.4+** installed and operational
- [ ] **VAST Catalog enabled** — Settings > VAST Catalog > Enabled
  - [ ] Snapshot frequency: 30 minutes
  - [ ] Retention: 7 days
- [ ] **VAST Protocol Auditing enabled** — Settings > Auditing > Enabled
  - [ ] Logs all S3, NFS, SMB operations
  - [ ] Output: VAST Database table
- [ ] **Multiprotocol views** — Views have S3 enabled alongside NFS/SMB
- [ ] **AWS S3 account** with:
  - [ ] IAM user for ArchiveTrail
  - [ ] Permissions: GetObject, PutObject, DeleteObject, GetObjectTagging, PutObjectTagging, HeadObject
  - [ ] Target bucket created (e.g., `corp-cold-tier`)
- [ ] **Docker registry** accessible to DataEngine
  - [ ] Registry credentials configured in VMS
  - [ ] Push permissions confirmed
- [ ] **Network connectivity** — DataEngine can reach:
  - [ ] VAST S3 endpoint
  - [ ] AWS S3 endpoint
- [ ] **Cluster admin access** — To create tables, functions, pipelines

## Step 1: Create VAST Database Schema and Tables

Run these SQL scripts in VAST DB Query Editor (VMS > Database > Query Editor):

### 1.1 Create Schema

Copy and run:

```sql
CREATE SCHEMA IF NOT EXISTS vast."archive/lineage";
```

### 1.2 Create Tables

Copy and run:

```sql
CREATE TABLE IF NOT EXISTS vast."archive/lineage".offload_config (
    config_key        VARCHAR,
    config_value      VARCHAR,
    updated_by        VARCHAR,
    updated_at        TIMESTAMP,
    change_reason     VARCHAR
);

CREATE TABLE IF NOT EXISTS vast."archive/lineage".asset_registry (
    element_handle       VARCHAR,
    registration_id      VARCHAR,
    original_path        VARCHAR,
    original_bucket      VARCHAR,
    original_view        VARCHAR,
    file_name            VARCHAR,
    file_extension       VARCHAR,
    file_size_bytes      BIGINT,
    file_ctime           TIMESTAMP,
    file_mtime           TIMESTAMP,
    file_atime           TIMESTAMP,
    owner_uid            VARCHAR,
    owner_login          VARCHAR,
    nfs_mode_bits        INTEGER,
    current_location     VARCHAR,
    current_aws_bucket   VARCHAR,
    current_aws_key      VARCHAR,
    current_aws_region   VARCHAR,
    registered_at        TIMESTAMP,
    last_state_change    TIMESTAMP,
    source_md5           VARCHAR,
    destination_md5      VARCHAR
);

CREATE TABLE IF NOT EXISTS vast."archive/lineage".lifecycle_events (
    event_id             VARCHAR,
    element_handle       VARCHAR,
    registration_id      VARCHAR,
    event_type           VARCHAR,
    event_timestamp      TIMESTAMP,
    source_path          VARCHAR,
    destination_path     VARCHAR,
    aws_bucket           VARCHAR,
    aws_key              VARCHAR,
    file_size_bytes      BIGINT,
    file_atime           TIMESTAMP,
    file_mtime           TIMESTAMP,
    pipeline_run_id      VARCHAR,
    function_name        VARCHAR,
    triggered_by         VARCHAR,
    success              BOOLEAN,
    error_message        VARCHAR,
    checksum_value       VARCHAR,
    config_snapshot      VARCHAR
);

CREATE TABLE IF NOT EXISTS vast."archive/lineage".config_change_log (
    change_id            VARCHAR,
    config_key           VARCHAR,
    old_value            VARCHAR,
    new_value            VARCHAR,
    changed_by           VARCHAR,
    changed_at           TIMESTAMP,
    change_reason        VARCHAR
);
```

### 1.3 Seed Default Configuration

Copy and run (adjust values to match your environment):

```sql
INSERT INTO vast."archive/lineage".offload_config
    (config_key, config_value, updated_by, updated_at, change_reason)
VALUES
    ('atime_threshold_days',     '60',                        'admin', now(), 'Initial setup'),
    ('target_aws_bucket',        'corp-cold-tier',            'admin', now(), 'Initial setup'),
    ('target_aws_region',        'us-east-1',                 'admin', now(), 'Initial setup'),
    ('target_aws_storage_class', 'INTELLIGENT_TIERING',       'admin', now(), 'Cost-optimized tiering'),
    ('source_paths',             '/tenant/projects,/tenant/media', 'admin', now(), 'Initial setup'),
    ('auto_delete_local',        'false',                     'admin', now(), 'Start conservative'),
    ('dry_run',                  'true',                      'admin', now(), 'Initial setup'),
    ('batch_size',               '500',                       'admin', now(), 'Initial setup'),
    ('verify_checksum',          'true',                      'admin', now(), 'Data integrity enforcement');
```

**Verify:**

```sql
SELECT * FROM vast."archive/lineage".offload_config;
```

Should show 9 rows.

## Step 2: Prepare Source Code and Build Functions

### 2.1 Clone or Download ArchiveTrail

```bash
git clone https://github.com/<org>/archive-trail.git
cd archive-trail
```

### 2.2 Set Build Variables

```bash
export REGISTRY=<DOCKER_REGISTRY>  # e.g., docker.company.com:5000
export VERSION=$(date +%Y%m%d-%H%M%S)
```

### 2.3 Build All Functions

```bash
# Copy shared library into each function directory
make prep-functions

# Build function images with VAST CNB + Dockerfile.fix
make build-all

# Verify images were built
docker images | grep archive-trail
```

**Output:**
```
archive-trail-discover          latest     <image_id>
archive-trail-offload           latest     <image_id>
archive-trail-verify-purge      latest     <image_id>
```

## Step 3: Push Images to Registry

```bash
# Tag for registry
docker tag archive-trail-discover:latest $REGISTRY/archive-trail-discover:$VERSION
docker tag archive-trail-offload:latest $REGISTRY/archive-trail-offload:$VERSION
docker tag archive-trail-verify-purge:latest $REGISTRY/archive-trail-verify-purge:$VERSION

# Push to registry
docker push $REGISTRY/archive-trail-discover:$VERSION
docker push $REGISTRY/archive-trail-offload:$VERSION
docker push $REGISTRY/archive-trail-verify-purge:$VERSION

# Or use make shorthand
make push-all
```

## Step 4: Create DataEngine Functions

In VMS UI: **DataEngine > Functions > Create Function**

### 4.1 Create discover Function

| Field | Value |
|-------|-------|
| **Name** | `archive-trail-discover` |
| **Image Repository** | `<REGISTRY>/archive-trail-discover` |
| **Image Tag** | `$VERSION` (from Step 2) |
| **Container Registry** | Select your registry credential |
| **Memory** | 2 GB |
| **CPU** | 1 core |
| **Timeout** | 30 minutes |

**Environment Variables:**
- `S3_ENDPOINT` → `https://<VAST_DATA_VIP>`
- `S3_ACCESS_KEY` → (from VAST IAM)
- `S3_SECRET_KEY` → (from VAST IAM)
- `VAST_DB_ENDPOINT` → Same as S3_ENDPOINT
- `VAST_DB_ACCESS_KEY` → Same as S3_ACCESS_KEY
- `VAST_DB_SECRET_KEY` → Same as S3_SECRET_KEY
- `VAST_CLUSTER_NAME` → (e.g., `prod-us-east-1`)

### 4.2 Create offload Function

| Field | Value |
|-------|-------|
| **Name** | `archive-trail-offload` |
| **Image Repository** | `<REGISTRY>/archive-trail-offload` |
| **Image Tag** | `$VERSION` |
| **Memory** | 4 GB |
| **CPU** | 2 cores |
| **Timeout** | 60 minutes |

**Environment Variables:**
- All from discover, plus:
- `AWS_ACCESS_KEY_ID` → (from AWS IAM)
- `AWS_SECRET_ACCESS_KEY` → (from AWS IAM)
- `AWS_DEFAULT_REGION` → `us-east-1` (or your region)

### 4.3 Create verify_purge Function

| Field | Value |
|-------|-------|
| **Name** | `archive-trail-verify-purge` |
| **Image Repository** | `<REGISTRY>/archive-trail-verify-purge` |
| **Image Tag** | `$VERSION` |
| **Memory** | 2 GB |
| **CPU** | 1 core |
| **Timeout** | 30 minutes |

**Environment Variables:**
- All from offload

## Step 5: Create DataEngine Pipeline

In VMS UI: **DataEngine > Pipelines > Create Pipeline**

### 5.1 Create Pipeline

| Field | Value |
|-------|-------|
| **Name** | `archive-trail-tiering` |
| **Description** | Automated cold data tiering with genealogy tracking |

### 5.2 Add Schedule Trigger

- **Trigger Type** → Schedule
- **Frequency** → Daily
- **Time** → 2:00 AM (or preferred off-peak window)

### 5.3 Connect Functions

Create a pipeline DAG:

```
Schedule Trigger
       ↓
   discover
       ↓
  offload_and_track
       ↓
   verify_and_purge
```

In the UI, drag connections between function boxes.

### 5.4 Verify Pipeline

```
Pipeline: archive-trail-tiering
├── Trigger: Schedule (daily 2 AM)
├── discover
│   └── Input: Clock event
│   └── Output: candidates list
├── offload_and_track
│   └── Input: candidates
│   └── Output: offloaded/failed lists
└── verify_and_purge
    └── Input: (queries DB directly)
    └── Output: purged/failed lists
```

## Step 6: Deployment Validation

### 6.1 Manual Test Run

Trigger the pipeline manually to test (avoid waiting for schedule):

1. VMS UI → DataEngine → Pipelines → archive-trail-tiering
2. **Actions** → **Run Now**
3. Monitor execution

### 6.2 Check Function Logs

After run completes:

VMS UI → DataEngine → Runs → [Latest Run]

View logs for each function:
- Click on discover → View Logs
- Click on offload_and_track → View Logs
- Click on verify_and_purge → View Logs

### 6.3 Verify Database Records

Query the database to confirm data was written:

```sql
-- Check if discover found any files
SELECT COUNT(*) as scanned_count
FROM vast."archive/lineage".lifecycle_events
WHERE event_type = 'SCANNED';

-- Check registry for registered files
SELECT COUNT(*) as registered_count
FROM vast."archive/lineage".asset_registry;

-- Check for errors
SELECT event_type, count(*) as count
FROM vast."archive/lineage".lifecycle_events
WHERE success = false
GROUP BY event_type;
```

### 6.4 Verify AWS S3

Check if files were actually copied to AWS (in copy-only or live mode):

```bash
aws s3 ls s3://corp-cold-tier/ --recursive --summarize
```

Check metadata on an uploaded object:

```bash
aws s3api head-object \
  --bucket corp-cold-tier \
  --key <first-offloaded-path>
```

Should show genealogy metadata headers.

## Step 7: Phased Rollout

### Phase 1: Dry-Run Validation (1-2 weeks)

**Configuration:**
```sql
UPDATE vast."archive/lineage".offload_config 
SET config_value='true' WHERE config_key='dry_run';

UPDATE vast."archive/lineage".offload_config 
SET config_value='false' WHERE config_key='auto_delete_local';
```

**Validation:**
- Run pipeline daily (automatic via schedule)
- Query SCANNED events to see what would be offloaded
- Spot-check discovered files are actually old (verify atime)
- Check for errors in logs
- Estimate offload volume

**Duration:** Until confident in discovery logic (1-2 weeks typical)

### Phase 2: Copy-Only (1-4 weeks)

**Configuration:**
```sql
UPDATE vast."archive/lineage".offload_config 
SET config_value='false' WHERE config_key='dry_run';

UPDATE vast."archive/lineage".offload_config 
SET config_value='false' WHERE config_key='auto_delete_local';
```

**Validation:**
- Files copied to AWS, local copies retained
- Verify AWS copy retrievable: `aws s3 cp s3://corp-cold-tier/path /tmp/test`
- Monitor AWS costs (S3 storage for copies)
- Check checksum verification succeeds (no CHECKSUM_MISMATCH events)
- Test manual recall from AWS (for future automation)

**Duration:** Until confident in copy integrity and recovery (2-4 weeks typical)

### Phase 3: Auto-Purge (Production)

**Configuration:**
```sql
UPDATE vast."archive/lineage".offload_config 
SET config_value='true' WHERE config_key='auto_delete_local';
```

**Validation:**
- Monitor LOCAL_DELETED events
- Verify storage savings on VAST
- Set up alerting for COPY_FAILED and LOCAL_DELETE_FAILED events

**Ongoing:**
- Query genealogy data regularly
- Monitor AWS costs vs. storage savings
- Update threshold/paths as needed

## Deployment Checklist

- [ ] Prerequisites verified (VAST 5.4+, Catalog, Protocol Auditing, multiprotocol views)
- [ ] AWS IAM credentials obtained (access key + secret)
- [ ] Docker registry access confirmed
- [ ] VAST DB schema created (`archive/lineage`)
- [ ] 4 tables created (offload_config, asset_registry, lifecycle_events, config_change_log)
- [ ] Config table seeded with defaults
- [ ] Source code cloned and build environment set up
- [ ] All 3 functions built successfully
- [ ] All 3 images pushed to registry
- [ ] 3 DataEngine functions created with correct image tags and environment variables
- [ ] Pipeline created and connected (discover → offload → verify_purge)
- [ ] Schedule trigger configured (daily 2 AM or preferred time)
- [ ] Manual test run completed successfully
- [ ] Database queries show expected records
- [ ] AWS S3 lists offloaded files with correct metadata
- [ ] Phase 1 (dry-run) validation completed
- [ ] Phase 2 (copy-only) validation completed
- [ ] Phase 3 (auto-purge) enabled for production

## Post-Deployment

### Monitoring

Set up monitoring for:

1. **Pipeline Health**
   - Daily run completion (check execution logs)
   - Function success/failure rates
   
2. **Data Quality**
   - COPY_FAILED or CHECKSUM_MISMATCH events
   - LOCAL_DELETE_FAILED events
   
3. **Storage Metrics**
   - Files offloaded per day
   - Storage freed on VAST
   - AWS S3 costs
   
4. **Configuration Drift**
   - Track config changes via config_change_log
   - Alert on unexpected changes

### Backup Strategy

- **VAST DB tables** — Use VAST backup/snapshots
- **AWS S3** — Enable versioning and cross-region replication for critical data
- **Configuration** — Track config_change_log separately (audit trail)

### Troubleshooting Resources

See **[Operations Guide](Operations-Guide.md)** for:
- Common errors and solutions
- Log analysis
- Performance tuning
- Manual triggers and CLI

## Support

For issues during deployment, refer to:
- **[Configuration Guide](Configuration-Guide.md)** — Environment variables and parameters
- **[Architecture Guide](Architecture.md)** — System design details
- **[VAST Platform Setup](VAST-Platform-Setup.md)** — Platform prerequisites
