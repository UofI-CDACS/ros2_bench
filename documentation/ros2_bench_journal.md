# ros2_bench — Research Journal

ROS2 middleware benchmarking on Raspberry Pi cluster.  
This is a living document. Add entries chronologically as the project progresses.

---

## Entry format

```
## YYYY-MM-DD — Short title

**Problem:** What question or issue prompted this entry.
**Research:** What was looked into, what options were considered.
**Decision:** What was chosen and why. What was explicitly rejected and why.
**Implementation:** What was built or changed.
**Results:** What the data or outcome showed. Leave blank until known.
**Open questions:** What this entry leaves unresolved.
```

---

## Infrastructure

Hostnames, IPs, and provisioning state. Update as the cluster changes.

| Hostname | IP | User | OS | Python | Docker | Notes |
|----------|----|------|----|--------|--------|-------|
| rospi-0 | TBD | rospi | — | — | — | HAT needs fixing; get IP from Dave |
| rospi-1 | 172.23.254.24 | rospi | Ubuntu 24.04.2 LTS | 3.12.3 | 29.0.2 | ✅ SSH |
| rospi-2 | 172.23.254.22 | rospi | Ubuntu 24.04.3 LTS | 3.12.3 | 29.0.2 | ✅ SSH |
| rospi-3 | 172.23.254.23 | rospi | Ubuntu 24.04.3 LTS | 3.12.3 | 29.0.2 | ✅ SSH |
| rospi-4 | 172.23.254.19 | rospi | Ubuntu 24.04.2 LTS | 3.12.3 | 29.0.2 | ✅ SSH |
| server | TBD | TBD | — | — | — | Get IP from Dave |

SSH keys provisioned via `ssh-copy-id`. Config entries in `~/.ssh/config` per device.  
NTP/chrony: **not yet configured** — required before one-way latency analysis.  
ROS2 Jazzy: install status not yet recorded here — add a column when confirmed.

---

## 2025-XX-XX — Prior work: rospi-net / netstress (abandoned, research preserved)

**Problem:**
First attempt at the project. Goal was to stress-test ROS2 networking across
the Pi cluster by creating configurable router nodes that publish and subscribe
to multiple topics at configurable rates.

**Research:**

- *Approach:* A single `router` node configurable entirely via YAML parameter
  files. Each Pi runs a router; topics and publish rates are set per-node
  without code changes. Nodes publish on specified topics with an incrementing
  counter per message, and subscribe to specified topics logging receipt counts.
  Demonstrated working across multiple Pis.

- *Future directions identified:* Virtual components defined entirely by config
  — e.g. a node that listens to two sensor topics and publishes a derived topic
  only when both arrive within a time window; or a publisher with configurable
  timing jitter. Also considered Gazebo-driven message generation for more
  realistic traffic, though noted this is less abstract and less controllable
  than synthetic loads.

- *Data storage — MongoDB evaluated:* MongoDB was the intended storage backend.
  Found to be cumbersome to set up and maintain on the cluster. Not a query
  performance finding — the operational overhead of running and managing a
  MongoDB instance across Pis was the friction point.

**Decision:**
Abandoned. Two reasons: the router node approach measured traffic volume and
message counts but had no latency measurement methodology — it wasn't actually
answering the core research question. And MongoDB's operational burden wasn't
justified at this stage. Moved to the parcel-bench approach instead.

**Research carried forward:**
- YAML-driven node configuration is a good pattern — carried forward into the
  idea of config-driven benchmarks in later phases.
- MongoDB overhead concern informed the decision to evaluate both MongoDB *and*
  MariaDB for the storage phase rather than committing to either upfront.
- Virtual component / configurable topology ideas remain interesting for future
  phases once baseline point-to-point measurements are solid.
- Confirmed that a simple pub/sub stress test working across Pis is achievable
  with minimal setup.

**Implementation:** None carried forward. Package was `netstress`.

**Results:** Router node confirmed working across multiple Pis. Publish/subscribe
at configurable rates demonstrated. No latency data captured.

**Open questions:** None — resolved by decision to start fresh.

---

## 2026-03-XX — Prior work: ros2-parcel-bench (abandoned, research preserved)

**Problem:**
Same underlying goal — measure ROS2 middleware performance across multiple
devices on the Pi cluster. This was the first attempt at the project under the
name `ros2-parcel-bench`.

