import logging
import os
import subprocess
import tempfile
import threading
import time

# .NET Framework連携用のライブラリ読み込み
try:
    from pythonnet import load

    load("netfx")
except Exception as e:
    print(f"[WARNING] Pythonnet load failed: {e}")

try:
    import clr
except Exception:
    clr = None

from .base import TTSProvider

logger = logging.getLogger(__name__)


class AIVoiceProvider(TTSProvider):
    """
    A.I.VOICE Editor API を利用したTTSプロバイダー．
    .NET DLLを介してエディタを制御する．
    """

    def __init__(self):
        self.tts_control = None
        self.host_status = None
        self.all_presets = []
        self.display_presets = []
        self.current_base_preset = ""
        self._lock = threading.Lock()

        self.dll_path = os.getenv(
            "AIVOICE_DLL_PATH",
            r"C:\Program Files\AI\AIVoice\AIVoiceEditor\AI.Talk.Editor.Api.dll",
        )

    def initialize(self):
        if not clr:
            logger.error("pythonnet (clr) is not available.")
            return

        if not os.path.exists(self.dll_path):
            logger.error(f"A.I.VOICE DLL not found at: {self.dll_path}")
            return

        try:
            clr.AddReference(self.dll_path)
            from AI.Talk.Editor.Api import HostStatus, TtsControl  # type: ignore

            self.tts_control = TtsControl()
            self.host_status = HostStatus

            connected = self._ensure_connection(allow_launch=False)
            if not connected:
                logger.warning("Failed to connect to A.I.VOICE Editor.")
                self.all_presets = []
                self.display_presets = []
                return

            if self.tts_control:
                raw_presets = list(self.tts_control.VoicePresetNames)
                self.all_presets = [str(p) for p in raw_presets]
            else:
                self.all_presets = []

            logger.info("=== Loaded A.I.VOICE Presets ===")
            for p in self.all_presets:
                logger.info(f" - {p}")
            logger.info("==================================")

            seen = set()
            ignore_suffixes = ["_JOY", "_SAD", "_ANGRY", "_SURPRISE", "_NORMAL", "_SAN"]

            self.display_presets = []
            for name in self.all_presets:
                base_name = name
                for suffix in ignore_suffixes:
                    if base_name.endswith(suffix):
                        base_name = base_name[: -len(suffix)]
                        break

                if base_name not in seen:
                    self.display_presets.append(base_name)
                    seen.add(base_name)

            self.display_presets.sort()
            if self.display_presets and not self.current_base_preset:
                self.current_base_preset = self.display_presets[0]

            logger.info(f"A.I.VOICE Init: Loaded {len(self.all_presets)} presets.")

        except Exception as e:
            logger.error(f"A.I.VOICE Init Error: {e}")

    def _ensure_connection(self, allow_launch: bool = False):
        if not self.tts_control:
            return False

        try:
            if self.tts_control.Status == self.host_status.Idle:
                return True
        except Exception:
            pass

        try:
            available_hosts = self.tts_control.GetAvailableHostNames()

            if not available_hosts and allow_launch:
                editor_path = (
                    os.getenv("AIVOICE_APP_PATH")
                    or r"C:\Program Files\AI\AIVoice\AIVoiceEditor\AIVoiceEditor.exe"
                )
                if os.path.exists(editor_path):
                    subprocess.Popen(editor_path)
                    for _ in range(15):
                        time.sleep(1)
                        if self.tts_control.GetAvailableHostNames():
                            break

            hosts = self.tts_control.GetAvailableHostNames()
            if hosts:
                try:
                    self.tts_control.Initialize(hosts[0])
                except Exception:
                    pass
                try:
                    self.tts_control.Connect()
                except Exception:
                    pass

            if self.tts_control.Status == self.host_status.Idle:
                return True

        except Exception as e:
            logger.error(f"Connection Retry Failed: {e}")

        return False

    def _resolve_base_preset(self, preset_name: str) -> str | None:
        """プリセット名（部分一致・スペース無し含む）から正規のベースプリセット名を解決する"""
        if preset_name in self.display_presets:
            return preset_name
        norm_query = preset_name.replace(" ", "").replace("　", "")
        for p in self.display_presets:
            norm_p = p.replace(" ", "").replace("　", "")
            if norm_query in norm_p or norm_p in norm_query:
                return p
        return None

    def generate_audio(
        self, text: str, emotion: str, voice_override: str | None = None
    ) -> bytes | None:
        with self._lock:
            if not self._ensure_connection():
                return None

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                temp_path = tf.name

            try:
                base_preset = self.current_base_preset
                if voice_override:
                    resolved = self._resolve_base_preset(voice_override)
                    if resolved:
                        base_preset = resolved

                target_preset = base_preset

                if emotion == "NORMAL":
                    candidate = f"{base_preset}_NORMAL"
                    if candidate in self.all_presets:
                        target_preset = candidate
                    elif base_preset in self.all_presets:
                        target_preset = base_preset
                else:
                    candidate = f"{base_preset}_{emotion}"
                    if candidate in self.all_presets:
                        target_preset = candidate
                    else:
                        normal_candidate = f"{base_preset}_NORMAL"
                        if normal_candidate in self.all_presets:
                            target_preset = normal_candidate

                if (
                    target_preset
                    and self.tts_control.CurrentVoicePresetName != target_preset
                ):
                    self.tts_control.CurrentVoicePresetName = target_preset
                    logger.info(f"Switched Preset to: {target_preset}")

                should_fallback_param = False
                if emotion != "NORMAL":
                    if not target_preset.endswith(f"_{emotion}"):
                        should_fallback_param = True

                if should_fallback_param:
                    self._apply_fallback_parameters(emotion)
                else:
                    self._apply_fallback_parameters("RESET")

                self.tts_control.Text = text
                self.tts_control.SaveAudioToFile(temp_path)

                if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    with open(temp_path, "rb") as f:
                        return f.read()
                return None

            except Exception as e:
                logger.error(f"A.I.VOICE Speak Error: {e}")
                return None
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

    def _apply_fallback_parameters(self, emotion):
        try:
            p, s, r, v = 1.0, 1.0, 1.0, 1.0

            if emotion == "JOY":
                p, s, r = 1.15, 1.05, 1.20
            elif emotion == "SAD":
                p, s, r = 0.92, 0.85, 0.80
            elif emotion == "ANGRY":
                p, s, r, v = 1.05, 1.15, 1.30, 1.10
            elif emotion == "SURPRISE":
                p, s, r = 1.25, 1.10, 1.30
            elif emotion == "RESET":
                pass

            self.tts_control.MasterPitch = p
            self.tts_control.MasterSpeed = s
            self.tts_control.MasterPitchRange = r
            self.tts_control.MasterVolume = v
        except Exception as e:
            logger.error(f"Fallback Param Error: {e}")

    def get_presets(self) -> list[str]:
        if not self.display_presets and self._ensure_connection(allow_launch=False):
            self.initialize()
        return self.display_presets

    def set_preset(self, preset_name: str) -> bool:
        if preset_name in self.display_presets:
            self.current_base_preset = preset_name
            return True
        # 部分一致フォールバック（"紲星あかり" -> "紲星 あかり" 等）
        norm_query = preset_name.replace(" ", "").replace("　", "")
        for p in self.display_presets:
            norm_p = p.replace(" ", "").replace("　", "")
            if norm_query in norm_p or norm_p in norm_query:
                self.current_base_preset = p
                return True
        return False

    def terminate(self):
        if self.tts_control:
            try:
                self.tts_control.Disconnect()
                self.tts_control.Terminate()
            except Exception:
                pass
