# Loki Architecture and Upgrade — 2026-09-20

## Purpose

This document records the current Loki architecture used by the telemetry stack and the validation performed during the Loki container upgrade from 3.0.0 to 3.7.8.

The goal is to preserve enough information about Loki's storage, schema, retention, persistence, and upgrade process to make future maintenance and upgrades easier to evaluate safely.

---

## 1. Current Loki architecture

Loki runs as a single container in the telemetry Docker Compose stack.

It receives log data from Grafana Alloy and provides the Loki data source used by Grafana.

Loki is intentionally not published to the Synology host or LAN. Alloy and Grafana access Loki on TCP port 3100 over the internal `telemetry-net` Docker network using Docker DNS.

The Loki configuration is mounted read-only:

```text
./loki/config.yml -> /etc/loki/config.yml
```

Loki runtime data is persisted separately:

```text
./loki/data -> /loki
```

This keeps configuration under Git control while preserving Loki runtime data across container recreation and image upgrades.

---

## 2. Storage and schema

The current Loki schema configuration uses:

```yaml
schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
```

The important characteristics are:

- index store: `tsdb`
- schema: `v13`
- object store: local filesystem
- index period: 24 hours

Filesystem storage is configured under `/loki`:

```yaml
common:
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
```

Because `/loki` is backed by the persistent `./loki/data` host directory, Loki's stored data and operational state survive container recreation.

The existing TSDB v13 schema was retained during the Loki 3.7.8 upgrade. No schema migration or new schema period was introduced merely because the Loki container version changed.

---

## 3. Single-node configuration

This deployment is a single-node Loki installation.

The relevant configuration is:

```yaml
replication_factor: 1

ring:
  kvstore:
    store: inmemory
```

A replication factor of `1` matches the single-instance deployment.

The in-memory ring is recreated when Loki starts. Ring initialization messages can therefore appear during startup while Loki components join their rings and become active.

---

## 4. Retention and compactor

Log retention is configured for 30 days:

```yaml
limits_config:
  retention_period: 30d
```

Retention is enabled through the Loki compactor:

```yaml
compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem
  retention_delete_delay: 2h
```

The compactor working directory resides beneath the persistent `/loki` path.

This allows Loki to enforce the configured 30-day retention period while keeping compactor data on persistent storage.

---

## 5. Upgrade principle

Loki upgrades require additional care because the container operates directly against persistent log storage.

Before changing the running Loki container, the upgrade process should verify:

- the target image and architecture
- compatibility of the existing Loki configuration with the target version
- the existing schema and storage configuration
- persistence of `/loki`
- availability of the previous image for rollback
- validity of the complete Docker Compose configuration

A Loki version upgrade does not by itself require changing the configured storage schema.

Schema changes should be treated as a separate storage-design decision rather than being combined automatically with a container image upgrade.

---

## 6. Loki container upgrade

After reviewing the existing storage and schema configuration, Loki was upgraded from:

```text
3.0.0
```

to:

```text
3.7.8
```

The upgrade was performed independently of Alloy, Prometheus, Grafana, and the Unbound exporter.

### Pre-upgrade state

The running Loki 3.0.0 container reported:

```text
Loki 3.0.0
revision b4f7181c7a
linux/amd64
```

Before the upgrade, the container was running normally with `RestartCount=0`.

The existing configuration already used TSDB with schema v13 and filesystem object storage, so no storage-schema migration was introduced as part of this container upgrade.

### Candidate image validation

The Loki 3.7.8 image was explicitly pulled before changing the Compose image reference.

The candidate image was identified as:

```text
Image:      grafana/loki:3.7.8
Digest:     sha256:1107dd5274e0ada47e42472b7a7e71f3b2a2fe878878108f3e2f9e51528f0193
Image ID:   sha256:ceccdbc45e274f08eb23d6ca6e0b648921d580c592ab47b4304225c0f17a406a
Platform:   amd64/linux
```

The candidate binary reported:

```text
Loki 3.7.8
branch release-3.7.x
revision 09e6ce2f
build date 2026-09-17T13:46:29Z
go1.26.6
linux/amd64
```

The existing Loki configuration was then validated against the 3.7.8 image without mounting the live `/loki` data directory:

```bash
sudo docker run --rm \
  -v "$PWD/loki/config.yml:/etc/loki/config.yml:ro" \
  grafana/loki:3.7.8 \
  -config.file=/etc/loki/config.yml \
  -verify-config
```

The result was:

```text
config is valid
```

This established configuration compatibility before the candidate image was allowed to access the persistent Loki data.

The previous Loki 3.0.0 image was retained locally for rollback.

---

## 7. Deployment

The Docker Compose image reference was changed from:

```yaml
image: grafana/loki:3.0.0
```

