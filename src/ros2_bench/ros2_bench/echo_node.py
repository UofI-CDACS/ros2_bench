"""
echo_node — ros2_bench
======================
Runs on the *responder* machine (Machine B).

Subscribes to  /bench/ping
Publishes  to  /bench/pong

The message payload is echoed back completely unchanged so that the sender
can read its own embedded timestamp and calculate RTT without any clock
synchronisation between the two machines.

Launch
------
  ros2 run ros2_bench echo

Optionally override the topic names via ROS2 parameters:
  ros2 run ros2_bench echo --ros-args -p ping_topic:=/bench/ping -p pong_topic:=/bench/pong
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class EchoNode(Node):

    def __init__(self):
        super().__init__('bench_echo')

        # -- parameters (override via --ros-args -p name:=value) --------------
        self.declare_parameter('ping_topic', '/bench/ping')
        self.declare_parameter('pong_topic', '/bench/pong')

        ping_topic = self.get_parameter('ping_topic').get_parameter_value().string_value
        pong_topic = self.get_parameter('pong_topic').get_parameter_value().string_value

        # -- publisher & subscriber -------------------------------------------
        self._pub = self.create_publisher(String, pong_topic, qos_profile=10)

        self._sub = self.create_subscription(
            String,
            ping_topic,
            self._on_ping,
            qos_profile=10,
        )

        self.get_logger().info(
            f'EchoNode ready  |  listening on "{ping_topic}"  →  replying on "{pong_topic}"'
        )

    # -------------------------------------------------------------------------

    def _on_ping(self, msg: String) -> None:
        """Immediately republish the received message to the pong topic."""
        self._pub.publish(msg)


# -----------------------------------------------------------------------------

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


if __name__ == '__main__':
    main()
