# Prometheus Architecture and Upgrade Record --- 2026-09-20

## Purpose

Prometheus provides the metrics storage and query layer for the OPNsense
observability stack. It currently collects metrics from itself and from
the custom Unbound exporter, and Grafana queries Prometheus over the
private Docker network.

This document records the deployed architecture and the validated
upgrade from Prometheus 3.12.0 to 3.14.0.

## Architecture

### Container

-   Image: `prom/prometheus:v3.14.0`
-   Container name: `prometheus`
-   Runtime user: `65534:65534`
-   Restart policy: `unless-stopped`
-   Image policy: `pull_policy: never`
-   Time zone: `Pacific/Honolulu`
-   Network: `telemetry-net`
-   Prometheus is intentionally **not published to the Synology host**.
-   Grafana accesses Prometheus on port `9090` over `telemetry-net`
    using Docker DNS.

### Persistent storage

Prometheus TSDB data is persisted outside the container:

``` yaml
- ./prometheus/data:/prometheus
```

The TSDB path is explicitly configured as:

``` text
--storage.tsdb.path=/prometheus
```

Retention is:

``` text
--storage.tsdb.retention.time=30d
```

Because the TSDB resides in `./prometheus/data`, recreating or upgrading
the Prometheus container does not intentionally remove historical
metrics.

### Configuration

The Prometheus configuration is mounted read-only:

``` yaml
- ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
```

Current global intervals:

-   Scrape interval: 60 seconds
-   Evaluation interval: 60 seconds

Current scrape jobs:

1.  `prometheus`
    -   Target: `localhost:9090`
    -   Prometheus self-monitoring.
2.  `unbound`
    -   Target: `unbound-exporter:9101`
    -   Collects metrics from the custom Unbound exporter over
        `telemetry-net`.

The current configuration does not define alerting rules, recording
rules, remote write/read, or service discovery.

## Pre-upgrade state

The previous deployed version was:

``` text
Prometheus 3.12.0
revision: 9f27dffc1f93ca23287972f632025879f2d1c658
build: 2026-05-28 15:49:37
Go: go1.26.3
platform: linux/amd64
```

Previous image:

``` text
prom/prometheus:v3.12.0
Image ID: sha256:d2f7aaa363e1c220487d776f21a5d7a3416834826038c1d2b38f1adb9079ed93
```

Before the upgrade the container was running with `RestartCount=0`.

### Pre-upgrade TSDB analysis

`promtool tsdb analyze /prometheus` successfully read the existing TSDB
before the upgrade.

Representative baseline:

-   Total series: 749
-   Label names: 33
-   Unique label pairs: 559
-   Postings entries: 2945
-   Prometheus self-monitoring series: 706
-   Unbound exporter series: 43
-   No concerning series churn was observed in the analysis.

This established that the existing TSDB was readable before allowing the
candidate image to access it.

## Upgrade candidate

Selected version:

``` text
Prometheus 3.14.0
revision: d7598b7141418fa35be2b5ec5d0fefb634199610
build: 2026-08-17 16:49:19
Go: go1.26.6
platform: linux/amd64
```

Pulled image:

``` text
prom/prometheus:v3.14.0
Registry digest:
sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0

Local image ID:
sha256:31c1e0aacb3a1914563c4e9b1e8d0a55095bf433aa43c1b0fe695959742845bd
```

The image was verified as `amd64/linux`.

The old 3.12.0 image was deliberately retained during validation as a
rollback image.

## Pre-deployment validation

The 3.14.0 candidate was first tested without mounting the live
`/prometheus` data directory.

Its `promtool` validated the existing configuration:

``` text
SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax
```

Only after this validation was the Compose image changed from:

``` text
prom/prometheus:v3.12.0
```

to:

``` text
prom/prometheus:v3.14.0
```

The complete Compose configuration was then validated successfully with:

``` bash
sudo docker compose -f docker-compose.yml config --quiet
```

No output indicated a valid Compose configuration.

## Deployment

Only Prometheus was recreated:

``` bash
sudo docker compose up -d --no-deps prometheus
```

This avoided unnecessarily restarting the other telemetry services.

Immediately after deployment:

``` text
Image=prom/prometheus:v3.14.0
ImageID=sha256:31c1e0aacb3a1914563c4e9b1e8d0a55095bf433aa43c1b0fe695959742845bd
Status=running
RestartCount=0
```

This was the first point at which Prometheus 3.14.0 accessed the
existing persistent TSDB.

## TSDB startup validation

Prometheus 3.14.0 successfully opened the existing TSDB.

Startup logs showed:

-   Existing TSDB blocks were detected as healthy.
-   On-disk memory-mappable chunks were replayed.
-   The WAL checkpoint was loaded.
-   WAL segments 1265 through 1269 were loaded.
-   WAL replay completed successfully.
-   The TSDB started successfully.
-   The configured 30-day retention was applied.
-   `prometheus.yml` loaded successfully.
-   The server reported that it was ready to receive web requests.

The WAL replay completed in approximately 667 ms.

After startup, Prometheus also successfully performed normal write-side
TSDB operations:

-   Wrote a new block.
-   Performed head garbage collection.
-   Created a new WAL checkpoint.
-   Compacted existing blocks.
-   Deleted obsolete blocks produced by normal compaction.

This demonstrated that 3.14.0 could both read the existing TSDB and
continue normal writes and maintenance.

### GOMAXPROCS warning

Startup emitted:

``` text
Failed to set GOMAXPROCS automatically
open /sys/fs/cgroup/cpu/cpu.cfs_quota_us: no such file or directory
```

This occurred while Prometheus attempted automatic CPU-limit detection
in the Synology container environment. Prometheus continued startup
normally, the TSDB opened successfully, and no runtime failure was
observed. No configuration change was made solely for this warning.

## Scrape validation

After the upgrade, `promtool query instant` was used to query the `up`
metric.

Both configured targets returned `1`:

``` text
up{instance="localhost:9090", job="prometheus"} => 1
up{instance="unbound-exporter:9101", job="unbound"} => 1
```

This confirmed that Prometheus self-scraping and the Unbound exporter
scrape resumed successfully after the upgrade.

## Historical data continuity

Grafana's **Unbound DNS Performance** dashboard was inspected after the
upgrade.

A multi-day view showed historical Prometheus data from well before the
upgrade. A shorter view spanning the approximately 17:00 Hawaii-time
restart showed metric series continuing across the upgrade window and
fresh samples after restart.

Together with the successful TSDB block loading and WAL replay, this
verifies that:

-   Historical data written under Prometheus 3.12.0 remains queryable by
    3.14.0.
-   New metrics are being collected under 3.14.0.
-   Grafana can query the Prometheus data source successfully after the
    upgrade.

## Stability check

Final observed state:

``` text
Status=running
RestartCount=0
StartedAt=2026-09-21T03:00:06.405388279Z
```

The UTC start time corresponds to approximately 17:00 on 2026-09-20 in
Hawaii.

No restart loop or post-upgrade instability was observed during
validation.

## Rollback

During validation, `prom/prometheus:v3.12.0` was retained locally as the
rollback image.

If rollback is required before intentionally removing that image:

1.  Change the Compose image back to `prom/prometheus:v3.12.0`.
2.  Validate the Compose configuration.
3.  Recreate only Prometheus with `--no-deps`.
4.  Inspect startup logs and verify the scrape targets and Grafana
    metrics.

Because Prometheus 3.14.0 has already written to and compacted the
persistent TSDB, any future rollback should be treated as a controlled
operation rather than assuming that changing the image tag alone is
risk-free.

## Upgrade result

The upgrade from Prometheus 3.12.0 to 3.14.0 passed the planned
validation checks:

-   Candidate image identity and architecture verified.
-   Existing configuration validated using the candidate image before
    live deployment.
-   Existing TSDB verified readable before upgrade.
-   Compose configuration validated.
-   Only the Prometheus service was recreated.
-   Existing TSDB blocks loaded as healthy.
-   WAL replay completed successfully.
-   New TSDB writes, checkpoints, and compaction succeeded.
-   Both configured scrape targets returned `up = 1`.
-   Historical and post-upgrade metrics were visible in Grafana.
-   Container remained running with `RestartCount=0`.

Prometheus 3.14.0 is therefore the validated deployed version for this
stack.
