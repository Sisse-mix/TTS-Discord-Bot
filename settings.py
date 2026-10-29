import json
import logging
import os
import pathlib

logger = logging.getLogger(__name__)

# プロジェクトのルートディレクトリ
BASE_DIR = pathlib.Path(__file__).parent
CHARACTERS_DIR = BASE_DIR / "characters"
CONFIG_FILE = BASE_DIR / "config.json"

# --- TTSエンジン設定 ---
TTS_ENGINE = os.getenv("TTS_ENGINE", "aivoice")

# --- A.I.VOICE 設定 ---
AIVOICE_DLL_PATH = os.getenv(
    "AIVOICE_DLL_PATH",
    r"C:\Program Files\AI\AIVoice\AIVoiceEditor\AI.Talk.Editor.Api.dll",
)
AIVOICE_APP_PATH = os.getenv(
    "AIVOICE_APP_PATH", r"C:\Program Files\AI\AIVoice\AIVoiceEditor\AIVoiceEditor.exe"
)

# --- VOICEVOX 設定 ---
VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
VOICEVOX_SPEAKER_ID = int(os.getenv("VOICEVOX_SPEAKER_ID", "3"))
VOICEVOX_APP_PATH = os.getenv("VOICEVOX_APP_PATH", "")

# --- LLM 設定 ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:1234/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "lm-studio")
LLM_MODEL = os.getenv("LLM_MODEL", "local-model")

# --- 音声加工パイプライン設定 (Rust Core) ---
AUDIO_GAIN_DB = float(os.getenv("AUDIO_GAIN_DB", "3.0"))
AUDIO_SILENCE_THRESHOLD = int(os.getenv("AUDIO_SILENCE_THRESHOLD", "100"))
AUDIO_REVERB_ENABLED = os.getenv("AUDIO_REVERB_ENABLED", "true").lower() == "true"
AUDIO_REVERB_DELAY_MS = int(os.getenv("AUDIO_REVERB_DELAY_MS", "50"))
AUDIO_REVERB_DECAY = float(os.getenv("AUDIO_REVERB_DECAY", "0.3"))
AUDIO_REVERB_MIX = float(os.getenv("AUDIO_REVERB_MIX", "0.15"))

# --- 起動時の初期設定・現在状態保持用 ---
STARTUP_CHARACTER = None
CURRENT_PERSONA_ID = None
CURRENT_PERSONA_DISPLAY_NAME = "デフォルト"

# --- AIシステム設定 ---
SYSTEM_PROMPT = """
命令：あなたはアシスタントAIです。
"""

# Discordステータス
STATUS_MESSAGES = [
    "マスターのサポート",
    "Pythonで開発中",
    "サーバーの監視",
    "/help でコマンド確認",
    "バグと戦っています",
    "コードの整理中",
    "APIのご機嫌取り",
    "ログを解析中",
    "今夜の献立を思案中",
    "歌の練習",
    "美味しいお店を検索中",
    "冷蔵庫の中身を確認中",
    "ボイストレーニング中",
    "お腹が空きました…",
    "激辛カレーに挑戦中",
    "マスターの作業を見守り中",
    "休憩しませんか？",
    "いつでも呼んでください",
    "あなたの帰りを待機中",
    "お昼寝中…( ˘ω˘ )",
    "システム最適化中",
    "アップデート待機中",
]