to:

```yaml
image: grafana/loki:3.7.8
```

`pull_policy: never` was retained so deployment would use the explicitly downloaded and inspected local image rather than implicitly pulling an image during container recreation.

The complete Docker Compose configuration was validated with:

```bash
sudo docker compose -f docker-compose.yml config --quiet
```

Validation completed without output or error.

Only Loki was then recreated:

```bash
sudo docker compose up -d --no-deps loki
```

The other telemetry services were left running.

---

## 8. Post-upgrade startup validation

After recreation, Docker confirmed that the running container used the exact candidate image:

```text
Image=grafana/loki:3.7.8
ImageID=sha256:ceccdbc45e274f08eb23d6ca6e0b648921d580c592ab47b4304225c0f17a406a
Status=running
RestartCount=0
```

Loki startup logs showed successful access to the existing persistent storage.

Important observations included:

- Loki loaded the existing `tsdb-2024-01-01` index store.
- Existing local index tables were loaded.
- The TSDB manager reported `successful=true`, `buckets=214`, `indices=2`, and `failures=0`.
- Checkpoint recovery completed successfully.
- WAL segment recovery completed with `errors=false`.
- The WAL started normally after recovery.
- The query scheduler became active.
- The compactor joined its ring and became `ACTIVE`.
- Loki reported `Loki started`.

One transient startup message was observed:

```text
level=error ... msg="error getting ingester clients" err="empty ring"
```

This occurred while the in-memory rings were still initializing. Subsequent startup messages showed the ingester, scheduler, and compactor becoming operational, and Loki completed startup successfully.

No continuing failure associated with this startup message was observed during validation.

---

## 9. Readiness validation

Loki 3.7.8 no longer contains `wget`, so an attempted readiness check from inside the Loki container could not execute the utility.

Readiness was instead checked over `telemetry-net` using a separate curl container.

The first request reached Loki successfully but occurred during Loki's intentional ingester readiness delay:

```text
Ingester not ready: waiting for 15s after being ready
```

After the delay, the readiness endpoint returned:

```text
ready
```

This confirmed that Loki's HTTP service was reachable over the internal Docker network and that Loki reached its ready state.

---

## 10. Historical-data and ingestion validation

The Grafana SOC Overview dashboard was inspected over a one-hour time range spanning the Loki restart.

The dashboard showed telemetry from both before and after the Loki 3.7.8 startup time.

This demonstrated two important properties:

1. Loki 3.7.8 could query historical TSDB data written before the upgrade.
2. New telemetry continued to be ingested after the upgrade.

The SOC dashboard continued to populate telemetry including:

- telemetry ingestion rate
- inbound firewall block activity
- attacker-network aggregation
- probed-port aggregation
- ICMP sweep activity

There was no blank historical cutoff at the Loki restart boundary.

This provides end-to-end validation of the path:

```text
OPNsense -> Alloy -> Loki 3.7.8 -> Grafana
```

using both pre-upgrade and post-upgrade log data.

---

## 11. Stability validation

After the functional checks, the Loki container was inspected again.

It remained:

```text
Status=running
RestartCount=0
```

The recorded container start time was:

```text
2026-09-21T02:40:23.315372279Z
```

No container restart occurred during the post-upgrade validation period.

---

## 12. Rollback position

The Loki 3.0.0 image was deliberately retained locally during validation.

This preserves a straightforward rollback option while the 3.7.8 deployment is being finalized and committed to Git.

The old image should not be removed until the repository changes are committed/pushed and the Loki upgrade is considered closed.

---

## Status

The Loki 3.0.0 to 3.7.8 runtime upgrade has been successfully validated.

Validation completed:

- [x] Existing Loki storage/schema configuration reviewed
- [x] TSDB v13 retained
- [x] Filesystem object storage retained
- [x] Persistent `/loki` storage confirmed
- [x] 30-day retention and compactor configuration reviewed
- [x] Candidate 3.7.8 image explicitly pulled and identified
- [x] Candidate platform confirmed as `amd64/linux`
- [x] Existing configuration validated against Loki 3.7.8 before deployment
- [x] Complete Docker Compose configuration validated
- [x] Only the Loki service recreated
- [x] Running image matched the validated candidate image
- [x] Existing TSDB indexes loaded successfully
- [x] WAL recovery completed without errors
- [x] Compactor became active
- [x] Loki readiness endpoint returned `ready`
- [x] Historical pre-upgrade telemetry remained queryable
- [x] Fresh post-upgrade telemetry continued to arrive
- [x] Grafana SOC Overview continued to operate across the upgrade
- [x] Loki remained running with `RestartCount=0`

The corresponding repository changes are the Loki image version in `docker-compose.yml`, the pinned Loki version in `README.md`, and this document.
