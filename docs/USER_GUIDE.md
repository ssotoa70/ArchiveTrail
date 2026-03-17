# ArchiveTrail User Guide

A step-by-step guide for storage administrators and IT managers to deploy and operate ArchiveTrail—a system that automatically moves cold data from VAST storage to AWS cloud while keeping a complete record of where everything went.

---

## 1. What is ArchiveTrail?

**In plain English:** ArchiveTrail is an automated system that finds files in your VAST storage that haven't been used in a while (e.g., 60 days), makes a copy of them in AWS S3, and optionally deletes the old copy from local storage to save space. Every time a file moves, the system records what happened, when it happened, where it came from, and where it went—so you always know the complete history of every file.

**The problem it solves:** Organizations that manage large amounts of storage face a challenge: old data takes up expensive local space, but you still need to know exactly where it is and be able to prove that no data was lost in the process. ArchiveTrail automates this while maintaining an audit trail that would satisfy a compliance audit.

**Key benefits:**

- Automatically frees up local storage space by moving old files to cheaper AWS cloud storage
- Records a complete "birth certificate" for every file showing its origin, current location, and all moves
- Verifies that copies are identical to originals (using checksums)
- Allows you to recall files from AWS if needed later
- Provides an independent audit trail that proves data integrity was maintained
- Lets you change the "old" threshold (e.g., from 60 days to 90 days) without losing track of what was moved under previous rules
- Works with any storage protocol—NFS, SMB, or S3

---

## 2. How It Works (Conceptual)

### The Three-Stage Pipeline

Think of ArchiveTrail as a mail sorting system with three stations:

**Stage 1: Discovery (The Mailroom)**
Every morning at 2 AM (you can change this), ArchiveTrail checks your VAST storage's catalog—a snapshot of all your files. It looks for files with an "last accessed" timestamp older than your threshold (default: 60 days). When it finds a cold file, it records it in the registry—think of this like the file receiving a registration number or "birth certificate."

**Stage 2: Offload (The Shipping Clerk)**
For each cold file discovered, ArchiveTrail copies it to your AWS S3 bucket. During the copy, it calculates a checksum—a unique fingerprint of the file's contents. After the copy is complete, it reads the copy back and verifies the fingerprint matches. If they don't match, it stops and reports a problem instead of deleting anything. Once verified, the file is marked as "stored in both places" (BOTH state).

**Stage 3: Optional Purge (The Cleanup Crew)**
If you've enabled automatic deletion, ArchiveTrail checks that the AWS copy is still there and intact, then deletes the local copy. Once deleted, the registry is updated to show the file is now "only in AWS" (LOCAL_DELETED state).

At **every single step**, ArchiveTrail writes a record to the lifecycle events log—a journal that never gets overwritten. This journal is your audit trail.

### Genealogy: Every File Gets a "Birth Certificate"

In VAST, every file has an **element handle**—a permanent ID assigned by the system that stays with the file forever, even if someone renames it or moves it to a different folder. ArchiveTrail uses the element handle as the identity anchor and records:

- **Original identity:** Where the file came from, its name, size, creation date, who owns it
- **Current identity:** Where it lives now (local storage, AWS, or both)
- **Proof of integrity:** Checksums that prove the copy is identical to the original

This means you can always ask: "Where is file X?" or "What happened to this file?" and get a definitive answer backed by data.

### Three Independent Layers of Traceability

ArchiveTrail records every action in three places simultaneously (see diagram in IMPLEMENTATION.md):

**Layer 1: Application Tracking (ArchiveTrail Tables)**
- The asset registry: a master record for each file (one row per file, ever)
- The lifecycle events: every action on every file (registered, copied, verified, deleted)
- The config change log: every time you change a setting, with who changed it and why

**Layer 2: Platform Corroboration (VAST Native Features)**
- The VAST Catalog: periodic snapshots of your filesystem that are kept for 7 days (independent confirmation of what files existed)
- VAST Protocol Audit: a platform-level log of every read, write, and delete operation across S3, NFS, and SMB

**Layer 3: Destination Metadata (AWS S3)**
- Every file copied to AWS gets metadata tags that record its VAST origin, original path, and checksum

**Why three layers?** If one system has a problem (data corruption, logs deleted, etc.), the other two can reconstruct the chain of custody. Even if someone gets access to just the AWS bucket, they can trace objects back to their VAST origin.

---

## 3. Prerequisites Checklist

Before you start deploying ArchiveTrail, verify you have everything you need:

**Infrastructure:**
- [ ] VAST Data Platform cluster (v4.0 or newer) with direct S3 access
- [ ] AWS account with S3 bucket (or ability to create one)
- [ ] AWS credentials with S3 permission (GetObject, PutObject, DeleteObject, PutObjectTagging)
- [ ] Kubernetes cluster (for running the DataEngine functions)
- [ ] Network connectivity from K8s cluster to both VAST cluster and AWS

**VAST Configuration:**
- [ ] Direct S3 endpoint to your VAST cluster (typically something like `https://vip-pool.vast.local`)
- [ ] Access credentials to VAST database (Trino access)
- [ ] One or more views configured with **multiple protocols enabled** (must have both NFS or SMB **plus S3**—files need to be readable via S3)
- [ ] At least 7 days of free disk space for VAST Catalog snapshots

**Knowledge & Access:**
- [ ] You can log into VAST management UI (web browser)
- [ ] You have admin access to VAST Settings
- [ ] You can run SQL commands against VAST database (via Trino or similar)
- [ ] You have Docker installed locally or access to a container registry
- [ ] You have permissions to create Kubernetes resources (Deployments, ConfigMaps, etc.)

**AWS Account Details (Get These Ready):**
- [ ] AWS Access Key ID (or use IAM role if running in AWS)
- [ ] AWS Secret Access Key
- [ ] Target S3 bucket name (or permission to create one)
- [ ] Target AWS region (e.g., `us-east-1`)

---

## 4. Deployment Guide (Step-by-Step)

