from setuptools import setup

package_name = "ros2_bench"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Your Name",
    maintainer_email="you@example.com",
    description="ROS2 middleware benchmarking suite",
    license="MIT",
    entry_points={
        "console_scripts": [
            "sender  = ros2_bench.sender_node:main",
            "echo    = ros2_bench.echo_node:main",
        ],
    },
)