**Research:**

- *Topology explored:* Multi-hop "parcel forwarding" — a generator node
  produces messages, stations forward them hop-by-hop, endpoints receive them.
  This was successfully demonstrated: two stations on separate Pis, two on a
  PC, generator on the first, all communicating across the network. Verified
  working with screenshots.

- *Data capture — rosbag evaluated:*  
  rosbag was identified as a convenient capture mechanism. It can record all
  traffic on specified topics and write `.db3` files on a configurable schedule
  (by time interval or max file size), which enables near-real-time data
  access.  
  Key problem discovered: rosbag `.db3` files store **serialized messages**.
  They cannot be queried directly for meaningful data — deserialization is
  required first, either via a Python parsing step or a conversion pipeline
  before visualization. This means rosbag alone cannot feed Grafana or SQL
  queries without an intermediate transform layer.  
  Secondary concern: rosbag adds network overhead by subscribing to all
  recorded topics, which contaminates raw latency measurements.

- *Alternative to rosbag considered:* Designating certain stations as
  "endpoints" via parameter, which write their messages to a local database
  directly. This keeps benchmark traffic clean (no bag subscriber on the
  network during the run) but loses real-time visibility. Both approaches could
  run simultaneously for comparison.

- *Next steps identified before abandonment:* More complex topology testing
  via launch files; configuration files for defaults; a tool to read a bag file
  and automatically recreate testing conditions.

**Decision:**
Abandoned this codebase and started fresh. Reasons:

- The parcel-forwarding topology conflated two concerns: testing multi-hop
  routing behavior and measuring point-to-point middleware latency. The new
  project focuses on the latter first, cleanly, before adding topology complexity.
- The rosbag deserialization problem meant a transform layer was needed anyway —
  so direct JSON output from the benchmark node is simpler and equally capable.
- Starting fresh allows a cleaner architecture with explicit measurement
  methodology from the outset rather than retrofitting it.

**Research carried forward into new project:**
- rosbag is not suitable as a direct data source for Grafana/SQL without a
  deserialization transform — confirmed by direct experimentation.
- The rosbag overhead concern informed the decision to keep the benchmark node's
  output path (stdout JSON) completely separate from any DB or bag subscriber.
- Multi-hop topology remains a valid future direction once point-to-point
  baseline measurements are solid.
- The cluster is confirmed working for multi-machine ROS2 communication
  (rospi-1 through rospi-4 + PC all communicating successfully).

**Implementation:** None carried forward. New package is `ros2_bench`.

**Results:** Multi-machine ROS2 pub/sub confirmed working across the cluster.
`.db3` bag files successfully captured. Deserialization requirement confirmed.

**Open questions:** None — resolved by the decision to start fresh.

---

## 2026-03-09 — Prototype: ping-pong RTT benchmark

**Problem:**
We want to measure ROS2 middleware latency between two physical machines on a
Pi cluster, and understand how much of the observed latency is the middleware
vs. raw network cost. No existing tooling captures both in a way that is easy
to iterate on and store for later analysis.

**Research:**

- *Topology:* Considered one-way publish, ping-pong (A→B→A), and multi-hop.
  Ping-pong chosen because it exercises the full pub/sub path in both
  directions and requires no clock sync between machines — both timestamps are
  taken on Machine A with its own monotonic clock.

- *Message type:* Considered `std_msgs/ByteMultiArray` (good for size sweeps),
  a custom `.msg` (most precise), and `std_msgs/String` with a JSON payload
  (simplest). Chose `std_msgs/String` + JSON for the prototype — inspectable
  with `ros2 topic echo`, no build-time `.msg` dependency, trivially parseable.
  To revisit when C++ port begins.

- *Network baseline:* Considered UDP echo socket (same protocol as DDS, more
  accurate), ICMP ping (kernel-handled, not the same path as DDS), and a
  manual config value. Chose ICMP ping for the prototype — available everywhere
  with no extra setup. Documented that `ros2_overhead_ms` is therefore an
  upper-bound estimate, not a precise figure.

- *Baseline sampling frequency:* Considered per-send (rejected — contaminates
  the measurement window with extra traffic), periodic re-sampling (unnecessary
  complexity on a stable LAN), and once at startup (chosen). On LAN, network
  conditions between Pis are stable across a run. Noted that WAN would require
  periodic re-sampling and timestamp-joining during analysis.

