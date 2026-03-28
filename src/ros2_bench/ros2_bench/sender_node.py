"""
sender_node — ros2_bench
========================
Runs on the *sender* machine (Machine A).

Workflow per message
--------------------
1.  Build a JSON payload containing a unique sender_id (UUID), seq number,
    and send timestamp, then publish on /bench/ping.
2.  Echo nodes stamp their identity and republish on /bench/pong.
3.  This node receives ALL pongs on the domain but only processes ones
    where sender_id matches its own UUID — so multiple senders can share
    the same DDS domain without interference.
4.  RTT is recorded per (responder_ip, responder_node) pair.
5.  ICMP baseline is measured once per discovered responder, asynchronously,
    so message flow is never blocked by ping measurement.
6.  Loss is tracked per responder independently.

Output record (JSON line to stdout):
    {
      "sender_id":        "a1b2c3d4-...",   # UUID unique to this node instance
      "seq":              1,
      "t_send_ns":        123456789000,
      "t_recv_ns":        123457012000,
      "rtt_ms":           0.223,
      "ping_ms":          0.118,            # null until baseline completes
      "ros2_overhead_ms": 0.105,            # null until baseline completes
      "rmw":              "rmw_fastrtps_cpp",
      "sender_host":      "rospi-1",
      "responder_ip":     "172.23.254.22",
      "responder_node":   "bench_echo",
      "msg_bytes":        64
    }

Multi-sender / multi-responder
-------------------------------
Any number of sender nodes and echo nodes can run simultaneously on the domain.
- Each sender filters pongs by sender_id — ignores anything not addressed to it.
- Each echo node stamps its own IP and node name into the pong payload.
- Per-responder loss is reported in the summary at the end of the run.

Launch
------
  ros2 run ros2_bench sender --ros-args \\
      -p send_count:=100             \\
      -p send_interval_ms:=100       \\
      -p ping_samples:=20

Parameters
----------
send_count       Number of messages to send                  (default 100)
send_interval_ms Milliseconds between sends                  (default 100)
ping_samples     ICMP pings to fire for baseline measurement (default 20)
ping_topic       (default /bench/ping)
pong_topic       (default /bench/pong)
"""

import json
import os
import queue
import re
import socket
import subprocess
import threading
import time
import uuid

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mono_ns() -> int:
    """Monotonic nanosecond timestamp — always from this machine's clock."""
    return time.monotonic_ns()