ArchiveTrail deployment happens in six phases. Each phase builds on the previous one, and you can stop after Phase 5 to test before going live.

### Phase 1: Prepare Your VAST Cluster

In this phase, you'll enable features in VAST that ArchiveTrail needs to detect and track files.

#### Step 1.1: Enable VAST Catalog

The VAST Catalog is like a periodic snapshot of your filesystem. ArchiveTrail uses it to detect cold files.

1. Log into your VAST management UI (web browser)
2. Navigate to **Settings > VAST Catalog**
3. Click the toggle to **Enable** (if not already enabled)
4. Set the following values:
   - **Save new catalog copies every:** 30 minutes (you can use any interval from 10-60 min; 30 is recommended)
   - **Keep catalog copies for:** 7 days (this enables historical queries)
   - **Store filesystem snapshots:** Yes
   - **Keep filesystem snapshots for:** 7 days
5. Click **Save**

> **What this means:** Your VAST cluster will now take a snapshot of your filesystem every 30 minutes and keep the last 7 days of snapshots. This snapshot includes information about file size, modification time, last access time (atime), and owner. ArchiveTrail queries this snapshot to find "cold" files (those not accessed in 60+ days).

#### Step 1.2: Enable Protocol Auditing

Protocol auditing is like a security camera for file operations. It records every S3, NFS, and SMB operation on your cluster, giving you an independent witness to what happened.

1. From **Settings**, navigate to **Auditing**
2. Click the toggle to **Enable**
3. Configure:
   - **Protocols to audit:** S3, NFSv3, NFSv4, SMB (select all that apply to your environment)
   - **Operations to log:** Create/Delete, Modify Data, Read Metadata
   - **Output location:** VAST Database table (this makes the logs queryable via SQL)
4. Click **Save**

> **What this means:** Every read, write, and delete operation on your files is now logged to a table in the VAST database. ArchiveTrail will use this log as a second source of truth to verify that operations it claims to have done actually happened.

#### Step 1.3: Add Custom Tag for Offload Status

Tags are like labels you can stick on files. ArchiveTrail will tag files to mark their offload status (e.g., "copied to AWS," "deleted locally").

1. From **Settings > VAST Catalog**, find the **User Defined Attributes** section
2. Click **Add New Attribute**
3. Fill in:
   - **Type:** Tag
   - **Column Name:** offload_status (exactly as written)
4. Click **Save**

> **What this means:** ArchiveTrail can now use S3 object tagging to mark files. When it copies a file to AWS, it tags the local S3 copy with `offload_status=COPIED`. This tag will appear in the Catalog snapshots, making the state visible in queries.

#### Step 1.4: Verify Multiprotocol Views

Files must be accessible via S3 for ArchiveTrail to copy them. Check your views (the paths where users access files):

1. Navigate to **Element Store > Views**
2. For each view that will participate in tiering, click **Edit**
3. Under **Protocols**, verify that **S3** is enabled alongside your other protocols (NFS, SMB, etc.)
   - At minimum: NFS or SMB **+ S3**
4. If S3 was not enabled, enable it and **Save**

> **What this means:** Files in these views can now be read via S3 (by the DataEngine functions) and also via NFS/SMB (by your users). This multiprotocol approach ensures compatibility.

---

### Phase 2: Set Up the Database

ArchiveTrail needs three tables in the VAST database to store its metadata. You'll run three SQL scripts to create them.

#### Step 2.1: Prepare the SQL Scripts

The ArchiveTrail project includes three SQL files in the `sql/` directory:
1. `001_create_schema.sql` — Creates the schema (folder) for ArchiveTrail tables
2. `002_create_tables.sql` — Creates the four tables (asset_registry, lifecycle_events, offload_config, config_change_log)
3. `003_seed_config.sql` — Loads default configuration values

#### Step 2.2: Access the VAST Database

You need a SQL client connected to your VAST database. VAST uses Trino as the SQL interface. Your IT team may provide a Trino CLI client, or you can use a web-based Trino interface if available.

**Example command** (if you have Trino CLI installed):
```
trino --server https://vms.vast.local --user admin --password
```

> **What this means:** You're connecting to the VAST Metadata Service (VMS) which stores all metadata about your files. Trino is a SQL query engine that lets you read and write to this database.

#### Step 2.3: Run the SQL Scripts in Order

> **IMPORTANT:** Run these scripts in order (001, then 002, then 003). Do not skip any or run them out of order.

**For script 001 (create schema):**

Open the file `ArchiveTrail/sql/001_create_schema.sql` in a text editor. Copy the entire contents. In your Trino client or SQL editor, paste and run the contents. You should see a message indicating the schema was created.

**For script 002 (create tables):**

Repeat the same process with `sql/002_create_tables.sql`. This creates the four tables where ArchiveTrail will store its data.

**For script 003 (seed config):**

Repeat with `sql/003_seed_config.sql`. This loads the default configuration values into the `offload_config` table.

#### Step 2.4: Verify the Tables Were Created

After running all three scripts, verify everything worked:

