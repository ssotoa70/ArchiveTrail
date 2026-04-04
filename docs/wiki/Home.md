# ArchiveTrail Wiki

Welcome to the ArchiveTrail documentation hub. This wiki provides comprehensive guides for deploying, configuring, and operating ArchiveTrail on VAST Data Platform.

## Documentation Structure

### Getting Started

- **[Main README](../README.md)** — Project overview, quick start, and feature summary
- **[VAST Platform Setup](VAST-Platform-Setup.md)** — Prerequisites and platform configuration before deployment

### Architecture & Design

- **[Architecture Guide](Architecture.md)** — System design, pipeline flow, state machines, and traceability layers
- **[Database Schema](Database-Schema.md)** — Complete table definitions, relationships, and example queries

### Deployment & Operations

- **[Deployment Guide](Deployment-Guide.md)** — Step-by-step production deployment checklist
- **[Configuration Guide](Configuration-Guide.md)** — Parameter reference and tuning options
- **[Operations Guide](Operations-Guide.md)** — Day-to-day operations, CLI reference, troubleshooting

## Quick Navigation

### By Role

**Platform Administrators**
1. Start with [VAST Platform Setup](VAST-Platform-Setup.md) to enable required features
2. Review [Deployment Guide](Deployment-Guide.md) for production deployment steps
3. Consult [Operations Guide](Operations-Guide.md) for ongoing management

**Data Engineers**
1. Read [Architecture Guide](Architecture.md) to understand the design
2. Review [Database Schema](Database-Schema.md) for data model details
3. Check [Operations Guide](Operations-Guide.md) for CLI tools and queries

**Security & Compliance**
1. Review [Architecture Guide](Architecture.md), specifically the "Traceability Layers" section
2. Understand the genealogy query patterns in [Database Schema](Database-Schema.md)
3. See [Operations Guide](Operations-Guide.md) for audit query examples

### By Task

**I want to deploy ArchiveTrail**
→ [VAST Platform Setup](VAST-Platform-Setup.md) → [Deployment Guide](Deployment-Guide.md)

**I need to configure parameters**
→ [Configuration Guide](Configuration-Guide.md)

**I need to understand what happened to a file**
→ [Database Schema](Database-Schema.md) (genealogy queries section)

**I'm seeing errors or performance issues**
→ [Operations Guide](Operations-Guide.md) (troubleshooting section)

**I want to understand the design**
→ [Architecture Guide](Architecture.md)

## Key Concepts

### Element Handle
Each file in VAST has a unique **Element Handle** assigned by the platform. This handle survives renames and moves, making it the perfect immutable identity anchor for ArchiveTrail. All genealogy queries use the handle to track a file across its entire lifecycle.

### Traceability Layers
ArchiveTrail implements three independent, cross-verifiable traceability layers:
1. **Application Layer** — ArchiveTrail tables in VAST DB
2. **Platform Layer** — VAST Catalog snapshots and Protocol Auditing
3. **Destination Layer** — AWS S3 object metadata

Each layer can independently prove the chain of custody.

### State Machine
Files progress through states as they are discovered, offloaded, and purged:
```
Unknown → LOCAL → BOTH → LOCAL_DELETED → RECALLED
```
Every state transition is immutably recorded in lifecycle_events.

### Config Genealogy
Configuration changes are fully tracked. Every event includes a snapshot of the active config, answering questions like "what threshold was active when file X was offloaded?"

## Core Tables

| Table | Purpose |
|-------|---------|
| `asset_registry` | Master identity table — one row per file, ever. Immutable once created. |
| `lifecycle_events` | Append-only audit trail. Every state transition produces one or more rows. |
| `offload_config` | Current operational parameters. User-configurable. |
| `config_change_log` | History of all config changes with timestamps and reasons. |

For detailed schema, see [Database Schema](Database-Schema.md).

## Pipeline Overview

The ArchiveTrail pipeline runs on a schedule (e.g., daily at 2 AM) and consists of three stages:

1. **Discover** — Query VAST Catalog for cold files (atime > threshold)
2. **Offload** — Copy to AWS S3 with checksum verification
3. **Verify & Purge** — Optionally delete local copies after AWS verification

Each stage emits detailed lifecycle events. Pipeline can be run in dry-run mode for validation.

## Phased Rollout Strategy

1. **Dry-Run Phase** — `dry_run=true`, `auto_delete_local=false`
   - Validates discovery and copy logic
   - No actual data movement or deletion
   
2. **Copy-Only Phase** — `dry_run=false`, `auto_delete_local=false`
   - Copies files to AWS
   - Local copies retained for safety
   - Builds confidence in data integrity

3. **Auto-Purge Phase** — `dry_run=false`, `auto_delete_local=true`
   - Automatically deletes local copies after AWS verification
   - Full production tiering

## Support

For issues, questions, or contributions, see the main [README](../README.md).
