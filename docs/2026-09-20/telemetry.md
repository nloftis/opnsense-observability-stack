# DS224+ DSM Restore --- Telemetry Uplift Notes

**Project:** Telemetry Stack\
**Platform:** Synology DS224+ / DSM / Container Manager\
**Uplift validation:** 2026-09-21

## Purpose

This document records the discoveries, changes, decisions,
troubleshooting steps, and validation performed while restoring and
uplifting the telemetry stack after the DS224+ DSM restore.

It is intentionally separate from `README.md`:

-   `README.md` describes the current architecture and how the stack is
    operated.
-   This file records why changes were made, what failed during
    recovery, and what should be remembered for a future restore or
    uplift.

## Final Verified Architecture

``` text
Logs:
OPNsense -- RFC5424 TCP/514 --> Alloy --> Loki --> Grafana

Metrics:
OPNsense Unbound API -- HTTPS/443 --> unbound-exporter --> Prometheus --> Grafana
```

All five containers share the Docker bridge network `telemetry-net` on
`172.24.0.0/16`.

Host-published services:

-   Alloy syslog: TCP/514
-   Alloy HTTP/debug UI: TCP/12345
-   Grafana UI: TCP/3000

Internal-only services:

-   Loki: TCP/3100
-   Prometheus: TCP/9090
-   unbound-exporter: TCP/9101

## Source of Truth and Runtime State

A major decision during the uplift was to separate source/configuration
from mutable runtime data.

### Pop!\_OS / Git repository

The local Git repository is the source of truth for:

-   `docker-compose.yml`
-   Alloy configuration
-   Loki configuration
-   Prometheus configuration
-   unbound-exporter source and Dockerfile
-   `.env.example`
-   `.gitignore`
-   project documentation

The real `.env` remains untracked.

### Synology

Synology contains the deployed configuration plus mutable runtime state:

-   `alloy/data/`
-   `loki/data/`
-   `prometheus/data/`
-   `grafana/`

Runtime data is not copied back into the Git repository during normal
synchronization. NAS backup/snapshot mechanisms are the appropriate
protection for runtime state.

This avoids service-owned/ACL-backed runtime files changing permissions
in the Pop!\_OS source tree.

## Git and File-Mode Cleanup

Synology/NFS copies can present ordinary files as mode `777`. Copying
those files back to Pop!\_OS can therefore introduce unwanted executable
bits or other mode changes into Git.

For copies from Synology back into the source tree, this pattern proved
useful:

``` bash
cp -r --no-preserve=mode <source-directory> ~/GitHub/
```

The local Git working tree was normalized so configuration/source files
are not accidentally executable.

The repository ignores:

-   `.env` and `.env.*` except `.env.example`
-   API-key reference files (`*_apikey.txt`)
-   runtime data directories
-   Grafana runtime state
-   draft README files
-   editor/OS artifacts

## Credential Handling

The unbound exporter reads its required OPNsense API values from environment variables:

```python
OPNSENSE_URL = os.environ["OPNSENSE_URL"]
API_KEY = os.environ["OPNSENSE_API_KEY"]
API_SECRET = os.environ["OPNSENSE_API_SECRET"]
```

Compose references those values from `.env`.

The real `.env` is excluded from Git and was set to mode `600` on Synology. The local Pop!_OS copy is also mode `600`.

An old OPNsense API-key reference text file was removed from the Synology deployment and retained only as a local ignored reference. Its local mode was restricted to `600`.

Git history was reviewed before the final commit. The initial exporter already obtained the API key and secret from environment variables, and the initial `.env.example` contained placeholder values rather than real credentials. Searches for the current API key and secret values found no matches in reachable Git history.

No evidence was found that the OPNsense API credentials were committed to this repository, so credential rotation or Git history rewriting was not required as part of this uplift.

Do not place actual credentials in this document.

## Docker Network Uplift

The stack was consolidated onto one user-defined Docker bridge:

``` yaml
networks:
  telemetry-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.24.0.0/16
```

Static container IPs are not used. Docker DNS names are used between
services:

-   `loki:3100`
-   `prometheus:9090`
-   `unbound-exporter:9101`

