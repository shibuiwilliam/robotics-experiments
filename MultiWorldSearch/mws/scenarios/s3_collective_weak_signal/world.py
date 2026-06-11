"""MuJoCo world for Scenario 3: collective weak signal discovery.

World: inspection floor with 10 objects on a conveyor.
Objects obj_01..obj_10, of which obj_01, obj_04, obj_07, obj_10 belong to lot_L
and carry a micro_defect attribute.  Three patrol robots observe over time.
"""

from __future__ import annotations

SCENARIO3_XML = """
<mujoco model="inspection_floor">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 5" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="10 10 0.1" rgba="0.92 0.92 0.92 1"/>

    <!-- 10 objects on the conveyor line, spaced 1m apart -->
    <body name="obj_01" pos="0 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_02" pos="1 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_03" pos="2 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_04" pos="3 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_05" pos="4 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_06" pos="5 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_07" pos="6 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_08" pos="7 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_09" pos="8 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>
    <body name="obj_10" pos="9 0 0.15">
      <geom type="box" size="0.1 0.1 0.15" rgba="0.7 0.7 0.3 1"/>
    </body>

    <!-- Three patrol robots at different stations -->
    <body name="robot_1" pos="-1 -2 0.2">
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.8 0.3 1"/>
    </body>
    <body name="robot_2" pos="4 -2 0.2">
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.3 0.8 1"/>
    </body>
    <body name="robot_3" pos="8 -2 0.2">
      <geom type="capsule" size="0.12 0.2" rgba="0.8 0.3 0.3 1"/>
    </body>
  </worldbody>
</mujoco>
"""
