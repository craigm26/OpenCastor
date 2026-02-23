"""
Sim-to-Real Transfer Bridge.

Exports OpenCastor episode memory to simulation formats for policy learning,
and imports trajectories from simulators back into the episode store.

Supported formats:
  - MuJoCo XML + MJCF trajectory (HDF5 / JSON)
  - Gazebo SDF + ROS2 bag (if ros2 is available)
  - Generic HDF5 trajectory (h5py)
  - OpenAI Gym-compatible rollout dicts (for stable-baselines3)

API:
  GET  /api/sim/export/{format}   — {episodes} → sim format file
  POST /api/sim/import            — upload sim trajectory → episode store
  GET  /api/sim/formats           — list supported export formats
  GET  /api/sim/config/{sim}      — generate sim config from RCAN

Env:
  CASTOR_SIM_DIR     — directory for sim files (default ~/.castor/sim)
  MUJOCO_MODEL_PATH  — path to existing MuJoCo XML model (optional)

Install (optional):
  pip install mujoco      (physics simulation)
  pip install h5py        (HDF5 trajectory storage)
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger("OpenCastor.SimBridge")

_SIM_DIR = os.getenv("CASTOR_SIM_DIR", os.path.expanduser("~/.castor/sim"))

try:
    import h5py

    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

try:
    import mujoco

    HAS_MUJOCO = True
except ImportError:
    HAS_MUJOCO = False

_singleton: Optional["SimBridge"] = None
_lock = threading.Lock()

_SUPPORTED_FORMATS = ["json", "mjcf", "sdf", "hdf5", "gym"]

# ── MuJoCo XML template ────────────────────────────────────────────────────────

_MUJOCO_XML_TEMPLATE = """\
<?xml version="1.0" ?>
<mujoco model="{robot_name}">
  <compiler angle="radian" coordinate="local" inertiafromgeom="true"/>
  <option gravity="0 0 -9.81" integrator="RK4" timestep="0.01"/>

  <worldbody>
    <light diffuse=".5 .5 .5" dir="0 0 -1" directional="true" pos="0 0 3"/>
    <geom friction="1 0.005 0.0001" name="floor" pos="0 0 0" rgba="0.8 0.9 0.8 1"
          size="20 20 0.1" type="plane"/>

    <body name="chassis" pos="0 0 0.1">
      <joint armature="0" axis="1 0 0" damping="0" limited="false" name="root_x" pos="0 0 0" type="slide"/>
      <joint armature="0" axis="0 1 0" damping="0" limited="false" name="root_y" pos="0 0 0" type="slide"/>
      <joint armature="0" axis="0 0 1" damping="0" limited="false" name="root_z" pos="0 0 0" type="hinge"/>
      <geom density="500" name="chassis_geom" pos="0 0 0" rgba="0.2 0.4 0.8 1"
            size="0.15 0.1 0.05" type="box"/>
      <camera name="front_cam" mode="fixed" pos="0.15 0 0.05" euler="0 15 0"/>

      <!-- Left wheel -->
      <body name="left_wheel" pos="-0.1 0.11 0">
        <joint axis="0 1 0" damping="0.1" name="left_wheel_joint" type="hinge"/>
        <geom density="500" name="lw_geom" rgba="0.1 0.1 0.1 1"
              size="0.05 0.02" type="cylinder"/>
      </body>
      <!-- Right wheel -->
      <body name="right_wheel" pos="-0.1 -0.11 0">
        <joint axis="0 1 0" damping="0.1" name="right_wheel_joint" type="hinge"/>
        <geom density="500" name="rw_geom" rgba="0.1 0.1 0.1 1"
              size="0.05 0.02" type="cylinder"/>
      </body>
    </body>
  </worldbody>

  <actuator>
    <velocity ctrllimited="true" ctrlrange="-10 10" joint="left_wheel_joint" name="left_motor"/>
    <velocity ctrllimited="true" ctrlrange="-10 10" joint="right_wheel_joint" name="right_motor"/>
  </actuator>
