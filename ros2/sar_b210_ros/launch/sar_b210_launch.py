import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

ARGUMENTS = [
    DeclareLaunchArgument(
        'config_file', default_value='default.yaml',
        description='Run config from share/sar_b210_ros/config/ (an absolute path also works)'),
    DeclareLaunchArgument(
        'run_root', default_value=os.path.expanduser('~/b210_runs'),
        description='folder the run folders are written under'),
    DeclareLaunchArgument(
        'write_run_folder', default_value='true',
        description='write the run folder (iq.bin, bursts.csv, ...) when a run ends'),
    DeclareLaunchArgument(
        'publish_iq', default_value='true',
        description='false: b210/bursts carries metadata only (empty iq); raw IQ stays in the run folder'),
    DeclareLaunchArgument(
        'publish_queue_depth', default_value='192',
        description='bursts the publish queue holds (~65 bursts/s, 224 KB each; 192 is ~3 s)'),
    DeclareLaunchArgument(
        'namespace', default_value='',
        description='node namespace; topics are b210/... relative to it'),
]


def launch_setup(context, *args, **kwargs):
    config_file = LaunchConfiguration('config_file').perform(context)
    if not os.path.isabs(config_file):
        config_file = os.path.join(
            get_package_share_directory('sar_b210_ros'), 'config', config_file)
    node = Node(
        package='sar_b210_ros',
        executable='sar_b210_node',
        name='sar_b210',
        namespace=LaunchConfiguration('namespace'),
        output='screen',
        parameters=[{
            'config_path': config_file,
            'run_root': LaunchConfiguration('run_root'),
            'write_run_folder': ParameterValue(LaunchConfiguration('write_run_folder'), value_type=bool),
            'publish_iq': ParameterValue(LaunchConfiguration('publish_iq'), value_type=bool),
            'publish_queue_depth': ParameterValue(LaunchConfiguration('publish_queue_depth'), value_type=int),
        }],
    )
    return [node]


def generate_launch_description():
    return LaunchDescription(ARGUMENTS + [OpaqueFunction(function=launch_setup)])