In your Trino client, run:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'archive/lineage';
```

You should see four tables listed:
- `offload_config`
- `asset_registry`
- `lifecycle_events`
- `config_change_log`

> **What this means:** The database is ready. These four tables are where ArchiveTrail will record which files were offloaded, their lifecycle events, and any configuration changes.

#### Step 2.5: Review and Customize the Configuration (Optional)

The seed script (`003_seed_config.sql`) loaded default values. If you need different settings right now, you can update them before going live.

In Trino, run:
```sql
SELECT config_key, config_value FROM vast."archive/lineage".offload_config
ORDER BY config_key;
```

This shows you all the current settings. Common settings you might want to change:

- **atime_threshold_days:** Files older than this (not accessed in this many days) are considered "cold" and will be offloaded. Default: 60. You could set it to 30 (more aggressive) or 90 (more conservative).
- **target_aws_bucket:** The S3 bucket where cold files will be copied. Default: `corp-cold-tier`. Update this to your actual bucket name.
- **target_aws_region:** The AWS region. Default: `us-east-1`. Change if your bucket is in a different region.
- **source_paths:** Comma-separated list of paths to monitor. Default: `/tenant/projects,/tenant/media`. Update to the actual paths you want to tier.

To update a setting, run (example):
```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '90'
WHERE config_key = 'atime_threshold_days';
```

> **What this means:** The configuration table is flexible. You can change thresholds, paths, and AWS targets without restarting anything. Every change is logged for audit purposes.

---

### Phase 3: Configure Your Environment

ArchiveTrail needs AWS credentials and cluster information to operate. These are stored in an environment file.

#### Step 3.1: Locate or Create the .env File

In the ArchiveTrail project directory, find or create a file named `.env` (if it doesn't exist, copy `.env.example`):

```bash
cp .env.example .env
```

#### Step 3.2: Fill in the Environment Variables

Open `.env` in a text editor and fill in each field:

| Variable | What It Is | Example |
|----------|-----------|---------|
| `VAST_S3_ENDPOINT` | The S3 address of your VAST cluster | `https://vip-pool.vast.local` |
| `VAST_CLUSTER_NAME` | A label to identify this VAST cluster in AWS metadata | `vast-prod-cluster-01` |
| `AWS_ACCESS_KEY_ID` | Your AWS account key (like a username) | `AKIA...` (20+ characters) |
| `AWS_SECRET_ACCESS_KEY` | Your AWS account secret (like a password) | (long string) |
| `AWS_DEFAULT_REGION` | The AWS region where your S3 bucket is | `us-east-1` or `us-west-2` |
| `VASTDB_ENDPOINT` | The VAST metadata service address | `https://vms.vast.local` |
| `VASTDB_ACCESS_KEY` | Your VAST database credentials | (provided by VAST admin) |
| `VASTDB_SECRET_KEY` | Your VAST database credentials | (provided by VAST admin) |

#### Step 3.3: Getting AWS Credentials

If you don't have AWS credentials:

1. Log into your AWS console as an admin
2. Go to **IAM > Users**
3. Select your user (or create a new user for this application)
4. Go to **Security Credentials**
5. Click **Create Access Key**
6. Select "Application running outside AWS" (or if running in Kubernetes on AWS, select "Local code")
7. Copy the Access Key ID and Secret Access Key into `.env`

> **SECURITY WARNING:** These credentials are sensitive. Do NOT commit the `.env` file to version control. If running in Kubernetes, store these in a Secret object instead (ask your K8s admin).

#### Step 3.4: Verify the .env File

Once filled in, the `.env` file should look like:

```
VAST_S3_ENDPOINT=https://vip-pool.vast.local
VAST_CLUSTER_NAME=vast-prod-cluster-01
AWS_ACCESS_KEY_ID=AKIA1234567890ABCD
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG+j39sQSzVAQwrDAl
AWS_DEFAULT_REGION=us-east-1
VASTDB_ENDPOINT=https://vms.vast.local
VASTDB_ACCESS_KEY=vast-db-user
VASTDB_SECRET_KEY=vast-db-password
```

> **What this means:** ArchiveTrail now has the credentials and addresses it needs to read from VAST, write to AWS, and log events to the VAST database.

---

### Phase 4: Build & Deploy

In this phase, you'll package ArchiveTrail into a container and deploy it to Kubernetes so it runs automatically.

#### Step 4.1: Build the Docker Container

A container is like a self-contained package that includes ArchiveTrail and all its dependencies. You'll build this once and reuse it.

From the ArchiveTrail directory, run:

```bash
make docker-build
```

or:

```bash
docker build -t archive-trail:latest .
```

This reads the `Dockerfile` and builds a container image called `archive-trail:latest`. The process takes a few minutes.

> **What this means:** A container image is created, ready to be pushed to your container registry (the system where your Kubernetes cluster pulls images from).

#### Step 4.2: Push to Your Container Registry

Your Kubernetes cluster pulls images from a container registry. Common registries: Docker Hub, AWS ECR, or an internal registry.

Ask your Kubernetes admin for your registry URL. Then:

```bash
docker tag archive-trail:latest <REGISTRY_URL>/archive-trail:latest
docker push <REGISTRY_URL>/archive-trail:latest
```

Example (with AWS ECR):
```bash
docker tag archive-trail:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/archive-trail:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/archive-trail:latest
```

> **What this means:** The container image is now stored in your registry where the Kubernetes cluster can access it.

#### Step 4.3: Create the DataEngine Pipeline

Now you'll set up the automated pipeline in VAST DataEngine. This is the "scheduler" that will run ArchiveTrail every day at 2 AM.

Log into your VAST management UI and navigate to **DataEngine > Pipelines**.

Create a new pipeline with these components:

**1. Schedule Trigger**
- Name: `archive-trail-schedule`
- Trigger Type: Schedule
- Cron Expression: `0 2 * * *` (runs daily at 2 AM UTC)
- Description: "Daily ArchiveTrail tiering run"

**2. Discover Function**
- Name: `discover`
- Image: `<REGISTRY_URL>/archive-trail:latest`
- Handler: `archive_trail.functions.discover:handler`
- Environment: (load from `.env` or create ConfigMap in K8s)
- Timeout: 1800 seconds (30 minutes)
- Memory: 2048 MB
- Description: "Query Catalog for cold files, register them"

**3. Offload Function**
- Name: `offload`
- Image: `<REGISTRY_URL>/archive-trail:latest`
- Handler: `archive_trail.functions.offload:handler`
- Depends On: `discover`
- Timeout: 3600 seconds (1 hour, adjust based on your data volume)
- Memory: 4096 MB
- Description: "Copy cold files to AWS, verify checksums"

**4. Verify & Purge Function**
- Name: `verify_purge`
- Image: `<REGISTRY_URL>/archive-trail:latest`
- Handler: `archive_trail.functions.verify_purge:handler`
- Depends On: `offload`
- Timeout: 1800 seconds (30 minutes)
- Memory: 2048 MB
- Description: "Optionally delete local copies after verifying AWS copies"