</mujoco>
"""

# ── Gazebo SDF template ────────────────────────────────────────────────────────

_GAZEBO_SDF_TEMPLATE = """\
<?xml version="1.0" ?>
<sdf version="1.8">
  <model name="{robot_name}">
    <pose>0 0 0.1 0 0 0</pose>
    <link name="chassis">
      <inertial>
        <mass>2.0</mass>
        <inertia><ixx>0.01</ixx><ixy>0</ixy><ixz>0</ixz><iyy>0.01</iyy><iyz>0</iyz><izz>0.01</izz></inertia>
      </inertial>
      <collision name="collision">
        <geometry><box><size>0.3 0.2 0.1</size></box></geometry>
      </collision>
      <visual name="visual">
        <geometry><box><size>0.3 0.2 0.1</size></box></geometry>
        <material><ambient>0.2 0.4 0.8 1</ambient></material>
      </visual>
    </link>

    <link name="left_wheel">
      <pose>-0.1 0.11 0 -1.5707963 0 0</pose>
      <inertial><mass>0.5</mass></inertial>
      <collision name="collision"><geometry><cylinder><radius>0.05</radius><length>0.02</length></cylinder></geometry></collision>
      <visual name="visual"><geometry><cylinder><radius>0.05</radius><length>0.02</length></cylinder></geometry></visual>
    </link>

    <link name="right_wheel">
      <pose>-0.1 -0.11 0 -1.5707963 0 0</pose>
      <inertial><mass>0.5</mass></inertial>
      <collision name="collision"><geometry><cylinder><radius>0.05</radius><length>0.02</length></cylinder></geometry></collision>
      <visual name="visual"><geometry><cylinder><radius>0.05</radius><length>0.02</length></cylinder></geometry></visual>
    </link>

    <joint name="left_wheel_joint" type="revolute">
      <parent>chassis</parent><child>left_wheel</child>
      <axis><xyz>0 0 1</xyz></axis>
    </joint>
    <joint name="right_wheel_joint" type="revolute">
      <parent>chassis</parent><child>right_wheel</child>
      <axis><xyz>0 0 1</xyz></axis>
    </joint>
  </model>