# --- デフォルトレスポンス集 (フォールバック用) ---
DEFAULT_RESPONSES = {
    # --- アラーム機能 ---
    "alarm_set_text": [
        "⏰ セット完了: **{time}** {icon} 「{message}」\n(任せてください！)",
        "⏰ 了解です: **{time}** {icon} 「{message}」\n(ちゃんと起きてくださいね？)",
        "⏰ **{time}** {icon} 「{message}」\n(セットしました！)",
    ],
    "alarm_set_voice": [
        "{time}に、アラームをセットしました。[NORMAL]",
        "{time}ですね。了解です！[JOY]",
        "{time}に起こしますね。任せてください。[JOY]",
        "{time}にセット完了です！[NORMAL]",
    ],
    "alarm_list_empty_text": "アラームはセットされていません",
    "alarm_list_empty_voice": "アラームはセットされていません。[NORMAL]",
    "alarm_list_voice": "現在設定されているアラームの一覧です。[NORMAL]",
    "alarm_delete_text": "🗑️ 削除しました: **{time}** {message}",
    "alarm_delete_voice": [
        "アラームを削除しました。[NORMAL]",
        "設定を取り消しました。[NORMAL]",
    ],
    "alarm_notify_text": [
        "⏰ **時間です！** <@{user_id}>\n{message}",
        "⏰ **起きてくださーい！** <@{user_id}>\n{message}",
        "⏰ **お時間になりました！** <@{user_id}>\n{message}",
    ],
    "alarm_notify_voice": [
        "時間になりました。{message}[NORMAL]",
        "マスター、時間ですよ！{message}[JOY]",
        "起きてください！時間です！{message}[ANGRY]",
        "お知らせします。{message}[NORMAL]",
    ],
    # --- 辞書機能 ---
    "dict_add_text": "📖 登録しました: **{word}** → **{reading}**",
    "dict_add_voice": [
        "{word}を、{reading}と覚えました。[JOY]",
        "{word}は、{reading}ですね。メモしました！[NORMAL]",
        "新しい言葉ですね。{reading}、覚えました。[JOY]",
    ],
    "dict_delete_text": "🗑️ 削除しました: **{word}**",
    "dict_delete_voice": "{word}の登録を削除しました。[SAD]",
    "dict_empty_text": "辞書は空です",
    "dict_empty_voice": "辞書にはまだ何も登録されていません。[NORMAL]",
    "dict_list_voice": "私が覚えている単語の一覧です。[NORMAL]",
    # --- タイマー機能 ---
    "timer_set_text": "⏳ **{minutes}分** 後にお知らせします",
    "timer_set_voice": [
        "{minutes}分、測りますね。[NORMAL]",
        "{minutes}分ですね、よーい、スタート！[JOY]",
        "はい、{minutes}分のタイマーをセットしました。[NORMAL]",
    ],
    "timer_list_empty_text": "稼働中のタイマーはありません",
    "timer_list_empty_voice": "今動いているタイマーはありません。[NORMAL]",
    "timer_list_voice": "現在稼働中のタイマーはこちらです。[NORMAL]",
    "timer_delete_text": "🗑️ タイマーを削除しました (設定: {minutes}分)",
    "timer_delete_voice": "タイマーを取り消しました。[NORMAL]",
    "timer_notify_text": [
        "⏳ **{minutes}分が経過しました！** <@{user_id}>",
        "⏳ **{minutes}分経ちましたよー！** <@{user_id}>",
    ],
    "timer_notify_voice": [
        "時間になりましたよ。[NORMAL]",
        "{minutes}分、経ちました！[JOY]",
        "マスター、タイマーが鳴ってますよ！[JOY]",
        "お時間です！作業の区切りをつけてくださいね。[NORMAL]",
    ],
    # --- ダイス機能 ---
    "dice_result_voice": [
        "ダイスの結果は、{total}です。[NORMAL]",
        "えいっ！結果は、{total}でした！[JOY]",
        "んー……{total}、です！[NORMAL]",
    ],
    "dice_critical_text": "\n👑 **クリティカルです！！**",
    "dice_critical_voice": [
        "クリティカルです！{total}が出ました！すごい！[JOY]",
        "わぁっ！クリティカルです！さすがマスター！[JOY]",
        "やりました！{total}、クリティカルです！[JOY]",
    ],
    "dice_fumble_text": "\n💀 **ファンブルです...**",
    "dice_fumble_voice": [
        "ああっ……ファンブルです……{total}しか出ませんでした……[SAD]",
        "うう……ファンブル、ですね……どんまいです。[SAD]",
        "ファンブル……次こそはきっと大丈夫です！[ANGRY]",
    ],
    # --- ボイスチャンネル入退室・状態変化 ---
    "join_greet_first": [
        "お久しぶりです！待ってましたよ！[JOY]",
        "あ、マスター！お久しぶりです！[JOY]",
        "元気にしてましたか？お久しぶりです。[NORMAL]",
    ],
    "join_greet_normal": [
        "こんにちは！[JOY]",
        "あ、こんにちは。ご用命ですか？[NORMAL]",
        "はい、準備できてますよ。[JOY]",
        "お疲れ様です！[JOY]",
    ],
    "mute_start": ["ミュートしました。[NORMAL]", "お口チャック、ですね。[NORMAL]"],
    "mute_end": ["ミュートを解除しました。[JOY]", "はーい、聞こえてますよ！[JOY]"],
    "deaf_start": "スピーカーをオフにしました。[NORMAL]",
    "deaf_end": "おかえりなさい！[JOY]",
    "stream_start": "配信を始めました！頑張ってください！[JOY]",
    "stream_end": "配信、お疲れ様でした。[NORMAL]",
    "video_start": "カメラをオンにしました。[JOY]",
    "video_end": "カメラをオフにしました。[NORMAL]",
    "move_voice": [
        "ここから話しますね。[NORMAL]",
        "移動しました！[JOY]",
        "聞こえ方、変わりましたか？[NORMAL]",
    ],
    "char_change_voice": [
        "衣装替えしました！似合ってますか？[JOY]",
        "はい、交代しました！[JOY]",
    ],
    "disconnect_msg": [
        "また呼んでくださいね！[JOY]",
        "失礼します。ゆっくり休んでくださいね。[NORMAL]",
        "バイバイ！[JOY]",
    ],
    # --- エラー処理・時報 ---
    "chat_error_reply": [
        "ごめんなさい、ちょっと頭が痛くて……うまく考えがまとまりませんでした。[SAD]",
        "うーん……うまく答えが出せません。もう一度聞いてもらえますか？[SAD]",
    ],
    "time_signal_voice": [
        "{hour}時になりました。[NORMAL]",
        "マスター、{hour}時ですよ！[JOY]",
    ],
}