> **What this means:** ArchiveTrail will now run automatically every day at 2 AM. It will discover cold files, copy them to AWS, and optionally delete the local copies.

---

### Phase 5: Test (Dry Run)

Before you let ArchiveTrail delete any files, it's critical to test it in "dry run" mode. In dry run mode, it will discover files and pretend to copy them, but nothing is actually deleted or modified.

#### Step 5.1: Ensure dry_run is Enabled

In Trino, verify:

```sql
SELECT config_value FROM vast."archive/lineage".offload_config
WHERE config_key = 'dry_run';
```

The value should be `true`. If not, update it:

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'true'
WHERE config_key = 'dry_run';
```

#### Step 5.2: Manually Trigger a Pipeline Run

Instead of waiting for 2 AM, you can trigger a pipeline run manually to test immediately.

In the VAST DataEngine UI under **Pipelines**, find your `archive-trail-tiering` pipeline and click **Run Now** or **Trigger Manually**.

#### Step 5.3: Monitor the Run

The pipeline should complete in a few minutes to a few hours (depending on how many cold files you have). Watch the logs in the DataEngine UI. You should see:

- **Discover function:** "Found X cold files"
- **Offload function:** "DRY_RUN: would copy [file] to AWS" (no actual copies)
- **Verify & Purge function:** Skipped (because nothing was copied)

#### Step 5.4: Check the Lifecycle Events

In Trino, query the events to see what happened:

```sql
SELECT event_type, COUNT(*) as cnt
FROM vast."archive/lineage".lifecycle_events
GROUP BY event_type
ORDER BY cnt DESC;
```

You should see:
- REGISTERED: N (one per cold file found)
- THRESHOLD_EVALUATED: N (one per cold file)
- SCANNED: N (dry run events, showing what would have been copied)

Example output:
```
event_type            cnt
REGISTERED            1250
THRESHOLD_EVALUATED   1250
SCANNED               1250
```

#### Step 5.5: Cross-Check with VAST Audit Log

To verify the discovery really scanned the Catalog, query the VAST audit log:

In Trino:
```sql
SELECT COUNT(*) FROM vast."audit/schema".audit_table
WHERE timestamp > now() - INTERVAL '1' HOUR
  AND operation LIKE '%GetObject%'
  AND object_path LIKE '%/catalog_table%';
```

This confirms that the Catalog was read during your test run.

> **What this means:** ArchiveTrail successfully ran in dry mode. It found cold files, registered them, and would have copied them if not in dry run. No actual data was modified, and you have an audit trail proving it ran.

---

### Phase 6: Go Live

Once you've verified the dry run, it's time to enable actual copying and deletion.

#### Step 6.1: Switch from dry_run to Live Mode

In Trino:

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'false'
WHERE config_key = 'dry_run';
```

> **What this means:** The next pipeline run will actually copy files to AWS.

#### Step 6.2: Keep auto_delete_local Disabled (Optional Conservative Approach)

If you want to be extra cautious, you can run for a while with `auto_delete_local = false`. In this mode:
- Files ARE copied to AWS and verified
- Local copies are NOT deleted
- You can review the results before enabling deletion

Later, when you're confident, update:

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = 'true'
WHERE config_key = 'auto_delete_local';
```

#### Step 6.3: Monitor the First Live Run

Wait for the next scheduled pipeline run (2 AM) or trigger it manually. This time, it will:

1. Discover cold files
2. Copy them to AWS
3. Verify checksums
4. (If auto_delete_local = true) Delete local copies

Monitor in the DataEngine logs and in Trino:

```sql
SELECT event_type, COUNT(*) as cnt, MAX(event_timestamp) as latest
FROM vast."archive/lineage".lifecycle_events
WHERE event_timestamp > now() - INTERVAL '1' HOUR
GROUP BY event_type
ORDER BY event_type;
```

You should see events like:
- COPY_STARTED
- CHECKSUM_VERIFIED
- COPY_COMPLETED
- LOCAL_DELETE_REQUESTED (if auto_delete_local = true)
- LOCAL_DELETED (if auto_delete_local = true)

#### Step 6.4: Verify Files in AWS

Log into your AWS console and navigate to your S3 bucket. You should see files in the structure they had in VAST. Each file should have metadata tags:

- `offload_status: COPIED` (or `PURGED` if local deletion was enabled)
- `offload_destination: s3://bucket/path`
- `offload_timestamp: [ISO date/time]`
- Other tags with VAST cluster info and checksums

Click on a file and view its metadata to confirm the genealogy tags are present.

#### Step 6.5: Set Up Monitoring (Recommended)

Configure alerting for failures. You want to know immediately if something goes wrong.

Set up a monitoring rule that checks for events with `success = false`:

```sql
-- Check for failed operations in the last hour
SELECT COUNT(*) as failures
FROM vast."archive/lineage".lifecycle_events
WHERE event_timestamp > now() - INTERVAL '1' HOUR
  AND success = false;
```

If the count is > 0, alert your ops team.

> **What this means:** ArchiveTrail is now automatically tiering cold files to AWS. You have an audit trail of every operation, and you're alerted to any failures.

---

## 5. Day-to-Day Operations

### Check Pipeline Status

After the pipeline runs, see how many files were processed:

**Via VAST DataEngine UI:**
- Navigate to **Pipelines > archive-trail-tiering > Runs**
- Review the latest run's status and logs

**Via SQL (in Trino):**
```sql
-- How many assets are in each location?
SELECT current_location, COUNT(*) as count,
       SUM(file_size_bytes) / (1024.0^3) as size_gb
FROM vast."archive/lineage".asset_registry
GROUP BY current_location;
```

Example output:
```
current_location    count    size_gb
LOCAL               5000     50.25
BOTH                8500     85.10
LOCAL_DELETED       15200    152.30
```

