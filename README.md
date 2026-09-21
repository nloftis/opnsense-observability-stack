# Telemetry Stack

**Synology DS224+ | Docker via Container Manager**

## Overview

This stack provides a log aggregation and visualization pipeline for OPNsense firewall logs, plus metrics collection for DNS performance via a custom Prometheus exporter. It replaces the earlier syslog-ng + Promtail architecture with Grafana Alloy as a single unified collector.

## Architecture

```
OPNsense (syslog TCP 514) --> Alloy --> Loki --> Grafana
OPNsense API (HTTPS 443)  --> unbound-exporter --> Prometheus --> Grafana
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

The Git repository on Pop!_OS is the source of truth for configuration and application source files. Mutable container data exists only on the Synology deployment and is not copied back into the Git repository.

### Git Repository (Pop!_OS)

```text
telemetry/
├── alloy/
│   └── config.alloy
├── loki/
│   └── config.yml
├── prometheus/
│   └── prometheus.yml
├── unbound-exporter/
│   ├── Dockerfile
│   └── unbound_exporter.py
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

A local `.env` may also exist for deployment purposes but is excluded from Git.

### Synology Deployment

```text
/volume1/docker/telemetry/
├── alloy/
│   ├── config.alloy
│   └── data/
├── loki/
│   ├── config.yml
│   └── data/
├── prometheus/
│   ├── prometheus.yml
│   └── data/
├── unbound-exporter/
│   ├── Dockerfile
│   └── unbound_exporter.py
├── grafana/
├── docker-compose.yml
└── .env
```

Persistent runtime data:

- `alloy/data/` — Alloy state
- `loki/data/` — Loki log storage and indexes
- `prometheus/data/` — Prometheus TSDB
- `grafana/` — Grafana database, dashboards, plugins, and state

These runtime directories are intentionally excluded from Git. They may be protected through NAS backup/snapshot mechanisms, but should not be copied back into the source repository as part of normal deployment.

## Ports

Only services that require host access are published by Docker.

| Port | Exposure | Purpose |
|---|---|---|
| 514/TCP | Host-published | OPNsense RFC5424 syslog ingestion by Alloy |
| 3000/TCP | Host-published | Grafana UI |
| 12345/TCP | Host-published | Alloy HTTP/debug UI |
| 3100/TCP | Internal only | Loki API, reached by Alloy/Grafana over `telemetry-net` |
| 9090/TCP | Internal only | Prometheus, reached by Grafana over `telemetry-net` |
| 9101/TCP | Internal only | unbound-exporter metrics, reached by Prometheus over `telemetry-net` |

UDP/514 is not used. OPNsense sends syslog to Alloy over TCP.

## Access

| Service | Access |
|---|---|
| Grafana | `http://192.168.20.10:3000` |
| Alloy UI | `http://192.168.20.10:12345` |
| Loki | Internal Docker DNS: `http://loki:3100` |
| Prometheus | Internal Docker DNS: `http://prometheus:9090` |
| unbound-exporter | Internal Docker DNS: `http://unbound-exporter:9101` |

Loki, Prometheus, and unbound-exporter are intentionally not published to the Synology host. Their consumers use Docker DNS on `telemetry-net`.

## Versions

Pinned image versions (last verified 2026-09-21):

| Image | Version |
|---|---|
| grafana/alloy | v1.19.2 |
| grafana/loki | 3.0.0 |
| grafana/grafana | 13.0.2 |
| prom/prometheus | v3.12.0 |
| unbound-exporter | locally built from `./unbound-exporter/Dockerfile` |

unbound-exporter base image: `python:3.14-slim`
Libraries: `prometheus_client==0.26.0`, `requests==2.34.2`

**Access credentials:**
- Grafana admin password: see password manager
- OPNsense API key/secret: see password manager; stored in `.env` on the deployment host and referenced by Compose. `.env` is excluded from Git.

**Note on `pull_policy: never`:**
All pre-built image containers use `pull_policy: never` to prevent automatic image updates. Container Manager's "Build" function will fail on first run if the image is not already cached locally. Always use the CLI:

```bash
cd /volume1/docker/telemetry && sudo docker compose up -d
```

To intentionally upgrade an image, explicitly pull the desired pinned version with `docker pull`, verify the downloaded image, update the image version in
`docker-compose.yml`, validate the Compose configuration, and recreate only the affected service. `pull_policy: never` can remain enabled because the required
image has already been cached locally.

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
| Description | Alloy Telemetry |

> **Note:** Transport was changed from UDP(4) to TCP(4) for more reliable log delivery. TCP provides guaranteed delivery and is preferred over UDP for syslog forwarding where log loss is unacceptable.

> **Note:** RFC5424 MUST be checked. Without it, Alloy cannot parse the syslog stream and will log `"expecting a version value"` errors continuously.

**OPNsense API (required by unbound-exporter):**

- System → Access → Users → nloftis → API key (ticket icon in Commands)
- OPNsense firewall rule required: allow the NAS/telemetry path to the OPNsense API on HTTPS (443)
- Synology DSM firewall must allow Docker subnet `172.24.0.0/16` so telemetry containers can reach external destinations, including the OPNsense API
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
| `src_net` | x.x.x.0/24 (dynamic — attacker-network aggregation) |
| `dst_port` | port number (dynamic — TCP/UDP only, absent for ICMP) |

