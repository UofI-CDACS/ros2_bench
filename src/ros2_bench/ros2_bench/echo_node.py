"""
echo_node — ros2_bench
======================
Runs on any *responder* machine.

Subscribes to  /bench/ping
Publishes  to  /bench/pong

Parses the incoming JSON, stamps its own IP and node name into the payload,
and republishes. The sender_id from the original ping is preserved unchanged
so each sender can filter pongs addressed to it.

Multiple echo nodes can run simultaneously on the same DDS domain — each
stamps its own identity, so senders can tell responses apart.

Launch
------
  ros2 run ros2_bench echo

Override topic names if needed:
  ros2 run ros2_bench echo --ros-args -p ping_topic:=/bench/ping -p pong_topic:=/bench/pong
"""

import json
import socket

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def _local_ip() -> str:
    """
    Resolve this machine's non-loopback IP using a UDP trick:
    connect a socket to an external address (no data is sent) and read
    back which local interface the OS chose. More reliable than hostname
    resolution on machines with multiple interfaces or unusual /etc/hosts.
    Falls back to 127.0.0.1 if it fails.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class EchoNode(Node):
    def __init__(self):
        super().__init__("bench_echo")

        self.declare_parameter("ping_topic", "/bench/ping")
        self.declare_parameter("pong_topic", "/bench/pong")

        ping_topic = self.get_parameter("ping_topic").get_parameter_value().string_value
        pong_topic = self.get_parameter("pong_topic").get_parameter_value().string_value

        self._pub = self.create_publisher(String, pong_topic, qos_profile=10)
        self._sub = self.create_subscription(
            String, ping_topic, self._on_ping, qos_profile=10
        )

        # Resolve once at startup — IP won't change during a run i hope
        self._my_ip = _local_ip()

        self.get_logger().info(
            f'EchoNode ready  |  {self._my_ip}  |  "{ping_topic}" → "{pong_topic}"'
        )

    def _on_ping(self, msg: String) -> None:
        """
        Parse the ping, stamp responder identity, republish as pong.
        sender_id is preserved unchanged — the sender uses it to filter.
        """
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f"Non-JSON ping ignored: {msg.data!r}")
            return

        data["responder_ip"] = self._my_ip
        data["responder_node"] = self.get_name()

        pong = String()
        pong.data = json.dumps(data)
        self._pub.publish(pong)


def main(args=None):
    rclpy.init(args=args)
    node = EchoNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