This tells you:
- 5,000 files remain local (not old enough yet)
- 8,500 files are in both locations (copied but not deleted yet)
- 15,200 files are AWS-only (copied and local deleted)

### Find Where a File Is Now

**If you have a file path or name:**

Via CLI (if you have shell access to the ArchiveTrail container):
```bash
archive-trail locate "/tenant/projects/report_2024.xlsx"
```

Output:
```
handle=0x1A2B3C4D  location=LOCAL_DELETED  original=/tenant/projects/report_2024.xlsx
  -> s3://corp-cold-tier/tenant/projects/report_2024.xlsx
  md5=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

**Via SQL (in Trino):**
```sql
SELECT element_handle, original_path, current_location,
       current_aws_bucket, current_aws_key, last_state_change
FROM vast."archive/lineage".asset_registry
WHERE original_path LIKE '%report_2024%'
LIMIT 10;
```

### View a File's Complete History

**Via CLI:**
```bash
archive-trail history 0x1A2B3C4D
```

Output:
```
Lifecycle for element 0x1A2B3C4D:
────────────────────────────────────────────────────────────────────────────────
  [2026-03-17 02:01:12]  REGISTERED........................ OK
  [2026-03-17 02:01:12]  THRESHOLD_EVALUATED............. --
    from: /tenant/projects/report_2024.xlsx
  [2026-03-17 02:01:13]  COPY_STARTED..................... --
    to: s3://corp-cold-tier/tenant/projects/report_2024.xlsx
  [2026-03-17 02:03:45]  CHECKSUM_VERIFIED............... OK
    md5: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
  [2026-03-17 02:03:46]  COPY_COMPLETED.................. OK
  [2026-03-18 02:00:01]  LOCAL_DELETE_REQUESTED......... --
  [2026-03-18 02:00:03]  LOCAL_DELETED................... OK
```

This shows every action in chronological order with timestamps, success/failure, and any details.

**Via SQL (in Trino):**
```sql
SELECT event_timestamp, event_type, source_path, destination_path,
       success, error_message, checksum_value
FROM vast."archive/lineage".lifecycle_events
WHERE element_handle = '0x1A2B3C4D'
ORDER BY event_timestamp ASC;
```

### Change the Threshold

You can change when files are considered "old" anytime. The change is automatically logged.

**Via SQL (in Trino):**

To change from 60 days to 90 days:

```sql
UPDATE vast."archive/lineage".offload_config
SET config_value = '90',
    updated_at = now(),
    change_reason = 'Q2 2026 policy update: keep local copies longer'
WHERE config_key = 'atime_threshold_days';
```

**Via CLI:**

```bash
archive-trail config set atime_threshold_days 90 --by admin --reason "Q2 policy change"
```

The next pipeline run will use the new threshold.

> **Important:** Changing the threshold only affects NEW discoveries. Files already offloaded under the old threshold stay offloaded. See the glossary for "Config Snapshot" to understand how changes are tracked.

### View Statistics

Get a quick summary of the entire system:

**Via CLI:**
```bash
archive-trail stats
```

Output:
```
Asset Registry Summary:
  LOCAL                    5000 assets  (50.25 GB)
  BOTH                     8500 assets  (85.10 GB)
  LOCAL_DELETED           15200 assets  (152.30 GB)

Lifecycle Event Counts:
  REGISTERED              30000
  THRESHOLD_EVALUATED     30000
  COPY_STARTED            28500
  COPY_COMPLETED          28500
  CHECKSUM_VERIFIED       28500
  LOCAL_DELETE_REQUESTED   8500
  LOCAL_DELETED            8500
  COPY_FAILED                50
  LOCAL_DELETE_FAILED        20
```

This shows you:
- How many files are in each location
- Total storage at each location
- Overall success rates (28,500 completed out of 28,500 started = 100%)
- Any failures to investigate

**Via SQL (in Trino):**

```sql
-- Storage summary
SELECT current_location, COUNT(*) as file_count,
       SUM(file_size_bytes) / (1024.0^3) as size_gb,
       SUM(file_size_bytes) / (1024.0^4) as size_tb
FROM vast."archive/lineage".asset_registry
GROUP BY current_location
ORDER BY size_tb DESC;

-- Recent events
SELECT event_type, COUNT(*) as count,
       MAX(event_timestamp) as latest
FROM vast."archive/lineage".lifecycle_events
WHERE event_timestamp > now() - INTERVAL '24' HOUR
GROUP BY event_type
ORDER BY count DESC;
```

### View Configuration

See all current settings:

**Via CLI:**
```bash
archive-trail config list
```

Output:
```json
{
  "atime_threshold_days": "90",
  "target_aws_bucket": "corp-cold-tier",
  "target_aws_region": "us-east-1",
  "source_paths": "/tenant/projects,/tenant/media",
  "auto_delete_local": "true",
  "dry_run": "false",
  "batch_size": "500",
  "verify_checksum": "true",
  "vast_s3_endpoint": "https://vip-pool.vast.local",
  "vast_cluster_name": "vast-cluster-01"
}
```

**Via SQL (in Trino):**
```sql
SELECT config_key, config_value, updated_by, updated_at, change_reason
FROM vast."archive/lineage".offload_config
ORDER BY config_key;
```

### View Configuration Change History

Who changed what, and when?

**Via CLI:**
```bash
archive-trail config history
```

Output:
```
Config change history:
  [2026-03-20 14:30:15]  atime_threshold_days: '60' -> '90'  by admin (Q2 policy update)
  [2026-03-17 10:22:08]  auto_delete_local: 'false' -> 'true'  by storage-ops (Confidence period complete)
  [2026-03-17 02:00:00]  dry_run: 'true' -> 'false'  by admin (Go live)
