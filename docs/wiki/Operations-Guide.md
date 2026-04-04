# Operations Guide

Daily operations, monitoring, troubleshooting, and CLI reference for ArchiveTrail.

## Monitoring Dashboard

### Key Metrics

Monitor these metrics daily:

```sql
-- Pipeline execution summary (last 7 days)
SELECT
    DATE(event_timestamp) as date,
    COUNT(DISTINCT pipeline_run_id) as runs,
    COUNT(CASE WHEN event_type = 'REGISTERED' THEN 1 END) as discovered,
    COUNT(CASE WHEN event_type = 'COPY_COMPLETED' THEN 1 END) as offloaded,
    COUNT(CASE WHEN event_type = 'LOCAL_DELETED' THEN 1 END) as purged,
    COUNT(CASE WHEN success = false THEN 1 END) as errors
FROM vast."archive/lineage".lifecycle_events
WHERE event_timestamp > now() - INTERVAL '7' DAY
GROUP BY DATE(event_timestamp)
ORDER BY date DESC;
```

**Output:**
```
date       | runs | discovered | offloaded | purged | errors
2026-03-20 | 1    | 1,234      | 1,200     | 1,100  | 5
2026-03-19 | 1    | 1,456      | 1,410     | 1,400  | 2
2026-03-18 | 1    | 890        | 875       | 850    | 0
```

### Storage Metrics

```sql
-- Total data offloaded (GB)
SELECT
    ROUND(SUM(file_size_bytes) / 1024.0 / 1024.0 / 1024.0, 2) as total_gb,
    COUNT(DISTINCT element_handle) as unique_files
FROM vast."archive/lineage".asset_registry
WHERE current_location IN ('AWS', 'BOTH', 'LOCAL_DELETED');

-- Offloaded by source path
SELECT
    original_path,
    COUNT(*) as count,
    ROUND(SUM(file_size_bytes) / 1024.0 / 1024.0 / 1024.0, 2) as gb,
    MAX(last_state_change) as last_offload
FROM vast."archive/lineage".asset_registry
WHERE current_location IN ('AWS', 'BOTH', 'LOCAL_DELETED')
GROUP BY original_path
ORDER BY gb DESC;
```

### Error Tracking

```sql
-- Recent errors by type
SELECT
    event_type,
    COUNT(*) as count,
    MAX(event_timestamp) as last_error
FROM vast."archive/lineage".lifecycle_events
WHERE success = false
  AND event_timestamp > now() - INTERVAL '7' DAY
GROUP BY event_type
ORDER BY count DESC;

-- Most recent errors with details
SELECT
    event_timestamp,
    event_type,
    source_path,
    error_message
FROM vast."archive/lineage".lifecycle_events
WHERE success = false
  AND event_timestamp > now() - INTERVAL '24' HOUR
ORDER BY event_timestamp DESC
LIMIT 20;
```

## Genealogy Queries

### "Where is file X now?"

**Find by original path:**
```sql
SELECT
    element_handle,
    original_path,
    file_size_bytes,
    current_location,
    current_aws_bucket,
    current_aws_key,
    source_md5,
    registered_at,
    last_state_change
FROM vast."archive/lineage".asset_registry
WHERE original_path LIKE '%report.pdf';
```

**Find by element handle:**
```sql
SELECT
    element_handle,
    original_path,
    current_location,
    current_aws_bucket,
    current_aws_key,
    last_state_change
FROM vast."archive/lineage".asset_registry
WHERE element_handle = '0x1A2B3C4D';
```

### "Show complete lifecycle of file X"

```sql
SELECT
    event_type,
    event_timestamp,
    source_path,
    destination_path,
    success,
    error_message,
    checksum_value,
    pipeline_run_id
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x1A2B3C4D'
ORDER BY event_timestamp ASC;
```

