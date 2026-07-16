# Telemetry Stack

**Synology DS224+ | Docker via Container Manager**

## Overview

This stack provides a log aggregation and visualization pipeline for OPNsense firewall logs, plus metrics collection for DNS performance via a custom Prometheus exporter. It replaces the earlier syslog-ng + Promtail architecture with Grafana Alloy as a single unified collector.

## Architecture

```
OPNsense (syslog UDP/TCP 514) --> Alloy --> Loki --> Grafana
OPNsense API (HTTPS 443)      --> unbound-exporter --> Prometheus --> Grafana
```

**Components:**

| Component | Role |
|---|---|
| Grafana Alloy | Receives syslog from OPNsense, classifies, forwards to Loki |
| Grafana Loki | Log storage and indexing (30-day retention) |
| Prometheus | Metrics storage and scraping (30-day retention) |
| unbound-exporter | Custom Python exporter — scrapes OPNsense DNS stats API, exposes cache hit rate and DNS performance metrics |
| Grafana | Visualization and SOC-style dashboards |

**Two data paths:**

- **Logs** (syslog): firewall events, DHCP, DNS query logs → Loki
- **Metrics** (HTTP): DNS performance stats → Prometheus

**Why Prometheus for DNS stats?**

The OPNsense GUI resets DNS counters (cachehits, cachemiss, queries) on every DNS service restart. The DNS service restarts on every NIC link flap via `rc.linkup -> unbound_configure_do()`, which occurs on PC suspend/wake. Prometheus scrapes every 60 seconds and preserves history across restarts. `rate()` handles counter resets correctly, giving a continuous, meaningful cache hit rate over time that the GUI cannot provide.

## Directory Structure

```
/volume1/docker/telemetry/
  docker-compose.yml
  alloy/
    config.alloy          # Alloy pipeline configuration
    data/                 # Alloy persistent state
    GeoLite2-City.mmdb    # MaxMind GeoIP database (download separately)
  loki/
    config.yml            # Loki configuration
    data/                 # Loki log storage
      chunks/
      compactor/
      rules/
      tsdb-shipper-cache/
  prometheus/
    prometheus.yml        # Prometheus scrape configuration
    data/                 # Prometheus metrics storage
  unbound-exporter/
    Dockerfile            # Builds local Python exporter image
    unbound_exporter.py   # Exporter script
  grafana/                # Grafana persistent state
```

## Ports

| Port | Purpose |
|---|---|
| 514/UDP | OPNsense syslog ingestion (Alloy) |
| 514/TCP | OPNsense syslog ingestion (Alloy) |
| 3000/TCP | Grafana UI |
| 3100/TCP | Loki API |
| 9090/TCP | Prometheus UI |
| 9101/TCP | unbound-exporter metrics endpoint |
| 12345/TCP | Alloy debug UI |

## Access

| Service | URL |
|---|---|
| Grafana | http://192.168.20.10:3000 |
| Loki API | http://192.168.20.10:3100 |
| Prometheus UI | http://192.168.20.10:9090 |
| unbound-exporter | http://192.168.20.10:9101/metrics |
| Alloy UI | http://192.168.20.10:12345 |

## Versions

Pinned image versions (last verified 2026-06-06):

| Image | Version |
|---|---|
| grafana/alloy | v1.16.2 |
| grafana/loki | 3.0.0 |
| grafana/grafana | 13.0.2 |
| prom/prometheus | v3.12.0 |
| unbound-exporter | locally built from `./unbound-exporter/Dockerfile` |

unbound-exporter base image: `python:3.14-slim`
Libraries: `prometheus_client==0.21.1`, `requests==2.32.3`

**Access credentials:**
- Grafana admin password: see password manager
- OPNsense API key/secret: see password manager (nloftis user API key), stored in `docker-compose.yml` environment variables

**Note on `pull_policy: never`:**
All pre-built image containers use `pull_policy: never` to prevent automatic image updates. Container Manager's "Build" function will fail on first run if the image is not already cached locally. Always use the CLI:

```bash
cd /volume1/docker/telemetry && sudo docker compose up -d
```

To intentionally upgrade an image, temporarily remove `pull_policy: never` for that container, pull, then restore the setting.