def _measure_icmp_baseline(host: str, samples: int, logger) -> float | None:
    """
    Fire `samples` pings at `host`, return the average RTT in milliseconds.
    Returns None if ping fails (e.g. firewall blocks ICMP).
    """
    logger.info(f"ICMP baseline: {samples} pings → {host} …")
    try:
        result = subprocess.run(
            ["ping", "-c", str(samples), "-q", host],
            capture_output=True,
            text=True,
            timeout=samples * 2 + 5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warn(f"ping failed: {exc}")
        return None

    # Parse "rtt min/avg/max/mdev = 0.412/0.531/0.812/0.091 ms"
    match = re.search(
        r"rtt min/avg/max/mdev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms",
        result.stdout,
    )
    if not match:
        logger.warn(f"Could not parse ping output:\n{result.stdout}")
        return None

    logger.info(
        f"ICMP baseline ({host})  min/avg/max = "
        f"{match.group(1)}/{match.group(2)}/{match.group(3)} ms"
    )
    return float(match.group(2))


def _detect_rmw() -> str:
    return os.environ.get("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp (default)")


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class SenderNode(Node):

    def __init__(self):
        super().__init__("bench_sender")

        # -- parameters -------------------------------------------------------
        self.declare_parameter("send_count",       100)
        self.declare_parameter("send_interval_ms", 100)
        self.declare_parameter("ping_samples",     20)
        self.declare_parameter("ping_topic",       "/bench/ping")
        self.declare_parameter("pong_topic",       "/bench/pong")

        self._send_count   = self.get_parameter("send_count").get_parameter_value().integer_value
        self._interval_s   = self.get_parameter("send_interval_ms").get_parameter_value().integer_value / 1000.0
        self._ping_samples = self.get_parameter("ping_samples").get_parameter_value().integer_value
        ping_topic         = self.get_parameter("ping_topic").get_parameter_value().string_value
        pong_topic         = self.get_parameter("pong_topic").get_parameter_value().string_value

        # -- identity ---------------------------------------------------------
        # UUID4 is generated fresh each time the node starts.
        # Using UUID rather than hostname so that two nodes on the same machine
        # (or two machines with the same hostname) never collide.
        self._sender_id   = str(uuid.uuid4())
        self._sender_host = socket.gethostname()
        self._rmw         = _detect_rmw()

        # -- shared state (all access must hold self._lock) -------------------
        #
        # _responders: keyed by (responder_ip, responder_node)
        #   Each entry tracks:
        #     baseline     — ICMP avg RTT in ms, or None while pending
        #     received_seqs — set of seq numbers heard from this responder
        #
        # _pending: seq → t_send_ns
        #   Used to detect overall message loss at the end of the run.
        #   Popped on the first pong for a given seq (any responder).
        #   Per-responder loss is tracked via received_seqs, not _pending.
        #
        self._responders: dict[tuple[str, str], dict] = {}
        self._pending:    dict[int, int]              = {}
        self._received    = 0
        self._seq         = 0
        self._lock        = threading.Lock()

        # -- baseline queue ---------------------------------------------------
        # queue.Queue is thread-safe and has a blocking .get() built in.
        # The baseline worker waits on it without holding any lock or sleeping.
        # Items are (responder_ip, responder_node) tuples.
        self._baseline_q: queue.Queue = queue.Queue()
        self._baseline_thread = threading.Thread(
            target=self._baseline_worker, daemon=True
        )
        self._baseline_thread.start()

        # -- pub / sub --------------------------------------------------------
        self._pub = self.create_publisher(String, ping_topic, qos_profile=10)
        self._sub = self.create_subscription(
            String, pong_topic, self._on_pong, qos_profile=10
        )

        self.get_logger().info(
            f"SenderNode ready"
            f"  |  sender_id={self._sender_id}"
            f"  |  host={self._sender_host}"
            f"  |  RMW={self._rmw}"
            f"  |  send_count={self._send_count}"
            f"  |  interval_ms={self._interval_s * 1000:.0f}"
        )

        # -- benchmark thread -------------------------------------------------
        self._bench_thread = threading.Thread(
            target=self._run_benchmark, daemon=True
        )
        self._bench_thread.start()

    # -------------------------------------------------------------------------
    # Baseline worker
    # -------------------------------------------------------------------------

    def _baseline_worker(self) -> None:
        """
        Background thread. Blocks on the queue waiting for new responders.
        When one arrives, runs ICMP baseline and writes the result back into
        _responders under _lock.

        queue.Queue.get() releases no locks and holds no locks — it simply
        blocks until an item is available, so there is no contention with
        _on_pong or _run_benchmark while waiting.
        """
        while True:
            # Block here until a new (ip, node) pair needs a baseline.
            # timeout=1 lets the thread notice if the process is shutting down.
            try:
                ip, node = self._baseline_q.get(timeout=1)
            except queue.Empty:
                continue

            baseline = _measure_icmp_baseline(ip, self._ping_samples, self.get_logger())
            key = (ip, node)

            with self._lock:
                if key in self._responders:
                    self._responders[key]["baseline"] = baseline
                    self.get_logger().info(
                        f"Baseline stored for {node} @ {ip}: {baseline} ms"
                    )

    # -------------------------------------------------------------------------
    # Pong handler (called by rclpy.spin in the main thread)
    # -------------------------------------------------------------------------

    def _on_pong(self, msg: String) -> None:
        """
        Receives ALL pongs on the domain. Filters to only process messages
        addressed to this sender_id, then records RTT and updates per-responder
        state under _lock.
        """
        t_recv_ns = _mono_ns()

        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f"Non-JSON pong ignored: {msg.data!r}")
            return

        # --- Filter: only process pongs addressed to this sender instance ---
        if data.get("sender_id") != self._sender_id:
            return

        seq       = data.get("seq")
        t_send_ns = data.get("t_send_ns")

        if seq is None or t_send_ns is None:
            self.get_logger().warn(f"Pong missing fields: {data}")
            return

        rtt_ms         = (t_recv_ns - t_send_ns) / 1_000_000.0
        responder_ip   = data.get("responder_ip",   "unknown")
        responder_node = data.get("responder_node", "unknown")
        key            = (responder_ip, responder_node)

        new_responder = False

        with self._lock:
            if key not in self._responders:
                # First time we have heard from this responder.
                # Create its entry with baseline=None (will be filled async).
                self._responders[key] = {
                    "baseline":      None,
                    "received_seqs": set(),
                }
                new_responder = True

            self._responders[key]["received_seqs"].add(seq)
            baseline = self._responders[key]["baseline"]

            # Pop from _pending on first pong for this seq (any responder).
            # This tracks overall send/receive accounting.
            self._pending.pop(seq, None)
            self._received += 1

        # Schedule ICMP baseline outside the lock — queue.put() is thread-safe
        # and does not need _lock held.
        if new_responder:
            self._baseline_q.put((responder_ip, responder_node))

        overhead_ms = (rtt_ms - baseline) if baseline is not None else None

        record = {
            "sender_id":        self._sender_id,
            "seq":              seq,
            "t_send_ns":        t_send_ns,
            "t_recv_ns":        t_recv_ns,
            "rtt_ms":           round(rtt_ms, 6),
            "ping_ms":          round(baseline, 6) if baseline is not None else None,
            "ros2_overhead_ms": round(overhead_ms, 6) if overhead_ms is not None else None,
            "rmw":              self._rmw,
            "sender_host":      self._sender_host,
            "responder_ip":     responder_ip,
            "responder_node":   responder_node,
            "msg_bytes":        len(msg.data.encode()),
        }

        print(json.dumps(record), flush=True)

    # -------------------------------------------------------------------------
    # Benchmark thread
    # -------------------------------------------------------------------------

    def _run_benchmark(self) -> None:
        """
        Sends _send_count messages spaced _interval_s seconds apart, then
        waits for stragglers and prints a per-responder loss summary.
        """
        self.get_logger().info("Waiting 1 s for DDS discovery …")
        time.sleep(1.0)

        self.get_logger().info(f"Starting benchmark: {self._send_count} messages …")

        for _ in range(self._send_count):
            with self._lock:
                seq = self._seq
                self._seq += 1

            t_send_ns = _mono_ns()

            payload = json.dumps({
                "sender_id": self._sender_id,
                "seq":       seq,
                "t_send_ns": t_send_ns,
                "payload":   "",          # extend for message size sweep
            })

            msg      = String()
            msg.data = payload

            with self._lock:
                self._pending[seq] = t_send_ns

            self._pub.publish(msg)
            time.sleep(self._interval_s)

        # Wait for in-flight pongs
        self.get_logger().info("All messages sent. Waiting for stragglers …")
        time.sleep(max(2.0, self._interval_s * 5))

        # --- Per-responder loss summary --------------------------------------
        with self._lock:
            overall_lost  = len(self._pending)
            responder_snap = {
                k: len(v["received_seqs"])
                for k, v in self._responders.items()
            }

        self.get_logger().info(
            f"Benchmark complete  |  sent={self._send_count}"
            f"  total_received={self._received}"
            f"  overall_lost={overall_lost}"
        )

        for (ip, node), received in responder_snap.items():
            lost = self._send_count - received
            self.get_logger().info(
                f"  responder {node} @ {ip}"
                f"  received={received}"
                f"  lost={lost}"
                f"  loss%={lost / self._send_count * 100:.1f}%"
            )

        rclpy.shutdown()


# ---------------------------------------------------------------------------


def main(args=None):
    rclpy.init(args=args)
    try:
        node = SenderNode()
    except SystemExit:
        return

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()


if __name__ == "__main__":
    main()