```

**Via SQL (in Trino):**
```sql
SELECT config_key, old_value, new_value, changed_by, changed_at, change_reason
FROM vast."archive/lineage".config_change_log
ORDER BY changed_at DESC
LIMIT 50;
```

---

## 6. Configuration Reference

This table explains every configuration setting you can adjust.

| Key | What It Does | Default | Examples | When to Change |
|-----|--------------|---------|----------|----------------|
| `atime_threshold_days` | Files not accessed in this many days are considered "cold" and will be offloaded | `60` | `30`, `60`, `90`, `180` | Change policy: use 30 for aggressive tiering, 90 for conservative |
| `target_aws_bucket` | The S3 bucket where cold files are copied | `corp-cold-tier` | `my-company-cold-tier`, `archive-bucket-prod` | You're using a different AWS bucket |
| `target_aws_region` | The AWS region where the bucket is located | `us-east-1` | `us-west-2`, `eu-west-1`, `ap-southeast-1` | Your bucket is in a different region |
| `source_paths` | Comma-separated list of VAST paths to monitor (empty = all) | `/tenant/projects,/tenant/media` | `/home,/archive,/project-data` | You want to include/exclude certain directories |
| `auto_delete_local` | If `true`, delete local copies after verifying AWS copies (frees local space). If `false`, keep local copies and just use AWS as backup | `false` | `true` (aggressive), `false` (safe) | After testing, set to `true` to free local storage |
| `dry_run` | If `true`, simulate the pipeline without making changes (discover + scan, but no copies or deletes). If `false`, actually copy and delete | `true` | `true` (testing), `false` (production) | Switch from `true` to `false` when confident |
| `batch_size` | How many cold files to process per pipeline run (limits resource usage) | `500` | `100`, `500`, `1000`, `2000` | Increase if runs are fast and local hardware is not stressed; decrease if runs are slow or K8s nodes are overloaded |
| `verify_checksum` | If `true`, read back copied files and verify MD5 matches original (catches corruption). If `false`, skip verification (faster but less safe) | `true` | `true` (safe), `false` (fast) | Keep as `true` unless network bandwidth is extremely limited |
| `vast_s3_endpoint` | The S3 endpoint URL of your VAST cluster (used to read files for copying) | `https://vip-pool.vast.local` | `https://vip-pool.vast.local`, `https://s3.vast.example.com` | You have a different S3 address for VAST |
| `vast_cluster_name` | A label identifying this VAST cluster (embedded in AWS metadata for genealogy) | `vast-cluster-01` | `prod-cluster`, `backup-cluster`, `dr-cluster` | You're running multiple VAST clusters and want to identify which one offloaded a file |
| `catalog_schema` | The schema path where VAST stores Catalog data (typically fixed) | `catalog/schema` | (should not change) | Only change if your VAST admin configured a custom path |
| `catalog_table` | The table name where VAST stores Catalog data (typically fixed) | `catalog_table` | (should not change) | Only change if your VAST admin configured a custom name |

---

## 7. Troubleshooting

### Pipeline Hasn't Run Yet

**Symptom:** The pipeline is scheduled but no events appear in the database.

**Solution:**
1. Check the DataEngine UI: **Pipelines > archive-trail-tiering > Runs**. Does it show any run attempts?
2. If no runs: The schedule may not have been created correctly. Verify the cron expression is `0 2 * * *` (daily at 2 AM UTC).
3. If runs show but with errors: Check the function logs to see the error message.
4. Manually trigger a run: **Run Now** button in the DataEngine UI.

### Pipeline Ran But Found No Cold Files

**Symptom:** The pipeline completed successfully but showed 0 cold files discovered.

**Solution:**
1. Your files may not be old enough. Check the threshold:
   ```sql
   SELECT config_value FROM vast."archive/lineage".offload_config
   WHERE config_key = 'atime_threshold_days';
   ```
   If your files are younger than this threshold, they won't be discovered yet.

2. Your source paths may be wrong:
   ```sql
   SELECT config_value FROM vast."archive/lineage".offload_config
   WHERE config_key = 'source_paths';
   ```
   Verify the paths match directories where you actually have files.

3. The Catalog may not have atime data. Check if your views have atime tracking enabled (VAST Settings > Views > atime_frequency).

### COPY_FAILED Events

**Symptom:** The lifecycle events show `COPY_FAILED` for some files.

**Solution:**

1. Check the error message:
   ```sql
   SELECT source_path, error_message, event_timestamp
   FROM vast."archive/lineage".lifecycle_events
   WHERE event_type = 'COPY_FAILED'
   ORDER BY event_timestamp DESC
   LIMIT 10;
   ```

2. Common errors:
   - **"Access denied to S3 bucket"** → AWS credentials are wrong. Check `.env` and verify the access key has S3 permissions.
   - **"No such bucket"** → The bucket doesn't exist or the region is wrong. Create the bucket or fix `target_aws_bucket` and `target_aws_region`.
   - **"Network timeout"** → Network connectivity issue. Verify K8s nodes can reach AWS.
   - **"File not found"** → The file was deleted or moved between discovery and copy. This is normal; the error is logged and the next run will skip it.

3. If errors are persistent, trigger a manual run and check the DataEngine logs for detailed error output.

### CHECKSUM_MISMATCH Events

**Symptom:** The pipeline shows `CHECKSUM_MISMATCH` for some files.

**What this means:** The file was successfully copied to AWS, but when ArchiveTrail read it back to verify, the checksum didn't match. This is a data integrity issue.

**Solution:**

1. Check how many mismatches:
   ```sql
   SELECT COUNT(*) FROM vast."archive/lineage".lifecycle_events
   WHERE event_type = 'CHECKSUM_MISMATCH';
   ```

2. List the affected files:
   ```sql
   SELECT element_handle, source_path, error_message, event_timestamp
   FROM vast."archive/lineage".lifecycle_events
   WHERE event_type = 'CHECKSUM_MISMATCH'
   ORDER BY event_timestamp DESC;
   ```

3. This is very rare. Possible causes:
   - Network corruption during transfer (very unlikely with TLS)
   - File was modified between read and write (if file is actively being used, this can happen)
   - AWS S3 backend issue (contact AWS support)

