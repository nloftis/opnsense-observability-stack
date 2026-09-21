# Grafana Architecture and Upgrade Record --- 2026-09-20

## Purpose

Grafana provides the visualization and query interface for the OPNsense
observability stack. It queries Loki for log telemetry and Prometheus for
metrics over the private Docker network.

This document records the deployed architecture and the validated upgrade
from Grafana 13.0.2 to 13.2.2.

## Architecture

### Container

-   Image: `grafana/grafana:13.2.2`
-   Container name: `grafana`
-   Restart policy: `unless-stopped`
-   Image policy: `pull_policy: never`
-   Time zone: `Pacific/Honolulu`
-   Network: `telemetry-net`
-   Grafana is published to the Synology host on TCP port `3000`.
-   Grafana accesses Loki and Prometheus over `telemetry-net` using Docker
    DNS rather than host-published service ports.

### Persistent storage

Grafana state is persisted outside the container:

``` yaml
- ./grafana:/var/lib/grafana
```

The persistent directory contains the Grafana SQLite database and plugin
state, including:

``` text
grafana.db
plugins/
unified-search/
```

Because `/var/lib/grafana` is bind-mounted from `./grafana`, recreating or
upgrading the Grafana container does not intentionally remove dashboards,
data sources, users, preferences, or other persisted Grafana state.

### Configuration

The current deployment does not mount a custom `grafana.ini` or Grafana
provisioning configuration.

Relevant Compose environment settings are:

``` yaml
- TZ=Pacific/Honolulu
- GF_SECURITY_ADMIN_PASSWORD=${GF_ADMIN_PASSWORD}
- GF_ANALYTICS_REPORTING_ENABLED=false
```

Anonymous usage reporting is intentionally disabled.

### Data sources

Two data sources are configured in Grafana:

1.  `loki`
    -   UID: `cfdw8ptjsn7y8a`
    -   Type: Loki
    -   URL: `http://loki:3100`
    -   Access: proxy
    -   Default data source: yes
2.  `prometheus`
    -   UID: `bfochhiutxuyob`
    -   Type: Prometheus
    -   URL: `http://prometheus:9090`
    -   Access: proxy
    -   Default data source: no

Neither Loki nor Prometheus needs to publish its application port to the
Synology host for Grafana access.

## Pre-upgrade state

The previous deployed version was:

``` text
Grafana 13.0.2
commit: 3fcdbc5a
branch: release-13.0.2
```

Previous image:

``` text
grafana/grafana:13.0.2
Image ID:
sha256:072d3db0101e9fa5776fa9c454d8221994aeedf43e9abebe6652f42bf19bdc59
```

Before the upgrade the container was running with `RestartCount=0`.

### Installed plugins

Before the upgrade, the explicitly installed plugins were:

``` text
grafana-pyroscope-app @ 1.17.0
grafana-exploretraces-app @ 1.3.2
grafana-lokiexplore-app @ 1.0.36
grafana-metricsdrilldown-app @ 1.0.31
```

### Pre-upgrade log baseline

The pre-upgrade logs contained two transient session authentication
warnings:

``` text
Failed to authenticate request
[session.token.rotate] token needs to be rotated
```

Grafana otherwise remained functional. These warnings were recorded so
that the same message after the upgrade would not incorrectly be treated
as a new 13.2.2 regression.

## Upgrade candidate

Selected version:

``` text
Grafana 13.2.2
commit: 1bea008f7e4e858b6824c9e364d608bd4d10b13a
branch: release-13.2.2#patched
platform: linux/amd64
```

Pulled image:

``` text
grafana/grafana:13.2.2

Registry digest:
sha256:ac461fb352abc50da10a51c7d02462e9c05488f11f53f14b3ad79a8145f638a0

Local image ID:
sha256:747aaf291145d9a84a547f1fa4edf48697371db6ab386b81c95ad3a725c20f17
```

The candidate was initially pulled using the floating `13.2` tag. That tag
resolved to Grafana 13.2.2 and the same registry digest and local image ID.

Because Compose was intentionally changed to the exact
`grafana/grafana:13.2.2` tag and uses `pull_policy: never`, the exact
`13.2.2` tag was subsequently pulled before deployment.

This preserved the stack's pinned-image policy while confirming that the
evaluated `13.2` candidate and the exact `13.2.2` image were identical.

The old 13.0.2 image was deliberately retained during validation as a
rollback image.

## Database backup

Grafana uses SQLite for its persistent application database:

``` text
/var/lib/grafana/grafana.db
```

Before allowing Grafana 13.2.2 to access the database, only the Grafana
container was stopped.

No `grafana.db-wal` or `grafana.db-shm` files were present.

A quiescent backup was then created:

``` text
grafana/grafana.db.pre-13.2.2
```

The backup preserved the original file metadata and was verified
byte-for-byte against `grafana.db` with `cmp`.

Grafana 13.0.2 was restarted after the backup and returned to:

