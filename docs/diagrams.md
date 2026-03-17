# ArchiveTrail Diagrams

Visual reference for the ArchiveTrail cold data tiering solution.

---

## 1. High-Level Architecture

```mermaid
flowchart TD
    clients["Users & Applications<br/>(NFS / SMB / S3)"]
    vast["VAST Element Store<br/>Local Storage"]
    catalog["VAST Catalog<br/>Snapshots every 30 min"]
    engine["DataEngine Pipeline<br/>Scheduled daily at 2 AM"]
    aws["AWS S3<br/>Cold Tier Bucket"]
    db["VAST DB<br/>Tracking Tables"]
    audit["VAST Protocol Audit<br/>Independent Witness"]

    clients -->|"read & write files"| vast
    vast -->|"periodic snapshot"| catalog
    catalog -->|"query cold files"| engine
    engine -->|"copy files"| aws
    engine -->|"record events"| db
    vast -->|"log all operations"| audit

    classDef vastStyle fill:#2563eb,stroke:#1e40af,color:#fff,rx:12
    classDef awsStyle fill:#f59e0b,stroke:#d97706,color:#fff,rx:12
    classDef engineStyle fill:#7c3aed,stroke:#5b21b6,color:#fff,rx:12
    classDef userStyle fill:#6b7280,stroke:#4b5563,color:#fff,rx:12

    class vast,catalog,audit,db vastStyle
    class aws awsStyle
    class engine engineStyle
    class clients userStyle
```

---

## 2. Pipeline Flow

```mermaid
flowchart LR
    trigger["Schedule Trigger<br/>Daily at 2 AM"]
    discover["Discover<br/>Find cold files in Catalog<br/>Register in VAST DB"]
    offload["Offload & Track<br/>Copy to AWS S3<br/>Verify checksums"]
    purge["Verify & Purge<br/>Re-verify AWS copy<br/>Delete local if enabled"]

    trigger -->|"start pipeline"| discover
    discover -->|"pass candidate list"| offload
    offload -->|"pass verified files"| purge

    classDef triggerStyle fill:#6b7280,stroke:#4b5563,color:#fff,rx:10
    classDef discoverStyle fill:#2563eb,stroke:#1e40af,color:#fff,rx:10
    classDef offloadStyle fill:#7c3aed,stroke:#5b21b6,color:#fff,rx:10
    classDef purgeStyle fill:#dc2626,stroke:#991b1b,color:#fff,rx:10

    class trigger triggerStyle
    class discover discoverStyle
    class offload offloadStyle
    class purge purgeStyle
```

---

## 3. File State Machine

```mermaid
stateDiagram-v2
    [*] --> Unknown
    Unknown --> LOCAL : File discovered as cold<br/>(atime exceeds threshold)
    LOCAL --> BOTH : Copied to AWS S3<br/>(checksum verified)
    BOTH --> LOCAL_DELETED : Local copy deleted<br/>(auto-delete enabled)
    LOCAL_DELETED --> RECALLED : File recalled from AWS<br/>(future feature)
    RECALLED --> BOTH : Re-offloaded to AWS

    note right of Unknown
        File exists on VAST
        but not yet seen by
        ArchiveTrail
    end note

    note right of BOTH
        File exists on both
        VAST and AWS S3
    end note

    note right of LOCAL_DELETED
        Only the AWS S3
        copy remains
    end note
```

---

## 4. Traceability Layers

```mermaid
flowchart TD
    subgraph layer1["Layer 1 - Application Tables (VAST DB)"]
        ar["Asset Registry<br/>Master identity & state"]
        le["Lifecycle Events<br/>Every state transition"]
        oc["Offload Config<br/>Current parameters"]
        cl["Config Change Log<br/>Config history"]
    end

    subgraph layer2["Layer 2 - VAST Platform Audit"]
        cat["VAST Catalog<br/>Namespace snapshots (7-day)"]
        tag["S3 Tags<br/>offload_status in Catalog"]
        pa["Protocol Audit<br/>S3 / NFS / SMB operations"]
    end

    subgraph layer3["Layer 3 - AWS S3 Metadata"]
        meta["Object Metadata<br/>element-handle<br/>original-path<br/>source-cluster<br/>source-md5"]
    end

    layer1 -.-|"corroborated by"| layer2
    layer2 -.-|"corroborated by"| layer3

    note1["Any two layers can<br/>reconstruct the full<br/>chain of custody"]

    layer3 -.- note1

    classDef appLayer fill:#2563eb,stroke:#1e40af,color:#fff,rx:8
    classDef platformLayer fill:#059669,stroke:#047857,color:#fff,rx:8
    classDef awsLayer fill:#f59e0b,stroke:#d97706,color:#fff,rx:8
    classDef noteStyle fill:#f3f4f6,stroke:#9ca3af,color:#374151,rx:8

    class ar,le,oc,cl appLayer
    class cat,tag,pa platformLayer
    class meta awsLayer
    class note1 noteStyle

    style layer1 fill:#dbeafe,stroke:#2563eb,rx:12
    style layer2 fill:#d1fae5,stroke:#059669,rx:12
    style layer3 fill:#fef3c7,stroke:#f59e0b,rx:12
```

