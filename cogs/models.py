from typing import List
from pydantic import BaseModel, ConfigDict, Field


class CharacterVoiceMap(BaseModel):
    """キャラクターごとのデフォルトTTSボイス名マッピング"""

    aivoice: str = ""
    voicevox: str = ""


class CharacterResponses(BaseModel):
    """キャラクターのセリフ設定 (Pydantic V2 対応)"""

    model_config = ConfigDict(extra="ignore")

    # キャラクター表示名と推奨ボイス設定（任意）
    display_name: str = ""
    default_voices: CharacterVoiceMap = Field(default_factory=CharacterVoiceMap)

    # 必須ではないフィールドにはデフォルト値を与える
    alarm_set_text: List[str] | str = Field(default_factory=lambda: ["セットしたよ！"])
    alarm_set_voice: List[str] | str = Field(
        default_factory=lambda: ["セットしました。[NORMAL]"]
    )
    alarm_list_empty_text: List[str] | str = "アラームはないよ"
    alarm_list_empty_voice: List[str] | str = "アラームはありません。[NORMAL]"
    alarm_list_voice: List[str] | str = "アラーム一覧だよ。[NORMAL]"
    alarm_delete_text: List[str] | str = "削除したよ"
    alarm_delete_voice: List[str] | str = Field(
        default_factory=lambda: ["削除しました。[NORMAL]"]
    )
    alarm_notify_text: List[str] | str = Field(default_factory=lambda: ["時間だよ！"])
    alarm_notify_voice: List[str] | str = Field(
        default_factory=lambda: ["時間になりました。[NORMAL]"]
    )

    dict_add_text: List[str] | str = "覚えたよ"
    dict_add_voice: List[str] | str = Field(
        default_factory=lambda: ["覚えました。[JOY]"]
    )
    dict_delete_text: List[str] | str = "忘れたよ"
    dict_delete_voice: List[str] | str = "忘れました。[NORMAL]"
    dict_empty_text: List[str] | str = "辞書は空だよ"
    dict_empty_voice: List[str] | str = "辞書は空っぽです。[NORMAL]"
    dict_list_voice: List[str] | str = "辞書一覧だよ。[NORMAL]"

    timer_set_text: List[str] | str = "{minutes}分後に教えるね"
    timer_set_voice: List[str] | str = Field(
        default_factory=lambda: ["タイマーセット。[NORMAL]"]
    )
    timer_list_empty_text: List[str] | str = "タイマーはないよ"
    timer_list_empty_voice: List[str] | str = "動いているタイマーはありません。[NORMAL]"
    timer_list_voice: List[str] | str = "タイマー一覧だよ。[NORMAL]"
    timer_delete_text: List[str] | str = "タイマー削除"
    timer_delete_voice: List[str] | str = "タイマーを消しました。[NORMAL]"
    timer_notify_text: List[str] | str = Field(default_factory=lambda: ["時間だよ！"])
    timer_notify_voice: List[str] | str = Field(
        default_factory=lambda: ["時間になりました。[NORMAL]"]
    )

    dice_result_voice: List[str] | str = Field(
        default_factory=lambda: ["結果は{total}だよ。[NORMAL]"]
    )
    dice_critical_text: List[str] | str = "\nクリティカル！"
    dice_critical_voice: List[str] | str = Field(
        default_factory=lambda: ["クリティカル！[JOY]"]
    )
    dice_fumble_text: List[str] | str = "\nファンブル..."
    dice_fumble_voice: List[str] | str = Field(
        default_factory=lambda: ["ファンブル...[SAD]"]
    )

    join_greet_first: List[str] | str = Field(
        default_factory=lambda: ["こんにちは！[JOY]"]
    )
    join_greet_normal: List[str] | str = Field(default_factory=lambda: ["はい！[JOY]"])

    mute_start: List[str] | str = Field(
        default_factory=lambda: ["ミュートします。[NORMAL]"]
    )
    mute_end: List[str] | str = Field(default_factory=lambda: ["ミュート解除。[JOY]"])
    deaf_start: List[str] | str = "スピーカーオフ。[NORMAL]"
    deaf_end: List[str] | str = "スピーカーオン。[JOY]"
    stream_start: List[str] | str = "配信開始！[JOY]"
    stream_end: List[str] | str = "配信終了。[NORMAL]"
    video_start: List[str] | str = "カメラオン。[JOY]"
    video_end: List[str] | str = "カメラオフ。[NORMAL]"

    move_voice: List[str] | str = Field(
        default_factory=lambda: ["移動しました。[NORMAL]"]
    )
    char_change_voice: List[str] | str = Field(
        default_factory=lambda: ["交代しました。[JOY]"]
    )
    disconnect_msg: List[str] | str = Field(default_factory=lambda: ["またね！[JOY]"])
    chat_error_reply: List[str] | str = Field(
        default_factory=lambda: ["エラーです。[SAD]"]
    )
    time_signal_voice: List[str] | str = Field(
        default_factory=lambda: ["{hour}時になりました。[NORMAL]"]
    )


class CharacterConfig(BaseModel):
    """キャラクター設定全体"""

    responses: CharacterResponses