- *RMW switching:* Controlled entirely via `RMW_IMPLEMENTATION` env var. No
  code changes between RMW benchmark runs. RMW name stamped on every output
  record automatically.

- *Output format:* Considered direct DB writes from the benchmark node
  (rejected — couples the node to DB availability and adds latency to the
  measurement loop) and JSON lines to stdout (chosen). Stdout output is
  redirectable, replayable, and DB-agnostic. DB export is a separate later
  phase.

- *Language:* Python for the prototype (faster iteration, easier to deploy
  identically across Pis). C++ port planned for the precision phase — Python
  overhead is constant across RMW comparisons so relative figures are valid,
  but absolute overhead figures will be higher than C++ equivalents.

**Decision:**
Build a two-node Python ROS2 package (`sender_node` + `echo_node`) deployed
identically to all Pis. Any Pi can play either role via launch argument. ICMP
baseline at startup, JSON lines to stdout, RMW via env var.

**Implementation:**
- `ros2_bench` ROS2 Jazzy Python package
- `sender_node`: publishes to `/bench/ping`, subscribes to `/bench/pong`,
  records RTT, runs ICMP baseline at startup, emits JSON lines to stdout
- `echo_node`: subscribes to `/bench/ping`, republishes unchanged to `/bench/pong`
- Output record fields: `seq`, `t_send_ns`, `t_recv_ns`, `rtt_ms`, `ping_ms`,
  `ros2_overhead_ms`, `rmw`, `sender_host`, `responder_ip`, `msg_bytes`

**Results:**
_Not yet collected. Update once first runs are complete on the Pi cluster._

**Direction note:**
The progression across all three attempts shows the same underlying mistake
corrected: rospi-net and parcel-bench both built infrastructure before the
measurement methodology was solid. This prototype fixes that — one well-defined
number (RTT), taken cleanly on one clock, with documented limitations. Sweeps,
DB export, Grafana, and multi-hop topologies all build on top of that number
being trustworthy. Order is correct.

**Open questions:**
- When will NTP/chrony be confirmed stable across the cluster? Required before
  one-way latency analysis is valid.
- What message sizes and send rates should the Phase 2 sweep cover?
  Suggested starting points: 64 B / 1 KB / 64 KB / 1 MB, at 1 / 10 / 100 / 1000 Hz.
- MongoDB vs MariaDB: what query patterns matter most to benchmark?
  Time-range queries? Aggregations by RMW? Full scans?
- MongoDB is back on the table for the DB export phase despite operational
  overhead being the reason it was dropped from rospi-net. The use case is
  different (query benchmarking, not primary storage), but the setup burden
  hasn't changed. Decide consciously before that phase: Docker on a Pi, on the
  server, or elsewhere. Don't let setup friction stall Phase 3 the same way it
  did before.

---

## Known limitations (current)

These apply to all entries until explicitly resolved. Update this list as
limitations are addressed.

- **`ros2_overhead_ms` is an upper-bound estimate.** ICMP is kernel-handled;
  DDS goes through user-space serialization, UDP framing, and back. Not a
  precise middleware figure.
- **Single startup baseline assumes stable LAN.** Not valid for WAN. WAN
  would need periodic re-sampling + timestamp-join during analysis.
- **RTT only — one-way latency not valid until NTP is confirmed.**
  Both timestamps are on Machine A's clock.
- **echo_node processing time is included in RTT.** Not subtracted.
  Small and consistent; valid for relative comparisons.
- **Python overhead inflates absolute figures.** Constant across RMW
  comparisons so relative figures are valid. C++ port needed for absolute.
- **Mismatched RMWs fail silently.** Both machines must have the same
  `RMW_IMPLEMENTATION` set before launch.

---

## Planned phases

| Phase | Goal | Status |
|-------|------|--------|
| 1 | Ping-pong RTT prototype (Python) | ✅ Complete |
| 2 | Variable sweep runner (msg size, send rate) | 🔲 Not started |
| 3 | DB export layer (MongoDB + MariaDB) | 🔲 Not started |
| 4 | Grafana dashboards | 🔲 Not started |
| 5 | C++ port for absolute precision | 🔲 Not started |
| 6 | One-way latency (requires NTP confirmed) | 🔲 Blocked |