# 実行時に使用されるレスポンス辞書（初期値はデフォルトのコピー）
RESPONSES = DEFAULT_RESPONSES.copy()


def get_available_personas() -> dict[str, dict]:
    """
    characters/ フォルダ内のすべての人格設定を走査してメタデータ一覧を返す。
    戻り値: { "akari": {"display_name": "紲星あかり", "dir": Path(...), "default_voices": {...}}, ... }
    """
    personas: dict[str, dict] = {}
    if not CHARACTERS_DIR.exists():
        return personas

    for d in sorted(CHARACTERS_DIR.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        persona_id = d.name
        display_name = persona_id
        default_voices = {}

        json_files = list(d.glob("*.json"))
        if json_files:
            try:
                with open(json_files[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
                display_name = data.get("display_name") or persona_id
                default_voices = data.get("default_voices") or {}
            except Exception:
                pass

        personas[persona_id] = {
            "id": persona_id,
            "display_name": display_name,
            "dir": d,
            "default_voices": default_voices,
        }
    return personas


def load_character_config(char_dir_or_id: pathlib.Path | str | None) -> bool:
    """
    指定されたキャラクターフォルダ（またはID）の設定ファイルを読み込み、
    settings.SYSTEM_PROMPT と settings.RESPONSES を更新する。
    None や "none" が渡された場合は LLM オフ・デフォルトセリフにリセットする。
    """
    global RESPONSES, SYSTEM_PROMPT, CURRENT_PERSONA_ID, CURRENT_PERSONA_DISPLAY_NAME

    RESPONSES = DEFAULT_RESPONSES.copy()
    SYSTEM_PROMPT = None
    CURRENT_PERSONA_ID = None
    CURRENT_PERSONA_DISPLAY_NAME = "デフォルト (LLMオフ)"

    if not char_dir_or_id or str(char_dir_or_id).lower() == "none":
        return False

    if isinstance(char_dir_or_id, str):
        char_dir = CHARACTERS_DIR / char_dir_or_id
    else:
        char_dir = char_dir_or_id

    if not char_dir.exists() or not char_dir.is_dir():
        logger.warning(f"Character directory not found: {char_dir}")
        return False

    CURRENT_PERSONA_ID = char_dir.name
    CURRENT_PERSONA_DISPLAY_NAME = char_dir.name

    txt_file = None
    json_file = None

    for f in sorted(char_dir.glob("*")):
        if f.suffix == ".txt" and txt_file is None:
            txt_file = f
        elif f.suffix == ".json" and json_file is None:
            json_file = f

    if txt_file:
        try:
            with open(txt_file, "r", encoding="utf-8") as f:
                SYSTEM_PROMPT = f.read()
            logger.info(f"Loaded prompt from: {txt_file.name}")
        except Exception as e:
            logger.error(f"Failed to load prompt: {e}")
    else:
        logger.warning(f"No .txt prompt found in {char_dir.name}")

    if json_file:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                custom_responses = json.load(f)
            RESPONSES.update(custom_responses)
            if custom_responses.get("display_name"):
                CURRENT_PERSONA_DISPLAY_NAME = custom_responses["display_name"]
            logger.info(f"Loaded responses from: {json_file.name}")
        except Exception as e:
            logger.error(f"Failed to load json responses: {e}")

    return bool(txt_file)