---

## 5. Deployment Steps

```mermaid
flowchart TD
    s1["Step 1<br/>Enable VAST Catalog"]
    s2["Step 2<br/>Enable Protocol Auditing"]
    s3["Step 3<br/>Add offload_status tag<br/>to Catalog index"]
    s4["Step 4<br/>Enable S3 on all views"]
    s5["Step 5<br/>Create DB schema & tables"]
    s6["Step 6<br/>Seed config with<br/>initial values"]
    s7["Step 7<br/>Build DataEngine<br/>function containers"]
    s8["Step 8<br/>Create Schedule Trigger"]
    s9["Step 9<br/>Build pipeline:<br/>trigger + 3 functions"]
    s10["Step 10<br/>Deploy with dry_run = true"]
    s11{"Step 11<br/>Review events &<br/>cross-check audit.<br/>Results OK?"}
    s12["Step 12<br/>Set dry_run = false<br/>auto_delete = false<br/>(copy only)"]
    s13{"Step 13<br/>Confidence period<br/>passed?"}
    s14["Enable auto_delete = true<br/>Full production mode"]

    s1 --> s3
    s2 --> s5
    s3 --> s5
    s4 --> s7
    s5 --> s6
    s6 --> s7
    s7 --> s9
    s8 --> s9
    s9 --> s10
    s10 --> s11
    s11 -->|"Yes"| s12
    s11 -->|"No - investigate"| s10
    s12 --> s13
    s13 -->|"Yes"| s14
    s13 -->|"No - keep monitoring"| s12

    classDef configStep fill:#2563eb,stroke:#1e40af,color:#fff,rx:8
    classDef buildStep fill:#7c3aed,stroke:#5b21b6,color:#fff,rx:8
    classDef deployStep fill:#059669,stroke:#047857,color:#fff,rx:8
    classDef decisionStep fill:#f59e0b,stroke:#d97706,color:#fff,rx:8
    classDef finalStep fill:#16a34a,stroke:#15803d,color:#fff,rx:8

    class s1,s2,s3,s4 configStep
    class s5,s6,s7,s8,s9 buildStep
    class s10,s12 deployStep
    class s11,s13 decisionStep
    class s14 finalStep
```

---

## 6. Data Flow During Offload

```mermaid
sequenceDiagram
    participant DE as DataEngine<br/>Function
    participant VS as VAST S3<br/>(Local)
    participant AWS as AWS S3<br/>(Cold Tier)
    participant DB as VAST DB<br/>(Tracking)

    Note over DE: Pipeline triggered at 2 AM

    DE->>DB: Log COPY_STARTED event
    DE->>VS: Read file (GetObject)
    VS-->>DE: File contents
    Note over DE: Compute MD5 checksum<br/>of source file

    DE->>AWS: Upload file (PutObject)<br/>+ genealogy metadata
    AWS-->>DE: Upload confirmed

    DE->>AWS: Re-read file for verification
    AWS-->>DE: File contents
    Note over DE: Compute MD5 of<br/>destination file

    alt Checksums match
        DE->>DB: Log CHECKSUM_VERIFIED
        DE->>DB: Log COPY_COMPLETED
        DE->>DB: Update registry: state = BOTH
        DE->>VS: Tag file: offload_status = COPIED
    else Checksums do NOT match
        DE->>DB: Log CHECKSUM_MISMATCH
        Note over DE: File state stays LOCAL<br/>No further action
    end
```

---

## 7. Configuration Change Flow

```mermaid
sequenceDiagram
    participant Admin
    participant DB as VAST DB
    participant Log as Config<br/>Change Log
    participant Pipeline as Next Pipeline<br/>Run

    Admin->>DB: UPDATE offload_config<br/>e.g. threshold = 90 days

    Note over DB: Old value captured<br/>before overwrite

    DB->>Log: INSERT into config_change_log<br/>old_value = 60<br/>new_value = 90<br/>changed_by = admin<br/>reason = "seasonal adjustment"

    DB-->>Admin: Config updated successfully

    Note over Pipeline: Next scheduled run<br/>(daily at 2 AM)

    Pipeline->>DB: Read current config
    DB-->>Pipeline: threshold = 90 days

    Note over Pipeline: Config snapshot embedded<br/>in every lifecycle event

    Pipeline->>DB: Log THRESHOLD_EVALUATED<br/>config_snapshot = {"threshold": 90, ...}

    Note over DB: Future auditors can see<br/>exactly which config was<br/>active for every decision
```