This keeps service discovery independent of container IP changes after
rebuilds.

## Host Port Reduction

Loki, Prometheus, and unbound-exporter no longer need host-published
ports.

They are consumed only by other containers on `telemetry-net`.

The retained host ports are:

-   TCP/514 --- OPNsense syslog to Alloy
-   TCP/12345 --- Alloy HTTP/debug UI
-   TCP/3000 --- Grafana UI

This reduces unnecessary NAS host exposure.

## DSM Firewall Discoveries

The DSM firewall was a critical part of the recovery.

### Docker subnet allowance

Containers on `172.24.0.0/16` could not reach the OPNsense API even
though the Synology host itself could reach it.

Symptoms:

-   Synology host HTTPS request to OPNsense succeeded.
-   unbound-exporter timed out connecting to `192.168.1.1:443`.
-   Docker network and routing were otherwise present.

Adding a DSM firewall allow rule for source:

``` text
172.24.0.0/16
```

above the final deny rule immediately restored exporter connectivity.

This behavior had also been encountered previously with another Docker
project and should be checked early after a DSM restore.

### OPNsense syslog source address

A DSM rule was initially created for TCP/514 from `192.168.1.1`. Syslog
still failed.

Packet capture showed OPNsense was actually connecting from:

``` text
192.168.20.1 -> 192.168.20.10:514
```

OPNsense uses the interface address on the destination NAS network, so
the correct DSM firewall rule is:

``` text
Source:   192.168.20.1
Port:     TCP/514
Action:   Allow
```

After changing the rule, packet capture showed:

1.  OPNsense sending TCP payload to Synology.
2.  Synology acknowledging it.
3.  Docker forwarding the payload into the Alloy container.
4.  Alloy acknowledging receipt.

This is an important restore-time diagnostic: do not assume the OPNsense
management/LAN address will be the source address of traffic sent to
another directly connected subnet.

## OPNsense Syslog Configuration

The verified remote syslog destination is:

-   Transport: TCP(4)
-   Host: `192.168.20.10`
-   Port: `514`
-   RFC5424: enabled
-   Applications: unrestricted
-   Facilities: unrestricted
-   Levels: info and above
-   Description: `Alloy Telemetry`

UDP/514 was removed from the telemetry design.

RFC5424 remains required by the Alloy syslog parser.

## Persistent Data Permissions After DSM Restore

Synology ACL-backed directories displayed unusual Unix modes after the
restore. Effective write tests were more useful than attempting to
normalize every displayed mode.

### Loki

Runtime user:

``` text
10001:10001
```

The data directory was owner-correct but not writable. Fixed with:

``` bash
sudo chmod u+rwx /volume1/docker/telemetry/loki/data
```

### Prometheus

Compose runs Prometheus as:

``` text
65534:65534
```

The data directory was not writable. Fixed with:

``` bash
sudo chmod u+rwx /volume1/docker/telemetry/prometheus/data
```

### Grafana

Image runtime user:

``` text
472:0
```

The persistent Grafana directory was not writable. Fixed with:

``` bash
sudo chmod u+rwx /volume1/docker/telemetry/grafana
```

### Alloy

The current Alloy image runs as root. Effective write tests to its
data/positions storage succeeded; no permission change was required.

### Decision

Do not recursively normalize modes throughout Synology ACL-backed
runtime trees merely because modes look unusual.

Instead:

1.  Determine the effective container user.
2.  Test actual write access.
3.  Make the smallest permission change required.
4.  Retest.

Loki and Grafana continue using their image-default users. Prometheus
retains the explicit `user: "65534:65534"` setting.

## Unbound Exporter Changes

The exporter was kept intentionally small.

Changes included:

-   Required environment variables now use `os.environ[...]`, causing
    missing required configuration to fail clearly.
-   `urllib3` import was moved to the normal import section.
-   A comment documents why TLS certificate verification is disabled.
-   The exporter remains non-root (`65534:65534`).
-   Python dependencies are pinned.

Current dependency versions:

``` text
prometheus_client==0.26.0
requests==2.34.2
```

The OPNsense API uses a self-signed certificate, so the exporter
currently uses `verify=False`. This is an intentional local-network
decision, not an accidental omission.