**Output Example:**
```
event_type              | event_timestamp       | source_path           | success
REGISTERED              | 2026-03-17 02:01:12  | /tenant/projects/r... | true
THRESHOLD_EVALUATED     | 2026-03-17 02:01:12  | /tenant/projects/r... | true
COPY_STARTED            | 2026-03-17 02:01:13  | /tenant/projects/r... | true
CHECKSUM_VERIFIED       | 2026-03-17 02:03:45  | /tenant/projects/r... | true
COPY_COMPLETED          | 2026-03-17 02:03:46  | /tenant/projects/r... | true
LOCAL_DELETE_REQUESTED  | 2026-03-18 02:00:01  | /tenant/projects/r... | true
LOCAL_DELETED           | 2026-03-18 02:00:03  | /tenant/projects/r... | true
```

### "Cross-reference with VAST Protocol Audit"

**Verify our function actually read the file:**
```sql
SELECT
    timestamp,
    protocol,
    operation,
    object_path,
    user_name,
    bytes
FROM vast."audit/schema".audit_table
WHERE object_path LIKE '%report.pdf%'
  AND timestamp BETWEEN '2026-03-17 02:00:00' AND '2026-03-17 02:05:00'
ORDER BY timestamp;
```

**Why?** Independent witness that our function actually performed the operations claimed in lifecycle_events.

### "What config was active when file X was offloaded?"

```sql
SELECT
    event_timestamp,
    json_extract_scalar(config_snapshot, '$.atime_threshold_days') AS threshold_days,
    json_extract_scalar(config_snapshot, '$.auto_delete_local') AS auto_delete,
    json_extract_scalar(config_snapshot, '$.target_aws_bucket') AS bucket,
    json_extract_scalar(config_snapshot, '$.verify_checksum') AS verify_checksum
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x1A2B3C4D'
  AND event_type = 'COPY_COMPLETED';
```

### "List all files from path X in state Y"

```sql
SELECT
    element_handle,
    file_name,
    file_size_bytes,
    current_location,
    registered_at,
    last_state_change
FROM vast."archive/lineage".asset_registry
WHERE original_path LIKE '/tenant/projects/2024/%'
  AND current_location = 'LOCAL_DELETED'
ORDER BY last_state_change DESC;
```

### "Find files offloaded under old threshold"

```sql
SELECT
    a.element_handle,
    a.original_path,
    e.event_timestamp,
    json_extract_scalar(e.config_snapshot, '$.atime_threshold_days') AS threshold_at_offload,
    (SELECT config_value FROM vast."archive/lineage".offload_config
     WHERE config_key = 'atime_threshold_days') AS current_threshold
FROM vast."archive/lineage".asset_registry a
JOIN vast."archive/lineage".lifecycle_events e
  ON a.element_handle = e.element_handle
  AND e.event_type = 'COPY_COMPLETED'
WHERE json_extract_scalar(e.config_snapshot, '$.atime_threshold_days')
      != (SELECT config_value FROM vast."archive/lineage".offload_config
          WHERE config_key = 'atime_threshold_days')
ORDER BY e.event_timestamp DESC;
```

**Use case:** Audit for policy compliance after threshold changes.

### "Show config change history"

```sql
SELECT
    changed_at,
    config_key,
    old_value,
    new_value,
    changed_by,
    change_reason
FROM vast."archive/lineage".config_change_log
WHERE config_key = 'atime_threshold_days'
ORDER BY changed_at DESC;
```

## Configuration Changes

### Updating Parameters

All operational parameters are in the `offload_config` table and can be updated at any time without restarting.

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '<new_value>',
    updated_by = '<username>',
    updated_at = now(),
    change_reason = '<reason>'
WHERE config_key = '<key>';
```

The change takes effect on the next pipeline run.

### Common Changes

**Adjust threshold (e.g., 60 → 90 days):**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '90', updated_by = 'admin', updated_at = now(),
    change_reason = 'Policy change: increase retention'
WHERE config_key = 'atime_threshold_days';
```

**Add more source paths:**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '/tenant/projects,/tenant/media,/archive/logs',
    updated_by = 'admin', updated_at = now(),
    change_reason = 'Include logs directory'
WHERE config_key = 'source_paths';
```

**Change AWS storage class:**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'GLACIER', updated_by = 'admin', updated_at = now(),
    change_reason = 'Cost optimization: archive to Glacier'
WHERE config_key = 'target_aws_storage_class';
```

