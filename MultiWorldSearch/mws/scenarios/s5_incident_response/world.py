"""MuJoCo world for Scenario 5: incident response.

World: multi-room facility with room_A (leak location), room_B (occluded region),
exit_1 and exit_2, and 3 robots (robot_1, robot_2, robot_3).
"""

from __future__ import annotations

SCENARIO5_XML = """
<mujoco model="incident_facility">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 6" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="10 10 0.1" rgba="0.9 0.9 0.9 1"/>

    <!-- room_A: leak location -->
    <body name="room_A_wall_north" pos="0 3 1">
      <geom type="box" size="4 0.1 1" rgba="0.7 0.7 0.7 1"/>
    </body>
    <body name="room_A_wall_south" pos="0 -3 1">
      <geom type="box" size="4 0.1 1" rgba="0.7 0.7 0.7 1"/>
    </body>

    <!-- leak_source: substance_X container in room_A -->
    <body name="leak_source" pos="1 0 0.3">
      <geom type="cylinder" size="0.3 0.3" rgba="0.9 0.2 0.1 1"/>
      <site name="leak_sensor" pos="0 0 0.3"/>
    </body>

    <!-- room_B: occluded region (behind partition) -->
    <body name="room_B_partition" pos="5 0 1">
      <geom type="box" size="0.1 3 1" rgba="0.6 0.6 0.6 1"/>
    </body>

    <!-- exit_1: main exit south -->
    <body name="exit_1" pos="0 -5 1">
      <geom type="box" size="0.8 0.1 1" rgba="0.2 0.8 0.2 1"/>
    </body>

    <!-- exit_2: secondary exit east -->
    <body name="exit_2" pos="8 0 1">
      <geom type="box" size="0.1 0.8 1" rgba="0.2 0.8 0.2 1"/>
    </body>

    <!-- robot_1: in room_A, will detect leak -->
    <body name="robot_1" pos="0 0 0.2">
      <joint name="r1_x" type="slide" axis="1 0 0"/>
      <joint name="r1_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.8 0.3 1"/>
    </body>

    <!-- robot_2: standby, will be dispatched for recon -->
    <body name="robot_2" pos="-2 -2 0.2">
      <joint name="r2_x" type="slide" axis="1 0 0"/>
      <joint name="r2_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.3 0.8 1"/>
    </body>

    <!-- robot_3: perimeter, will monitor leak -->
    <body name="robot_3" pos="3 2 0.2">
      <joint name="r3_x" type="slide" axis="1 0 0"/>
      <joint name="r3_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.8 0.8 0.3 1"/>
    </body>
  </worldbody>
</mujoco>
"""
