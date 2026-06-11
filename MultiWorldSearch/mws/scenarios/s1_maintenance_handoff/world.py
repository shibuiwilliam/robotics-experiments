"""MuJoCo world for Scenario 1: maintenance handoff.

World: 1 room with pump_07 (diagnostic channels), valve_03, workbench.
Two heterogeneous robots: PatrolRobot + MobileManipulator.
"""

from __future__ import annotations

SCENARIO1_XML = """
<mujoco model="maintenance_workshop">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 4" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="5 5 0.1" rgba="0.9 0.9 0.9 1"/>

    <!-- pump_07: the target equipment with diagnostic channels -->
    <body name="pump_07" pos="2 1 0.4">
      <joint name="pump_07_vibration" type="slide" axis="0 0 1" range="-0.01 0.01"/>
      <geom type="cylinder" size="0.25 0.4" rgba="0.7 0.3 0.3 1"/>
      <site name="pump_07_temp_sensor" pos="0 0 0.4"/>
      <site name="pump_07_vibration_sensor" pos="0.25 0 0"/>
    </body>

    <!-- valve_03: adjacent to pump, target for VLA skill -->
    <body name="valve_03" pos="2.5 1 0.3">
      <joint name="valve_03_handle" type="hinge" axis="0 0 1"/>
      <geom type="box" size="0.1 0.1 0.15" rgba="0.3 0.5 0.7 1"/>
    </body>

    <!-- workbench: tool/part storage -->
    <body name="workbench" pos="0 2 0.5">
      <geom type="box" size="0.8 0.4 0.5" rgba="0.6 0.5 0.4 1"/>
    </body>

    <!-- patrol_robot: mobile sensor platform -->
    <body name="patrol_robot" pos="-1 0 0.2">
      <joint name="patrol_x" type="slide" axis="1 0 0"/>
      <joint name="patrol_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.8 0.3 1"/>
    </body>

    <!-- mobile_manipulator: different morphology, VLA-driven -->
    <body name="mobile_manipulator" pos="3 -1 0.3">
      <joint name="manip_x" type="slide" axis="1 0 0"/>
      <joint name="manip_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.15 0.3" rgba="0.3 0.3 0.8 1"/>
      <body name="arm_link1" pos="0 0 0.3">
        <joint name="arm_joint1" type="hinge" axis="0 1 0"/>
        <geom type="capsule" size="0.04 0.2" rgba="0.4 0.4 0.9 1"/>
      </body>
    </body>
  </worldbody>

  <sensor>
    <jointpos name="pump_vibration_pos" joint="pump_07_vibration"/>
    <jointvel name="pump_vibration_vel" joint="pump_07_vibration"/>
    <jointpos name="valve_handle_pos" joint="valve_03_handle"/>
  </sensor>
</mujoco>
"""