``` text
Status=running
RestartCount=0
```

This provided a verified pre-migration database copy before deployment of
13.2.2.

## Pre-deployment validation

The Compose source image was changed from:

``` text
grafana/grafana:13.0.2
```

to:

``` text
grafana/grafana:13.2.2
```

The deployment copy on Synology was inspected to confirm the exact pinned
tag.

The complete Compose configuration was then validated successfully on
Synology with:

``` bash
sudo docker compose config --quiet
```

No output indicated a valid Compose configuration.

Docker/Compose validation is performed on Synology because the Pop!_OS
Git source-of-truth workstation does not have Docker installed.

### Exact-tag pull behavior

The first recreation attempt failed safely because only the floating
`grafana/grafana:13.2` tag was locally available while Compose requested
`grafana/grafana:13.2.2` and `pull_policy: never` prevented an implicit
pull.

The existing container was inspected immediately afterward and remained:

``` text
Image=grafana/grafana:13.0.2
Status=running
RestartCount=0
```

The exact `grafana/grafana:13.2.2` tag was then pulled explicitly. Its
registry digest matched the previously evaluated candidate.

No database migration occurred during the failed recreation attempt.

## Deployment

Only Grafana was recreated:

``` bash
sudo docker compose up -d --no-deps grafana
```

This avoided unnecessarily restarting the other telemetry services.

Immediately after deployment:

``` text
Version 13.2.2
commit: 1bea008f7e4e858b6824c9e364d608bd4d10b13a
branch: release-13.2.2#patched

Image=grafana/grafana:13.2.2
ImageID=sha256:747aaf291145d9a84a547f1fa4edf48697371db6ab386b81c95ad3a725c20f17
Status=running
RestartCount=0
```

This was the first point at which Grafana 13.2.2 accessed the existing
persistent database.

## Database migration validation

Grafana 13.2.2 successfully opened the existing SQLite database.

Startup logs showed:

-   10 main database migrations completed successfully.
-   2 secret-store migrations completed successfully.
-   12 resource database migrations completed successfully.
-   The unified-storage short-URL migration completed successfully.
-   Access-control cleanup completed successfully with zero deprecated
    permissions removed.
-   Grafana initialized the resource database using SQLite.
-   The search index initialized successfully.
-   Grafana ultimately reported `All modules healthy`.
-   The HTTP server began listening normally on port `3000`.

No failed database migration was observed.

### SQLite permission warning

Startup emitted:

``` text
SQLite database file has broader permissions than it should
path=/var/lib/grafana/grafana.db
mode=-rwxrwxrwx
expected=-rw-r-----
```

This is not an upgrade failure. The database was opened and migrated
successfully, and Grafana remained operational.

The broad permissions are associated with the existing Synology
file-permission/ACL behavior and should be investigated separately rather
than changing permissions during upgrade validation.

## Plugin validation

The four explicitly installed pre-upgrade plugins remained present at
their original versions:

``` text
grafana-exploretraces-app @ 1.3.2
grafana-lokiexplore-app @ 1.0.36
grafana-metricsdrilldown-app @ 1.0.31
grafana-pyroscope-app @ 1.17.0
```

After the upgrade, Grafana also reported packaged/data-source plugins in
the persistent plugin state, including:

``` text
prometheus @ 13.1.9
stackdriver @ 12.6.2
tempo @ 13.2.1
elasticsearch @ 12.9.0
grafana-pyroscope-datasource @ 13.0.6
opentsdb @ 13.0.3
grafana-advisor-app @ 1.0.2
zipkin @ 12.4.8
```

Startup logs showed successful registration of Loki, Prometheus, the four
pre-existing application plugins, and the other packaged data-source
plugins.

No separate plugin upgrade was performed during this Grafana upgrade.

### Plugin HTTP warnings

While the browser loaded the Grafana UI, the logs emitted warnings similar
to:

``` text
Failed to write gzipped response
error="http: request method or response status code does not allow body"
```

These occurred for the four application plugin JavaScript modules.
The plugins had registered successfully and the Grafana UI and dashboards
continued to function. No configuration change was made solely for these
warnings.

## Data-source validation

The Grafana API showed that both existing data-source records survived the
database migration with the same UIDs, URLs, and default-data-source
settings.

### Loki

Grafana's data-source health endpoint returned:

``` json
{"message":"Data source successfully connected.","status":"OK"}
```

This verified the Grafana-to-Loki path over `telemetry-net`.

### Prometheus

Grafana's data-source health endpoint returned:

``` json
{
  "details": {
    "application": "Prometheus",
    "features": {
      "rulerApiEnabled": false
    }
  },
  "message": "Successfully queried the Prometheus API.",
  "status": "OK"
}
```

This verified that the Prometheus data-source plugin loaded the existing
configuration and could query Prometheus successfully.

## Dashboard and historical data continuity

### Prometheus-backed dashboard

