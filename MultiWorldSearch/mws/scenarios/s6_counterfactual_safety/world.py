"""MuJoCo world for Scenario 6: counterfactual safety.

World: hazardous warehouse with stacked cargo (3 boxes) and a pressurized valve.
Actor: SafetyAgent that queries MWS before acting.
"""

from __future__ import annotations

SCENARIO6_XML = """
<mujoco model="hazardous_warehouse">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 6" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="10 10 0.1" rgba="0.85 0.85 0.85 1"/>

    <!-- stacked_cargo: 3 boxes stacked (exceeds 2-box safety limit) -->
    <body name="cargo_box_1" pos="1 1 0.25">
      <geom type="box" size="0.25 0.25 0.25" rgba="0.8 0.5 0.2 1" mass="5"/>
      <body name="cargo_box_2" pos="0 0 0.5">
        <geom type="box" size="0.25 0.25 0.25" rgba="0.8 0.5 0.2 1" mass="5"/>
        <body name="cargo_box_3" pos="0 0 0.5">
          <geom type="box" size="0.25 0.25 0.25" rgba="0.9 0.3 0.3 1" mass="5"/>
        </body>
      </body>
    </body>

    <!-- pressurized_valve: pressure vessel with gauge -->
    <body name="pressurized_valve" pos="3 2 0.4">
      <geom type="cylinder" size="0.15 0.4" rgba="0.4 0.4 0.7 1"/>
      <site name="pressure_gauge" pos="0.15 0 0.2"/>
    </body>

    <!-- safety_agent: mobile robot that inspects hazards -->
    <body name="safety_agent" pos="-1 0 0.2">
      <joint name="safety_x" type="slide" axis="1 0 0"/>
      <joint name="safety_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.2 0.7 0.2 1"/>
    </body>
  </worldbody>

  <sensor>
    <framepos name="cargo3_pos" objtype="body" objname="cargo_box_3"/>
  </sensor>
</mujoco>
"""
