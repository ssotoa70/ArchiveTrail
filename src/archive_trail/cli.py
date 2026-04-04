"""ArchiveTrail CLI — Manual operations and queries.

Provides command-line access to ArchiveTrail for:
  - Running the pipeline manually (outside DataEngine)
  - Querying asset genealogy and lifecycle events
  - Updating configuration with change tracking
  - Viewing pipeline status and statistics

Usage:
    python -m archive_trail discover --dry-run
    python -m archive_trail locate "*.xlsx"
    python -m archive_trail history <element_handle>
    python -m archive_trail config set atime_threshold_days 90 --by admin --reason "Q2 policy"
    python -m archive_trail stats
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from archive_trail.db import (
    create_session,
    ensure_tables,
    get_table,
    TABLE_ASSET_REGISTRY,
    TABLE_LIFECYCLE_EVENTS,
    TABLE_CONFIG_CHANGE_LOG,
)
from archive_trail.config import ArchiveTrailConfig
from archive_trail.registry import AssetRegistry

logger = logging.getLogger("archive_trail.cli")


class ManualContext:
    """Minimal context object for manual pipeline runs.

    Mimics the VAST DataEngine ctx object with a logger attribute.
    """

    def __init__(self):
        self.run_id = f"manual-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.logger = logging.getLogger("archive_trail.manual")


def _get_session():
    """Create a VastDB session for CLI use."""
    session = create_session(logger=logger)
    ensure_tables(session, logger=logger)
    return session


def cmd_discover(args: argparse.Namespace) -> None:
    """Run the discover function manually."""
    ctx = ManualContext()
    session = _get_session()

    # Import the discover handler and wire it up
    from archive_trail.config import ArchiveTrailConfig
    from archive_trail.events import EventEmitter, EventType
    from archive_trail.helpers import days_since, full_path
    from archive_trail.registry import AssetRegistry

    config = ArchiveTrailConfig(session, logger=ctx.logger)
    registry = AssetRegistry(session, logger=ctx.logger)
    emitter = EventEmitter(session, logger=ctx.logger)

    config_snapshot = config.to_snapshot()
    threshold = config.atime_threshold_days
    batch_size = config.batch_size

    print(f"Discover: threshold={threshold}d, dry_run={config.dry_run}, batch={batch_size}")
    print(f"Run ID: {ctx.run_id}")

    # Get already offloaded handles
    offloaded_handles = registry.get_offloaded_handles()
    print(f"Already offloaded: {len(offloaded_handles)} handles")
    print("(Catalog query would run here in DataEngine environment)")


def cmd_locate(args: argparse.Namespace) -> None:
    """Find where a file is now."""
    session = _get_session()

    import pyarrow as pa
    with session.transaction() as tx:
        table = get_table(tx, TABLE_ASSET_REGISTRY)
        # Simple select — filter in Python for LIKE patterns
        result = table.select(columns=[
            "element_handle", "original_path", "current_location",
            "current_aws_bucket", "current_aws_key", "source_md5",
        ])

    pattern = args.pattern.replace("*", "").replace("%", "")
    found = False
    for i in range(result.num_rows):
        path = result.column("original_path")[i].as_py()
        if pattern in (path or ""):
            found = True
            handle = result.column("element_handle")[i].as_py()
            location = result.column("current_location")[i].as_py()
            aws_bucket = result.column("current_aws_bucket")[i].as_py()
            aws_key = result.column("current_aws_key")[i].as_py()
            md5 = result.column("source_md5")[i].as_py()

            print(f"  handle={handle}  location={location}  original={path}")
            if aws_bucket:
                print(f"    -> s3://{aws_bucket}/{aws_key}")
            if md5:
                print(f"    md5={md5}")

    if not found:
        print(f"No assets found matching '{args.pattern}'")


def cmd_history(args: argparse.Namespace) -> None:
    """Show the full lifecycle of an element."""
    session = _get_session()

    import pyarrow as pa
    with session.transaction() as tx:
        table = get_table(tx, TABLE_LIFECYCLE_EVENTS)
        predicate = pa.compute.field("element_handle") == args.handle
        result = table.select(
            filter=predicate,
            columns=[
                "event_type", "event_timestamp", "source_path",
                "destination_path", "success", "checksum_value",
                "error_message",
            ],
        )

    if result.num_rows == 0:
        print(f"No lifecycle events found for handle: {args.handle}")
        return

    print(f"Lifecycle for element {args.handle}:")
    print(f"{'─' * 80}")
    for i in range(result.num_rows):
        event_type = result.column("event_type")[i].as_py()
        ts = result.column("event_timestamp")[i].as_py()
        success = result.column("success")[i].as_py()
        status = "OK" if success else ("FAIL" if success is False else "--")

        print(f"  [{ts}]  {event_type:.<30s} {status}")

        src = result.column("source_path")[i].as_py()
        dst = result.column("destination_path")[i].as_py()
        cksum = result.column("checksum_value")[i].as_py()
        err = result.column("error_message")[i].as_py()

        if src:
            print(f"    from: {src}")
        if dst:
            print(f"      to: {dst}")
        if cksum:
            print(f"    md5:  {cksum}")
        if err:
            print(f"    note: {err}")


def cmd_config_list(args: argparse.Namespace) -> None:
    """Show current configuration."""
    session = _get_session()
    config = ArchiveTrailConfig(session, logger=logger)
    print("Current ArchiveTrail configuration:")
    print(json.dumps(json.loads(config.to_snapshot()), indent=2))


def cmd_config_set(args: argparse.Namespace) -> None:
    """Update a configuration value with change tracking."""
    session = _get_session()
    config = ArchiveTrailConfig(session, logger=logger)
    config.update(args.key, args.value, changed_by=args.by, reason=args.reason)
    print(f"Config updated: {args.key} = {args.value}")


def cmd_config_history(args: argparse.Namespace) -> None:
    """Show config change history."""
    session = _get_session()

    with session.transaction() as tx:
        table = get_table(tx, TABLE_CONFIG_CHANGE_LOG)
        result = table.select(columns=[
            "config_key", "old_value", "new_value",
            "changed_by", "changed_at", "change_reason",
        ])

    if result.num_rows == 0:
        print("No config changes recorded")
        return

    print("Config change history:")
    for i in range(result.num_rows):
        key = result.column("config_key")[i].as_py()
        old = result.column("old_value")[i].as_py()
        new = result.column("new_value")[i].as_py()
        by = result.column("changed_by")[i].as_py()
        at = result.column("changed_at")[i].as_py()
        reason = result.column("change_reason")[i].as_py()
        print(f"  [{at}]  {key}: '{old}' -> '{new}'  by {by} ({reason})")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show pipeline statistics."""
    session = _get_session()

    # Asset summary by location
    with session.transaction() as tx:
        reg_table = get_table(tx, TABLE_ASSET_REGISTRY)
        reg_result = reg_table.select(columns=["current_location", "file_size_bytes"])

    # Aggregate in Python
    location_stats: dict[str, dict] = {}
    for i in range(reg_result.num_rows):
        loc = reg_result.column("current_location")[i].as_py() or "UNKNOWN"
        size = reg_result.column("file_size_bytes")[i].as_py() or 0
        if loc not in location_stats:
            location_stats[loc] = {"count": 0, "total_bytes": 0}
        location_stats[loc]["count"] += 1
        location_stats[loc]["total_bytes"] += size

    print("Asset Registry Summary:")
    for loc, stats in sorted(location_stats.items()):
        size_gb = stats["total_bytes"] / (1024 ** 3)
        print(f"  {loc:.<20s} {stats['count']:>8d} assets  ({size_gb:.2f} GB)")

    # Event summary by type
    with session.transaction() as tx:
        evt_table = get_table(tx, TABLE_LIFECYCLE_EVENTS)
        evt_result = evt_table.select(columns=["event_type"])

    event_counts: dict[str, int] = {}
    for i in range(evt_result.num_rows):
        et = evt_result.column("event_type")[i].as_py() or "UNKNOWN"
        event_counts[et] = event_counts.get(et, 0) + 1

    print("\nLifecycle Event Counts:")
    for et, cnt in sorted(event_counts.items(), key=lambda x: -x[1]):
        print(f"  {et:.<30s} {cnt:>8d}")


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