4. **Recovery:** Files with mismatches are NOT deleted locally. They stay in LOCAL state. The next pipeline run will skip them. Manually investigate or re-run the copy.

### LOCAL_DELETE_FAILED Events

**Symptom:** The pipeline shows `LOCAL_DELETE_FAILED` for some files.

**Solution:**

1. Check the errors:
   ```sql
   SELECT source_path, error_message, event_timestamp
   FROM vast."archive/lineage".lifecycle_events
   WHERE event_type = 'LOCAL_DELETE_FAILED'
   ORDER BY event_timestamp DESC
   LIMIT 10;
   ```

2. Common errors:
   - **"File in use"** → The file is being accessed by a user. Wait and retry later (or in the next pipeline run).
   - **"Permission denied"** → The DataEngine function doesn't have delete permission. This is a VAST permission issue; contact your VAST admin.
   - **"No such file"** → File was already deleted. This is fine; the error is logged but no problem occurred.

3. **Recovery:** Files with failed deletes remain in BOTH state (exist locally and on AWS). The next pipeline run will re-attempt deletion.

### Pipeline is Very Slow

**Symptom:** The pipeline takes many hours or times out.

**Solution:**

1. **Reduce batch_size:** If you have 100,000 old files, copying them all in one run is slow. Reduce the batch:
   ```sql
   UPDATE vast."archive/lineage".offload_config
   SET config_value = '100'
   WHERE config_key = 'batch_size';
   ```
   This will discover only 100 cold files per run. The next run will discover 100 more.

2. **Increase pipeline timeout:** In DataEngine, update the Offload function timeout to a higher value (e.g., 7200 seconds = 2 hours).

3. **Check network bandwidth:** If copying 100 GB to AWS takes a long time, network bandwidth may be limited. This is expected; monitor and optimize if possible.

### Cannot Connect to VAST Database

**Symptom:** Error like "Connection refused" or "Trino error" when running SQL scripts.

**Solution:**

1. Verify VASTDB_ENDPOINT in `.env`:
   ```bash
   grep VASTDB_ENDPOINT .env
   ```
   Should be something like `https://vms.vast.local`.

2. Test connectivity from your K8s cluster:
   ```bash
   kubectl run -it debug --image=curl --rm -- curl -v https://vms.vast.local
   ```
   Should connect successfully.

3. Check credentials in `.env`: `VASTDB_ACCESS_KEY` and `VASTDB_SECRET_KEY` must be correct.

4. Verify Trino CLI is installed and can connect manually:
   ```bash
   trino --server https://vms.vast.local --user <key> --password <secret>
   ```

### Files Are Not Showing in AWS

**Symptom:** The pipeline says it copied files, but you don't see them in the S3 bucket.

**Solution:**

1. Verify the bucket name and region:
   ```sql
   SELECT config_value FROM vast."archive/lineage".offload_config
   WHERE config_key IN ('target_aws_bucket', 'target_aws_region');
   ```

2. Log into AWS S3 console and manually navigate to the bucket. Is the bucket in the right region?

3. Check if files are there with a different path structure:
   ```bash
   aws s3 ls s3://corp-cold-tier/ --recursive | head -20
   ```

4. Check the COPY_COMPLETED events in the database to see the expected S3 paths:
   ```sql
   SELECT aws_bucket, aws_key, COUNT(*) FROM vast."archive/lineage".lifecycle_events
   WHERE event_type = 'COPY_COMPLETED'
   GROUP BY aws_bucket, aws_key
   LIMIT 5;
   ```

---

## 8. Safety & Recovery

### What Happens If the Pipeline Crashes Mid-Copy?

ArchiveTrail is designed to be crash-safe. Even if the DataEngine function dies while copying a 50 GB file, the system can recover:

1. **Partial file in AWS:** If the copy was halfway done when the function crashed, the partially-uploaded object in S3 is cleaned up (S3 handles incomplete multipart uploads after 7 days).

2. **Registry not updated:** Since the registry is only updated after verification succeeds, the file remains in LOCAL state. No record claims it was copied successfully.

3. **No local deletion:** The local copy is never deleted until after verification, so the original is always safe.

