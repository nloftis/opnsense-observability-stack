# Alloy Configuration Changes — 2026-09-20

## Purpose

This document records the changes made to the Alloy `wan_attackers` pipeline, the validation performed after those changes, and the subsequent Alloy container upgrade from v1.16.2 to v1.19.2.

The goal was to keep the WAN-attacker telemetry useful for the SOC dashboard while avoiding Loki query failures caused by excessive per-source-IP series cardinality.

---

## Scope of This Change

The Alloy changes made during this review are limited to:

1. Correct ICMP parsing in `wan_attackers`.
2. Removing `src_ip` from the indexed Loki label set while retaining it as an
   extracted/raw value.
3. Updating comments in `config.alloy` to document the reasons for those
   decisions.

---

## 1. WAN attacker stream

The `loki.process "wan_attackers"` pipeline continues to keep only:

- inbound WAN firewall blocks
- interface `igc0`
- blocked traffic
- TCP, UDP, and ICMP traffic

The pipeline writes the resulting telemetry to `loki.write.local.receiver`.

---

## 2. Separate TCP/UDP and ICMP parsing

### TCP/UDP

TCP and UDP firewall records contain source and destination ports.

The pipeline now parses:

- `proto_id`
- `proto`
- `length`
- `src_ip`
- `dst_ip`
- `src_port`
- `dst_port`

`dst_port` is promoted to a Loki label for the targeted-services / most-probed-ports analysis.

### ICMP

ICMP firewall records do not contain TCP/UDP port fields.

A separate `stage.match` and `stage.regex` was therefore added for ICMP records. It extracts:

- `proto_id`
- `proto`
- `length`
- `src_ip`
- `dst_ip`

`dst_port` is intentionally left unset for ICMP rather than assigning a synthetic value.

This allows ICMP telemetry to remain distinguishable from TCP/UDP traffic.

---

## 3. Source-IP cardinality change

Previously, `src_ip` was promoted to a Loki label.

That created a high-cardinality label because every distinct external attacker IP could create another Loki series. Longer time ranges could therefore exceed Loki's default query series limit.

The new configuration deliberately does **not** promote `src_ip` to a Loki label.

The full source IP is still present in the raw firewall log and can be extracted at query time when an individual attacker IP is needed.

This preserves the investigative information without making every source IP part of Loki's indexed label set.

---

## 4. `/24` attacker-network aggregation

The pipeline derives a `src_net` value from `src_ip`:

```text
x.x.x.0/24
```

The derivation is performed with:

- `stage.regex` against the extracted `src_ip`
- `stage.template` to construct the `/24` network

`src_net` is promoted to a Loki label.

This provides a lower-cardinality dimension for longer-range attacker aggregation.

---

## 5. Labels written by `wan_attackers`

The resulting label set is:

| Label | Purpose |
|---|---|
| `view` | `wan_attackers` stream identifier |
| `log_type` | `firewall` |
| `action` | `block` |
| `direction` | `in` |
| `src_zone` | `wan` |
| `src_net` | `/24` attacker-network aggregation |
| `dst_port` | TCP/UDP destination-port aggregation |

`src_ip` is **not** an indexed Loki label.

---

## 6. Validation performed

### Alloy configuration validation

The updated configuration was copied into the Synology Alloy configuration directory and validated with:

```bash
sudo docker exec alloy alloy validate /etc/alloy/config.alloy
```

The validation completed without an error.

Alloy was then restarted:

```bash
sudo docker compose restart alloy
```

The Alloy logs showed the configuration graph loading successfully, including:

```text
node_id=loki.process.wan_attackers
```

and the syslog listener started successfully.

---

## 7. Post-change ICMP validation

A naturally occurring blocked ICMP WAN event was subsequently observed after the configuration change.

The event appeared in Loki under:

```text
view="wan_attackers"
```

and showed:

- `action=block`
- `direction=in`
- `log_type=firewall`
- `src_zone=wan`
- a populated `src_net`
- no `dst_port`

This confirms that the new ICMP parsing path is functioning with live firewall telemetry.

No synthetic ICMP test traffic was required.

---

## 8. Loki cardinality validation

Before the change, the `src_ip`-based query over a one-hour range exceeded Loki's default 500-series query limit.

After removing `src_ip` as an indexed label and using `src_net` for aggregation, the **Most Active External Attackers** dashboard panel continued to return data when the dashboard time range was increased beyond one hour.

The panel is now aggregating by:

```logql
src_net
```

rather than by an indexed `src_ip`.

This confirms that the cardinality problem affecting the dashboard's longer-range attacker aggregation has been resolved.

