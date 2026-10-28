"""One-way Oli telemetry bridge; requires the vendor mrosbridger package."""

import re

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


DEFAULT_TOPICS = "/teleop/wb_cmd;/ImuData;/arm/ee_pose_state"


def validate_topics(value):
    if not value or not value.strip():
        raise ValueError("mros2ros_include must not be empty; select explicit topics.")
    if "；" in value or "\n" in value or "\r" in value:
        raise ValueError("Use ASCII ';' separators without line breaks.")
    topics = [topic.strip() for topic in value.split(";")]
    for topic in topics:
        if not re.fullmatch(r"/[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*", topic):
            raise ValueError(f"Invalid literal topic name: {topic!r}")
    if len(topics) != len(set(topics)):
        raise ValueError("Duplicate topics are not allowed.")
    return ";".join(topics)


def create_bridge(context):
    topics = validate_topics(LaunchConfiguration("mros2ros_include").perform(context))
    return [
        Node(
            package="mrosbridger",
            executable="mrosbridger",
            output="screen",
            parameters=[
                {
                    "bridge_mros2ros": ParameterValue(True, value_type=bool),
                    "bridge_ros2mros": ParameterValue(False, value_type=bool),
                    "ros2mros_execlude": ParameterValue("", value_type=str),
                    "ros2mros_include": ParameterValue("", value_type=str),
                    "mros2ros_execlude": ParameterValue("", value_type=str),
                    "mros2ros_include": ParameterValue(topics, value_type=str),
                }
            ],
        )
    ]


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "mros2ros_include",
                default_value=DEFAULT_TOPICS,
                description="Explicit mros source topics, separated by ASCII ';'.",
            ),
            OpaqueFunction(function=create_bridge),
        ]
    )