> **Note:** The full attacker `src_ip` is retained in the raw firewall log but is
> intentionally not promoted to a Loki label. Per-IP cardinality can exceed
> Loki's series query limit over longer time ranges. Individual source IPs can
> be extracted at query time when needed.

**Dashboard query patterns:**

Short range (≤15m) — top attacker IPs (query-time extraction):
```logql
topk(
  15,
  sum by (source_ip) (
    count_over_time(
      {view="wan_attackers"}
      | regexp ".*,in,.*?,(?P<proto_id>6|17),(?P<proto>tcp|udp),(?P<length>[0-9]+),(?P<source_ip>(?:[0-9]{1,3}\\.){3}[0-9]{1,3}),.*"
      [15m]
    )
  )
)
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

Prometheus is not host-published. To verify that metrics are being ingested, query Prometheus from inside the container:

```bash
sudo docker exec prometheus \
  wget -qO- 'http://localhost:9090/api/v1/query?query=unbound_queries_total'
```

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
```
sudo docker exec unbound-exporter \
  wget -qO- http://localhost:9101/metrics | grep "^unbound"
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
  - Historical note: `stats.grafana.org` may dominate older ranges due to Grafana usage-report retries that were blocked by AdGuard. Grafana reporting is now disabled in Compose.

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

Synology uses ACL-backed filesystem permissions, so the Unix mode shown by `ls -l` does not always tell the whole story. The important test is whether the user running inside each container can actually write to its persistent data directory.

Effective container users verified during the 2026-09 restore:

| Container | Effective user | Persistent data |
|---|---|---|
| Loki | `10001:10001` | `loki/data/` |
| Prometheus | `65534:65534` | `prometheus/data/` |
| Grafana | `472:0` | `grafana/` |
| Alloy | `root` | `alloy/data/` |
| unbound-exporter | `65534:65534` | None |

After the DSM restore, Loki, Prometheus, and Grafana retained the expected ownership but their data directories were not writable by their container users. Granting the owner write permission was sufficient:

```bash
sudo chmod u+rwx /volume1/docker/telemetry/loki/data
sudo chmod u+rwx /volume1/docker/telemetry/prometheus/data
sudo chmod u+rwx /volume1/docker/telemetry/grafana
```

Do not recursively normalize the modes of the runtime trees merely because Synology displays unexpected Unix permissions. Synology ACL inheritance may cause files and directories to display modes that would be unusual on a conventional Linux filesystem.

Instead, verify effective write access from the relevant container when troubleshooting permissions.

Prometheus is explicitly configured in Compose as `user: "65534:65534"`. Loki and Grafana use the users defined by their official images. Alloy runs as root in the current image. The unbound-exporter runs as `65534:65534` and has no persistent storage.

## Usage Reporting

Anonymous usage reporting is disabled for both Alloy and Grafana.

Alloy is started with:

```text
--disable-reporting
```

Grafana is started with:

```text
GF_ANALYTICS_REPORTING_ENABLED=false
```

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
## Docker Network and DSM Firewall

All telemetry containers share a dedicated Docker bridge network:

```yaml
networks:
  telemetry-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.24.0.0/16
```

Containers use Docker DNS names such as `loki`, `prometheus`, and `unbound-exporter` for internal communication. Static container IP addresses are not required.

Loki (3100), Prometheus (9090), and unbound-exporter (9101) are intentionally not published to the Synology host. Only services that require access from outside the Docker network are host-published.

### Synology DSM Firewall

The DSM firewall must allow traffic originating from the telemetry Docker subnet:

- Source: `172.24.0.0/16`
- Ports: All
- Protocols: All
- Action: Allow

This rule is required because DSM's final deny rule otherwise prevents telemetry containers from reaching destinations outside the Docker bridge. This was observed when unbound-exporter could not reach the OPNsense API even though the Synology host itself could.

OPNsense syslog requires a separate DSM firewall rule:

- Source: `192.168.20.1`
- Port: `514`
- Protocol: TCP
- Action: Allow

Although the OPNsense API is reached at `192.168.1.1`, OPNsense uses its NAS-interface address `192.168.20.1` as the source when sending syslog to the Synology at `192.168.20.10`. Packet capture confirmed this source address during troubleshooting.

Both telemetry firewall rules must appear above the final deny rule.

## Grafana Data Sources

| Name | URL | Default |
|---|---|---|
| loki | `http://loki:3100` | Yes |
| prometheus | `http://prometheus:9090` | No |

> **Note:** Use container names (not IP addresses) so Grafana resolves via Docker's internal DNS on the `telemetry-net` bridge network.

> **Note:** Prometheus datasource must be added manually via Connections → Data sources → Add → Prometheus.

## Loki Series Limit

Loki enforces a default limit of 500 unique series per query (`max_query_series_limit`). Per-IP labeling can exceed this limit when large numbers of external attacker IPs appear over longer time ranges.

The `wan_attackers` pipeline is designed to keep indexed label cardinality low:

- `src_ip` is retained in the raw firewall log but is not promoted to a Loki label.
- Short time ranges (≤15m): individual attacker IPs can be extracted at query time when needed.
- Longer time ranges (1h–24h): group by `src_net` (/24 aggregation).

If the series limit is hit on other queries, consider raising the limit in `loki/config.yml` under `limits_config`:

```yaml
max_query_series_limit: 1000
```
