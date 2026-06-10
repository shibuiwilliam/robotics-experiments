"""SmolVLA-based Vision-Language-Action encoder.

Uses HuggingFace's SmolVLA (450M params, part of LeRobot) for true VLA:
- Action prediction from image + instruction + robot state
- Scene embedding and affordance prediction delegate to CLIP (VLAEncoder)

Verified working on Apple Silicon MPS with transformers==5.3.0.
Falls back gracefully when lerobot is not installed.

SmolVLA fits the medium-frequency tier (~200ms-2s inference).
Must NOT be called in the kHz MuJoCo control loop (CLAUDE.md §13).
"""

from __future__ import annotations

import warnings
from typing import Any  # lerobot types are optional

import numpy as np
import structlog
from numpy.typing import NDArray

from psl.grounding.vla_encoder import (
    ActionPrediction,
    AffordancePrediction,
    VLAEncoder,
)
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry

logger = structlog.get_logger()

_SMOLVLA_MODEL = "lerobot/smolvla_base"
_SMOLVLA_ACTION_DIM = 6  # SmolVLA base outputs 6-DOF EE delta
_SMOLVLA_STATE_DIM = 6  # SmolVLA expects 6-dim state


def _smolvla_available() -> bool:
    """Check if lerobot is importable."""
    try:
        import lerobot  # noqa: F401

        return True
    except ImportError:
        return False


