create type job_state as enum (
  'created',
  'uploading',
  'uploaded',
  'queued',
  'assigned',
  'reconstructing',
  'training_probe',
  'training_full',
  'exporting',
  'completed',
  'failed',
  'cancelled'
);

create type worker_state as enum (
  'registering',
  'idle',
  'busy',
  'draining',
  'offline'
);

create table tenants (
  tenant_id text primary key,
  name text not null,
  created_at timestamptz not null default now()
);

create table users (
  user_id text primary key,
  tenant_id text not null references tenants(tenant_id),
  external_id text,
  display_name text,
  created_at timestamptz not null default now()
);

create table jobs (
  job_id text primary key,
  tenant_id text not null references tenants(tenant_id),
  user_id text references users(user_id),
  client_record_id text,
  capture_origin text not null,
  input_file_name text not null,
  input_content_type text not null,
  input_size_bytes bigint not null,
  input_storage_key text,
  input_upload_etag text,
  state job_state not null,
  stage text,
  phase_name text,
  current_tier text,
  title text,
  detail text,
  progress_fraction double precision,
  progress_basis text,
  elapsed_seconds integer,
  estimated_remaining_seconds integer,
  assigned_worker_id text,
  worker_generation integer not null default 0,
  artifact_manifest jsonb,
  artifact_manifest_storage_key text,
  artifact_primary_storage_key text,
  preview_storage_key text,
  metrics_storage_key text,
  failure_reason text,
  failure_detail text,
  retry_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  upload_completed_at timestamptz,
  assigned_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  cancelled_at timestamptz
);

create index jobs_state_created_idx on jobs(state, created_at);
create index jobs_assigned_worker_idx on jobs(assigned_worker_id);
create index jobs_tenant_created_idx on jobs(tenant_id, created_at desc);

create table job_events (
  event_id bigserial primary key,
  job_id text not null references jobs(job_id) on delete cascade,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index job_events_job_created_idx on job_events(job_id, created_at);

create table workers (
  worker_id text primary key,
  provider text not null,
  region text,
  instance_label text,
  host_fingerprint text,
  gpu_model text not null,
  gpu_count integer not null,
  vram_mb integer not null,
  cpu_cores integer,
  ram_mb integer,
  disk_free_mb integer,
  software_version text,
  capability_flags jsonb not null default '{}'::jsonb,
  state worker_state not null,
  current_job_id text,
  current_job_started_at timestamptz,
  lease_expires_at timestamptz,
  last_heartbeat_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index workers_state_heartbeat_idx on workers(state, last_heartbeat_at);

create table worker_heartbeats (
  heartbeat_id bigserial primary key,
  worker_id text not null references workers(worker_id) on delete cascade,
  state worker_state not null,
  current_job_id text,
  gpu_util double precision,
  gpu_mem_used_mb integer,
  cpu_util double precision,
  disk_free_mb integer,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index worker_heartbeats_worker_created_idx on worker_heartbeats(worker_id, created_at desc);

create table artifacts (
  artifact_id bigserial primary key,
  job_id text not null references jobs(job_id) on delete cascade,
  artifact_type text not null,
  storage_key text not null,
  size_bytes bigint,
  checksum_sha256 text,
  created_at timestamptz not null default now()
);

create index artifacts_job_idx on artifacts(job_id);