</sdf>
"""


class SimBridge:
    """Exports episodes to simulation formats and imports sim trajectories."""

    def __init__(self):
        os.makedirs(_SIM_DIR, exist_ok=True)
        logger.info(
            "SimBridge ready (sim_dir=%s, mujoco=%s, h5py=%s)",
            _SIM_DIR, HAS_MUJOCO, HAS_H5PY,
        )

    # ── Export ────────────────────────────────────────────────────────

    def export(
        self,
        episodes: list[dict],
        fmt: str = "json",
        robot_name: str = "opencastor_robot",
    ) -> dict:
        """Export episodes to the specified format.

        Returns {path, format, episode_count, size_bytes}.
        """
        fmt = fmt.lower()
        if fmt not in _SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {fmt}. Use: {_SUPPORTED_FORMATS}")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"episodes_{ts}.{fmt if fmt != 'mjcf' else 'xml'}"
        path = os.path.join(_SIM_DIR, filename)

        if fmt == "json":
            self._export_json(episodes, path)
        elif fmt == "mjcf":
            self._export_mjcf(episodes, path, robot_name)
        elif fmt == "sdf":
            self._export_sdf(episodes, path, robot_name)
        elif fmt == "hdf5":
            self._export_hdf5(episodes, path)
        elif fmt == "gym":
            self._export_gym(episodes, path)

        size = os.path.getsize(path) if os.path.exists(path) else 0
        return {
            "path": path,
            "format": fmt,
            "episode_count": len(episodes),
            "size_bytes": size,
        }

    def generate_sim_config(self, rcan_config: dict, sim: str = "mujoco") -> str:
        """Generate a simulation model config from an RCAN robot config."""
        robot_name = rcan_config.get("metadata", {}).get("robot_name", "opencastor_robot")
        if sim == "mujoco":
            return _MUJOCO_XML_TEMPLATE.format(robot_name=robot_name)
        elif sim in ("gazebo", "ros2"):
            return _GAZEBO_SDF_TEMPLATE.format(robot_name=robot_name)
        else:
            raise ValueError(f"Unknown sim: {sim}. Use: mujoco, gazebo")

    def import_trajectory(self, data: bytes, fmt: str = "json") -> list[dict]:
        """Parse a sim trajectory and return as episode dicts."""
        fmt = fmt.lower()
        if fmt == "json":
            return json.loads(data.decode())
        elif fmt == "hdf5" and HAS_H5PY:
            return self._import_hdf5(data)
        else:
            raise ValueError(f"Cannot import format: {fmt}")

    def supported_formats(self) -> list[str]:
        fmts = list(_SUPPORTED_FORMATS)
        if not HAS_H5PY:
            fmts.remove("hdf5")
        return fmts

    # ── Private export backends ───────────────────────────────────────

    def _export_json(self, episodes: list[dict], path: str):
        with open(path, "w") as f:
            json.dump(
                {
                    "format": "opencastor_episodes_v1",
                    "episode_count": len(episodes),
                    "episodes": episodes,
                },
                f,
                indent=2,
                default=str,
            )

    def _export_mjcf(self, episodes: list[dict], path: str, robot_name: str):
        """Export MuJoCo XML model + trajectory JSON sidecar."""
        xml = _MUJOCO_XML_TEMPLATE.format(robot_name=robot_name)
        with open(path, "w") as f:
            f.write(xml)
        # Write trajectory sidecar
        traj_path = path.replace(".xml", "_trajectory.json")
        self._export_json(episodes, traj_path)

    def _export_sdf(self, episodes: list[dict], path: str, robot_name: str):
        sdf = _GAZEBO_SDF_TEMPLATE.format(robot_name=robot_name)
        with open(path, "w") as f:
            f.write(sdf)
        traj_path = path.replace(".sdf", "_trajectory.json")
        self._export_json(episodes, traj_path)

    def _export_hdf5(self, episodes: list[dict], path: str):
        if not HAS_H5PY:
            # Fallback to JSON if h5py not installed
            self._export_json(episodes, path.replace(".hdf5", ".json"))
            return
        import numpy as np

        with h5py.File(path, "w") as f:
            f.attrs["format"] = "opencastor_v1"
            f.attrs["episode_count"] = len(episodes)
            for i, ep in enumerate(episodes):
                grp = f.create_group(f"episode_{i:04d}")
                for k, v in ep.items():
                    try:
                        grp.create_dataset(k, data=np.array(v) if isinstance(v, list) else str(v))
                    except Exception:
                        grp.attrs[k] = str(v)

    def _export_gym(self, episodes: list[dict], path: str):
        """Export as OpenAI Gym-compatible rollout dict (for stable-baselines3)."""
        rollouts = []
        for ep in episodes:
            rollouts.append(
                {
                    "obs": ep.get("observation", {}),
                    "acts": ep.get("action", {}),
                    "rewards": [ep.get("reward", 0.0)],
                    "dones": [ep.get("done", False)],
                    "infos": [ep.get("metadata", {})],
                }
            )
        with open(path, "w") as f:
            json.dump(rollouts, f, default=str)

    def _import_hdf5(self, data: bytes) -> list[dict]:
        import io as _io

        import numpy as np

        buf = _io.BytesIO(data)
        episodes = []
        with h5py.File(buf, "r") as f:
            for key in sorted(f.keys()):
                ep = {}
                grp = f[key]
                for k in grp.keys():
                    v = grp[k][()]
                    ep[k] = v.tolist() if hasattr(v, "tolist") else v
                for k, v in grp.attrs.items():
                    ep[k] = v
                episodes.append(ep)
        return episodes


def get_bridge() -> SimBridge:
    """Return the process-wide SimBridge singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = SimBridge()
    return _singleton
