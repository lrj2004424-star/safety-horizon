"""Two-family audio feedback for the debounced joint safety state."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import AudioAlertConfig
from .joint_engine import JointState

Player = Callable[[Path, float], bool]
Speaker = Callable[[str, str, int], bool]

# One identical pulse language; only total duration changes with risk severity.
RISK_SOUND_FILES = {
    JointState.ATTENTION: "01-yellow-attention-2s-68bpm.wav",
    JointState.WARNING: "02-orange-warning-4p5s-112bpm.wav",
    JointState.DANGER: "03-red-danger-9s-176bpm.wav",
}
SYSTEM_FALLBACK_FILE = "04-purple-grey-system-unavailable.wav"
ASSET_FILES = (*RISK_SOUND_FILES.values(), SYSTEM_FALLBACK_FILE)


@dataclass(frozen=True)
class AudioUpdate:
    state: JointState
    played: bool
    reason: str
    asset: str | None
    message: str | None


class AlertSoundManager:
    """Emit one risk pulse family or one short system-explanation sentence.

    Playback is non-blocking. Any debounced state change replaces the previous
    cue immediately, including a downgrade. NORMAL always stops and stays silent.
    """

    def __init__(
        self,
        config: AudioAlertConfig,
        assets_dir: str | Path,
        *,
        player: Player | None = None,
        speaker: Speaker | None = None,
    ) -> None:
        self.config = config
        self.assets_dir = Path(assets_dir)
        self._custom_player = player
        self._custom_speaker = speaker
        self._backend = "custom" if player is not None else _detect_audio_backend()
        self._speech_backend = "custom" if speaker is not None else _detect_speech_backend()
        self._process: subprocess.Popen[bytes] | None = None
        self._state: JointState | None = None
        self._last_played: dict[JointState, float] = {}
        self._last_system_played: float | None = None
        self._last_spoken_message: str | None = None
        self._risk_paths = {
            state: self.assets_dir / filename
            for state, filename in RISK_SOUND_FILES.items()
        }
        self._system_fallback_path = self.assets_dir / SYSTEM_FALLBACK_FILE
        missing = [
            str(self.assets_dir / filename)
            for filename in ASSET_FILES
            if not (self.assets_dir / filename).is_file()
        ]
        self.voice_available = bool(
            config.system_voice_enabled and self._speech_backend is not None
        )
        if not config.enabled:
            self.available = False
            self.status = "audio_disabled"
        elif missing:
            self.available = False
            self.status = "audio_assets_missing"
        elif self._backend is None:
            self.available = False
            self.status = "audio_backend_unavailable"
        else:
            self.available = True
            voice_status = (
                f"voice_{self._speech_backend}"
                if self.voice_available else "voice_tone_fallback"
            )
            self.status = f"audio_ready_{self._backend}_{voice_status}"

    def update(
        self,
        state: JointState,
        timestamp_s: float,
        *,
        reason: str | None = None,
    ) -> AudioUpdate:
        previous = self._state
        state_changed = previous != state
        self._state = state
        message = (
            system_sentence(state, reason)
            if state in (JointState.UNCERTAIN, JointState.FAULT)
            else None
        )

        if state == JointState.NORMAL:
            if self._is_playing():
                self._stop_active()
            return AudioUpdate(state, False, "normal_is_silent", None, None)
        if not self.config.enabled:
            return AudioUpdate(state, False, "audio_disabled", None, message)
        if not self.available:
            return AudioUpdate(state, False, self.status, None, message)
        if (
            timestamp_s < self.config.startup_grace_s
            and state not in (JointState.DANGER, JointState.FAULT)
        ):
            return AudioUpdate(state, False, "startup_grace", None, message)

        message_changed = bool(message and message != self._last_spoken_message)
        message_change_due = bool(
            message_changed
            and (
                self._last_system_played is None
                or timestamp_s - self._last_system_played >= 3.0
            )
        )
        if self._is_playing():
            if state_changed or message_change_due:
                self._stop_active()
            else:
                return AudioUpdate(state, False, "audio_busy", None, message)

        interval = self._repeat_interval(state)
        last_played = self._last_played.get(state)
        due = (
            state_changed
            or last_played is None
            or timestamp_s - last_played >= interval
            or message_change_due
        )
        if not due:
            return AudioUpdate(state, False, "cooldown", None, message)

        if state in RISK_SOUND_FILES:
            path = self._risk_paths[state]
            played = self._play_audio(path)
            asset = str(path)
        elif self.voice_available and message is not None:
            played = self._speak(message)
            asset = f"voice:{self.config.system_voice_name}"
        else:
            played = self._play_audio(self._system_fallback_path)
            asset = str(self._system_fallback_path)

        if played:
            self._last_played[state] = timestamp_s
            if message is not None:
                self._last_system_played = timestamp_s
                self._last_spoken_message = message
            trigger = (
                "state_entry"
                if state_changed
                else "reason_change"
                if message_change_due
                else "state_repeat"
            )
            return AudioUpdate(state, True, trigger, asset, message)
        self.available = False
        self.status = "audio_playback_failed"
        return AudioUpdate(state, False, self.status, asset, message)

    def close(self, *, stop_active: bool = True) -> None:
        if stop_active:
            self._stop_active()

    def _repeat_interval(self, state: JointState) -> float:
        return {
            JointState.ATTENTION: self.config.attention_repeat_s,
            JointState.WARNING: self.config.warning_repeat_s,
            JointState.DANGER: self.config.danger_repeat_s,
            JointState.UNCERTAIN: self.config.uncertain_repeat_s,
            JointState.FAULT: self.config.fault_repeat_s,
        }[state]

    def _is_playing(self) -> bool:
        if self._process is None:
            return False
        if self._process.poll() is None:
            return True
        self._process = None
        return False

    def _stop_active(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        self._process = None

    def _play_audio(self, path: Path) -> bool:
        if self._custom_player is not None:
            try:
                return bool(self._custom_player(path, self.config.volume))
            except Exception:
                return False
        return self._launch(_audio_command(self._backend, path, self.config.volume))

    def _speak(self, message: str) -> bool:
        if self._custom_speaker is not None:
            try:
                return bool(
                    self._custom_speaker(
                        message,
                        self.config.system_voice_name,
                        self.config.system_voice_rate,
                    )
                )
            except Exception:
                return False
        return self._launch(
            _speech_command(
                self._speech_backend,
                message,
                self.config.system_voice_name,
                self.config.system_voice_rate,
            )
        )

    def _launch(self, command: list[str] | None) -> bool:
        if command is None:
            return False
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            self._process = None
            return False


def system_sentence(state: JointState, reason: str | None) -> str:
    """Map internal reason codes to one fixed, non-identifying Chinese sentence."""
    normalized = (reason or "").lower()
    if state == JointState.FAULT or "stream_fault" in normalized or "read_failed" in normalized:
        return "摄像头断流，安全监测不可用。"
    if "single_wide_context" in normalized:
        return "当前广角画面不足以判断手部危险，请使用近景机位。"
    if "multiple_people" in normalized:
        return "当前画面包含多名人员，系统无法可靠关联双手与工位。"
    if "source_resolution_below" in normalized or "crop_resolution_too_low" in normalized:
        return "当前主码流或工位画面分辨率不足，无法可靠判断。"
    if "station_crop_not_verified" in normalized or "station_zones_not_calibrated" in normalized:
        return "当前工位尚未完成裁切区域和危险区标定。"
    if (
        "alignment" in normalized
        or "resolution_changed" in normalized
        or "fixed_camera_guard" in normalized
    ):
        return "摄像头位置或画面发生变化，请重新标定。"
    if "anchor" in normalized:
        return "固定机位校验失败，请检查摄像头。"
    if "machine_truth" in normalized or "machine_state" in normalized:
        return "机器状态信号缺失，系统暂时无法判断。"
    if any(value in normalized for value in ("hand", "upper_body", "visible", "occludes")):
        return "系统无法看清双手或上半身，请清除遮挡。"
    return "当前证据不足，系统暂时无法判断。"


def _detect_audio_backend() -> str | None:
    if sys.platform == "darwin" and shutil.which("afplay"):
        return "afplay"
    if sys.platform.startswith("linux"):
        if shutil.which("paplay"):
            return "paplay"
        if shutil.which("aplay"):
            return "aplay"
    if sys.platform.startswith("win") and shutil.which("powershell"):
        return "powershell"
    return None


def _detect_speech_backend() -> str | None:
    if sys.platform == "darwin" and shutil.which("say"):
        return "say"
    if sys.platform.startswith("linux") and shutil.which("spd-say"):
        return "spd-say"
    if sys.platform.startswith("win") and shutil.which("powershell"):
        return "powershell"
    return None


def _audio_command(backend: str | None, path: Path, volume: float) -> list[str] | None:
    if backend == "afplay":
        return ["afplay", "-v", f"{volume:.3f}", str(path)]
    if backend == "paplay":
        return ["paplay", str(path)]
    if backend == "aplay":
        return ["aplay", "-q", str(path)]
    if backend == "powershell":
        escaped = str(path).replace("'", "''")
        return [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()",
        ]
    return None


def _speech_command(
    backend: str | None,
    message: str,
    voice_name: str,
    rate: int,
) -> list[str] | None:
    if backend == "say":
        return ["say", "-v", voice_name, "-r", str(rate), message]
    if backend == "spd-say":
        relative_rate = max(-100, min(100, int((rate - 200) / 1.2)))
        return ["spd-say", "-r", str(relative_rate), message]
    if backend == "powershell":
        escaped = message.replace("'", "''")
        speech_rate = max(-10, min(10, int((rate - 200) / 12)))
        return [
            "powershell",
            "-NoProfile",
            "-Command",
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate={speech_rate}; $s.Speak('{escaped}')",
        ]
    return None
