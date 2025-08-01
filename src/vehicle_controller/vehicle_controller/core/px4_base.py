"""
Base PX4 Controller Class
Handles all the boilerplate ROS2 and PX4 communication
"""

__author__ = "PresidentPlant"
__contact__ = ""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy

# PX4 Messages
"""msgs for subscription"""
from px4_msgs.msg import VehicleStatus
from px4_msgs.msg import VehicleLocalPosition
from px4_msgs.msg import VehicleGlobalPosition
from px4_msgs.msg import SensorGps  # for log
from px4_msgs.msg import MissionResult

"""msgs for publishing"""
from px4_msgs.msg import VehicleCommand
from px4_msgs.msg import OffboardControlMode
from px4_msgs.msg import TrajectorySetpoint

import os
import numpy as np
from abc import ABC, abstractmethod

# gps
import pymap3d as p3d

# Custom Messages
from custom_msgs.msg import VehicleState, TargetLocation


class PX4BaseController(Node, ABC):
    """
    Base class for PX4 controllers that handles all the boilerplate
    ROS2 and PX4 communication setup.
    """

    def __init__(self, node_name: str, timer_period: float = 0.01):
        super().__init__(node_name)

        self.time_period = timer_period

        # Configure QoS profile
        self._setup_qos()

        # Initialize state variables
        self._init_state_variables()

        # Create subscribers and publishers
        self._create_subscribers()
        self._create_publishers()

        # Setup timers
        self._setup_timers()
        self.get_logger().info(f"{node_name} initialized")

    # =======================================
    # Setup functions (__init__)
    # =======================================

    def _setup_qos(self):
        """Configure QoS profile for publishing and subscribing"""
        self.qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

    def _init_state_variables(self):
        """Initialize all state variables"""
        # Vehicle status
        self.vehicle_status = VehicleStatus()
        self.vehicle_local_position = VehicleLocalPosition()
        self.vehicle_global_position = VehicleGlobalPosition()

        self.offboard_control_mode_params = {
            "position": True,
            "velocity": False,
            "acceleration": False,
            "attitude": False,
            "body_rate": False,
            "thrust_and_torque": False,
            "direct_actuator": False,
        }

        # Position, velocity, and yaw
        self.pos = np.array([0.0, 0.0, 0.0])  # local NED
        self.pos_gps = np.array([0.0, 0.0, 0.0])  # global GPS
        self.vel = np.array([0.0, 0.0, 0.0])
        self.yaw = 0.0

        # Home position
        self.get_position_flag = False
        self.home_set_flag = False
        self.home_position = np.array([0.0, 0.0, 0.0])
        self.home_position_gps = np.array([0.0, 0.0, 0.0])
        self.home_yaw = 0.0

        # Mission variables
        self.mission_result = None
        self.mission_wp_num = None

        # Heartbeat timing
        self.last_heartbeat_time = None

    def _create_subscribers(self):
        """Create all ROS2 subscribers"""
        self.vehicle_status_subscriber = self.create_subscription(
            VehicleStatus,
            "/fmu/out/vehicle_status_v1",
            self._vehicle_status_callback,
            self.qos_profile,
        )

        self.vehicle_local_position_subscriber = self.create_subscription(
            VehicleLocalPosition,
            "/fmu/out/vehicle_local_position",
            self._vehicle_local_position_callback,
            self.qos_profile,
        )

        self.vehicle_global_position_subscriber = self.create_subscription(
            VehicleGlobalPosition,
            "/fmu/out/vehicle_global_position",
            self._vehicle_global_position_callback,
            self.qos_profile,
        )
        
        self.vehicle_gps_subscriber = self.create_subscription(
            SensorGps,
            "/fmu/out/vehicle_gps_position",
            self._vehicle_gps_callback,
            self.qos_profile,
        )

        self.vehicle_mission_subscriber = self.create_subscription(
            MissionResult,
            "/fmu/out/mission_result",
            self._mission_result_callback,
            self.qos_profile,
        )

    def _create_publishers(self):
        """Create all ROS2 publishers"""
        self.vehicle_command_publisher = self.create_publisher(
            VehicleCommand, "/fmu/in/vehicle_command", self.qos_profile
        )

        self.offboard_control_mode_publisher = self.create_publisher(
            OffboardControlMode, "/fmu/in/offboard_control_mode", self.qos_profile
        )

        self.trajectory_setpoint_publisher = self.create_publisher(
            TrajectorySetpoint, "/fmu/in/trajectory_setpoint", self.qos_profile
        )

        self.vehicle_state_publisher = self.create_publisher(
            VehicleState, "/vehicle_state", self.qos_profile
        )

    def _setup_timers(self):
        """Setup ROS2 timers"""
        self.offboard_heartbeat = self.create_timer(
            self.time_period, self._offboard_heartbeat_callback
        )

        self.main_timer = self.create_timer(self.time_period, self._main_timer_callback)

    # =======================================
    # Timer Callback functions
    # =======================================

    def _offboard_heartbeat_callback(self):
        """Heartbeat callback to maintain offboard mode"""
        now = self.get_clock().now()
        if self.last_heartbeat_time is not None:
            delta = (now - self.last_heartbeat_time).nanoseconds / 1e9
            if delta > 1.0:
                self.get_logger().warn(f"Heartbeat delay detected: {delta:.3f}s")
        self.last_heartbeat_time = now

        # Publish offboard control mode (can be overridden)
        self.publish_offboard_control_mode(**self.offboard_control_mode_params)

    def _main_timer_callback(self):
        """Main timer callback - calls the abstract main_loop method"""
        try:
            self.main_loop()
        except Exception as e:
            self.get_logger().error(f"Error in main loop: {e}")

    @abstractmethod
    def main_loop(self):
        """
        Abstract method to be implemented by subclasses.
        This is where the main control logic should go.
        """
        pass

    # =======================================
    # Subscriber Callback functions
    # =======================================

    def _vehicle_status_callback(self, msg):
        """Callback for vehicle status updates"""
        self.vehicle_status = msg
        self.on_vehicle_status_update(msg)

    def _vehicle_local_position_callback(self, msg):
        """Callback for local position updates"""
        self.vehicle_local_position = msg
        self.pos = np.array([msg.x, msg.y, msg.z])
        self.vel = np.array([msg.vx, msg.vy, msg.vz])
        self.yaw = msg.heading
        self.on_local_position_update(msg)

    def _vehicle_global_position_callback(self, msg):
        """Callback for global position updates"""
        self.get_position_flag = True
        self.vehicle_global_position = msg
        self.pos_gps = np.array([msg.lat, msg.lon, msg.alt])
        self.on_global_position_update(msg)
        if self.get_position_flag and self.home_set_flag:
            self.pos = self.pos - self.home_position

    def _vehicle_gps_callback(self, msg):
        """Callback for GPS updates"""
        if msg.fix_type >= 3:
            self.vehicle_gps = msg
        else:
            self.get_logger().warn("GPS fix type is less than 3, no valid GPS data")          

    def _mission_result_callback(self, msg):
        self.mission_result = msg
        self.mission_wp_num = msg.seq_current

    # =======================================
    # Additional Functions
    # =======================================

    def on_vehicle_status_update(self, msg):
        """Override to handle vehicle status updates"""
        # Could add additional status monitoring here
        pass

    def on_local_position_update(self, msg):
        """Override to handle local position updates"""
        # Could add position monitoring here
        pass

    def on_global_position_update(self, msg):
        """Override to handle global position updates"""
        # Could add GPS monitoring here
        pass

    # =======================================
    # Publisher Callback functions
    # =======================================

    def publish_vehicle_command(self, command, **kwargs):
        """Publish a vehicle command"""
        msg = VehicleCommand()
        msg.command = command
        msg.param1 = kwargs.get("param1", float("nan"))
        msg.param2 = kwargs.get("param2", float("nan"))
        msg.param3 = kwargs.get("param3", float("nan"))
        msg.param4 = kwargs.get("param4", float("nan"))
        msg.param5 = kwargs.get("param5", float("nan"))
        msg.param6 = kwargs.get("param6", float("nan"))
        msg.param7 = kwargs.get("param7", float("nan"))
        msg.target_system = 1
        msg.target_component = 1
        msg.source_system = 1
        msg.source_component = 1
        msg.from_external = True
        msg.timestamp = int(self.get_clock().now().nanoseconds / 1000)
        self.vehicle_command_publisher.publish(msg)

    def publish_offboard_control_mode(self, **kwargs):
        """Publish offboard control mode"""
        msg = OffboardControlMode()
        msg.position = kwargs.get("position", False)
        msg.velocity = kwargs.get("velocity", False)
        msg.acceleration = kwargs.get("acceleration", False)
        msg.attitude = kwargs.get("attitude", False)
        msg.body_rate = kwargs.get("body_rate", False)
        msg.thrust_and_torque = kwargs.get("thrust_and_torque", False)
        msg.direct_actuator = kwargs.get("direct_actuator", False)
        msg.timestamp = int(self.get_clock().now().nanoseconds / 1000)
        self.offboard_control_mode_publisher.publish(msg)

    def publish_setpoint(self, **kwargs):
        """Publish trajectory setpoint (relative to home position)"""
        msg = TrajectorySetpoint()
        msg.position = list(
            kwargs.get("pos_sp", np.nan * np.zeros(3)) + self.home_position
        )
        msg.velocity = list(kwargs.get("vel_sp", np.nan * np.zeros(3)))
        msg.yaw = kwargs.get("yaw_sp", float("nan"))
        msg.timestamp = int(self.get_clock().now().nanoseconds / 1000)
        self.trajectory_setpoint_publisher.publish(msg)

    # =======================================
    # Set Home position functions
    # =======================================

    def set_home_position(self):
        self.home_position = self.pos
        self.home_position_gps = self.pos_gps
        self.home_yaw = self.yaw
        self.home_set_flag = True

    def set_gps_to_local(self, num_wp, gps_WP, home_gps_pos):
        """
        Convert GPS waypoints to local coordinates relative to home position.
        input: num_wp, gps_WP, home_gps_pos
        output: local_wp array
        """
        # home_gps_pos = currunt self.pos
        if self.home_set_flag:
            # convert GPS waypoints to local coordinates relative to home position
            local_wp = []
            for i in range(0, num_wp):
                # gps_WP = [lat, lon, rel_alt]
                wp_position = p3d.geodetic2ned(
                    self.gps_WP[i][0],
                    self.gps_WP[i][1],
                    self.gps_WP[i][2] + home_gps_pos[2],
                    home_gps_pos[0],
                    home_gps_pos[1],
                    home_gps_pos[2],
                )
                wp_position = np.array(wp_position)
                local_wp.append(wp_position)
            return local_wp
        else:
            self.get_logger().error(
                "Home position not set. Cannot convert GPS to local coordinates."
            )
            return None

    # =======================================
    # Utility methods
    # check & change vehicle status
    # =======================================

    # check

    def is_armed(self):
        """Check if vehicle is armed"""
        return self.vehicle_status.arming_state == VehicleStatus.ARMING_STATE_ARMED

    def is_disarmed(self):
        """Check if vehicle is disarmed"""
        return self.vehicle_status.arming_state == VehicleStatus.ARMING_STATE_DISARMED

    def is_offboard_mode(self):
        """Check if vehicle is in offboard mode"""
        return self.vehicle_status.nav_state == VehicleStatus.NAVIGATION_STATE_OFFBOARD

    def is_mission_mode(self):
        """Check if vehicle is in mission mode"""
        return (
            self.vehicle_status.nav_state == VehicleStatus.NAVIGATION_STATE_AUTO_MISSION
        )

    def is_auto_takeoff(self):
        """Check if vehicle is in auto takeoff mode"""
        return (
            self.vehicle_status.nav_state == VehicleStatus.NAVIGATION_STATE_AUTO_TAKEOFF
        )

    def is_auto_loiter(self):
        """Check if vehicle is in auto loiter mode"""
        return (
            self.vehicle_status.nav_state == VehicleStatus.NAVIGATION_STATE_AUTO_LOITER
        )

    # publish

    def arm(self):
        """Arm the vehicle"""
        self.publish_vehicle_command(
            VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, param1=1.0
        )

    def disarm(self):
        """Disarm the vehicle"""
        self.publish_vehicle_command(
            VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, param1=0.0
        )

    def set_offboard_mode(self):
        """Set vehicle to offboard mode"""
        if not self.is_offboard_mode():
            # 1:main mode, 2:mode=offboard
            self.publish_vehicle_command(
                VehicleCommand.VEHICLE_CMD_DO_SET_MODE, param1=1.0, param2=6.0
            )

    def set_mission_mode(self):
        """Set vehicle to mission mode"""
        if not self.is_mission_mode():
            # 1:main mode, 2:mode=mission
            self.publish_vehicle_command(
                VehicleCommand.VEHICLE_CMD_DO_SET_MODE,
                param1=1.0,
                param2=4.0,
                param3=4.0,
            )

    def takeoff(self, altitude=None):
        """Command takeoff"""
        if altitude is not None:
            self.publish_vehicle_command(
                VehicleCommand.VEHICLE_CMD_NAV_TAKEOFF, param7=altitude
            )
        else:
            self.publish_vehicle_command(VehicleCommand.VEHICLE_CMD_NAV_TAKEOFF)

    def land(self, altitude=0.0):
        """Command landing"""
        self.publish_vehicle_command(
            VehicleCommand.VEHICLE_CMD_NAV_LAND, param7=altitude
        )