**Note on unbound-exporter (locally built):**
`pull_policy: never` does not apply to locally built images. After any change to `unbound_exporter.py` or `Dockerfile`:

```bash
sudo docker compose build unbound-exporter
sudo docker compose up -d unbound-exporter
```

## OPNsense Configuration

**System → Settings → Logging → Remote**

**Entry 1 (General/Firewall/DHCP):**

| Field | Value |
|---|---|
| Transport | TCP(4) |
| Applications | Nothing selected (all) |
| Levels | Info and above |
| Hostname | 192.168.20.10 |
| Port | 514 |
| RFC5424 | Checked |
| Description | Telemetry |

> **Note:** Transport was changed from UDP(4) to TCP(4) for more reliable log delivery. TCP provides guaranteed delivery and is preferred over UDP for syslog forwarding where log loss is unacceptable.

> **Note:** RFC5424 MUST be checked. Without it, Alloy cannot parse the syslog stream and will log `"expecting a version value"` errors continuously.

**OPNsense API (required by unbound-exporter):**

- System → Access → Users → nloftis → API key (ticket icon in Commands)
- Firewall rule required: Allow NAS net → 192.168.1.1 HTTPS (443)
  - Added to Firewall → Rules → NAS
  - Description: `Allow (NAS) -> OPNsense API`
- Services → Unbound DNS → Advanced → Extended Statistics: Checked

## Log Classification

Alloy classifies incoming logs into five types via label `log_type`:

| Type | Description | Pattern |
|---|---|---|
| `firewall` | OPNsense filterlog entries (pass/block events) | `,igc[0-9]+,match,(pass\|block),` |
| `dhcp` | DHCP transaction entries (REQUEST, ACK, OFFER, etc.) | `DHCP(REQUEST\|ACK\|OFFER\|DISCOVER\|RELEASE\|INFORM\|NAK)` |
| `dns` | AdGuard Home query log forwarded via OPNsense syslog-ng | JSON containing `QH`, `QT`, `IP`, `T` keys |
| `syslog_stats` | syslog-ng internal pipeline statistics (every ~10 minutes) | `Log statistics;` |
| `general` | Everything else (cron jobs, system events, etc.) | — |

> **Note:** The DNS resolver and AdGuard Home logs are NOT forwarded via OPNsense remote syslog directly. AdGuard query logs are routed through OPNsense's internal syslog-ng configuration and arrive as JSON payloads.

**Example LogQL queries:**

```logql
{job="opnsense", log_type="firewall"}
{job="opnsense", log_type="dhcp"}
{job="opnsense", log_type="dns"}
{job="opnsense", log_type="syslog_stats"}
{job="opnsense", log_type="general"}
```

## Alloy Pipeline Design

Alloy runs two parallel processing pipelines for every incoming log:

- **classify**: Full classification and label enrichment pipeline. All logs are typed, firewall logs get full label extraction (`iface`, `action`, `direction`, `src_ip`, `dst_ip`, `dst_port`, `proto`, `src_zone`, `dst_zone`). Written to the main Loki stream.
- **wan_attackers**: Dedicated low-cardinality stream for the SOC attacker dashboard panels. Processes only inbound WAN blocks on igc0 (TCP/UDP/ICMP). Extracts `src_ip`, `src_net` (/24 subnet), and `dst_port` with minimal labels to avoid Loki series limits.

**Why two pipelines?**

Loki enforces a hard limit of 500 unique series per query. Querying the full classify stream for attacker data (grouped by `src_ip`) causes this limit to be exceeded at longer time ranges (1h+) when 500+ unique attacker IPs appear. The `wan_attackers` stream solves this by:

- Using `src_net` (/24 subnet) instead of `src_ip` for long-range aggregation
- Keeping label cardinality minimal by design
- Allowing short-range queries to use `src_ip` and long-range to use `src_net`

### Alloy Label Design — Cardinality Notes

The following fields are intentionally NOT promoted to Loki labels in the classify pipeline, despite being extracted by the regex:

| Field | Reason for exclusion |
|---|---|
| `src_port` | Ephemeral source ports (range 1024–65535) create extreme label cardinality with no dashboard query value. Extracted by regex (required to reach `dst_port` by field position) but discarded. |
| `proto_id` | Numeric protocol ID (6=TCP, 17=UDP, 1=ICMP). Redundant with the human-readable `proto` label. Discarded after extraction. |

### WAN Attackers Stream — Label Reference

Labels written by the `wan_attackers` pipeline:

| Label | Value |
|---|---|
| `view` | `"wan_attackers"` (static — stream identifier) |
| `log_type` | `"firewall"` (static) |
| `action` | `"block"` (static) |
| `direction` | `"in"` (static) |
| `src_zone` | `"wan"` (static) |
| `src_ip` | attacker IP (dynamic — for short-range queries ≤15m) |
| `src_net` | x.x.x.0/24 (dynamic — for long-range queries 1h–24h) |
| `dst_port` | port number (dynamic — TCP/UDP only, absent for ICMP) |

**Dashboard query patterns:**

Short range (≤15m) — top attacker IPs:
```logql
topk(15, sum by (src_ip)(count_over_time({view="wan_attackers"}[$__range])))
```

Long range (1h–24h) — top attacker networks:
```logql
topk(15, sum by (src_net)(count_over_time({view="wan_attackers"}[$__range])))
```

Most probed ports (TCP/UDP only, any range):
```logql
topk(10, sum by (dst_port)(count_over_time({view="wan_attackers", dst_port=~".+"}[$__range])))
```

ICMP sweep activity (time series):
```logql
sum(rate({view="wan_attackers"} | dst_port="" [$__interval]))
```

### Important — Alloy Pipeline History Behavior

Alloy pipelines do NOT rewrite history. When the pipeline is updated:

- Logs already stored in Loki retain their original labels
- New logs follow the updated labeling from the restart point forward
- Dashboard panels may show mixed behavior until the time window fills with newly labeled data (up to 30 days for full retention window)

After any `config.alloy` change, always verify with:

```logql
{view="wan_attackers"} | line_format "src={{.src_ip}} net={{.src_net}} dst_port={{.dst_port}}"
```

## Prometheus

Prometheus scrapes metrics from unbound-exporter and stores them for 30 days.

- Configuration: `/volume1/docker/telemetry/prometheus/prometheus.yml`
- Data: `/volume1/docker/telemetry/prometheus/data/`

**Current scrape targets:**

| Target | Endpoint | Metrics |
|---|---|---|
| prometheus (self) | `localhost:9090` | Prometheus health metrics |
| unbound | `unbound-exporter:9101` | DNS performance metrics |

- Scrape interval: 60s
- Retention: 30 days

To verify scrape targets are UP: http://192.168.20.10:9090/targets

## Unbound-Exporter — Custom Prometheus Exporter

### Background — Why This Exists

The goal was to track DNS cache hit rate over time to measure the impact of configuration changes (suspend/wake behavior, TTL settings, cache tuning). The OPNsense GUI shows these stats but resets them on every DNS service restart, making long-term trending impossible via the GUI alone.

Prometheus was chosen as the metrics store because:

- It scrapes and preserves history every 60 seconds regardless of restarts
- The `rate()` function handles counter resets correctly and automatically
- Cache hit rate = `rate(hits[5m]) / rate(queries[5m])` survives restarts

### Alternatives Considered and Rejected

**Option 1 — unbound-control stats (CLI tool)**
`unbound-control` connects to Unbound's control socket on port 8953. Rejected: OPNsense does not expose the control-enable setting in the GUI and manages Unbound's config programmatically. Enabling it via CLI is fragile — OPNsense overwrites changes on config sync or service restart.

**Option 2 — opnsense-exporter (AthennaMind, `ghcr.io/athennamind/opnsense-exporter`)**
A community-built Prometheus exporter for OPNsense that runs as a Docker container. Evaluated and confirmed working for firewall packet counters, service status, and gateway metrics. Rejected for this use case: as of v0.0.14, the exporter calls `api/unbound/diagnostics/stats` successfully (zero endpoint errors) but only exposes `opnsense_unbound_dns_uptime_seconds` from that response. Cache hits, misses, queries, and recursion time are not implemented.

