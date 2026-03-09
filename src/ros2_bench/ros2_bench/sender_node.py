"""
sender_node — ros2_bench
========================
Runs on the *sender* machine (Machine A).

Workflow per message
--------------------
1.  Build a JSON payload:  {"seq": N, "t_send_ns": <mono_ns>, "payload": ""}
2.  Publish it on  /bench/ping
3.  Wait for the echo back on  /bench/pong
4.  Record t_recv_ns (same clock, same machine — no cross-machine sync needed)
5.  Log a JSON line to stdout:

    {
      "seq":              1,
      "t_send_ns":        123456789000,
      "t_recv_ns":        123457012000,
      "rtt_ms":           0.223,          # wall-clock round-trip
      "ping_ms":          0.118,          # ICMP baseline (half = one-way estimate)
      "ros2_overhead_ms": 0.105,          # rtt_ms − ping_ms
      "rmw":              "rmw_fastrtps_cpp",
      "sender_host":      "pi-a",
      "responder_ip":     "192.168.1.101",
      "msg_bytes":        64
    }

ICMP baseline
-------------
A burst of pings is sent to the responder IP *before* the benchmark starts.
The median round-trip from ping is stored and subtracted from every ROS2 RTT
to estimate middleware overhead.

Because both t_send_ns and t_recv_ns are taken on Machine A with the same
monotonic clock, the calculation is valid even without NTP sync.

Launch
------
  ros2 run ros2_bench sender --ros-args \\
      -p responder_ip:=192.168.1.101 \\
      -p send_count:=100             \\
      -p send_interval_ms:=100       \\
      -p ping_samples:=20

Parameters
----------
responder_ip     IP of the echo node machine                 (required)
send_count       Number of messages to send                  (default 100)
send_interval_ms Milliseconds between sends                  (default 100)
ping_samples     ICMP pings to fire for baseline measurement (default 20)
ping_topic       (default /bench/ping)
pong_topic       (default /bench/pong)
"""

import json
import os
import re
import socket
import subprocess
import time
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mono_ns() -> int:
    """Monotonic nanosecond timestamp — always from Machine A's clock."""
    return time.monotonic_ns()