---

## 9. Grafana SOC Overview review

The three SOC Overview panels associated with the `wan_attackers` stream were inspected after the Alloy changes.

### Inbound ICMP Sweep Activity

Current query:

```logql
sum(rate({view="wan_attackers"} | dst_port="" [$__interval]))
```

No change was required.

The panel correctly continues to represent ICMP traffic by selecting entries without a `dst_port`.

### Most Active External Attackers

Current query aggregates by:

```logql
src_net
```

No change was required.

The panel already matches the new lower-cardinality architecture.

### Most Probed Ports

Current query aggregates by:

```logql
dst_port
```

with a non-empty `dst_port` filter.

No change was required.

This naturally limits the panel to TCP/UDP traffic because ICMP records do not have a `dst_port`.

### Dashboard review conclusion

No SOC Overview dashboard query required modification as a result of the Alloy label change.

No new dashboard panel was added.

---

## 10. Final architecture

The resulting design is:

```text
OPNsense firewall
       |
       v
Alloy syslog receiver
       |
       v
loki.process "classify"
       |
       v
loki.process "wan_attackers"
       |
       +--> TCP/UDP parsing
       |       |
       |       +--> src_ip (extracted, not indexed)
       |       +--> src_net (indexed)
       |       +--> dst_port (indexed)
       |
       +--> ICMP parsing
               |
               +--> src_ip (extracted, not indexed)
               +--> src_net (indexed)
               +--> no dst_port
       |
       v
Loki
```

The important design decision is that **individual WAN source IPs remain available in the raw telemetry without being promoted to a high-cardinality Loki label**.

---

## 11. Alloy container upgrade

After the `wan_attackers` configuration changes were completed and validated,
the Alloy container was upgraded from:

```text
v1.16.2
```

to:

```text
v1.19.2
```

The upgrade was performed independently of Loki and the other telemetry
components.

### Pre-upgrade validation

Before modifying the running deployment:

- The `v1.19.2` container image was confirmed to provide a `linux/amd64`
  build compatible with the Synology host.
- The existing `v1.16.2` image was retained locally for rollback.
- The `v1.19.2` image was pulled and inspected locally.
- The image's Alloy binary reported `v1.19.2` and `linux/amd64`.
- The existing `config.alloy` was validated using the `v1.19.2` container
  image before the running Alloy service was changed.
- The updated Docker Compose configuration was validated before deployment.

### Deployment

The Docker Compose image reference was changed from:

```yaml
image: grafana/alloy:v1.16.2
```

to:

```yaml
image: grafana/alloy:v1.19.2
```

`pull_policy: never` was retained so that deployment uses the explicitly
downloaded local image rather than implicitly pulling an image during
container creation.

Only the Alloy service was recreated:

```bash
sudo docker compose up -d --no-deps alloy
```

The other telemetry containers were left running.

### Post-upgrade validation

After the upgrade:

- The running container was confirmed to use `grafana/alloy:v1.19.2`.
- The running image ID matched the previously inspected candidate image.
- Alloy successfully evaluated:
  - `loki.source.syslog.opnsense`
  - `loki.process.classify`
  - `loki.process.wan_attackers`
  - `loki.write.local`
- The TCP syslog listener successfully started on port 514.
- The Alloy HTTP service successfully started on port 12345.
- No startup warnings or errors affecting the telemetry pipeline were
  observed.
- Fresh post-upgrade firewall telemetry appeared in Grafana.
- Fresh ICMP WAN sweep activity appeared in the SOC Overview dashboard,
  confirming that the revised ICMP parsing path continued to function under
  Alloy v1.19.2.
- `src_net` attacker aggregation and `dst_port` aggregation continued to
  populate the associated dashboard panels.
- The container remained running with `RestartCount=0`.

The previous `v1.16.2` image was deliberately retained locally after the
upgrade to preserve a straightforward rollback path.

The Alloy v1.19.2 upgrade is therefore considered successfully validated.

---

## Status

The Alloy changes for this review are complete.

Validation completed:

- [x] TCP/UDP destination-port extraction
- [x] Separate ICMP parsing
- [x] ICMP WAN block observed after the change
- [x] `src_ip` removed as an indexed label
- [x] `src_net` retained for aggregation
- [x] `dst_port` retained for targeted-services analysis
- [x] One-hour-plus `src_net` dashboard query verified
- [x] SOC Overview dashboard reviewed
- [x] No dashboard query changes required

This document should be committed together with the corresponding `alloy/config.alloy` and `README.md` changes.
