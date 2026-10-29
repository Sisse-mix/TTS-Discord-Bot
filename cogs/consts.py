import json
import logging
import os
import re
from typing import Type, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 英語・日本語・小文字などの感情表記揺れを吸収するパターン
VALID_EMOTIONS = {"JOY", "SAD", "ANGRY", "SURPRISE", "NORMAL"}

EMOTION_ALIASES = {
    "JOY": "JOY",
    "HAPPY": "JOY",
    "SMILE": "JOY",
    "FUN": "JOY",
    "喜": "JOY",
    "喜び": "JOY",
    "楽": "JOY",
    "笑": "JOY",
    "SAD": "SAD",
    "CRY": "SAD",
    "SORRY": "SAD",
    "悲": "SAD",
    "悲しみ": "SAD",
    "哀": "SAD",
    "泣": "SAD",
    "ANGRY": "ANGRY",
    "ANGER": "ANGRY",
    "MAD": "ANGRY",
    "怒": "ANGRY",
    "怒り": "ANGRY",
    "SURPRISE": "SURPRISE",
    "SURPRISED": "SURPRISE",
    "SHOCK": "SURPRISE",
    "驚": "SURPRISE",
    "驚き": "SURPRISE",
    "NORMAL": "NORMAL",
    "CALM": "NORMAL",
    "NEUTRAL": "NORMAL",
    "平": "NORMAL",
    "平静": "NORMAL",
    "通常": "NORMAL",
}

# [JOY], 【SAD】, (angry), [喜び] などを検出する正規表現
TAG_PATTERN = (
    r"[\[【(（]\s*("
    + "|".join(re.escape(k) for k in EMOTION_ALIASES.keys())
    + r")\s*[\]】)）]"
)
TAG_REGEX = re.compile(TAG_PATTERN, re.IGNORECASE)

T = TypeVar("T", bound=BaseModel)


def normalize_emotion(raw_tag: str) -> str:
    """タグ文字列から正規化された感情名 (JOY, SAD, ANGRY, SURPRISE, NORMAL) を返す"""
    clean = re.sub(r"[\[【(（\]】)）\s]", "", raw_tag).upper()
    return EMOTION_ALIASES.get(clean, "NORMAL")


def infer_emotion_from_text(text: str, default: str = "NORMAL") -> str:
    """
    LLMが感情タグを付け忘れた場合に、文末記号や語彙から感情を自動推定するフォールバック関数
    """
    t = text.strip()
    if not t:
        return default

    # 1. 悲しみ・謝罪・落胆系
    if any(
        w in t
        for w in [
            "ごめん",
            "申し訳",
            "悲し",
            "寂し",
            "辛",
            "つらい",
            "泣",
            "残念",
            "しょんぼり",
            "うぅ",
            "うう",
            "ぐすっ",
            "失敗",
            "ダメ",
        ]
    ):
        return "SAD"
    if ("……" in t or "..." in t or "…" in t) and not ("！" in t or "!" in t):
        return "SAD"

    # 2. 怒り・ツッコミ・抗議系
    if any(
        w in t
        for w in [
            "怒",
            "ダメです",
            "いけません",
            "いい加減に",
            "ぷんぷん",
            "許しません",
            "許さない",
            "こら",
            "もう！",
            "バカ",
            "むかっ",
        ]
    ):
        return "ANGRY"

    # 3. 驚き・疑問強調系
    if any(
        w in t
        for w in [
            "えっ",
            "ええっ",
            "まさか",
            "びっくり",
            "驚",
            "なんと",
            "うそっ",
            "本当ですか",
            "ほんとですか",
            "！？",
            "!?",
            "?!",
            "？！",
        ]
    ):
        return "SURPRISE"

    # 4. 喜び・ポジティブ・感嘆系
    if any(
        w in t
        for w in [
            "嬉し",
            "うれし",
            "楽し",
            "やった",
            "すご",
            "最高",
            "ありがとう",
            "大好き",
            "わーい",
            "えへへ",
            "ふふ",
            "♪",
            "✨",
        ]
    ):
        return "JOY"
    if t.endswith("！") or t.endswith("!"):
        return "JOY"

    return default


def strip_all_emotion_tags(text: str) -> str:
    """テキスト内のすべての感情タグを除去して整形する（Discordチャット表示用）"""
    if not isinstance(text, str):
        return str(text)
    cleaned = TAG_REGEX.sub("", text)
    # 連続する空白を整理（改行は維持）
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def extract_emotion(text: str, default_emotion: str = "NORMAL") -> tuple[str, str]:
    """
    単一文から感情タグを抽出して削除する。
    タグが存在しない場合はテキスト内容から自動推定する。
    """
    if not isinstance(text, str):
        return str(text), default_emotion

    match = TAG_REGEX.search(text)
    if match:
        emotion = normalize_emotion(match.group(1))
        clean_text = strip_all_emotion_tags(text)
        return clean_text, emotion

    clean_text = text.strip()
    emotion = infer_emotion_from_text(clean_text, default=default_emotion)
    return clean_text, emotion