def _measure_icmp_baseline(host: str, samples: int, logger) -> float | None:
    """
    Fire `samples` pings at `host`, return the *median* RTT in milliseconds.
    Returns None if ping fails (e.g. firewall blocks ICMP).
    """
    logger.info(f'Running ICMP baseline: {samples} pings → {host} …')
    try:
        result = subprocess.run(
            ['ping', '-c', str(samples), '-q', host],
            capture_output=True,
            text=True,
            timeout=samples * 2 + 5,  # generous timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warn(f'ping failed: {exc}')
        return None

    # Parse "rtt min/avg/max/mdev = 0.412/0.531/0.812/0.091 ms"
    match = re.search(
        r'rtt min/avg/max/mdev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms',
        result.stdout,
    )
    if not match:
        logger.warn(f'Could not parse ping output:\n{result.stdout}')
        return None

    avg_ms = float(match.group(2))
    logger.info(f'ICMP baseline  min/avg/max = '
                f'{match.group(1)}/{match.group(2)}/{match.group(3)} ms')
    return avg_ms


def _detect_rmw() -> str:
    """Read RMW_IMPLEMENTATION env var; fall back to a readable default."""
    return os.environ.get('RMW_IMPLEMENTATION', 'rmw_fastrtps_cpp (default)')


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class SenderNode(Node):

    def __init__(self):
        super().__init__('bench_sender')

        # -- parameters -------------------------------------------------------
        self.declare_parameter('responder_ip',    '')
        self.declare_parameter('send_count',      100)
        self.declare_parameter('send_interval_ms', 100)
        self.declare_parameter('ping_samples',    20)
        self.declare_parameter('ping_topic',      '/bench/ping')
        self.declare_parameter('pong_topic',      '/bench/pong')

        self._responder_ip    = self.get_parameter('responder_ip').get_parameter_value().string_value
        self._send_count      = self.get_parameter('send_count').get_parameter_value().integer_value
        self._interval_s      = self.get_parameter('send_interval_ms').get_parameter_value().integer_value / 1000.0
        self._ping_samples    = self.get_parameter('ping_samples').get_parameter_value().integer_value
        ping_topic            = self.get_parameter('ping_topic').get_parameter_value().string_value
        pong_topic            = self.get_parameter('pong_topic').get_parameter_value().string_value

        if not self._responder_ip:
            self.get_logger().fatal(
                'Parameter "responder_ip" is required.\n'
                '  ros2 run ros2_bench sender --ros-args -p responder_ip:=<IP>'
            )
            raise SystemExit(1)

        # -- state ------------------------------------------------------------
        self._rmw             = _detect_rmw()
        self._sender_host     = socket.gethostname()
        self._ping_baseline   = None   # filled before first send
        self._pending: dict[int, int] = {}   # seq → t_send_ns
        self._seq             = 0
        self._received        = 0
        self._lock            = threading.Lock()

        # -- pub / sub --------------------------------------------------------
        self._pub = self.create_publisher(String, ping_topic, qos_profile=10)
        self._sub = self.create_subscription(
            String, pong_topic, self._on_pong, qos_profile=10
        )

        self.get_logger().info(
            f'SenderNode ready  |  {self._sender_host} → {self._responder_ip}'
            f'  |  RMW: {self._rmw}'
            f'  |  {self._send_count} msgs @ {self._interval_s * 1000:.0f} ms intervals'
        )

        # -- kick off benchmark in a background thread so spin() can run -----
        self._bench_thread = threading.Thread(
            target=self._run_benchmark, daemon=True
        )
        self._bench_thread.start()

    # -------------------------------------------------------------------------

    def _on_pong(self, msg: String) -> None:
        """Called on receipt of an echoed message. Records RTT and logs."""
        t_recv_ns = _mono_ns()

        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Received non-JSON pong: {msg.data!r}')
            return

        seq = data.get('seq')
        t_send_ns = data.get('t_send_ns')

        if seq is None or t_send_ns is None:
            self.get_logger().warn(f'Pong missing fields: {data}')
            return

        rtt_ms = (t_recv_ns - t_send_ns) / 1_000_000.0

        # ROS2 overhead = RTT minus full ICMP ping round-trip baseline.
        # This gives a conservative (upper-bound) overhead estimate.
        ping_ms     = self._ping_baseline
        overhead_ms = (rtt_ms - ping_ms) if ping_ms is not None else None

        record = {
            'seq':              seq,
            't_send_ns':        t_send_ns,
            't_recv_ns':        t_recv_ns,
            'rtt_ms':           round(rtt_ms, 6),
            'ping_ms':          round(ping_ms, 6) if ping_ms is not None else None,
            'ros2_overhead_ms': round(overhead_ms, 6) if overhead_ms is not None else None,
            'rmw':              self._rmw,
            'sender_host':      self._sender_host,
            'responder_ip':     self._responder_ip,
            'msg_bytes':        len(msg.data.encode()),
        }

        # JSON-lines to stdout — easy to redirect / pipe to a DB exporter
        print(json.dumps(record), flush=True)

        with self._lock:
            self._pending.pop(seq, None)
            self._received += 1

    # -------------------------------------------------------------------------

    def _run_benchmark(self) -> None:
        """Background thread: measure baseline, then send all messages."""

        # 1. ICMP baseline — must complete before we start sending
        self._ping_baseline = _measure_icmp_baseline(
            self._responder_ip, self._ping_samples, self.get_logger()
        )

        # 2. Brief pause to let the echo node subscriber connect
        self.get_logger().info('Waiting 1 s for DDS discovery …')
        time.sleep(1.0)

        self.get_logger().info(f'Starting benchmark: {self._send_count} messages …')

        for i in range(self._send_count):
            seq = self._seq
            self._seq += 1

            t_send_ns = _mono_ns()

            payload = json.dumps({
                'seq':       seq,
                't_send_ns': t_send_ns,
                'payload':   '',       # extend here for size sweep later
            })

            msg = String()
            msg.data = payload

            with self._lock:
                self._pending[seq] = t_send_ns

            self._pub.publish(msg)

            time.sleep(self._interval_s)

        # 3. Wait a little for in-flight pongs to arrive
        self.get_logger().info('All messages sent. Waiting for stragglers …')
        time.sleep(max(2.0, self._interval_s * 5))

        # 4. Summary
        with self._lock:
            lost = len(self._pending)

        self.get_logger().info(
            f'Benchmark complete  |  sent={self._send_count}'
            f'  received={self._received}'
            f'  lost={lost}'
        )

        # Signal the main thread to shut down
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


if __name__ == '__main__':
    main()
