"""MuJoCo world for Scenario 7: Order to Fulfillment.

World: Warehouse with 3 shelves (sku_A, sku_B, sku_C), a packing_station,
and a picking_robot.
"""

from __future__ import annotations

SCENARIO7_XML = """
<mujoco model="order_fulfillment_warehouse">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 4" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="10 10 0.1" rgba="0.9 0.9 0.9 1"/>

    <!-- shelf_A: holds sku_A items (3 units, 1 damaged) -->
    <body name="shelf_A" pos="1 0 1.0">
      <geom type="box" size="0.5 0.3 1.0" rgba="0.6 0.4 0.3 1"/>
      <body name="sku_A_1" pos="-0.2 0 0.8">
        <geom type="box" size="0.08 0.08 0.08" rgba="0.2 0.6 0.2 1"/>
      </body>
      <body name="sku_A_2" pos="0.0 0 0.8">
        <geom type="box" size="0.08 0.08 0.08" rgba="0.2 0.6 0.2 1"/>
      </body>
      <body name="sku_A_3_damaged" pos="0.2 0 0.8">
        <geom type="box" size="0.08 0.08 0.08" rgba="0.8 0.2 0.2 1"/>
      </body>
    </body>

    <!-- shelf_B: holds sku_B items (2 units) -->
    <body name="shelf_B" pos="3 0 1.0">
      <geom type="box" size="0.5 0.3 1.0" rgba="0.6 0.4 0.3 1"/>
      <body name="sku_B_1" pos="-0.1 0 0.8">
        <geom type="box" size="0.08 0.08 0.08" rgba="0.2 0.2 0.6 1"/>
      </body>
      <body name="sku_B_2" pos="0.1 0 0.8">
        <geom type="box" size="0.08 0.08 0.08" rgba="0.2 0.2 0.6 1"/>
      </body>
    </body>

    <!-- shelf_C: physically empty (ghost inventory: WMS says 5, reality 0) -->
    <body name="shelf_C" pos="5 0 1.0">
      <geom type="box" size="0.5 0.3 1.0" rgba="0.6 0.4 0.3 1"/>
    </body>

    <!-- packing_station -->
    <body name="packing_station" pos="3 3 0.5">
      <geom type="box" size="0.8 0.6 0.5" rgba="0.5 0.5 0.5 1"/>
    </body>

    <!-- picking_robot -->
    <body name="picking_robot" pos="0 2 0.3">
      <joint name="pick_x" type="slide" axis="1 0 0"/>
      <joint name="pick_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.15 0.3" rgba="0.3 0.7 0.3 1"/>
      <body name="gripper" pos="0 0 0.3">
        <joint name="gripper_joint" type="hinge" axis="0 1 0"/>
        <geom type="box" size="0.05 0.02 0.1" rgba="0.4 0.4 0.9 1"/>
      </body>
    </body>
  </worldbody>

  <sensor>
    <jointpos name="pick_x_pos" joint="pick_x"/>
    <jointpos name="pick_y_pos" joint="pick_y"/>
  </sensor>
</mujoco>
"""
