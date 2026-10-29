import json
import logging
import os
import requests
from .base import TTSProvider

logger = logging.getLogger(__name__)


class VoicevoxProvider(TTSProvider):
    """
    VOICEVOX EngineのHTTP APIラッパークラス．
    完全オンメモリ（一時ファイル不使用）およびコネクションプーリング対応．
    """

    def __init__(self):
        self.base_url = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
        # デフォルトスピーカーID (3: ずんだもん・ノーマル)
        self.current_speaker_id = int(os.getenv("VOICEVOX_SPEAKER_ID", "3"))
        self.speaker_map = {}
        self.session = requests.Session()

    def initialize(self):
        """スピーカー一覧を取得し，IDと名前のマッピングを作成する"""
        try:
            resp = self.session.get(f"{self.base_url}/speakers", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                self.speaker_map = {}
                for chara in data:
                    name = chara["name"]
                    for style in chara["styles"]:
                        # 検索用キー作成: "ずんだもん(ノーマル)"
                        key = f"{name}({style['name']})"
                        self.speaker_map[key] = style["id"]
                logger.info(
                    f"VOICEVOX Initialized. Default ID: {self.current_speaker_id} ({len(self.speaker_map)} styles)"
                )
            else:
                logger.error(f"VOICEVOX Connection Failed: Status {resp.status_code}")
        except Exception as e:
            logger.error(f"VOICEVOX Connection Error: {e}")

    def _adjust_mora_contour(self, query_data: dict, emotion: str):
        """
        スタイル変更による露骨な声質変化を避けつつ、キャラクター性を保ったまま
        VOICEVOXの音声パラメータ（全体スケール＋アクセント句のモーラ単位ピッチ・母音長）を緻密に調整する。
        """
        accent_phrases = query_data.get("accent_phrases", [])

        if emotion == "JOY":
            query_data["pitchScale"] = 0.03
            query_data["intonationScale"] = 1.25
            query_data["speedScale"] = 1.05
            query_data["volumeScale"] = 1.03
            query_data["postPhonemeLength"] = 0.05
            # 文末のモーラをわずかに跳ね上げて明るい響きにする
            if accent_phrases and accent_phrases[-1].get("moras"):
                last_mora = accent_phrases[-1]["moras"][-1]
                if last_mora.get("pitch", 0.0) > 0:
                    last_mora["pitch"] += 0.15

        elif emotion == "SAD":
            query_data["pitchScale"] = -0.03
            query_data["intonationScale"] = 0.78
            query_data["speedScale"] = 0.90
            query_data["volumeScale"] = 0.92
            query_data["prePhonemeLength"] = 0.12
            query_data["postPhonemeLength"] = 0.20
            # 文末モーラの母音を少し伸ばし、ピッチを下げてしょんぼり感を出す
            if accent_phrases and accent_phrases[-1].get("moras"):
                last_mora = accent_phrases[-1]["moras"][-1]
                if last_mora.get("vowel_length") is not None:
                    last_mora["vowel_length"] *= 1.25
                if last_mora.get("pitch", 0.0) > 0:
                    last_mora["pitch"] = max(3.0, last_mora["pitch"] - 0.20)

        elif emotion == "ANGRY":
            query_data["pitchScale"] = -0.01
            query_data["intonationScale"] = 1.30
            query_data["speedScale"] = 1.10
            query_data["volumeScale"] = 1.12
            query_data["postPhonemeLength"] = 0.04
            # 各アクセント句の先頭子音に力みを入れる
            for phrase in accent_phrases:
                moras = phrase.get("moras", [])
                if moras and moras[0].get("consonant_length") is not None:
                    moras[0]["consonant_length"] *= 1.20

        elif emotion == "SURPRISE":
            query_data["pitchScale"] = 0.05
            query_data["intonationScale"] = 1.35
            query_data["speedScale"] = 1.12
            query_data["volumeScale"] = 1.08
            query_data["prePhonemeLength"] = 0.03
            if accent_phrases and accent_phrases[-1].get("moras"):
                last_mora = accent_phrases[-1]["moras"][-1]
                if last_mora.get("pitch", 0.0) > 0:
                    last_mora["pitch"] += 0.25

        else:  # NORMAL
            query_data["pitchScale"] = 0.0
            query_data["intonationScale"] = 1.0
            query_data["speedScale"] = 1.0
            query_data["volumeScale"] = 1.0

    def _resolve_speaker_id(self, preset_name: str) -> int | None:
        if not self.speaker_map:
            self.initialize()
        if preset_name in self.speaker_map:
            return self.speaker_map[preset_name]
        norm_target = f"{preset_name}(ノーマル)"
        if norm_target in self.speaker_map:
            return self.speaker_map[norm_target]
        for name, pid in self.speaker_map.items():
            if preset_name in name:
                return pid
        return None

    def generate_audio(
        self, text: str, emotion: str, voice_override: str | None = None
    ) -> bytes | None:
        """AudioQueryの作成と音声合成の2ステップを実行し、オンメモリのWAVバイト列を返す"""
        try:
            if not self.speaker_map:
                self.initialize()

            speaker_id = self.current_speaker_id
            if voice_override:
                resolved_id = self._resolve_speaker_id(voice_override)
                if resolved_id is not None:
                    speaker_id = resolved_id

            # 1. Audio Queryの作成（イントネーション等のデータ生成）
            params = {"text": text, "speaker": speaker_id}
            query_resp = self.session.post(
                f"{self.base_url}/audio_query", params=params, timeout=10.0
            )

            if query_resp.status_code != 200:
                logger.error(f"VOICEVOX Query Error: {query_resp.text}")
                return None

            query_data = query_resp.json()

            # --- パラメータ＆モーラ抑揚調整 ---
            self._adjust_mora_contour(query_data, emotion)

            # 2. 音声合成 (Synthesis) -> オンメモリで直接bytesを返す
            headers = {"Content-Type": "application/json"}
            synth_resp = self.session.post(
                f"{self.base_url}/synthesis",
                headers=headers,
                params={"speaker": speaker_id},
                data=json.dumps(query_data),
                timeout=20.0,
            )

            if synth_resp.status_code == 200:
                return synth_resp.content
            else:
                logger.error(f"VOICEVOX Synthesis Error: {synth_resp.status_code}")
                return None

        except Exception as e:
            logger.error(f"VOICEVOX Generation Exception: {e}")
            return None

    def get_presets(self) -> list[str]:
        if not self.speaker_map:
            self.initialize()
        return list(self.speaker_map.keys())

    def set_preset(self, preset_name: str) -> bool:
        resolved = self._resolve_speaker_id(preset_name)
        if resolved is not None:
            self.current_speaker_id = resolved
            return True
        return False

    def terminate(self):
        try:
            self.session.close()
        except Exception:
            pass
