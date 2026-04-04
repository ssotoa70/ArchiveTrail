# VAST Platform Setup

Prerequisites and platform configuration required before deploying ArchiveTrail. Complete these steps before starting the Deployment Guide.

## Overview

ArchiveTrail requires specific VAST platform features to be enabled and configured:

1. **VAST Catalog** — Periodic snapshots of filesystem metadata
2. **VAST Protocol Auditing** — Independent logging of S3/NFS/SMB operations
3. **S3 Object Tagging** — For Catalog indexing of offload status
4. **Multiprotocol Views** — S3 access to source paths

All of these are built into VAST Data Platform and available via VMS (VAST Management System) UI.

## Prerequisites Checklist

Before proceeding, verify:

- [ ] **VAST Data Platform 5.4+** — Required for Catalog and auditing features
- [ ] **VMS Access** — Admin credentials to VAST Management System
- [ ] **Element Store** — Initialized and populated with test files
- [ ] **Network** — Cluster management network operational

## Step 1: Enable VAST Catalog

VAST Catalog provides periodic snapshots of the filesystem, including metadata columns that ArchiveTrail queries (atime, mtime, ctime, etc.).

### 1.1 Navigate to Catalog Settings

1. Open **VMS UI** (https://<VMS_IP>)
2. Go to **Settings → VAST Catalog**
3. Click **Enable**

### 1.2 Configure Snapshot Frequency

| Setting | Recommended | Notes |
|---------|-------------|-------|
| **Snapshot Frequency** | 30 minutes | Granularity for discovering cold files |
| **Retention Period** | 7 days | Allows historical queries for audit |

Set in VMS UI:

```
Settings → VAST Catalog
├── Enable: ON
├── Save new catalog copies every: [30] minutes
├── Keep catalog copies for: [7] days
├── Store filesystem snapshots: ON
└── Keep filesystem snapshots for: [7] days
```

**Why 30 minutes?**
- ArchiveTrail runs once daily (default 2 AM)
- Each run queries the most recent Catalog snapshot
- 30 minutes = good balance between freshness and load
- For more frequent offloading, reduce to 15 minutes

**Why 7 days retention?**
- Enables point-in-time queries (e.g., "what did filesystem look like 5 days ago?")
- Provides audit trail corroboration
- Minimal storage overhead

### 1.3 Verify Catalog is Running

After enabling, check that snapshots are being taken:

1. Go to **Settings → VAST Catalog → Snapshots**
2. Verify the latest snapshot timestamp is recent (within 30 min)
3. Check snapshot size (should be proportional to filesystem size)

```
Latest snapshot: 2026-03-17 02:00:00 (1.2 GB, 50M items)
Previous snap:   2026-03-17 01:30:00 (1.2 GB, 49M items)
```

## Step 2: Enable Protocol Auditing

VAST Protocol Auditing provides an independent log of all S3, NFS, and SMB operations, serving as a corroboration layer for ArchiveTrail operations.

### 2.1 Navigate to Auditing Settings

1. Open **VMS UI**
2. Go to **Settings → Auditing**
3. Click **Enable**

### 2.2 Configure Protocols and Operations

```
Settings → Auditing
├── Enable: ON
├── Protocols:
│   ├── S3: ON
│   ├── NFSv3: ON
│   ├── NFSv4: ON
│   └── SMB: ON
├── Operations:
│   ├── Create/Delete: ON
│   ├── Modify Data: ON
│   └── Read Metadata: ON
├── Output Format: VAST Database
└── Retention: 30 days
```

**Explanation:**

| Setting | Value | Why |
|---------|-------|-----|
| S3 | Enabled | Logs S3 GetObject (copy), PutObject (verify), DeleteObject (purge) |
| NFS | Enabled | Logs file access via NFS (detects atime changes) |
| SMB | Enabled | Logs file access via SMB (client reads) |
| Create/Delete | Enabled | Logs when files are created or deleted |
| Modify Data | Enabled | Logs write operations (detects file modifications) |
| Read Metadata | Enabled | Logs metadata reads (important for atime tracking) |
| Output: VAST Database | Selected | Logs queryable via SQL in VAST DB |
| Retention | 30 days | Sufficient for compliance; longer if required |

### 2.3 Verify Audit Logging is Active

After enabling, check audit table:

```sql
-- Check audit table exists
SELECT COUNT(*) FROM vast."audit/schema".audit_table;

-- Verify recent entries exist
SELECT timestamp, protocol, operation, object_path
FROM vast."audit/schema".audit_table
WHERE timestamp > now() - INTERVAL '1' HOUR
ORDER BY timestamp DESC
LIMIT 10;
```

Should show recent S3/NFS/SMB operations.

## Step 3: Add S3 Tag Indexing for Catalog

Add `offload_status` as a user-defined attribute so ArchiveTrail can query offload state directly from the Catalog.

### 3.1 Navigate to Catalog Attributes

1. Open **VMS UI**
2. Go to **Settings → VAST Catalog → User Defined Attributes**
3. Click **Add Attribute**

### 3.2 Create `offload_status` Attribute

| Field | Value |
|-------|-------|
| **Attribute Type** | Tag |
| **Column Name** | offload_status |
| **Indexed** | YES |
| **Data Type** | String |

Fill in the dialog:

```
Attribute Type: Tag
Column Name: offload_status
Indexed: YES (important!)
Data Type: String
Description: "Offload status: COPIED when copied to AWS, PURGED when local deleted"
```

Click **Create**.

### 3.3 Verify Catalog Index

After creation, the Catalog will add a new column `tag_offload_status` on the next snapshot. Verify:

```sql
-- Check new column exists (after next 30min snapshot)
SELECT tag_offload_status, COUNT(*) as count
FROM vast."catalog/schema".catalog_table
GROUP BY tag_offload_status;
```

Initially, most files will have NULL offload_status. As ArchiveTrail runs, it tags files with COPIED/PURGED.

## Step 4: Enable S3 on Source Views

Ensure source paths have S3 protocol enabled alongside NFS/SMB.

### 4.1 List Views

1. Open **VMS UI**
2. Go to **Element Store → Views**
3. Find views containing your source paths (e.g., `/tenant/projects`)

### 4.2 Edit View to Enable S3

For each relevant view:

1. Click **Edit**
2. Under **Protocols**, check all of:
   - [ ] NFSv3
   - [ ] NFSv4
   - [ ] SMB
   - [ ] **S3 Bucket** (must be enabled for ArchiveTrail)

**Example:**

```
View: projects
├── Mount Path: /tenant/projects
├── Protocols:
│   ├── NFSv3: ON
│   ├── NFSv4: ON
│   ├── SMB: ON
│   └── S3 Bucket: ON ← Required
├── S3 Bucket Name: projects
└── Access Tiers: Hot, Warm
```

3. **Save**

### 4.3 Verify S3 Access

Test S3 endpoint and bucket accessibility:

```bash
# List the bucket via S3
aws s3 ls s3://projects/ --endpoint-url https://<VAST_VIP>

# Should see contents of /tenant/projects
```

If bucket access fails:
1. Check IAM credentials (generate in VAST UI if needed)
2. Verify endpoint URL is correct (https://<VAST_VIP> or hostname)
3. Check network connectivity to VAST cluster

## Step 5: Create S3 IAM User (Optional but Recommended)

While not strictly required, creating a dedicated IAM user for ArchiveTrail is a best practice.

### 5.1 Create IAM User in VAST

1. Open **VMS UI**
2. Go to **Settings → IAM → Users**
3. Click **Create User**

```
Username: archivetrail
Full Name: ArchiveTrail Service Account
Password: [Auto-generate]
Groups: [leave empty or create "archivetrail" group]
```

Click **Create**.

### 5.2 Generate Access Key

1. Click on **archivetrail** user
2. Go to **Access Keys**
3. Click **Generate Key**

Save the output:
```
Access Key ID: AKIA...
Secret Key: wJalr...
```

These are used in environment variables when deploying ArchiveTrail.

### 5.3 Set Permissions (Optional)

If using IAM policies (advanced):

Attach policy allowing S3 operations on all buckets:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObjectTagging",
        "s3:PutObjectTagging",
        "s3:HeadObject",
        "s3:ListBucket"
      ],
      "Resource": ["arn:aws:s3:::*"]
    }
  ]
}
```

## Step 6: Verify AWS S3 Connectivity

Ensure DataEngine pods can reach AWS S3.

### 6.1 Test from VAST Cluster

```bash
# SSH into a VAST node
ssh <VAST_NODE>