**Option 3 — Alloy JSON-to-metrics conversion**
Alloy's `remote.http` component can fetch JSON but has no native transformer to convert arbitrary JSON to Prometheus metrics format. No suitable built-in component exists for this use case.

**Option 4 — Cron job shipping stats via syslog (chosen against)**
A cron job on OPNsense could run the stats API call and ship output via syslog to Alloy, landing in Loki as a new `log_type`. Rejected: shoehorning metrics into a log store is architecturally wrong. Metrics belong in Prometheus, logs belong in Loki.

**Solution chosen — Custom Python exporter**
A small Python script (~70 lines) that calls the OPNsense API directly, parses the JSON response, and serves the metrics in Prometheus exposition format on port 9101. Runs as a locally-built Docker container in this stack. Prometheus scrapes it on the standard 60-second interval.

### Script Architecture

The script has three responsibilities:

1. **HTTP server** — `prometheus_client.start_http_server(9101)` starts a background thread serving `/metrics` on port 9101. This runs continuously and is what Prometheus scrapes.
2. **Metric definitions** — Counters and Gauges are defined at module level using the `prometheus_client` library. Counters are for cumulative values that reset on service restart (queries, hits, misses). Gauges are for point-in-time values (recursion time, cache sizes, queue depth).
   - Counter vs Gauge distinction is critical:
     - **Counters:** `prometheus_client` tracks resets automatically. Use `rate()` in PromQL — never graph raw counter values.
     - **Gauges:** point-in-time values, graph directly.
   - Counter manipulation note: `prometheus_client` Counters normally only increment. Since the OPNsense API returns absolute cumulative values (not deltas), and these reset to zero on service restart, we use `counter._value.set()` to force-set the internal value directly. This is intentional — Prometheus detects the reset (value going down) and `rate()` handles it correctly.
3. **Collect loop** — runs every 60 seconds, calls the API, parses the JSON, updates all metric values. Errors are logged but don't crash the exporter — it retries on the next cycle.

### API Response Structure — Critical JSON Path Notes

The OPNsense `api/unbound/diagnostics/stats` endpoint returns a nested JSON structure with per-thread data plus a pre-aggregated total. The script uses `data.total` for all counter metrics (pre-aggregated across all 4 threads).

Non-obvious path: `answer.rcode` fields (SERVFAIL, NXDOMAIN) are NOT under `data.total.num` — they are under `data.num` at the top level of the response, separate from the per-thread and total structures.

**Verified paths:**

| JSON Path | Metric |
|---|---|
| `data.total.num.queries` | `unbound_queries_total` |
| `data.total.num.cachehits` | `unbound_cache_hits_total` |
| `data.total.num.cachemiss` | `unbound_cache_misses_total` |
| `data.total.num.prefetch` | `unbound_prefetch_total` |
| `data.total.num.expired` | `unbound_expired_total` |
| `data.total.requestlist.exceeded` | `unbound_requestlist_exceeded_total` |
| `data.total.recursion.time.avg` | `unbound_recursion_time_avg_seconds` |
| `data.total.recursion.time.median` | `unbound_recursion_time_median_seconds` |
| `data.total.requestlist.avg` | `unbound_requestlist_avg` |
| `data.total.requestlist.max` | `unbound_requestlist_max` |
| `data.msg.cache.count` | `unbound_msg_cache_count` |
| `data.rrset.cache.count` | `unbound_rrset_cache_count` |
| `data.num.answer.rcode.SERVFAIL` | `unbound_answers_servfail_total` |
| `data.num.answer.rcode.NXDOMAIN` | `unbound_answers_nxdomain_total` |

### Source Files

`/volume1/docker/telemetry/unbound-exporter/`

- `Dockerfile` — FROM `python:3.14-slim`, installs `prometheus_client` and `requests`, copies script, exposes port 9101
- `unbound_exporter.py` — Main script: API call, metric definitions, collect loop, HTTP server startup

### Metrics Exposed

**Counters** (reset on DNS service restart — always use `rate()` in PromQL):

