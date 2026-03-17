"""ArchiveTrail DataEngine functions.

Three functions compose the tiering pipeline:
  1. discover     — Query VAST Catalog for cold files, register them
  2. offload      — Copy to AWS S3 with integrity verification
  3. verify_purge — Optionally delete local copies after re-verification
"""