The existing `Counter._value.set()` implementation was retained to avoid
changing metric semantics or existing dashboards during this uplift.

## Alloy Changes and Decisions

The Alloy configuration was reviewed with an emphasis on preserving
working dashboard semantics.

Key decisions:

-   TCP-only syslog listener.
-   Main firewall classification and label extraction retained.
-   ICMP processing includes `proto`.
-   Zone classification retained.
-   DHCP, syslog statistics, and AdGuard JSON processing retained.
-   Loki endpoint uses Docker DNS: `http://loki:3100/loki/api/v1/push`.
-   `src_ip`, `src_net`, and `dst_port` label strategy retained with
    explanatory rationale.
-   Potential `wan_attackers` ICMP regex concerns were not changed
    without evidence of a live problem.

Comments were treated as useful documentation of *why* configuration
exists, not as clutter.

## Reporting to stats.grafana.org

Repeated blocked DNS lookups to `stats.grafana.org` were investigated.

Historical logs showed the traffic originated from Grafana usage
reporting, not Alloy.

The final Compose configuration disables both:

``` text
Alloy:   --disable-reporting
Grafana: GF_ANALYTICS_REPORTING_ENABLED=false
```

AdGuard Home had been blocking `stats.grafana.org` via OISD Blocklist
Big, making the retries highly visible in the DNS dashboard.

Historical Loki ranges may continue to show those queries until they age
out.

## Runtime Validation

### Metrics path

After the DSM Docker-subnet firewall rule was added, exporter logs
changed from connection timeouts to successful scrapes.

A direct Prometheus API query returned `unbound_queries_total`,
verifying:

``` text
OPNsense API -> unbound-exporter -> Prometheus
```

The Grafana DNS Performance dashboard then showed:

-   historical data before the outage,
-   a gap corresponding to telemetry downtime,
-   current samples arriving after recovery.

This also confirmed the existing Prometheus TSDB survived the rebuild.

### Log path

Loki initially returned no current `{job="opnsense"}` results.

`tcpdump` showed OPNsense SYN packets reaching the NAS from
`192.168.20.1`, but no NAS response while the DSM firewall rule
incorrectly allowed `192.168.1.1`.

After correcting the rule to `192.168.20.1`, traffic flowed through the
Docker bridge into Alloy.

A direct Loki query then returned current OPNsense events with parsed
labels such as:

-   `log_type="firewall"`
-   `action`
-   `iface`
-   `proto`
-   `src_ip`
-   `dst_ip`
-   `src_zone`
-   `dst_zone`
-   `dst_port`

The SOC dashboard subsequently populated with current data while
preserving historical data before the outage.

Verified path:

``` text
OPNsense -> TCP/514 -> Alloy -> Loki -> Grafana
```

## Historical Data Preservation

The rebuild did not replace the existing persistent stores.

Grafana showed:

-   historical telemetry before the outage,
-   a visible gap while telemetry was down,
-   new data after recovery.

This confirmed preservation of Grafana, Loki, and Prometheus state.

## Final Runtime State

At the end of validation, all five containers remained running:

``` text
alloy
loki
prometheus
unbound-exporter
grafana
```

Both major data paths were verified end-to-end.

## Restore Checklist --- Lessons Learned

For a future DSM restore, check these early:

1.  Confirm persistent runtime directories survived and are mounted
    where Compose expects.
2.  Verify effective write access for Loki, Prometheus, Grafana, and
    Alloy.
3.  Confirm the telemetry Docker subnet exists as `172.24.0.0/16`.
4.  Confirm DSM firewall allows source `172.24.0.0/16`.
5.  Confirm DSM firewall allows `192.168.20.1` to TCP/514.
6.  Confirm OPNsense remote syslog uses TCP/514 and RFC5424.
7.  Verify exporter logs show successful OPNsense API scrapes.
8.  Query Prometheus directly for `unbound_queries_total`.
9.  Use `tcpdump` on TCP/514 if syslog is absent.
10. Query Loki directly for `{job="opnsense"}`.
11. Confirm DNS Performance and SOC dashboards show new data.
12. Preserve the source/runtime separation: Git is configuration;
    Synology owns runtime state.