- `unbound_queries_total` — Total DNS queries
- `unbound_cache_hits_total` — Total cache hits
- `unbound_cache_misses_total` — Total cache misses
- `unbound_prefetch_total` — Total prefetch operations
- `unbound_expired_total` — Total expired record serves
- `unbound_answers_servfail_total` — Total SERVFAIL answers
- `unbound_answers_nxdomain_total` — Total NXDOMAIN answers
- `unbound_requestlist_exceeded_total` — Total times request list exceeded

**Gauges** (point-in-time — graph directly):

- `unbound_recursion_time_avg_seconds` — Average cold lookup latency
- `unbound_recursion_time_median_seconds` — Median cold lookup latency
- `unbound_requestlist_avg` — Average request queue depth
- `unbound_requestlist_max` — Maximum request queue depth
- `unbound_msg_cache_count` — Message cache entry count
- `unbound_rrset_cache_count` — RRset cache entry count

### Key PromQL Queries

Cache hit rate (counter-reset safe, 5-minute window):
```promql
rate(unbound_cache_hits_total[5m]) / rate(unbound_queries_total[5m])
```

Cache hit rate (15-minute window, smoother trend):
```promql
rate(unbound_cache_hits_total[15m]) / rate(unbound_queries_total[15m])
```

Query rate:
```promql
rate(unbound_queries_total[5m])
```

### Operational Commands

Build image (required on first deploy and after any file changes):
```bash
sudo docker compose build unbound-exporter
```

Start/restart:
```bash
sudo docker compose up -d unbound-exporter
```

Verify metrics are flowing:
```bash
curl -s http://192.168.20.10:9101/metrics | grep "^unbound"
```

Check logs:
```bash
sudo docker logs unbound-exporter --tail=20
```

Expected log output (healthy):
```
INFO Unbound exporter started on port 9101
INFO Scraped: queries=XXXX hits=XXX misses=XXXX
```

## SOC Dashboard — Panel Reference

Dashboard: **SOC Overview** | Datasource: **Loki**

**Row 1 — Pipeline Health**

- **Telemetry Ingestion Rate** — Total log ingestion rate from OPNsense. Heartbeat panel — if flat, no data flows.
  ```logql
  sum(rate({job="opnsense"}[$__interval]))
  ```

**Row 2 — External Threats (time series)**

- **Total Inbound Block Rate** — All inbound WAN blocks over time (blocks/sec)
- **Inbound ICMP Sweep Activity** — ICMP-only inbound blocks over time (blocks/sec)

**Row 3 — External Threats (tables)**

- **Most Active External Attackers** — Top attacker /24 networks by block count
- **Most Probed Ports** — Top destination ports probed (TCP/UDP only)

**Row 4 — Internal Boundary**

- **Internal Trust Boundary Activity (Zone → Zone)** — Zone-to-zone pass traffic (pc/lan/nas)
- **Internal Trust Boundary – Port Breakdown** — Zone-to-zone breakdown by protocol and port

**Row 5 — Outbound Internet**

- **Outbound Internet Activity by Zone** — Outbound WAN pass traffic by source zone
- **Outbound Internet – Port Breakdown** — Outbound WAN breakdown by protocol and port

**Row 6 — Network Visibility (DHCP)**

- **DHCP Lease Activity** — Timestamped table of recent DHCP ACK events. Shows Time, IP Address, MAC Address, Interface. Answers: what devices connected and when?
  - Visualization: Table (Range query)
  - Query:
    ```logql
    {job="opnsense", log_type="dhcp"}
    | regexp
    | dhcp_type = "DHCPACK"
    | line_format JSON (ip, mac, iface fields)
    ```
  - Transformations: Extract fields (Line, JSON, Replace all fields ON, Keep time ON), Organize fields by name

- **Top Active DHCP Clients** — Ranked table of devices by DHCP request count. Shows MAC Address, Request Count. Answers: which devices are most chatty on DHCP?
  - Visualization: Table (Instant query)
  - Query:
    ```logql
    topk(10, sum by (mac_address)(
      count_over_time(
        {job="opnsense", log_type="dhcp"}
        | regexp mac_address
        [$__range]
      )
    ))
    ```
  - Transformations: Organize fields by name

**Row 7 — Network Visibility (DNS)**

