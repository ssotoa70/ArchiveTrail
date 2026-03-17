-- ArchiveTrail: Seed Configuration
-- Run after 002_create_tables.sql
-- Adjust values to match your environment before running.

INSERT INTO vast."archive/lineage".offload_config
    (config_key, config_value, updated_by, updated_at, change_reason)
VALUES
    ('atime_threshold_days',  '60',                                'admin', now(), 'Initial setup'),
    ('target_aws_bucket',     'corp-cold-tier',                    'admin', now(), 'Initial setup'),
    ('target_aws_region',     'us-east-1',                         'admin', now(), 'Initial setup'),
    ('source_paths',          '/tenant/projects,/tenant/media',    'admin', now(), 'Initial setup'),
    ('auto_delete_local',     'false',                             'admin', now(), 'Start conservative'),
    ('dry_run',               'true',                              'admin', now(), 'Initial setup'),
    ('batch_size',            '500',                               'admin', now(), 'Initial setup'),
    ('verify_checksum',       'true',                              'admin', now(), 'Data integrity enforcement'),
    ('vast_s3_endpoint',      'https://vip-pool.vast.local',       'admin', now(), 'VAST S3 endpoint'),
    ('vast_cluster_name',     'vast-cluster-01',                   'admin', now(), 'Source cluster identifier'),
    ('catalog_schema',        'catalog/schema',                    'admin', now(), 'VAST Catalog schema path'),
    ('catalog_table',         'catalog_table',                     'admin', now(), 'VAST Catalog table name');
