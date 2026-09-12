"""`gtwm doctor`：実行環境の検査。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from gtwm.utils.device import get_device

REQUIRED_ENV_VARS = (
    "PYTORCH_ENABLE_MPS_FALLBACK",
    "TOKENIZERS_PARALLELISM",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = major == 3 and minor == 11
    return CheckResult("Python", ok, f"{sys.version.split()[0]}")


def check_mps() -> CheckResult:
    device = get_device()
    return CheckResult("MPS", device == "mps", f"device={device}")


def check_mujoco_offscreen() -> CheckResult:
    try:
        import mujoco
    except ImportError:
        return CheckResult("MuJoCo", False, "未インストール（sim extra 未導入）")

    try:
        xml = "<mujoco><worldbody><light/><geom type='plane' size='1 1 0.1'/></worldbody></mujoco>"
        model = mujoco.MjModel.from_xml_string(xml)
        data = mujoco.MjData(model)
        renderer = mujoco.Renderer(model, height=64, width=64)
        mujoco.mj_forward(model, data)
        renderer.update_scene(data)
        frame = renderer.render()
        ok = frame is not None and frame.shape == (64, 64, 3)
        return CheckResult("MuJoCo", ok, "オフスクリーン描画に成功")
    except Exception as exc:  # noqa: BLE001 - 診断用途で全例外を捕捉する
        return CheckResult("MuJoCo", False, f"エラー: {exc}")


def check_ffmpeg() -> CheckResult:
    path = shutil.which("ffmpeg")
    if path is None:
        return CheckResult("ffmpeg", False, "未検出")
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5, check=False
        )
        version_line = out.stdout.splitlines()[0] if out.stdout else "unknown"
        return CheckResult("ffmpeg", out.returncode == 0, version_line)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("ffmpeg", False, f"エラー: {exc}")


def check_docker() -> CheckResult:
    path = shutil.which("docker")
    if path is None:
        return CheckResult("Docker", False, "未検出")
    try:
        out = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=5, check=False
        )
        return CheckResult("Docker", out.returncode == 0, out.stdout.strip() or "unknown")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("Docker", False, f"エラー: {exc}")


def check_env_file() -> CheckResult:
    exists = os.path.exists(".env")
    return CheckResult(".env", exists, "存在" if exists else "未作成（.env.example をコピー）")


def check_env_vars() -> CheckResult:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    ok = not missing
    detail = "全て設定済み" if ok else f"未設定: {', '.join(missing)}"
    return CheckResult("環境変数", ok, detail)


def run_all_checks() -> list[CheckResult]:
    return [
        check_python(),
        check_mps(),
        check_mujoco_offscreen(),
        check_ffmpeg(),
        check_docker(),
        check_env_file(),
        check_env_vars(),
    ]
