# Control Plane

This directory contains the production-facing control-plane path for the
Aether mobile-to-GPU pipeline:

- iPhone uploads media to object storage with background `URLSession`
- the control plane creates jobs, signs upload targets, tracks job state, and
  schedules workers
- rented GPU workers register, heartbeat, claim jobs, pull input from object
  storage, run the donor pipeline, and push artifacts back
- production ingress is expected to be a direct public HTTPS reverse proxy
  such as `Caddy` or `Nginx`, not a Cloudflare tunnel dependency
- production uploads are expected to target managed regional object storage
  such as `S3`, `R2`, or another S3-compatible service, not self-hosted MinIO

Current implementation status:

- `migrations/0001_initial.sql`
  Real Postgres schema for jobs, users, workers, heartbeats, artifacts, and
  job events.
- `app/`
  FastAPI control plane with real repository and object-storage signer
  implementations.
- `worker_agent/`
  Pull-based worker that downloads the input object, starts the donor
  autofallback pipeline, mirrors runtime status, uploads artifacts, and
  reports completion/failure.
- `ios/`
  Swift request/response models, foreground API client, and background upload
  coordinator aligned to the current control-plane API.

## Environment

Copy the sample environment and fill in your own values:

```bash
cp .env.example .env
```

Important variables:

- `CONTROL_PLANE_API_INGRESS_MODE`
  Recommended production value is `direct_tls`.
- `CONTROL_PLANE_DATABASE_URL`
  Postgres DSN for the control plane.
- `CONTROL_PLANE_OBJECT_STORAGE_PROVIDER`
  Recommended production value is `s3_compatible`.
- `CONTROL_PLANE_OBJECT_STORAGE_BUCKET`
  Bucket used for job input uploads and artifact outputs.
- `CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL`
  S3-compatible endpoint such as S3, R2, or MinIO.
- `CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID`
- `CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY`
- `CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL`
  Optional public/download base URL if artifacts should use public object URLs.
- `WORKER_RETAIN_LOCAL_PRIMARY_ARTIFACT`
  Default production value should be `0` so rented workers do not accumulate
  local copies after upload.
- `WORKER_UPLOAD_AUXILIARY_ARTIFACTS`
  Default production value should be `0` so only the final interactive 3DGS
  file is uploaded by default.
- `WORKER_DONOR_ROOT`
  Root of the donor pipeline checkout on the rented GPU worker.
- `WORKER_DONOR_START_SCRIPT`
  Entry script for launching the autofallback run on the worker.

## Bring-up order

Install runtime dependencies first:

```bash
python3 -m pip install -r requirements.txt
```

1. Apply `migrations/0001_initial.sql` to Postgres.
2. Start the FastAPI control plane with the configured database and object
   storage credentials.
3. Start one or more worker agents on rented GPU machines.
4. Point the iPhone app at the public control-plane base URL.

Example local bring-up:

```bash
python3 -m uvicorn control_plane.app.main:app --host 0.0.0.0 --port 8080
python3 -m control_plane.worker_agent.main
```

Example production bring-up on a public VM:

```bash
bash control_plane/scripts/setup_control_plane_node.sh
bash control_plane/scripts/setup_public_api_ingress_caddy.sh
```

## Notes

- This code is intentionally separate from the current local broker prototype
  so we can evolve the production control plane without destabilizing ongoing
  phone tests.
- The worker agent is designed for rented GPUs: it does not require machines
  to be known ahead of time, only that they can reach the control plane and
  object storage.
- `scripts/setup_control_plane_node.sh` now defaults to `managed` storage mode.
  Local MinIO is still supported, but only when explicitly requested with
  `CONTROL_PLANE_STORAGE_MODE=local_minio`.
- `scripts/setup_public_api_ingress_caddy.sh` is the recommended path for
  stable public HTTPS ingress. Treat Cloudflare tunnel only as an emergency
  fallback, not the primary production entry point.