- **Top Queried Domains** — Ranked table of most queried domain names. Shows Domain, Query Count. Answers: what are devices trying to reach?
  - Visualization: Table (Instant query)
  - Query:
    ```logql
    topk(15, sum by (QH)(
      count_over_time(
        {job="opnsense", log_type="dns"}
        | json
        [$__range]
      )
    ))
    ```
  - Transformations: Organize fields by name
  - Note: `stats.grafana.org` will dominate — expected Alloy reporting noise blocked by AdGuard and retried repeatedly.

- **Top DNS Clients** — Ranked table of devices by DNS query volume. Shows Client IP, Query Count. Answers: which devices are most active on DNS?
  - Visualization: Table (Instant query)
  - Query:
    ```logql
    topk(10, sum by (client_ip)(
      count_over_time(
        {job="opnsense", log_type="dns"}
        [$__range]
      )
    ))
    ```
  - Note: `client_ip` is an indexed label — no JSON parsing needed, cheaper than Top Queried Domains.
  - Transformations: Organize fields by name

**Column naming conventions:**

| Column | Meaning |
|---|---|
| Block Count | Count of firewall block events |
| Connections | Count of firewall pass events |
| Attacker Network | `src_net` label (/24 subnet of attacker source IP) |
| Query Count | Count of DNS queries |
| Request Count | Count of DHCP requests |

## DNS Performance Dashboard — Panel Reference

Dashboard: **DNS Performance** | Datasource: **Prometheus**

**Cache Hit Rate (time series)**
- What: Rolling cache hit rate over time — both 5min and 15min windows
- Why: Track cache performance trends; 15min line shows trend, 5min line shows reactive detail
- Visual: Time series, Percent (0.0–1.0)
- Legend: Hit Rate (5 min), Hit Rate (15 min)
- Query A: `rate(unbound_cache_hits_total[5m]) / rate(unbound_queries_total[5m])`
- Query B: `rate(unbound_cache_hits_total[15m]) / rate(unbound_queries_total[15m])`

**Cache Hit Rate (Current)**
- What: Current 5-minute cache hit rate as a single stat
- Why: At-a-glance current performance
- Visual: Stat panel, Percent (0.0–1.0)
- Query: `rate(unbound_cache_hits_total[5m]) / rate(unbound_queries_total[5m])`

**Total Query Rate**
- What: DNS queries per second over time
- Why: Understand query volume; correlate bursts with hit rate drops
- Visual: Time series
- Legend: Queries/sec
- Query: `rate(unbound_queries_total[5m])`

**Recursion Time**
- What: Average and median cold lookup latency
- Why: Upstream health indicator; gap between avg and median reveals outlier slow queries pulling the average up
- Visual: Time series, seconds
- Legend: Average, Median
- Query A: `unbound_recursion_time_avg_seconds`
- Query B: `unbound_recursion_time_median_seconds`

## Permission Setup — Important Notes for Synology

Synology uses ACL-based filesystem permissions that override standard Unix `chown` in some cases. Each container runs as its own internal user and requires the data directories to be pre-created and owned correctly BEFORE startup. Both `chown` AND `chmod` are required — `chown` alone is insufficient due to ACLs.

To determine what user a container runs as:
```bash
sudo docker run --rm --entrypoint sh <image> -c 'id'
```

**Loki** (runs as uid=10001, gid=10001):
```bash
sudo mkdir -p /volume1/docker/telemetry/loki/data/{rules,compactor,chunks}
sudo mkdir -p /volume1/docker/telemetry/loki/data/tsdb-shipper-cache
sudo chown -R 10001:10001 /volume1/docker/telemetry/loki/data
sudo chmod -R 775 /volume1/docker/telemetry/loki/data
```

**Grafana** (runs as uid=472, gid=0):
```bash
sudo chown -R 472:0 /volume1/docker/telemetry/grafana
sudo chmod -R 775 /volume1/docker/telemetry/grafana
```

**Prometheus** (runs as uid=65534, gid=65534 — nobody):
```bash
sudo mkdir -p /volume1/docker/telemetry/prometheus/data
sudo chown -R 65534:65534 /volume1/docker/telemetry/prometheus/data
sudo chmod -R 775 /volume1/docker/telemetry/prometheus/data
```

**Alloy** (runs as its default internal user, no override needed): No special permissions required. The `alloy/data` directory is created by synadmin and Alloy can write to it without modification.