class SmolVLAEncoder:
    """True VLA encoder using SmolVLA from HuggingFace LeRobot.

    When lerobot is installed and transformers==5.3.x, loads SmolVLA
    for action prediction. When not available, falls back to CLIP.

    Args:
        model_name: HuggingFace model identifier.
        device: PyTorch device ("mps", "cpu", "cuda").
        seed: Random seed for reproducibility.
        use_smolvla: If False, skip model loading (for testing fallback).
    """

    def __init__(
        self,
        model_name: str = _SMOLVLA_MODEL,
        device: str | None = None,
        seed: int = 42,
        use_smolvla: bool = True,
    ) -> None:
        self._model_name = model_name
        self._seed = seed
        self._model: Any = None
        self._tokenizer: Any = None
        self._device: str = device or _detect_device()
        self._use_smolvla = use_smolvla
        self._clip_encoder = VLAEncoder(use_real_clip=True, seed=seed)

        if use_smolvla and _smolvla_available():
            try:
                self._load_model()
            except Exception as e:
                warnings.warn(
                    f"SmolVLA model loading failed: {e}. Falling back to CLIP.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                self._model = None
                self._tokenizer = None

    def _load_model(self) -> None:
        """Load SmolVLA from HuggingFace via lerobot."""
        import torch

        logger.info("smolvla_loading", model=self._model_name, device=self._device)
        from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

        self._model = SmolVLAPolicy.from_pretrained(self._model_name)
        self._model.to(torch.device(self._device))
        self._model.eval()
        self._tokenizer = self._model.model.vlm_with_expert.processor.tokenizer
        logger.info("smolvla_loaded", device=self._device)

    @property
    def has_action_model(self) -> bool:
        """Whether SmolVLA is loaded and ready."""
        return self._model is not None

    @property
    def model_name(self) -> str:
        """Model identifier string."""
        return self._model_name if self.has_action_model else "clip_fallback"

    def predict_action(
        self,
        image: NDArray[np.uint8] | None,
        instruction: str,
        robot_state: dict[str, object],
        timestamp: float = 0.0,
    ) -> ActionPrediction:
        """Predict a robot action from scene image + instruction + state.

        Args:
            image: RGB image (H, W, 3) uint8, 256×256 recommended.
            instruction: Natural language task instruction.
            robot_state: Dict with 'joint_positions' key.
            timestamp: Current simulation time.

        Returns:
            ActionPrediction with ee_delta (6-DOF) and confidence.
        """
        if self._model is not None and image is not None:
            return self._predict_smolvla(image, instruction, robot_state)
        return self._predict_fallback(robot_state)

    def _predict_smolvla(
        self,
        image: NDArray[np.uint8],
        instruction: str,
        robot_state: dict[str, object],
    ) -> ActionPrediction:
        """Run SmolVLA inference with the verified working API."""
        import torch
        from lerobot.utils.constants import (
            OBS_LANGUAGE_ATTENTION_MASK,
            OBS_LANGUAGE_TOKENS,
        )

        with torch.no_grad():
            # Image: normalize to [0,1], permute to (1,C,H,W), duplicate for 3 cameras
            img_t = torch.from_numpy(image).permute(2, 0, 1).unsqueeze(0).float() / 255.0
            img_t = img_t.to(self._device)

            # State: SmolVLA expects 6-dim. Map from 7-joint Panda by taking first 6.
            jpos = np.asarray(robot_state.get("joint_positions", np.zeros(7)), dtype=np.float32)
            state_6 = jpos[:_SMOLVLA_STATE_DIM]
            state_t = torch.from_numpy(state_6).unsqueeze(0).float().to(self._device)

            # Language: tokenize with bool attention mask
            tokens = self._tokenizer(
                instruction,
                return_tensors="pt",
                padding="max_length",
                max_length=64,
                truncation=True,
            )

            observation: dict[str, Any] = {
                "observation.images.camera1": img_t,
                "observation.images.camera2": img_t,
                "observation.images.camera3": img_t,
                "observation.state": state_t,
                OBS_LANGUAGE_TOKENS: tokens["input_ids"].to(self._device),
                OBS_LANGUAGE_ATTENTION_MASK: tokens["attention_mask"].bool().to(self._device),
            }

            action = self._model.select_action(observation)
            action_np = action.cpu().numpy().astype(np.float64).flatten()

        ee_delta = (
            action_np[:_SMOLVLA_ACTION_DIM]
            if len(action_np) >= _SMOLVLA_ACTION_DIM
            else np.zeros(_SMOLVLA_ACTION_DIM)
        )

        return ActionPrediction(
            joint_targets=np.asarray(
                robot_state.get("joint_positions", np.zeros(7)), dtype=np.float64
            ),
            ee_delta=ee_delta,
            gripper=0.0,
            confidence=0.8,
            horizon=1,
        )

    def _predict_fallback(self, robot_state: dict[str, object]) -> ActionPrediction:
        """Fallback: zero action with zero confidence."""
        jpos = robot_state.get("joint_positions")
        jpos_arr = np.asarray(jpos, dtype=np.float64) if jpos is not None else np.zeros(7)
        return ActionPrediction(
            joint_targets=jpos_arr,
            ee_delta=np.zeros(_SMOLVLA_ACTION_DIM),
            gripper=0.0,
            confidence=0.0,
            horizon=0,
        )

    def predict_affordances(
        self,
        object_name: str,
        timestamp: float = 0.0,
        image: NDArray[np.uint8] | None = None,
    ) -> AffordancePrediction:
        """Predict affordances via CLIP (delegates to VLAEncoder)."""
        return self._clip_encoder.predict_affordances(object_name, timestamp, image)

    def encode_object(
        self,
        object_name: str,
        timestamp: float = 0.0,
        image: NDArray[np.uint8] | None = None,
    ) -> Phyte:
        """Encode object to Phyte embedding (delegates to VLAEncoder)."""
        return self._clip_encoder.encode_object(object_name, timestamp, image)

    def action_to_phyte(self, action: ActionPrediction, timestamp: float) -> Phyte:
        """Wrap an action prediction as a Phyte with covariance and provenance."""
        n = len(action.joint_targets)
        uncertainty = max(1.0 - action.confidence, 0.01)

        return Phyte(
            semantic_id="action:predicted_joint_targets",
            frame="world",
            pose=identity_se3(),
            timestamp=timestamp,
            clock_domain="agent",
            unit="rad",
            value=action.joint_targets,
            covariance=np.eye(n) * uncertainty,
            provenance=Provenance(
                chain=[
                    ProvenanceEntry(
                        source=f"smolvla:{self._model_name}"
                        if self.has_action_model
                        else "smolvla:fallback",
                        operation="predict_action",
                        timestamp=timestamp,
                    )
                ],
                confidence=action.confidence,
            ),
        )


def _detect_device() -> str:
    """Detect the best available PyTorch device."""
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"
