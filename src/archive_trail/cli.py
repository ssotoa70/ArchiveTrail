"""ArchiveTrail CLI — Manual operations and queries.

Provides command-line access to ArchiveTrail for:
  - Running the pipeline manually (outside DataEngine)
  - Querying asset genealogy and lifecycle events
  - Updating configuration with change tracking
  - Viewing pipeline status and statistics

Usage:
    python -m archive_trail.cli discover --dry-run
    python -m archive_trail.cli locate "*.xlsx"
    python -m archive_trail.cli history <element_handle>
    python -m archive_trail.cli config set atime_threshold_days 90 --by admin --reason "Q2 policy"
    python -m archive_trail.cli stats
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import vastdb

from archive_trail.config import ArchiveTrailConfig
from archive_trail.events import SCHEMA as EVENTS_SCHEMA
from archive_trail.functions.discover import handler as discover_handler
from archive_trail.functions.offload import handler as offload_handler
from archive_trail.functions.verify_purge import handler as verify_purge_handler
from archive_trail.registry import AssetRegistry

logger = logging.getLogger("archive_trail.cli")

SCHEMA = "archive/lineage"


class ManualContext:
    """Minimal context object for manual pipeline runs."""

    def __init__(self):
        self.run_id = f"manual-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def cmd_discover(args: argparse.Namespace) -> None:
    """Run the discover function manually."""
    ctx = ManualContext()
    result = discover_handler({}, ctx)
    candidates = result.get("candidates", [])
    print(f"Discovered {len(candidates)} candidates (run_id={ctx.run_id})")
    for c in candidates:
        print(f"  {c['path']}  (age: atime={c['atime']}, size={c['size']})")


def cmd_offload(args: argparse.Namespace) -> None:
    """Run discover + offload manually."""
    ctx = ManualContext()
    discover_result = discover_handler({}, ctx)
    candidates = discover_result.get("candidates", [])
    if not candidates:
        print("No candidates found")
        return
    print(f"Discovered {len(candidates)} candidates, starting offload...")
    offload_result = offload_handler(discover_result, ctx)
    offloaded = offload_result.get("offloaded", [])
    failed = offload_result.get("failed", [])
    print(f"Offloaded: {len(offloaded)}, Failed: {len(failed)}")


def cmd_purge(args: argparse.Namespace) -> None:
    """Run verify_purge manually for assets already in BOTH state."""
    ctx = ManualContext()
    result = verify_purge_handler({"pipeline_run_id": ctx.run_id}, ctx)
    purged = result.get("purged", [])
    failed = result.get("failed", [])
    skipped = result.get("skipped")
    if skipped:
        print(f"Purge skipped: {skipped}")
        return
    print(f"Purged: {len(purged)}, Failed: {len(failed)}")


def cmd_pipeline(args: argparse.Namespace) -> None:
    """Run the full pipeline: discover -> offload -> verify_purge."""
    ctx = ManualContext()
    print(f"Pipeline run: {ctx.run_id}")

    print("\n[1/3] Discover...")
    discover_result = discover_handler({}, ctx)
    candidates = discover_result.get("candidates", [])
    print(f"  Found {len(candidates)} candidates")

    if not candidates:
        print("  No candidates, pipeline complete.")
        return

    print("\n[2/3] Offload...")
    offload_result = offload_handler(discover_result, ctx)
    offloaded = offload_result.get("offloaded", [])
    failed = offload_result.get("failed", [])
    print(f"  Offloaded: {len(offloaded)}, Failed: {len(failed)}")

    print("\n[3/3] Verify & Purge...")
    purge_result = verify_purge_handler(offload_result, ctx)
    purged = purge_result.get("purged", [])
    purge_skipped = purge_result.get("skipped")
    if purge_skipped:
        print(f"  Purge skipped: {purge_skipped}")
    else:
        print(f"  Purged: {len(purged)}")

    print(f"\nPipeline complete: {ctx.run_id}")


def cmd_locate(args: argparse.Namespace) -> None:
    """Find where a file is now."""
    session = vastdb.Session()
    registry = AssetRegistry(session)
    pattern = args.pattern if "%" in args.pattern else f"%{args.pattern}%"
    assets = registry.find_by_path(pattern)
    if not assets:
        print(f"No assets found matching '{args.pattern}'")
        return
    for a in assets:
        print(
            f"  handle={a.element_handle}  "
            f"location={a.current_location}  "
            f"original={a.original_path}"
        )
        if a.current_aws_bucket:
            print(
                f"    -> s3://{a.current_aws_bucket}/{a.current_aws_key}"
            )
        if a.source_md5:
            print(f"    md5={a.source_md5}")


def cmd_history(args: argparse.Namespace) -> None:
    """Show the full lifecycle of an element."""
    session = vastdb.Session()
    rows = session.query(
        f"""
        SELECT event_type, event_timestamp, source_path, destination_path,
               aws_bucket, aws_key, success, checksum_value, error_message,
               pipeline_run_id, config_snapshot
        FROM vast."{SCHEMA}".lifecycle_events
        WHERE element_handle = ?
        ORDER BY event_timestamp ASC
        """,
        [args.handle],
    )
    if not rows:
        print(f"No lifecycle events found for handle: {args.handle}")
        return
    print(f"Lifecycle for element {args.handle}:")
    print(f"{'─' * 80}")
    for r in rows:
        status = "OK" if r.get("success") else "FAIL" if r.get("success") is False else "--"
        ts = r["event_timestamp"]
        print(f"  [{ts}]  {r['event_type']:.<30s} {status}")
        if r.get("source_path"):
            print(f"    from: {r['source_path']}")
        if r.get("destination_path"):
            print(f"      to: {r['destination_path']}")
        if r.get("checksum_value"):
            print(f"    md5:  {r['checksum_value']}")
        if r.get("error_message"):
            print(f"    note: {r['error_message']}")


def cmd_config_list(args: argparse.Namespace) -> None:
    """Show current configuration."""
    session = vastdb.Session()
    config = ArchiveTrailConfig(session)
    print("Current ArchiveTrail configuration:")
    print(json.dumps(json.loads(config.to_snapshot()), indent=2))


def cmd_config_set(args: argparse.Namespace) -> None:
    """Update a configuration value with change tracking."""
    session = vastdb.Session()
    config = ArchiveTrailConfig(session)
    config.update(args.key, args.value, changed_by=args.by, reason=args.reason)
    print(f"Config updated: {args.key} = {args.value}")


def cmd_config_history(args: argparse.Namespace) -> None:
    """Show config change history."""
    session = vastdb.Session()
    rows = session.query(
        f"""
        SELECT config_key, old_value, new_value, changed_by,
               changed_at, change_reason
        FROM vast."{SCHEMA}".config_change_log
        ORDER BY changed_at DESC
        LIMIT 50
        """
    )
    if not rows:
        print("No config changes recorded")
        return
    print("Config change history:")
    for r in rows:
        print(
            f"  [{r['changed_at']}]  {r['config_key']}: "
            f"'{r['old_value']}' -> '{r['new_value']}'  "
            f"by {r['changed_by']} ({r['change_reason']})"
        )


def cmd_stats(args: argparse.Namespace) -> None:
    """Show pipeline statistics."""
    session = vastdb.Session()

    location_counts = session.query(
        f"""
        SELECT current_location, COUNT(*) as cnt,
               SUM(file_size_bytes) as total_bytes
        FROM vast."{SCHEMA}".asset_registry
        GROUP BY current_location
        """
    )
    print("Asset Registry Summary:")
    for r in location_counts:
        size_gb = (r["total_bytes"] or 0) / (1024 ** 3)
        print(f"  {r['current_location']:.<20s} {r['cnt']:>8d} assets  ({size_gb:.2f} GB)")

    event_counts = session.query(
        f"""
        SELECT event_type, COUNT(*) as cnt
        FROM vast."{SCHEMA}".lifecycle_events
        GROUP BY event_type
        ORDER BY cnt DESC
        """
    )
    print("\nLifecycle Event Counts:")
    for r in event_counts:
        print(f"  {r['event_type']:.<30s} {r['cnt']:>8d}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="archive-trail",
        description="ArchiveTrail — Cold Data Tiering with Genealogy",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    sub.add_parser("discover", help="Run discovery phase")

    # offload
    sub.add_parser("offload", help="Run discover + offload")

    # purge
    sub.add_parser("purge", help="Run verify & purge for BOTH-state assets")

    # pipeline
    sub.add_parser("pipeline", help="Run full pipeline (discover -> offload -> purge)")

    # locate
    p_locate = sub.add_parser("locate", help="Find where a file is now")
    p_locate.add_argument("pattern", help="File path pattern to search")

    # history
    p_history = sub.add_parser("history", help="Show lifecycle of an element")
    p_history.add_argument("handle", help="VAST Element handle")

    # config
    p_config = sub.add_parser("config", help="Configuration management")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)

    config_sub.add_parser("list", help="Show current config")

    p_config_set = config_sub.add_parser("set", help="Update a config value")
    p_config_set.add_argument("key", help="Config key")
    p_config_set.add_argument("value", help="New value")
    p_config_set.add_argument("--by", required=True, help="Who is making the change")
    p_config_set.add_argument("--reason", required=True, help="Why")

    config_sub.add_parser("history", help="Show config change log")

    # stats
    sub.add_parser("stats", help="Show pipeline statistics")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    commands = {
        "discover": cmd_discover,
        "offload": cmd_offload,
        "purge": cmd_purge,
        "pipeline": cmd_pipeline,
        "locate": cmd_locate,
        "history": cmd_history,
        "stats": cmd_stats,
    }

    if args.command == "config":
        config_commands = {
            "list": cmd_config_list,
            "set": cmd_config_set,
            "history": cmd_config_history,
        }
        config_commands[args.config_cmd](args)
    else:
        commands[args.command](args)


if __name__ == "__main__":
    main()