**unbound-exporter** (locally built, no persistent storage): No permissions setup required.

**Why not `user: root`?**
Using `user: root` in `docker-compose.yml` works but is a security antipattern. The correct approach is to pre-create directories with the right ownership so each container runs as its intended non-root user.

**Why not PUID/PGID environment variables?**
PUID/PGID only work with images built on the LinuxServer.io base image (which includes an init script to create and switch to the specified user). Official Grafana and Loki images do not support PUID/PGID and will ignore these environment variables entirely.

The correct `docker-compose.yml` approach for Loki, Grafana, and Prometheus:
```yaml
user: "10001:10001"   # for Loki
user: "472:0"         # for Grafana
user: "65534:65534"   # for Prometheus
```

## Alloy Reporting

Alloy attempts to send telemetry/usage reports to `stats.grafana.org`. This domain is blocked by AdGuard Home (OISD Blocklist Big) on this network, causing repeated DNS failure messages in the Alloy logs.

The `--disable-reporting` flag is set in `docker-compose.yml` to suppress this.

> **Note:** `stats.grafana.org` still dominates the Top Queried Domains panel because AdGuard logs the blocked lookup attempt. This is expected noise.

## GeoIP Enrichment (Future)

The `GeoLite2-City.mmdb` file from MaxMind can be used with Alloy's `stage.geoip` to add city, country, and coordinates to firewall log entries.

**Requirements:**

- Free MaxMind account at maxmind.com
- Download `GeoLite2-City.mmdb` and place in `/volume1/docker/telemetry/alloy/`
- Mount the file into the Alloy container via `docker-compose.yml`
- Add `stage.geoip` to the `wan_attackers` pipeline in `config.alloy`
- Set up a monthly cron job to refresh the database

The MaxMind database updates monthly. Check OPNsense System → Settings → Cron to see if an existing update job can be leveraged or adapted.

Natural integration point: the `wan_attackers` pipeline already extracts `src_ip` which is the field `stage.geoip` requires. Country and city labels added there would enrich both the attacker table and the ICMP sweep panels.

## Container Manager

This project is managed via Synology Container Manager → Project.

- Project name: `telemetry`
- Path: `/volume1/docker/telemetry`

> **IMPORTANT:** Always manage this stack via CLI, not Container Manager UI. `pull_policy: never` is set on pre-built image containers. Container Manager's Build function will fail if images are not already cached locally.

To bring up the stack:
```bash
cd /volume1/docker/telemetry
sudo docker compose up -d
```

To rebuild unbound-exporter after code changes:
```bash
sudo docker compose build unbound-exporter
sudo docker compose up -d unbound-exporter
```

To restart individual containers:
```bash
sudo docker compose restart alloy
sudo docker compose restart loki
sudo docker compose restart grafana
sudo docker compose restart prometheus
sudo docker compose restart unbound-exporter
```

To check container status:
```bash
sudo docker compose ps
```

To check logs:
```bash
sudo docker logs alloy --tail=20
sudo docker logs loki --tail=20
sudo docker logs grafana --tail=20
sudo docker logs prometheus --tail=20
sudo docker logs unbound-exporter --tail=20
```

## Grafana Data Sources

| Name | URL | Default |
|---|---|---|
| loki | `http://loki:3100` | Yes |
| prometheus | `http://prometheus:9090` | No |

> **Note:** Use container names (not IP addresses) so Grafana resolves via Docker's internal DNS on the `loki-net` bridge network.

> **Note:** Prometheus datasource must be added manually via Connections → Data sources → Add → Prometheus.

## Loki Series Limit

Loki enforces a default limit of 500 unique series per query (`max_query_series_limit`). This limit is hit when grouping by high-cardinality labels like `src_ip` over long time ranges.

The `wan_attackers` pipeline was specifically designed to work within this limit:

- Short time ranges (≤15m): group by `src_ip` (individual attacker IPs)
- Long time ranges (1h–24h): group by `src_net` (/24 aggregation)

If the series limit is hit on other queries, consider raising the limit in `loki/config.yml` under `limits_config`:

```yaml
max_query_series_limit: 1000
```