Grafana's **Unbound DNS Performance** dashboard was inspected using a
six-hour window spanning the approximately 17:31 Hawaii-time Grafana
upgrade.

The dashboard showed:

-   Historical metrics from before the upgrade.
-   Continuous metric series across the upgrade window.
-   Fresh samples after the upgrade.
-   Normal panel rendering without data-source or plugin errors.

This verified historical and current Prometheus data continuity through
Grafana 13.2.2.

### Loki-backed dashboard

Grafana's **SOC Overview** dashboard was also inspected using a six-hour
window spanning the upgrade.

Multiple Loki-backed panels showed historical and current telemetry across
the upgrade boundary, including fresh data after the restart.

This verified that the Grafana-to-Loki path and normal Loki dashboard
rendering continued to function after the upgrade.

### Most Active External Attackers query

The **Most Active External Attackers** panel returned:

``` text
maximum number of series (500) reached for a single query
```

Grafana's Loki plugin logged the corresponding downstream HTTP 400
response.

Other Loki panels remained populated and the Loki data-source health check
returned `OK`, so this was identified as a panel-query/cardinality issue
rather than a Grafana or Loki connectivity failure.

The query should be reviewed separately to reduce its series cardinality,
for example by inspecting its existing stream selector and aggregation
before deciding on the appropriate correction.

## Post-upgrade log review

A post-upgrade warning/error review identified:

-   The known SQLite database permission warning.
-   Two startup warnings concerning alerting status sub-resources that do
    not support dual writing.
-   One HTTP 401 caused by an intentionally observed invalid password
    during API testing.
-   The same session-token rotation warning already observed before the
    upgrade.
-   Plugin gzip-response warnings while browser assets were requested.
-   Loki plugin errors corresponding to the known 500-series limit in the
    **Most Active External Attackers** panel.

Grafana nevertheless completed initialization, reported all modules
healthy, served the UI, successfully queried both data sources, and
rendered historical and current dashboard data.

No restart loop or general post-upgrade service failure was observed.

## Stability check

Final observed state:

``` text
Image=grafana/grafana:13.2.2
Status=running
RestartCount=0
StartedAt=2026-09-21T03:31:30.608486998Z
```

The UTC start time corresponds to approximately 17:31 on 2026-09-20 in
Hawaii.

## Deferred follow-up

The following items were deliberately kept outside the Grafana upgrade
itself:

1.  **Grafana SQLite permissions**
    -   Investigate and correct `grafana.db` being mode `777` rather than
        Grafana's expected `640`.
    -   Account for Synology ACL behavior and the source/deployment copy
        workflow before changing permissions.

2.  **SOC Overview — Most Active External Attackers**
    -   Inspect the existing Loki query.
    -   Reduce query cardinality so the panel does not exceed Loki's
        500-series limit.
    -   Do not choose an aggregation or selector change until the existing
        query has been reviewed.

## Rollback

During validation, `grafana/grafana:13.0.2` was retained locally as the
rollback image.

The verified pre-upgrade SQLite database is retained as:

``` text
grafana/grafana.db.pre-13.2.2
```

Because Grafana 13.2.2 has already migrated the live SQLite database,
rollback should **not** assume that changing the image tag back to 13.0.2
alone is safe.

A controlled rollback should:

1.  Stop Grafana.
2.  Preserve the current 13.2.2 database before replacing anything.
3.  Restore the verified `grafana.db.pre-13.2.2` database while Grafana is
    stopped.
4.  Change the Compose image back to `grafana/grafana:13.0.2`.
5.  Validate the Compose configuration on Synology.
6.  Recreate only Grafana with `--no-deps`.
7.  Verify startup logs, plugins, both data sources, and representative
    dashboards.

Plugin state under `./grafana/plugins` was also touched by the 13.2.2
startup process. A rollback should therefore be treated as a controlled
application-state rollback rather than assuming that restoring only the
container image is sufficient.

## Upgrade result

The upgrade from Grafana 13.0.2 to 13.2.2 passed the planned validation
checks:

-   Candidate image identity, digest, and architecture verified.
-   Exact pinned image tag made available before deployment.
-   Quiescent pre-upgrade SQLite backup created and verified byte-for-byte.
-   Compose configuration validated on Synology.
-   Only the Grafana service was recreated.
-   Existing SQLite database opened and migrations completed successfully.
-   Grafana reported all modules healthy.
-   Pre-existing application plugins remained installed.
-   Loki and Prometheus data-source records remained intact.
-   Loki data-source health returned `OK`.
-   Prometheus data-source health returned `OK`.
-   Historical and post-upgrade Prometheus metrics were visible in Grafana.
-   Historical and post-upgrade Loki telemetry was visible in Grafana.
-   Container remained running with `RestartCount=0`.

The SQLite permission warning and the Loki 500-series panel query are
tracked as separate follow-up work and do not indicate failure of the
Grafana 13.2.2 deployment.

Grafana 13.2.2 is therefore the validated deployed version for this
stack.