4. **Restart:** The next pipeline run will:
   - Skip files already copied (by checking if they're already in BOTH state)
   - Re-attempt files that failed or crashed
   - Continue with new files

**Example:** You have 10,000 cold files. The pipeline crashes after copying 3,000. The next run will:
- Verify the 3,000 already copied (registry checks)
- Delete the 3,000 locally (if auto_delete_local = true)
- Continue with the remaining 7,000

### How to Verify Data Integrity

ArchiveTrail maintains three independent layers of evidence. To verify a file was copied correctly:

**Layer 1: Check the registry in ArchiveTrail:**
```sql
SELECT element_handle, original_path, source_md5, destination_md5,
       current_location, last_state_change
FROM vast."archive/lineage".asset_registry
WHERE original_path LIKE '%specific_file%';
```

Expected: `source_md5` and `destination_md5` should be identical.

**Layer 2: Check the VAST Audit Log:**
```sql
SELECT timestamp, protocol, operation, object_path, bytes
FROM vast."audit/schema".audit_table
WHERE object_path LIKE '%specific_file%'
  AND operation IN ('GetObject', 'PutObject')
ORDER BY timestamp;
```

Expected: You should see a GetObject (read from VAST S3) and a PutObject (write to AWS). Times should be close together.

**Layer 3: Check AWS S3 Metadata:**

In AWS console:
1. Navigate to your S3 bucket
2. Find the object (should be at path `/tenant/projects/specific_file`)
3. Click the object and view **Properties > Metadata**

Expected: You should see tags like:
- `x-amz-meta-vast-element-handle`: (the VAST element ID)
- `x-amz-meta-vast-source-md5`: (the checksum)
- `x-amz-meta-vast-original-path`: (the original VAST path)

If all three layers match, the file was definitely copied correctly.

### How the 3-Layer Traceability Protects You

Imagine someone suspects ArchiveTrail of losing a file. Where is it?

**Scenario 1: Attacker deletes ArchiveTrail database**
- Layer 1 is gone, but Layers 2 and 3 remain
- You can query VAST Audit Log (Layer 2) to see the copy operation
- You can check AWS (Layer 3) to see the object and its metadata
- You can reconstruct the full history

**Scenario 2: Attacker deletes from AWS**
- Layer 3 is gone, but Layers 1 and 2 remain
- You can query ArchiveTrail registry (Layer 1) and ask "where should this file be?"
- You can check VAST Audit Log (Layer 2) to see the delete happened
- You know the file is gone, but you have proof of when and that it was verified before deletion

**Scenario 3: Network failure causes partial copy**
- Layer 1 shows only completed copies (checksum verified)
- Layer 2 shows all copy attempts
- Layer 3 shows what made it to AWS
- You can identify the gap and re-attempt only failed files

### Recovering a File from AWS (Future Feature)

In a future version, ArchiveTrail will support **recall**: downloading a file from AWS back to VAST.

For now, you can manually recall files:

1. Log into AWS console, find your file in S3
2. Download it to a temporary location
3. Use VAST NFS or S3 to upload it back to the original path
4. Query ArchiveTrail to confirm it's been re-offloaded

The registry will continue to track all copies (BOTH state), and you can manually update the `current_location` field if needed.

---

## 9. Glossary

**Asset Registry:** The master table in ArchiveTrail that tracks one row per file ever discovered. It records the file's identity (element handle), original location, current location (LOCAL, AWS, BOTH, or LOCAL_DELETED), and checksums. This is where you query "where is file X?"

**Config Snapshot:** A JSON copy of the entire offload configuration (threshold, paths, batch size, etc.) embedded in each lifecycle event. This allows you to answer "what threshold was active when this file was offloaded?" even if the threshold was changed later. The snapshot is immutable once the event is recorded.

**Element Handle:** A permanent, unique ID assigned by VAST to every file. The handle survives renames, moves, and permission changes. ArchiveTrail uses the element handle as the genealogy anchor (not the file path, which can change).

**Lifecycle Event:** An immutable log entry recording every state transition of a file (registered, scanned, copy started, checksum verified, copied, deleted, etc.). Each event includes the timestamp, success/failure status, any error messages, and a config snapshot.

**Genealogy:** The complete lineage history of a file—where it came from, when it was created, who owns it, when it was moved, when it was copied, where it is now, and proof of integrity. Every file has a genealogy in ArchiveTrail.

**VAST Catalog:** A periodic (e.g., every 30 minutes) snapshot of your filesystem metadata—file names, sizes, permissions, last access time (atime), modification time (mtime), creation time (ctime). ArchiveTrail uses the Catalog to detect cold files.

**VAST DataEngine:** The serverless compute engine in VAST that runs ArchiveTrail functions (discover, offload, verify_purge) on a schedule or triggered manually. Functions are packaged as Docker containers.

**VAST Protocol Audit:** A platform-level log of every S3, NFS, and SMB operation, independent from ArchiveTrail. Used as a "witness" to verify that operations claimed in the registry actually happened.

**Pipeline:** A sequence of DataEngine functions connected by triggers and dependencies. The ArchiveTrail pipeline is: Trigger (schedule) → Discover → Offload → Verify & Purge.

**Trigger:** A scheduled event (e.g., "every day at 2 AM") that starts a pipeline run. Schedule triggers are the most common; DataEngine also supports event triggers and manual triggers.

**View:** A VAST concept—a path where users access files. A view can expose the same storage via multiple protocols (NFS, SMB, S3). ArchiveTrail requires views to have S3 enabled so files can be read via S3 for copying.

**Cold Files:** Files that haven't been accessed (read) in more than the configured threshold (default 60 days). The "last access time" (atime) is recorded by VAST when a file is read (if the time since the last atime update exceeds the atime_frequency interval).

**Checksum:** A unique fingerprint of a file's contents (MD5 or SHA256). If a file is copied to AWS and has a different checksum on read-back, the copy is corrupted. ArchiveTrail verifies checksums to catch corruption.

**Dry Run:** A mode where the pipeline runs completely (discovers files, simulates copies) but makes no actual changes. Used for testing before going live.

**Offload:** The process of copying a file from local VAST storage to AWS S3. "Offloading" a file means moving it to cloud storage (the local copy may or may not be deleted depending on auto_delete_local setting).

---

## 10. Getting Help

### Questions About ArchiveTrail?

Consult the **IMPLEMENTATION.md** file in the ArchiveTrail project directory for detailed technical explanations of the architecture, database schema, and design decisions.

### Issues or Errors?

1. Check the **Troubleshooting** section (Section 7) above for common problems.
2. Review DataEngine logs in the VAST UI: **DataEngine > Pipelines > archive-trail-tiering > Runs > [latest run] > Logs**
3. Query the lifecycle_events table for detailed error messages:
   ```sql
   SELECT event_type, error_message, event_timestamp
   FROM vast."archive/lineage".lifecycle_events
   WHERE success = false
   ORDER BY event_timestamp DESC;
   ```

### Need to Customize or Extend?

ArchiveTrail is open source. The source code is in the `src/` directory. Key files:
- `src/archive_trail/functions/discover.py` — The discovery logic
- `src/archive_trail/functions/offload.py` — The copying and verification logic
- `src/archive_trail/functions/verify_purge.py` — The deletion logic
- `src/archive_trail/cli.py` — Command-line tools for manual operations
- `src/archive_trail/config.py` — Configuration management
- `src/archive_trail/registry.py` — Asset registry operations

The code is written in Python and is designed to be readable and modifiable. Contact your development team if you need custom features.

---

## End of User Guide

This guide covers deployment, operation, and troubleshooting of ArchiveTrail for non-technical users. For developers and architects, see **IMPLEMENTATION.md** for deep dives into design, database schema, and extension points.

**Last Updated:** 2026-03-17
**ArchiveTrail Version:** 0.1.0
