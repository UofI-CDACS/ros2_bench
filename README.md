# ros2_bench

A ROS2 Jazzy benchmarking package for measuring middleware round-trip time (RTT) between two machines and estimating ROS2 overhead against a raw network baseline.

> ⚠️ This is a prototype. See [Limitations](#limitations) before interpreting results.

---

## 📁 Project Structure

```
ros2_bench/
├── src/
│   └── ros2_bench/             # ROS2 package
│       ├── package.xml
│       ├── setup.py
│       ├── setup.cfg
│       ├── resource/
│       └── ros2_bench/
│           ├── sender_node.py  # Runs on Machine A — sends ping, records RTT
│           └── echo_node.py    # Runs on Machine B — echoes ping back
├── documentation/
│   └── ros2_bench_journal.md   # Research journal
├── README.md
└── LICENSE
```

---

## 🚀 Getting Started

### Prerequisites
- ROS2 Jazzy Jalisco
- Python 3.12+
- `ping` available on Machine A (for ICMP baseline)

### Build & install (run on every Pi)

```bash
cd ~/ros2_ws/src
git clone <this-repo>

cd ~/ros2_ws
colcon build --packages-select ros2_bench
source install/setup.bash
```

---

## 🧪 Usage Examples

### Basic ping-pong test

```bash
# Machine B — start echo node first
ros2 run ros2_bench echo

# Machine A — start sender
ros2 run ros2_bench sender --ros-args \
    -p responder_ip:=<MACHINE_B_IP> \
    -p send_count:=100              \
    -p send_interval_ms:=100
```

### Capture results to file

```bash
ros2 run ros2_bench sender --ros-args -p responder_ip:=<IP> \
    > results.jsonl 2>sender.log
```

### Swap RMW middleware

Set `RMW_IMPLEMENTATION` on **both** machines before launch — no code changes needed:

```bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
# install if needed: sudo apt install ros-jazzy-rmw-cyclonedds-cpp
```

---

## 🛠 How it works

```
Machine A (sender)                    Machine B (echo)
  publish JSON → /bench/ping    ──►   subscribe /bench/ping
  subscribe    ← /bench/pong    ◄──   republish unchanged

  rtt_ms           = (t_recv_ns − t_send_ns) / 1e6   ← both clocks on Machine A
  ping_ms          = ICMP median RTT at startup
  ros2_overhead_ms = rtt_ms − ping_ms
```

Output is one JSON line per round trip to stdout:

```json
{
  "seq": 42,
  "rtt_ms": 0.318,
  "ping_ms": 0.201,
  "ros2_overhead_ms": 0.117,
  "rmw": "rmw_fastrtps_cpp",
  "sender_host": "pi-a",
  "responder_ip": "192.168.1.101",
  "msg_bytes": 64
}
```

### Sender parameters

| Parameter          | Default       | Description                        |
|--------------------|---------------|------------------------------------|
| `responder_ip`     | *(required)*  | IP of the echo node machine        |
| `send_count`       | `100`         | Number of messages to send         |
| `send_interval_ms` | `100`         | Milliseconds between sends         |
| `ping_samples`     | `20`          | ICMP pings for baseline            |
| `ping_topic`       | `/bench/ping` | Override topic name                |
| `pong_topic`       | `/bench/pong` | Override topic name                |

---

## ⚠️ Limitations

- **`ros2_overhead_ms` is an estimate** — ICMP and DDS take different paths through the network stack
- **Baseline sampled once at startup** — valid on a stable LAN, not suitable for WAN
- **RTT only** — one-way latency requires NTP/PTP sync, not yet configured
- **Python overhead** included in all figures — C++ port planned for precision phase
- **RMW swaps** — restart both machines between runs; mismatched RMWs fail silently

---

## 📄 License

MIT License — University of Idaho Computer Science, Coeur d'Alene. See [LICENSE](LICENSE).

## 🤝 Contributing

See the [research journal](documentation/ros2_bench_journal.md) for design decisions, known limitations, and planned phases before contributing.

## 📦 Versions

| Version | Description |
|---------|-------------|
| 0.1.0   | Prototype — ping-pong RTT, Python nodes, ICMP baseline |
