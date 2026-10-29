from collections import deque
import logging
import os
import random
import re
import traceback
import discord
from discord import app_commands
from discord.ext import commands
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
import settings
from .consts import (
    TAG_REGEX,
    extract_emotion,
    infer_emotion_from_text,
    normalize_emotion,
    parse_emotions,
    strip_all_emotion_tags,
)

# ロガーの設定
logger = logging.getLogger(__name__)

# 文の区切り判定（句点・感嘆符・疑問符・改行が来て、その直後が閉じ括弧や連続記号でない位置）
SENTENCE_END_REGEX = re.compile(r"([。！？!?\n]+)(?![。！？!?…・」』）\)])")


def split_discord_message(text: str, max_len: int = 1900) -> list[str]:
    """
    2000文字を超える長文を、改行や句点を優先して max_len 文字以下のチャンクに安全に分割する。
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        # 改行または句点で区切れる位置を後ろから探す
        split_idx = remaining.rfind("\n", 0, max_len)
        if split_idx <= 0:
            split_idx = remaining.rfind("。", 0, max_len)
            if split_idx > 0:
                split_idx += 1  # 「。」を含める
        if split_idx <= 0:
            split_idx = max_len

        chunks.append(remaining[:split_idx].strip())
        remaining = remaining[split_idx:].strip()

    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


class StreamSentenceBuffer:
    """
    LLMのストリーミングトークンを逐次受け取り、
    1文が完成するごとに (セリフ, 感情タグ) を即座に抽出して返すストリームパーサー。
    文頭タグ方式 (`[JOY] こんにちは！`)・文末タグ方式 (`こんにちは！[JOY]`)・タグなし自動推定のすべてに対応。
    """

    def __init__(self):
        self.buffer = ""
        self.current_emotion: str | None = None

    def feed(self, token: str) -> list[tuple[str, str]]:
        self.buffer += token
        results: list[tuple[str, str]] = []

        while True:
            # 1. 先頭に感情タグがあれば取得して保持する
            leading_tag = TAG_REGEX.match(self.buffer.lstrip())
            if leading_tag:
                ws_len = len(self.buffer) - len(self.buffer.lstrip())
                tag_end = ws_len + leading_tag.end()
                if self.current_emotion is None:
                    self.current_emotion = normalize_emotion(leading_tag.group(1))
                self.buffer = self.buffer[tag_end:]
                continue

            # 2. バッファ途中に新しい感情タグが出現した場合、その手前までを1文として確定する
            mid_tag = TAG_REGEX.search(self.buffer)
            if mid_tag and mid_tag.start() > 0:
                before_text = self.buffer[: mid_tag.start()].strip()
                tag_emo = normalize_emotion(mid_tag.group(1))

                if self.current_emotion is None:
                    emo = tag_emo
                    self.buffer = self.buffer[mid_tag.end() :]
                else:
                    emo = self.current_emotion
                    self.current_emotion = None
                    self.buffer = self.buffer[mid_tag.start() :]

                if before_text:
                    results.append((before_text, emo))
                continue

            # 3. 句点・感嘆符で文が区切られたかチェック
            sent_match = SENTENCE_END_REGEX.search(self.buffer)
            if sent_match and (
                self.current_emotion is not None or len(self.buffer) >= 35
            ):
                cut_idx = sent_match.end()
                sentence = self.buffer[:cut_idx].strip()
                self.buffer = self.buffer[cut_idx:]
                if sentence:
                    emo = self.current_emotion or infer_emotion_from_text(
                        sentence, default="NORMAL"
                    )
                    self.current_emotion = None
                    results.append((sentence, emo))
                continue

            break

        return results

    def flush(self) -> list[tuple[str, str]]:
        """ストリーム完了時にバッファに残ったテキストをすべてフラッシュする"""
        rem = self.buffer.strip()
        self.buffer = ""
        if not rem:
            return []

        if self.current_emotion is not None:
            clean = strip_all_emotion_tags(rem)
            emo = self.current_emotion
            self.current_emotion = None
            return [(clean, emo)] if clean else []

        return parse_emotions(rem)


class ChatSystem(commands.Cog):
    """
    LLM (Large Language Model) を用いた対話機能を提供するクラス。
    ストリーミング生成と先行TTS合成により低遅延な対話を実現する。
    """

    def __init__(self, bot):
        self.bot = bot
        base_url = getattr(
            settings,
            "LLM_BASE_URL",
            os.getenv("LLM_BASE_URL", "http://localhost:1234/v1"),
        )
        api_key = getattr(
            settings, "LLM_API_KEY", os.getenv("LLM_API_KEY", "lm-studio")
        )
        self.model_name = getattr(
            settings, "LLM_MODEL", os.getenv("LLM_MODEL", "local-model")
        )
        # 45秒のタイムアウトを設定し、LLMサーバー応答停止時にBotが固まるのを防止
        self.llm_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=45.0,
        )
        # 会話履歴を保持する辞書 (channel_id: deque, オンメモリ保持)
        self.histories: dict[int, deque] = {}

    def _get_history(self, channel_id: int) -> deque:
        if channel_id not in self.histories:
            self.histories[channel_id] = deque(maxlen=20)
        return self.histories[channel_id]

    @app_commands.command(name="reset", description="LLMとの会話履歴をリセットします")
    async def reset_history(self, interaction: discord.Interaction):
        """現在のチャンネルの会話履歴を消去する"""
        channel_id = interaction.channel_id
        if channel_id in self.histories:
            self.histories[channel_id].clear()
            msg = "会話履歴をリセットしました。[JOY]"
        else:
            msg = "履歴はありませんでした。[NORMAL]"

        clean_text, emotion = extract_emotion(msg)
        await interaction.response.send_message(f"🧹 {clean_text}")

        if interaction.guild and interaction.guild.voice_client:
            audio_cog = self.bot.get_cog("AudioSystem")
            if audio_cog:
                audio_cog.enqueue_speech(
                    interaction.guild.voice_client, clean_text, emotion
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        is_mentioned = self.bot.user in message.mentions
        is_reply = False
        if message.reference and message.reference.resolved:
            resolved_author = getattr(message.reference.resolved, "author", None)
            if resolved_author == self.bot.user:
                is_reply = True

        if not (is_mentioned or is_reply):
            return

        if not settings.SYSTEM_PROMPT:
            return

        user_input = re.sub(rf"<@!?{self.bot.user.id}>", "", message.content).strip()
        if not user_input:
            return

        history = self._get_history(message.channel.id)
        messages = [{"role": "system", "content": settings.SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        audio_cog = self.bot.get_cog("AudioSystem")
        vc_client = (
            message.guild.voice_client
            if (
                audio_cog
                and message.guild
                and message.guild.voice_client
                and message.guild.voice_client.is_connected()
            )
            else None
        )

        async with message.channel.typing():
            try:
                stream_parser = StreamSentenceBuffer()
                full_response_chunks: list[str] = []

                stream = await self.llm_client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=0.7,
                    stream=True,
                )

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if not delta:
                        continue

                    full_response_chunks.append(delta)

                    # VC接続中は1文完成するごとに即座にTTS合成キューへ先行投入
                    if vc_client and audio_cog:
                        ready_sentences = stream_parser.feed(delta)
                        for sent_text, sent_emo in ready_sentences:
                            audio_cog.enqueue_speech(vc_client, sent_text, sent_emo)

                # 残りのバッファをフラッシュ
                if vc_client and audio_cog:
                    for sent_text, sent_emo in stream_parser.flush():
                        audio_cog.enqueue_speech(vc_client, sent_text, sent_emo)

                response_text = "".join(full_response_chunks).strip()
                if not response_text:
                    return

                # 成功時のみユーザー入力とアシスタント応答をペアで履歴に追加
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": response_text})

                clean_text = strip_all_emotion_tags(response_text)
                if clean_text:
                    # 2000文字を超える長文でも安全に分割して返信
                    chunks = split_discord_message(clean_text, max_len=1900)
                    for i, msg_part in enumerate(chunks):
                        if i == 0:
                            await message.reply(msg_part)
                        else:
                            await message.channel.send(msg_part)

            except (APIConnectionError, APITimeoutError) as e:
                logger.error(f"LLM connection or timeout error: {e}")
                await self._send_error_reply(message)

            except Exception as e:
                logger.error(f"Chat processing failed: {e}")
                logger.error(traceback.format_exc())
                await self._send_error_reply(message)

    async def _send_error_reply(self, message: discord.Message):
        try:
            audio_cog = self.bot.get_cog("AudioSystem")
            if audio_cog:
                clean_msg, emotion = audio_cog.get_response_text_and_emotion(
                    "chat_error_reply"
                )
            else:
                error_replies = settings.RESPONSES.get(
                    "chat_error_reply", ["エラーが発生しました。[SAD]"]
                )
                error_msg = (
                    random.choice(error_replies)
                    if isinstance(error_replies, list)
                    else error_replies
                )
                clean_msg, emotion = extract_emotion(error_msg)

            clean_msg = clean_msg or "エラーが発生しました。"
            await message.reply(clean_msg)

            if (
                audio_cog
                and message.guild
                and message.guild.voice_client
                and message.guild.voice_client.is_connected()
            ):
                audio_cog.enqueue_speech(message.guild.voice_client, clean_msg, emotion)

        except Exception as e:
            logger.error(f"Failed to send error reply: {e}")


async def setup(bot):
    await bot.add_cog(ChatSystem(bot))
