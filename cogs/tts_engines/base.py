from abc import ABC, abstractmethod


class TTSProvider(ABC):
    @abstractmethod
    def initialize(self):
        pass

    @abstractmethod
    def generate_audio(
        self, text: str, emotion: str, voice_override: str | None = None
    ) -> bytes | None:
        """
        音声データを生成し、WAVフォーマットのバイト列として返す。
        voice_override が指定された場合は、この生成のみ一時的に指定ボイスを使用する。
        """
        pass

    @abstractmethod
    def get_presets(self) -> list[str]:
        pass

    @abstractmethod
    def set_preset(self, preset_name: str) -> bool:
        pass

    @abstractmethod
    def terminate(self):
        pass