**Enable auto-purge:**
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'true', updated_by = 'admin', updated_at = now(),
    change_reason = 'Phase 2→3: enable automatic purge'
WHERE config_key = 'auto_delete_local';
```

## Manual Pipeline Triggers

### Trigger a Run Immediately

In VMS UI:
1. **DataEngine → Pipelines → archive-trail-tiering**
2. **Actions → Run Now**

Or via CLI (if DataEngine SDK available):
```bash
vastde pipeline run archive-trail-tiering
```

### Run a Specific Function

```bash
# Discover only (test detection)
vastde functions invoke archive-trail-discover --input '{"test": true}'

# Offload with manual candidate list
vastde functions invoke archive-trail-offload --input '{"candidates": [...]}'
```

### Test in Dry-Run Mode

```sql
-- Temporarily enable dry-run
UPDATE vast."archive/lineage".offload_config
SET config_value = 'true' WHERE config_key = 'dry_run';

-- Trigger pipeline
-- (via VMS UI or CLI)

-- Disable dry-run when done
UPDATE vast."archive/lineage".offload_config
SET config_value = 'false' WHERE config_key = 'dry_run';
```

## Troubleshooting

### "No files discovered"

**Symptoms:** Pipeline runs but 0 files in REGISTERED events.

**Check:**
1. Are there any old files? Query Catalog directly:
   ```sql
   SELECT COUNT(*) FROM vast."catalog/schema".catalog_table
   WHERE element_type = 'FILE' AND atime < now() - INTERVAL '60' DAY;
   ```

2. Are source_paths correct?
   ```sql
   SELECT config_value FROM vast."archive/lineage".offload_config
   WHERE config_key = 'source_paths';
   ```

3. Check threshold is reasonable:
   ```sql
   SELECT config_value FROM vast."archive/lineage".offload_config
   WHERE config_key = 'atime_threshold_days';
   ```

4. Check function logs for errors (VMS UI → DataEngine → Runs)

**Solutions:**
- Adjust threshold to find any cold files: `UPDATE ... SET config_value = '1'`
- Verify Catalog is actually being updated (Settings > VAST Catalog)
- Check file atime values are being set (depends on atime_frequency)

### "Checksum mismatch"

**Symptoms:** CHECKSUM_MISMATCH events, files not copied.

**Diagnosis:**
```sql
SELECT source_path, checksum_value, error_message
FROM vast."archive/lineage".lifecycle_events
WHERE event_type = 'CHECKSUM_MISMATCH'
ORDER BY event_timestamp DESC
LIMIT 10;
```

**Causes:**
- File modified during copy (race condition)
- Network corruption
- S3 bucket misconfigured
- AWS credential issue

**Solutions:**
1. **Temporary disable checksum verification:**
   ```sql
   UPDATE vast."archive/lineage".offload_config
   SET config_value = 'false' WHERE config_key = 'verify_checksum';
   ```

2. **Re-run failed files:**
   ```sql
   DELETE FROM vast."archive/lineage".asset_registry
   WHERE element_handle IN (
       SELECT DISTINCT element_handle FROM vast."archive/lineage".lifecycle_events
       WHERE event_type = 'CHECKSUM_MISMATCH'
   );
   ```

3. **Check network and S3 health:**
   ```bash
   # Test VAST S3 connectivity
   aws s3 ls s3://<vast-bucket> --endpoint-url https://<VAST_IP>
   
   # Test AWS S3 connectivity
   aws s3 ls s3://<aws-bucket>
   ```

### "Local delete failed"

**Symptoms:** LOCAL_DELETE_FAILED events, files stuck in BOTH state.

**Diagnosis:**
```sql
SELECT source_path, error_message
FROM vast."archive/lineage".lifecycle_events
WHERE event_type = 'LOCAL_DELETE_FAILED'
ORDER BY event_timestamp DESC
LIMIT 10;
```

**Common causes:**
- AWS copy not found (404) — delete aborted for safety
- File permissions issue
- VAST S3 connectivity issue

**Solutions:**

1. **If AWS copy not found:**
   ```sql
   -- Check if file actually exists in AWS
   -- (via AWS CLI: aws s3 ls s3://bucket/key)
   
   -- If file is lost, manually update state to UNKNOWN
   UPDATE vast."archive/lineage".asset_registry
   SET current_location = 'UNKNOWN'
   WHERE element_handle = '0x...';
   ```

2. **If permissions issue:**
   - Check S3 IAM credentials have DeleteObject permission
   - Verify bucket policy allows deletion

3. **Retry deletion:**
   - Fix underlying issue
   - Re-run pipeline (verify_purge will retry)

### "AWS copy not found during purge"

**Symptoms:** LOCAL_DELETE_FAILED with "NoSuchKey" or 404 error.

**Why it's safe:** Purge function aborts deletion if AWS copy can't be found (fail-safe design).

**Solutions:**
1. Investigate why AWS copy is missing
2. Either:
   a. Re-upload file from local backup
   b. Manually mark state as UNKNOWN if data loss is acceptable
   c. Extend retention on VAST side

### "Out of memory" or "timeout"

**Symptoms:** Function containers crash or hit time limits.

**Solutions:**
1. Decrease batch_size:
   ```sql
   UPDATE vast."archive/lineage".offload_config
   SET config_value = '250'  -- reduced from 500
   WHERE config_key = 'batch_size';
   ```

2. Disable checksum verification (halves memory usage):
   ```sql
   UPDATE vast."archive/lineage".offload_config
   SET config_value = 'false'
   WHERE config_key = 'verify_checksum';
   ```

3. Increase function resource limits (VMS UI → DataEngine → Functions → Edit):
   - Increase Memory (default 2-4 GB)
   - Increase Timeout (default 30-60 minutes)

### "S3 endpoint unreachable"

**Symptoms:** Functions crash immediately with "Unable to locate credentials" or connection timeout.

**Check:**
1. Endpoint URL syntax:
   ```bash
   # Should be https://IP or https://hostname
   echo $S3_ENDPOINT  # in function environment
   ```

2. Network connectivity:
   ```bash
   # From DataEngine container
   curl -v https://<VAST_IP>
   ```

3. Credentials:
   ```bash
   # Check environment variables are set
   env | grep S3_
   ```

**Solutions:**
- Verify endpoint in VMS pipeline configuration
- Check IAM credentials are valid (regenerate in VAST UI if needed)
- Verify network firewall allows DataEngine → VAST S3

## Performance Tuning

### Optimize for Throughput

```sql
-- Increase batch size for more files per run
UPDATE vast."archive/lineage".offload_config
SET config_value = '2000' WHERE config_key = 'batch_size';

-- Disable checksum verification (if acceptable for compliance)
UPDATE vast."archive/lineage".offload_config
SET config_value = 'false' WHERE config_key = 'verify_checksum';

-- Use faster storage class (INTELLIGENT_TIERING auto-tunes)
UPDATE vast."archive/lineage".offload_config
SET config_value = 'STANDARD' WHERE config_key = 'target_aws_storage_class';
```

**Increase function resources:**
- Memory: 8 GB (from 4 GB)
- CPU: 4 cores (from 2)
- Timeout: 120 minutes (from 60)

### Optimize for Reliability

```sql
-- Process fewer files per run to reduce failure impact
UPDATE vast."archive/lineage".offload_config
SET config_value = '100' WHERE config_key = 'batch_size';

-- Enable checksum verification for data integrity
UPDATE vast."archive/lineage".offload_config
SET config_value = 'true' WHERE config_key = 'verify_checksum';

-- Use durable, high-availability storage class
UPDATE vast."archive/lineage".offload_config
SET config_value = 'INTELLIGENT_TIERING' WHERE config_key = 'target_aws_storage_class';
```

**Increase function resources (conservatively):**
- Memory: 4 GB (adequate for small batches)
- CPU: 2 cores
- Timeout: 60 minutes

### Monitor Performance

```sql
-- Average copy time per file (minutes)
SELECT
    pipeline_run_id,
    COUNT(*) as files_copied,
    ROUND(
        (EXTRACT(epoch FROM (
            MAX(CASE WHEN event_type = 'COPY_COMPLETED' THEN event_timestamp END)
            - MIN(CASE WHEN event_type = 'COPY_STARTED' THEN event_timestamp END)
        )) / COUNT(*) / 60), 2
    ) as avg_mins_per_file
FROM vast."archive/lineage".lifecycle_events
WHERE event_type IN ('COPY_STARTED', 'COPY_COMPLETED')
  AND event_timestamp > now() - INTERVAL '7' DAY
GROUP BY pipeline_run_id
ORDER BY pipeline_run_id DESC
LIMIT 10;
```

## Alerting Setup

### Email Alerts (Example)

Set up a cron job to check for errors:

```bash
#!/bin/bash
# check_archive_trail_errors.sh

ERROR_COUNT=$(mysql -h $VAST_DB_HOST -u $USER -p$PASS -D vast \
  -e "SELECT COUNT(*) FROM \`archive/lineage\`.lifecycle_events 
      WHERE success = false AND event_timestamp > now() - INTERVAL 24 HOUR;" \
  | tail -1)

if [ $ERROR_COUNT -gt 0 ]; then
  echo "ArchiveTrail had $ERROR_COUNT errors in past 24 hours" | \
    mail -s "Alert: ArchiveTrail Errors" ops-team@company.com
fi
```

Schedule via cron:
```
0 9 * * * /usr/local/bin/check_archive_trail_errors.sh
```

### Key Alerts

Create alerts for:
- [ ] `COPY_FAILED` events (copy operation failed)
- [ ] `CHECKSUM_MISMATCH` events (data integrity issue)
- [ ] `LOCAL_DELETE_FAILED` events (purge failed, data stuck)
- [ ] Pipeline run failures (entire pipeline crash)
- [ ] No events in last 25 hours (scheduled run didn't execute)

## CLI Reference

### Local Development (from source)

```bash
# Install dev environment
make install

# Activate virtual environment
source .venv/bin/activate

# Run tests
make test

# Run linter
make lint

# Show local config
make config

# Run discover locally (reads from .env)
make discover

# Run full pipeline locally
make pipeline

# Show statistics
make stats
```

### DataEngine Functions (VMS)

```bash
# Build functions
make build-all

# Push to registry
make push-all

# Deploy (create/update in DataEngine)
make deploy-all

# Full workflow: build, push, deploy
make ship
```

## Maintenance Tasks

### Weekly

- [ ] Check monitoring dashboard for errors
- [ ] Review config change log for unexpected changes
- [ ] Spot-check that offloaded files are in AWS with correct metadata

### Monthly

- [ ] Analyze cost savings (files offloaded × storage cost delta)
- [ ] Review error trends
- [ ] Tune batch_size based on performance metrics
- [ ] Update documentation with lessons learned

### Quarterly

- [ ] Review and update threshold based on access patterns
- [ ] Audit all offloaded files for compliance
- [ ] Test recall/restore process (if enabled)
- [ ] Review AWS storage class optimization

## Disaster Recovery

### Restore Offloaded File to VAST

```bash
# Download from AWS S3
aws s3 cp s3://corp-cold-tier/tenant/projects/report.pdf /tmp/report.pdf

# Re-upload to VAST S3 (or NFS/SMB path)
aws s3 cp /tmp/report.pdf \
  s3://<vast-bucket>/tenant/projects/report.pdf \
  --endpoint-url https://<VAST_IP>

# Update registry to reflect file is back on VAST
# (Manual update for now; recall function coming)
```

### Restore VAST Database

If ArchiveTrail tables are lost:

1. Restore VAST DB from backup
2. Re-run schema creation scripts
3. Seed with default config (if empty)
4. Consider re-running discovery on existing AWS files (future feature)

### Rollback Configuration

View and revert config to a previous state:

```sql
-- See all config changes
SELECT * FROM vast."archive/lineage".config_change_log
ORDER BY changed_at DESC
LIMIT 20;

-- Revert to previous value
UPDATE vast."archive/lineage".offload_config
SET config_value = '<old_value>'
WHERE config_key = '<key>';
```

## Support

For additional help, see:
- **[Architecture Guide](Architecture.md)** — System design details
- **[Configuration Guide](Configuration-Guide.md)** — Parameter tuning
- **[Deployment Guide](Deployment-Guide.md)** — Initial setup