def parse_emotions(text: str) -> list[tuple[str, str]]:
    """
    文章を解析し、(セリフ, 感情) のタプルリストに分割する。
    - 文頭タグ形式: `[JOY] こんにちは！ [NORMAL] いい天気ですね。`
    - 文末タグ形式: `こんにちは！[JOY] いい天気ですね。[NORMAL]`
    - タグなし長文: 句点・感嘆符で自然に文分割し、各文の感情を文脈から自動推定
    のいずれのパターンでも正確に動作するハイブリッドパーサー。
    """
    if not text or not text.strip():
        return []

    parts = TAG_REGEX.split(text)
    # TAG_REGEXには1つのキャプチャグループがあるため、
    # parts は [text0, tag1, text1, tag2, text2, ...] の形になる

    if len(parts) == 1:
        # タグが1つも含まれていない場合 -> 文単位で分割して自動推定
        return _split_and_infer_sentences(text)

    # 先頭タグ方式か末尾タグ方式かを自動判定
    # parts[0] が空（または空白のみ）なら先頭にタグがある -> 先頭タグ方式
    first_text = parts[0].strip()
    is_prefix_mode = len(first_text) == 0

    segments: list[tuple[str, str]] = []

    if is_prefix_mode:
        # [TAG1] text1 [TAG2] text2 ...
        current_emo = "NORMAL"
        for i in range(1, len(parts), 2):
            raw_tag = parts[i]
            following_text = parts[i + 1].strip() if i + 1 < len(parts) else ""
            current_emo = normalize_emotion(raw_tag)
            if following_text:
                segments.append((following_text, current_emo))
    else:
        # text0 [TAG1] text1 [TAG2] ... (文末タグ方式 or 混在)
        for i in range(0, len(parts), 2):
            chunk = parts[i].strip()
            if not chunk:
                continue
            if i + 1 < len(parts):
                emo = normalize_emotion(parts[i + 1])
                segments.append((chunk, emo))
            else:
                # 末尾にタグがない残りテキスト
                emo = infer_emotion_from_text(
                    chunk, default=segments[-1][1] if segments else "NORMAL"
                )
                segments.append((chunk, emo))

    return segments


def _split_and_infer_sentences(text: str) -> list[tuple[str, str]]:
    """タグがない文章を文単位で分割し、それぞれに自動感情推定を適用する"""
    sentences = re.split(r"(?<=[。！？!?\n])", text)
    results: list[tuple[str, str]] = []
    for s in sentences:
        clean = s.strip()
        if clean:
            results.append((clean, infer_emotion_from_text(clean, default="NORMAL")))
    return results


def sanitize_text_for_tts(
    text: str, word_dict: dict[str, str] | None = None, max_length: int = 120
) -> str:
    """
    TTS読み上げ用のテキストサニタイズ処理：
    1. コードブロック・インラインコードの省略
    2. URLの省略
    3. Discordカスタム絵文字・メンション記号の整形
    4. ユーザー辞書による最長一致置換（短い単語が長い単語を壊すのを防ぐ）
    5. 長文の省略（「以下略」）
    """
    if not text:
        return ""

    # 1. コードブロックを省略
    text = re.sub(r"```[\s\S]*?```", " コードブロック省略 ", text)
    text = re.sub(r"`[^`]+`", " コード ", text)

    # 2. URLを省略
    text = re.sub(r"https?://\S+", " URL省略 ", text)

    # 3. Discordのカスタム絵文字 <:name:123456> -> name
    text = re.sub(r"<a?:([a-zA-Z0-9_]+):\d+>", r" \1 ", text)

    # 4. メンションやチャンネル参照の記号除去
    text = re.sub(r"<@!?\d+>", " メンション ", text)
    text = re.sub(r"<@&\d+>", " ロール ", text)
    text = re.sub(r"<#\d+>", " チャンネル ", text)

    # 5. Markdown装飾記号の除去
    text = re.sub(r"[*_~|>]", "", text)

    # 6. 単語辞書の最長一致置換（キーの文字数が長い順にソートして一括正規表現置換）
    if word_dict:
        sorted_keys = sorted((k for k in word_dict.keys() if k), key=len, reverse=True)
        if sorted_keys:
            pattern = re.compile("|".join(re.escape(k) for k in sorted_keys))
            text = pattern.sub(lambda m: word_dict[m.group(0)], text)

    # 余分な空白・改行を整理
    text = re.sub(r"\s+", " ", text).strip()

    # 7. 文字数制限
    if max_length > 0 and len(text) > max_length:
        text = text[:max_length] + "、以下略"

    return text


def load_json(filename, default):
    """
    JSONファイルを読み込み、データとして返します。
    読み込みに失敗した場合やファイルが存在しない場合はデフォルト値を返します。
    """
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load JSON from {filename}: {e}")
            return default
    return default


def load_pydantic_model(filename: str, model_class: Type[T]) -> T:
    """
    JSONファイルを読み込み、指定されたPydanticモデル(V2)に変換して返します。
    """
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            return model_class.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to load model from {filename}: {e}")
            return model_class()
    return model_class()


def save_json(filename, data):
    """データをアトミックに近い形で安全にJSONファイルとして保存する"""
    try:
        temp_file = f"{filename}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(temp_file, filename)
    except Exception as e:
        logger.error(f"Error saving {filename}: {e}")