# Test AWS endpoint (replace with your region)
curl -v https://s3.us-east-1.amazonaws.com/

# Should return HTTP 403 (auth required) — means endpoint is reachable
```

### 6.2 Configure AWS IAM User

In AWS:

1. Create IAM user for ArchiveTrail (or use existing)
2. Generate access key + secret
3. Attach policy with S3 permissions to target bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObjectTagging",
        "s3:PutObjectTagging",
        "s3:HeadObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::corp-cold-tier",
        "arn:aws:s3:::corp-cold-tier/*"
      ]
    }
  ]
}
```

### 6.3 Test AWS S3 Access

```bash
# From VAST node or DataEngine
export AWS_ACCESS_KEY_ID=<key>
export AWS_SECRET_ACCESS_KEY=<secret>
export AWS_DEFAULT_REGION=us-east-1

# Test bucket access
aws s3 ls s3://corp-cold-tier/
```

If successful, VAST can reach AWS. If not, check:
- AWS IAM user has correct permissions
- Access key is active (not disabled)
- Network allows outbound HTTPS to AWS endpoints

## Step 7: Create VAST Database Bucket (Optional)

By default, ArchiveTrail uses bucket `archive-trail-db` for tables. You can use an existing bucket or create a dedicated one.

### 7.1 Create Dedicated Bucket (Recommended)

