"""ros2 launch opensonar_ros sonar.launch.py [params:=FILE.yaml] [source:=IP|ip:port|FILE.son]"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch_ros.actions import Node


def _node(context):
    params = [context.launch_configurations["params"]]
    source = context.launch_configurations.get("source", "")
    if source:
        params.append({"source": source})
    return [Node(package="opensonar_ros", executable="sonar_node", name="sonar", output="screen", parameters=params)]


def generate_launch_description():
    default = os.path.join(get_package_share_directory("opensonar_ros"), "config", "sonar.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("params", default_value=default, description="parameter file"),
        DeclareLaunchArgument("source", default_value="", description="override the source: sonar IP, ip:port or .son"),
        OpaqueFunction(function=_node),
    ])