In VMS UI:

1. Go to **Element Store → Buckets**
2. Click **Create Bucket**

```
Bucket Name: archive-trail-db
Quota: 100 GB (adjust as needed)
Tier Assignment: Hot (for fast queries)
```

Click **Create**.

### 7.2 Verify Bucket Exists

```bash
# List buckets
aws s3 ls --endpoint-url https://<VAST_VIP>

# Should list archive-trail-db (or your chosen name)
```

## Step 8: Verify All Prerequisites

Run this final checklist before proceeding to Deployment Guide:

### Catalog Verification

```bash
# Check Catalog is active and recent
mysql -h <VAST_IP> -u admin -p<password> -e \
  "SELECT COUNT(*) as items, MAX(mtime) as last_updated FROM catalog_table;"
```

Expected: Recent timestamp, millions of items

### Audit Verification

```bash
# Check audit table has recent entries
mysql -h <VAST_IP> -u admin -p<password> -e \
  "SELECT COUNT(*) FROM audit_table WHERE timestamp > now() - INTERVAL 1 HOUR;"
```

Expected: Several hundred entries minimum

### S3 Access Verification

```bash
# Test VAST S3
aws s3 ls s3://projects/ --endpoint-url https://<VAST_VIP>

# Test AWS S3
aws s3 ls s3://corp-cold-tier/
```

Expected: Both commands succeed

### Network Verification

```bash
# From DataEngine pod (or test pod)
curl -v https://<VAST_VIP>        # VAST S3 endpoint
curl -v https://s3.amazonaws.com/ # AWS S3 endpoint
```

Expected: Both are reachable (even if returning auth errors)

## Troubleshooting Platform Setup

### Catalog Not Updating

**Symptoms:** Catalog timestamp is old, snapshots not taking.

**Solutions:**
1. Check VAST Catalog service is running (VMS UI → Services → vastcatalog)
2. Verify filesystem is healthy (no I/O errors)
3. Check free space in Catalog bucket
4. Restart Catalog service: `systemctl restart vast-catalog`

### Audit Table Empty

**Symptoms:** No entries in audit_table.

**Solutions:**
1. Verify auditing is enabled (Settings → Auditing → Enable)
2. Check protocols are selected (S3, NFS, SMB)
3. Perform a test operation:
   ```bash
   aws s3 cp test.txt s3://test-bucket/ --endpoint-url https://<VAST_VIP>
   ```
4. Wait 1 minute and check audit table again

### S3 TagSet Not Indexing

**Symptoms:** tag_offload_status column not appearing in Catalog.

**Solutions:**
1. Verify attribute was created (Settings → VAST Catalog → User Defined Attributes)
2. Wait for next Catalog snapshot (up to 30 minutes)
3. After snapshot, verify column exists:
   ```sql
   DESCRIBE catalog_table;  -- Should show tag_offload_status
   ```

### Network Connectivity Issues

**Symptoms:** DataEngine functions fail with timeout or connection refused.

**Solutions:**
1. Check firewall rules allow DataEngine → VAST S3 and AWS S3
2. Verify DNS resolution:
   ```bash
   nslookup s3.amazonaws.com
   nslookup <VAST_IP_or_hostname>
   ```
3. Test with curl from DataEngine container:
   ```bash
   curl -v https://<endpoint>/
   ```

## Next Steps

After completing all steps here, proceed to **[Deployment Guide](Deployment-Guide.md)** to build and deploy ArchiveTrail functions.

## Platform Configuration Reference

For quick reference, here's the complete platform configuration:

| Feature | Setting | Value |
|---------|---------|-------|
| **VAST Catalog** | Status | Enabled |
| | Snapshot Frequency | 30 minutes |
| | Retention | 7 days |
| **Protocol Auditing** | Status | Enabled |
| | Protocols | S3, NFS, SMB |
| | Operations | Create/Delete, Modify, Read |
| | Output | VAST DB |
| **Catalog Indexing** | Tag Attribute | offload_status |
| | Indexed | YES |
| **Multiprotocol** | S3 Enabled | YES on all source views |
| **VAST DB Bucket** | Name | archive-trail-db (default) |
| | Tier | Hot |
| **IAM User** | Name | archivetrail (recommended) |
| | Permissions | S3 full access (or scoped to bucket) |

## References

- **[Architecture Guide](Architecture.md)** — Why each feature is needed
- **[Configuration Guide](Configuration-Guide.md)** — Environment variables after setup
- **[Deployment Guide](Deployment-Guide.md)** — Next steps
