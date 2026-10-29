import asyncio
import io
import logging
import random
import traceback
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
import settings
from .consts import (
    extract_emotion,
    load_json,
    parse_emotions,
    sanitize_text_for_tts,
    save_json,
    strip_all_emotion_tags,
)
from .models import CharacterResponses
from .tts_engines import get_tts_provider

# Rust拡張モジュールのインポート
try:
    import rust_core
except ImportError:
    print("[CRITICAL] 'rust_core' module not found.")
    rust_core = None

logger = logging.getLogger(__name__)


class RustAudioSource(discord.AudioSource):
    """メモリ上のPCMデータを再生するAudioSource"""

    def __init__(self, pcm_data: bytes):
        self.stream = io.BytesIO(pcm_data)

    def read(self):
        return self.stream.read(3840)

    def is_opus(self):
        return False


class AudioSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 2段パイプライン（先行TTS合成キュー -> 再生キュー）
        self.synthesis_queue: asyncio.Queue = asyncio.Queue()
        self.playback_queue: asyncio.Queue = asyncio.Queue()
        self._generation_id: int = 0

        engine_name = getattr(settings, "TTS_ENGINE", "aivoice")
        self.tts_provider = get_tts_provider(engine_name)
        self.tts_provider.initialize()

        start_char = getattr(settings, "STARTUP_CHARACTER", None)
        if start_char:
            logger.info(f"Applying startup character: {start_char}")
            self.tts_provider.set_preset(start_char)

        # Pydantic V2 モデルとしてロード
        try:
            self.responses = CharacterResponses.model_validate(settings.RESPONSES)
        except Exception as e:
            logger.warning(
                f"Failed to validate RESPONSES into Pydantic model ({e}). Using default."
            )
            self.responses = CharacterResponses()

        self.word_dict = load_json("dictionary.json", {})
        self.config = load_json("config.json", {})
        # 保存されている全体オーディオ設定があれば起動時に反映
        if "audio_gain_db" in self.config:
            settings.AUDIO_GAIN_DB = float(self.config["audio_gain_db"])
        if "audio_reverb_enabled" in self.config:
            settings.AUDIO_REVERB_ENABLED = bool(self.config["audio_reverb_enabled"])
        if "audio_reverb_mix" in self.config:
            settings.AUDIO_REVERB_MIX = float(self.config["audio_reverb_mix"])

        self.synth_task: asyncio.Task | None = None
        self.play_task: asyncio.Task | None = None

    async def cog_load(self):
        loop = asyncio.get_running_loop()
        self.synth_task = loop.create_task(self._synthesis_worker())
        self.play_task = loop.create_task(self._playback_worker())

    def cog_unload(self):
        if self.synth_task:
            self.synth_task.cancel()
        if self.play_task:
            self.play_task.cancel()
        if self.tts_provider:
            self.tts_provider.terminate()

    def reload_config(self):
        self.config = load_json("config.json", {})

    def save_config(self):
        save_json("config.json", self.config)

    def get_user_voice_prefs(self, user_id: int) -> dict:
        """ユーザー個人のボイス・音量・リバーブ設定を取得する"""
        user_prefs = self.config.get("user_voices", {})
        return user_prefs.get(str(user_id), {})

    def update_responses(self, new_responses_dict: dict):
        """キャラクター変更時にレスポンス定義をPydantic V2モデルで更新する"""
        try:
            self.responses = CharacterResponses.model_validate(new_responses_dict)
            logger.info("AudioSystem responses updated.")
        except Exception as e:
            logger.error(f"Failed to update responses: {e}")

    def get_response_text_and_emotion(
        self, key: str, **kwargs
    ) -> tuple[str | None, str]:
        """
        Pydanticモデル（self.responses）からレスポンスを取得し、
        (タグ除去済みテキスト, 感情タグ) を返す共通メソッド。
        """
        raw_val = getattr(self.responses, key, None)
        if raw_val is None:
            raw_val = settings.RESPONSES.get(key)
        if raw_val is None:
            return None, "NORMAL"

        template = random.choice(raw_val) if isinstance(raw_val, list) else raw_val
        if not template:
            return None, "NORMAL"

        try:
            text = template.format(**kwargs)
        except Exception as e:
            logger.warning(f"Format error in response key '{key}': {e}")
            text = template

        return extract_emotion(text)

    # --- 2段パイプライン (先行合成 + 連続再生) ---

    async def _synthesis_worker(self):
        """
        第1ステージ: テキストを順次TTS合成＋Rust加工し、完了したAudioSourceを再生キューへ送る。
        """
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                (
                    vc_client,
                    text,
                    emotion,
                    gen_id,
                    voice_override,
                    gain_override,
                    reverb_override,
                ) = await self.synthesis_queue.get()
                try:
                    if (
                        gen_id == self._generation_id
                        and vc_client
                        and vc_client.is_connected()
                    ):
                        loop = asyncio.get_running_loop()
                        audio_source = await loop.run_in_executor(
                            None,
                            self._generate_audio_sync,
                            text,
                            emotion,
                            voice_override,
                            gain_override,
                            reverb_override,
                        )
                        if audio_source and gen_id == self._generation_id:
                            await self.playback_queue.put(
                                (vc_client, audio_source, gen_id)
                            )
                except Exception as e:
                    logger.error(f"Synthesis worker error: {e}")
                    logger.error(traceback.format_exc())
                finally:
                    self.synthesis_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Synthesis queue error: {e}")

    async def _playback_worker(self):
        """
        第2ステージ: 合成済みのAudioSourceを受け取り、Eventベースで遅延なく再生する。
        """
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                vc_client, audio_source, gen_id = await self.playback_queue.get()
                try:
                    if (
                        gen_id == self._generation_id
                        and vc_client
                        and vc_client.is_connected()
                    ):
                        await self.play_audio_source(vc_client, audio_source)
                except Exception as e:
                    logger.error(f"Playback worker error: {e}")
                    logger.error(traceback.format_exc())
                finally:
                    self.playback_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Playback queue error: {e}")

    def _generate_audio_sync(
        self,
        text: str,
        emotion: str,
        voice_override: str | None = None,
        gain_override: float | None = None,
        reverb_override: bool | None = None,
    ) -> RustAudioSource | None:
        """
        【別スレッド実行用】
        テキストサニタイズ＆最長一致辞書置換 -> オンメモリTTS生成 -> Rustパイプライン加工 -> AudioSource作成
        """
        if not rust_core:
            return None

        clean_text = sanitize_text_for_tts(text, self.word_dict, max_length=150)
        if not clean_text:
            return None

        try:
            # 1. TTSエンジンでWAVバイト列を生成（voice_override対応）
            wav_bytes = self.tts_provider.generate_audio(
                clean_text, emotion, voice_override=voice_override
            )

            if wav_bytes and len(wav_bytes) > 44:
                gain_db = (
                    float(gain_override)
                    if gain_override is not None
                    else float(getattr(settings, "AUDIO_GAIN_DB", 3.0))
                )
                reverb_enabled = (
                    bool(reverb_override)
                    if reverb_override is not None
                    else bool(getattr(settings, "AUDIO_REVERB_ENABLED", True))
                )

                # 2. Rustパイプライン処理 (オンメモリ・GIL解放済み)
                pcm_data = rust_core.process_audio_pipeline(
                    wav_bytes,
                    gain_db,
                    int(getattr(settings, "AUDIO_SILENCE_THRESHOLD", 100)),
                    reverb_enabled,
                    int(getattr(settings, "AUDIO_REVERB_DELAY_MS", 50)),
                    float(getattr(settings, "AUDIO_REVERB_DECAY", 0.3)),
                    float(getattr(settings, "AUDIO_REVERB_MIX", 0.15)),
                )
                if pcm_data:
                    return RustAudioSource(pcm_data)
            else:
                logger.warning(f"TTS generation returned empty data for: {clean_text}")
                return None

        except Exception as e:
            logger.error(f"Audio generation failed: {e}")
            logger.error(traceback.format_exc())
            return None

    async def play_audio_source(self, vc_client: discord.VoiceClient, audio_source):
        if not vc_client or not vc_client.is_connected():
            return
        try:
            while vc_client.is_playing() and vc_client.is_connected():
                await asyncio.sleep(0.05)

            if not vc_client.is_connected():
                return

            done_event = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _after_play(error):
                if error:
                    logger.error(f"Player callback error: {error}")
                loop.call_soon_threadsafe(done_event.set)

            vc_client.play(audio_source, after=_after_play)
            # 万が一VCが異常切断されてコールバックが来ない場合でもハングしないよう安全に待機
            while not done_event.is_set() and vc_client.is_connected():
                try:
                    await asyncio.wait_for(done_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    if not vc_client.is_playing():
                        break
        except Exception as e:
            logger.error(f"Playback error: {e}")

    def enqueue_speech(
        self,
        vc_client,
        text: str,
        emotion: str = "NORMAL",
        voice_override: str | None = None,
        gain_override: float | None = None,
        reverb_override: bool | None = None,
    ):
        if not text or not text.strip():
            return
        voice_tag = f", voice={voice_override}" if voice_override else ""
        logger.info(f"Audio Enqueued: {text} ({emotion}{voice_tag})")
        self.synthesis_queue.put_nowait(
            (
                vc_client,
                text.strip(),
                emotion,
                self._generation_id,
                voice_override,
                gain_override,
                reverb_override,
            )
        )

    def clear_queues(self):
        """合成キューと再生キューの両方を即座に破棄する"""
        self._generation_id += 1
        for q in (self.synthesis_queue, self.playback_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                    q.task_done()
                except asyncio.QueueEmpty:
                    break

    async def wait_queues(self):
        """すべての音声合成と再生が完了するまで待機する"""
        await self.synthesis_queue.join()
        await self.playback_queue.join()

    # --- Autocomplete Helpers ---

    async def preset_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        presets = self.tts_provider.get_presets()
        choices = [
            app_commands.Choice(name=p, value=p)
            for p in presets
            if current.lower() in p.lower()
        ]
        if "reset".startswith(current.lower()) or "解除" in current:
            choices.insert(
                0,
                app_commands.Choice(
                    name="🔄 個人設定をリセット (Botの標準ボイスを使用)", value="reset"
                ),
            )
        return choices[:25]

    async def persona_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        personas = settings.get_available_personas()
        choices = []
        for pid, info in personas.items():
            label = f"{info['display_name']} ({pid})"
            if current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label, value=pid))
        if "none".startswith(current.lower()) or "オフ" in current:
            choices.append(
                app_commands.Choice(name="LLMを使用しない (オフ)", value="none")
            )
        return choices[:25]

    # --- Commands ---

    @app_commands.command(
        name="say",
        description="入力したメッセージをVCで読み上げます（/myvoiceの個人ボイス・感情指定に対応）",
    )
    @app_commands.describe(
        text="読み上げるテキスト（例: こんにちは！ / [SAD] お腹すいた…）",
        emotion="感情を明示的に指定する場合（未指定時は文脈やタグから自動判定）",
    )
    @app_commands.choices(
        emotion=[
            app_commands.Choice(name="自動判定 (Auto)", value="AUTO"),
            app_commands.Choice(name="喜び (JOY)", value="JOY"),
            app_commands.Choice(name="平静 (NORMAL)", value="NORMAL"),
            app_commands.Choice(name="悲しみ (SAD)", value="SAD"),
            app_commands.Choice(name="怒り (ANGRY)", value="ANGRY"),
            app_commands.Choice(name="驚き (SURPRISE)", value="SURPRISE"),
        ]
    )
    async def say(
        self,
        interaction: discord.Interaction,
        text: str,
        emotion: str = "AUTO",
    ):
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc or not vc.is_connected():
            if interaction.user.voice and interaction.user.voice.channel:
                vc = await interaction.user.voice.channel.connect()
            else:
                await interaction.response.send_message(
                    "❌ ボイスチャンネルに接続していません。先に `/join` するかVCに入ってください。",
                    ephemeral=True,
                )
                return

        display_text = strip_all_emotion_tags(text)
        if not display_text:
            await interaction.response.send_message(
                "❌ 読み上げるテキストが空です。", ephemeral=True
            )
            return

        # ユーザー個人の /myvoice 設定を取得
        prefs = self.get_user_voice_prefs(interaction.user.id)
        u_voice = prefs.get("voice")
        u_gain = prefs.get("gain_db")
        u_reverb = prefs.get("reverb")

        if emotion != "AUTO":
            self.enqueue_speech(
                vc,
                display_text,
                emotion,
                voice_override=u_voice,
                gain_override=u_gain,
                reverb_override=u_reverb,
            )
            emo_badge = f"`[{emotion}]`"
        else:
            segments = parse_emotions(text)
            for seg_text, seg_emo in segments:
                self.enqueue_speech(
                    vc,
                    seg_text,
                    seg_emo,
                    voice_override=u_voice,
                    gain_override=u_gain,
                    reverb_override=u_reverb,
                )
            emo_badge = (
                " ".join(f"`[{e}]`" for _, e in segments) if segments else "`[NORMAL]`"
            )

        voice_note = f" 🎙️`{u_voice}`" if u_voice else ""
        await interaction.response.send_message(
            f"💬 **{interaction.user.display_name}**{voice_note}: {display_text} {emo_badge}"
        )

    @app_commands.command(
        name="myvoice",
        description="あなたが /say で話すときの専用ボイス・音量・リバーブを設定します（A.I.VOICE / VOICEVOX両対応）",
    )
    @app_commands.describe(
        voice="自分が /say で使うTTSボイス（'reset' を選ぶと個人設定を解除してBot標準に戻します）",
        gain_db="自分の声の音量調整 (dB, 例: 3.0=標準, 6.0=大きめ, 0.0=小さめ)",
        reverb="自分の声にリバーブ（エコー）をかけるか (True/False)",
    )
    @app_commands.autocomplete(voice=preset_autocomplete)
    async def myvoice(
        self,
        interaction: discord.Interaction,
        voice: Optional[str] = None,
        gain_db: Optional[float] = None,
        reverb: Optional[bool] = None,
    ):
        self.reload_config()
        user_voices = self.config.setdefault("user_voices", {})
        uid_str = str(interaction.user.id)

        if voice and voice.lower() == "reset":
            user_voices.pop(uid_str, None)
            self.save_config()
            await interaction.response.send_message(
                "🔄 あなたの個人ボイス設定をリセットしました（Botの標準ボイスを使用します）。",
                ephemeral=True,
            )
            return

        if voice is None and gain_db is None and reverb is None:
            current_prefs = user_voices.get(uid_str, {})
            if not current_prefs:
                await interaction.response.send_message(
                    "ℹ️ 現在、あなたの個人ボイスは未設定です（Bot標準ボイスを使用中）。\n"
                    "`/myvoice voice:<プリセット>` で専用の声を設定できます。",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"🎙️ **あなたの `/myvoice` 設定**:\n"
                    f"・専用ボイス: **{current_prefs.get('voice') or 'Bot標準'}**\n"
                    f"・音量 (Gain): **{current_prefs.get('gain_db', 'Bot標準')} dB**\n"
                    f"・リバーブ: **{current_prefs.get('reverb', 'Bot標準')}**",
                    ephemeral=True,
                )
            return

        prefs = user_voices.get(uid_str, {})
        if voice:
            presets = self.tts_provider.get_presets()
            matched = next(
                (p for p in presets if voice == p or voice in p),
                None,
            )
            if not matched:
                await interaction.response.send_message(
                    f"❌ プリセット `{voice}` が見つかりません。", ephemeral=True
                )
                return
            prefs["voice"] = matched

        if gain_db is not None:
            prefs["gain_db"] = max(-15.0, min(15.0, gain_db))

        if reverb is not None:
            prefs["reverb"] = reverb

        user_voices[uid_str] = prefs
        self.save_config()

        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name} さんの `/say` 専用ボイスを設定しました！**\n"
            f"・専用ボイス: **{prefs.get('voice') or 'Bot標準'}**\n"
            f"・音量 (Gain): **{prefs.get('gain_db', 'Bot標準')} dB**\n"
            f"・リバーブ: **{prefs.get('reverb', 'Bot標準')}**"
        )

    @app_commands.command(
        name="audio",
        description="Bot全体の基本音量（ゲイン）やリバーブ（エコー）をリアルタイムに調整します",
    )
    @app_commands.describe(
        gain_db="全体の基本音量 (dB, 標準: 3.0, 範囲: -15.0 〜 +15.0)",
        reverb="リバーブ（エコー）の有効/無効 (True/False)",
        reverb_mix="リバーブの深さ (0.05 〜 0.5, 標準: 0.15)",
    )
    async def audio_settings(
        self,
        interaction: discord.Interaction,
        gain_db: Optional[float] = None,
        reverb: Optional[bool] = None,
        reverb_mix: Optional[float] = None,
    ):
        self.reload_config()
        if gain_db is not None:
            clamped_gain = max(-15.0, min(15.0, gain_db))
            settings.AUDIO_GAIN_DB = clamped_gain
            self.config["audio_gain_db"] = clamped_gain

        if reverb is not None:
            settings.AUDIO_REVERB_ENABLED = reverb
            self.config["audio_reverb_enabled"] = reverb

        if reverb_mix is not None:
            clamped_mix = max(0.0, min(0.8, reverb_mix))
            settings.AUDIO_REVERB_MIX = clamped_mix
            self.config["audio_reverb_mix"] = clamped_mix

        self.save_config()

        await interaction.response.send_message(
            f"🎛️ **Bot全体のオーディオ設定を更新しました**\n"
            f"・基本音量 (Gain): **{settings.AUDIO_GAIN_DB} dB**\n"
            f"・リバーブ (Reverb): **{'ON' if settings.AUDIO_REVERB_ENABLED else 'OFF'}** (Mix: `{settings.AUDIO_REVERB_MIX}`)"
        )

    @app_commands.command(name="join", description="ボイスチャンネルに接続します")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message(
                "❌ ボイスチャンネルに入ってから実行してください。", ephemeral=True
            )
            return

        target_channel = interaction.user.voice.channel
        guild_vc = interaction.guild.voice_client

        if guild_vc and guild_vc.is_connected():
            if guild_vc.channel == target_channel:
                await interaction.response.send_message(
                    "既にこのボイスチャンネルに接続しています。", ephemeral=True
                )
            else:
                await guild_vc.move_to(target_channel)
                await interaction.response.send_message(
                    f"🔊 **{target_channel.name}** に移動しました。"
                )
                text, emo = self.get_response_text_and_emotion("move_voice")
                self.enqueue_speech(guild_vc, text or "移動しました", emo)
        else:
            vc = await target_channel.connect()
            await interaction.response.send_message(
                f"🔊 **{target_channel.name}** に接続しました。"
            )
            text, emo = self.get_response_text_and_emotion("join_greet_first")
            self.enqueue_speech(vc, text or "接続しました", emo)

    @app_commands.command(
        name="stop", description="現在の読み上げを停止し、キューを空にします"
    )
    async def stop(self, interaction: discord.Interaction):
        self.clear_queues()
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_playing():
            vc.stop()

        await interaction.response.send_message("⏹️ 読み上げを停止しました。")

    @app_commands.command(name="bye", description="ボイスチャンネルから切断します")
    async def bye(self, interaction: discord.Interaction):
        await interaction.response.defer()

        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_connected():
            await interaction.followup.send("👋 切断しています...")

            # 残っている未再生キューをクリアしてからお別れボイスだけを再生する
            self.clear_queues()
            if vc.is_playing():
                vc.stop()

            text, emo = self.get_response_text_and_emotion("disconnect_msg")
            if text:
                self.enqueue_speech(vc, text, emo)

            try:
                await asyncio.wait_for(self.wait_queues(), timeout=12.0)
            except asyncio.TimeoutError:
                logger.warning("Disconnect speech timed out; forcing disconnect.")

            await vc.disconnect()
        else:
            await interaction.followup.send(
                "ボイスチャンネルに接続していません。", ephemeral=True
            )

    @app_commands.command(
        name="char",
        description="キャラクターの人格設定・セリフ・TTSボイス（声・スタイル）を切り替えます",
    )
    @app_commands.describe(
        persona="切り替えるキャラクター人格（akari / yukari / zundamon / none）",
        voice="使用するTTSボイスプリセット（省略時はキャラクターの推奨ボイスに自動設定）",
    )
    @app_commands.autocomplete(persona=persona_autocomplete, voice=preset_autocomplete)
    async def char(
        self,
        interaction: discord.Interaction,
        persona: Optional[str] = None,
        voice: Optional[str] = None,
    ):
        if not persona and not voice:
            await interaction.response.send_message(
                f"🎭 **現在のキャラクター設定**\n"
                f"・人格設定: **{settings.CURRENT_PERSONA_DISPLAY_NAME}** (`{settings.CURRENT_PERSONA_ID or 'none'}`)\n"
                f"・TTSボイス: **{settings.STARTUP_CHARACTER or 'デフォルト'}**\n"
                f"（※変更する場合は `persona` または `voice` を指定してください）",
                ephemeral=True,
            )
            return

        personas = settings.get_available_personas()
        engine = getattr(settings, "TTS_ENGINE", "aivoice")
        persona_label = settings.CURRENT_PERSONA_DISPLAY_NAME

        # 1. 人格設定の切り替え（persona指定時、またはvoice名からの自動連動）
        if persona:
            if persona.lower() == "none":
                settings.load_character_config(None)
                persona_label = "オフ (システムセリフのみ)"
            elif persona in personas:
                settings.load_character_config(personas[persona]["dir"])
                persona_label = f"{personas[persona]['display_name']} (`{persona}`)"
                if not voice:
                    recommended = personas[persona]["default_voices"].get(engine)
                    if recommended:
                        voice = recommended
            else:
                await interaction.response.send_message(
                    f"❌ 人格設定 `{persona}` が見つかりません。", ephemeral=True
                )
                return

            self.update_responses(settings.RESPONSES)
            chat_cog = self.bot.get_cog("ChatSystem")
            if chat_cog and interaction.channel_id in chat_cog.histories:
                chat_cog.histories[interaction.channel_id].clear()

        # 2. TTSボイスの切り替え
        applied_voice = getattr(settings, "STARTUP_CHARACTER", "変更なし")
        if voice:
            if self.tts_provider.set_preset(voice):
                settings.STARTUP_CHARACTER = voice
                applied_voice = voice

                # もし persona が未指定で voice だけ指定された場合でも、
                # ボイス名に対応するキャラクター人格があれば自動連動する
                if not persona:
                    norm_style = voice.replace(" ", "").replace("　", "")
                    for pid, pinfo in personas.items():
                        disp = pinfo["display_name"].replace(" ", "").replace("　", "")
                        if disp and disp in norm_style:
                            settings.load_character_config(pinfo["dir"])
                            self.update_responses(settings.RESPONSES)
                            persona_label = (
                                f"{pinfo['display_name']} (`{pid}`・自動連動)"
                            )
                            break
            elif not persona:
                await interaction.response.send_message(
                    f"❌ プリセット `{voice}` が見つかりませんでした。", ephemeral=True
                )
                return
            else:
                applied_voice = f"{voice} (※未検出のため維持: {applied_voice})"

        # 3. config.json に保存して次回起動時にも反映
        self.reload_config()
        self.config["last_engine"] = engine
        self.config["last_persona"] = settings.CURRENT_PERSONA_ID
        if settings.STARTUP_CHARACTER:
            self.config["last_voice"] = settings.STARTUP_CHARACTER
        self.save_config()

        await interaction.response.send_message(
            f"🎭 **キャラクター設定を更新しました！**\n"
            f"・人格設定: **{persona_label}**\n"
            f"・TTSボイス: **{applied_voice}**"
        )

        if interaction.guild and interaction.guild.voice_client:
            text, emo = self.get_response_text_and_emotion("char_change_voice")
            self.enqueue_speech(
                interaction.guild.voice_client, text or "交代しました", emo
            )

    @app_commands.command(
        name="presets",
        description="現在利用可能なボイスとキャラクター人格の一覧を表示します",
    )
    async def list_presets(self, interaction: discord.Interaction):
        presets = self.tts_provider.get_presets()
        disp = "\n".join(f"・{p}" for p in presets[:20])
        if len(presets) > 20:
            disp += f"\n...他 {len(presets) - 20} 件"

        personas = settings.get_available_personas()
        persona_disp = "\n".join(
            f"・**{info['display_name']}** (`{pid}`)" for pid, info in personas.items()
        )

        await interaction.response.send_message(
            f"🎭 **現在の設定**: 人格={settings.CURRENT_PERSONA_DISPLAY_NAME} / ボイス={settings.STARTUP_CHARACTER}\n\n"
            f"**【利用可能なキャラクター人格 (`/char persona:...`)】**\n{persona_disp or 'なし'}\n\n"
            f"**【利用可能なTTSボイス (`/char voice:...` / `/myvoice`)】**\n{disp or 'なし'}"
        )

    @app_commands.command(
        name="autojoin",
        description="VC自動参加・ミュート等の状態通知・時報のON/OFFを設定します",
    )
    @app_commands.describe(
        enabled="VC入室時の自動参加を有効にするか (True/False)",
        channel="自動参加を許可する特定のボイスチャンネル（未指定時は全VC対象）",
        state_notify="ミュート・配信開始・カメラ等の状態変化ボイス通知を有効にするか (True/False)",
        time_signal="毎時00分の時報ボイスを有効にするか (True/False)",
    )
    async def autojoin(
        self,
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        channel: Optional[discord.VoiceChannel] = None,
        state_notify: Optional[bool] = None,
        time_signal: Optional[bool] = None,
    ):
        self.reload_config()
        if enabled is not None:
            self.config["auto_join_enabled"] = enabled
            if channel:
                self.config["auto_join_channel_id"] = channel.id
            elif enabled:
                self.config["auto_join_channel_id"] = None
        elif channel is not None:
            self.config["auto_join_channel_id"] = channel.id

        if state_notify is not None:
            self.config["voice_state_notify"] = state_notify

        if time_signal is not None:
            self.config["time_signal_enabled"] = time_signal

        self.save_config()

        aj_on = self.config.get("auto_join_enabled", True)
        aj_ch = self.config.get("auto_join_channel_id")
        sn_on = self.config.get("voice_state_notify", True)
        ts_on = self.config.get("time_signal_enabled", True)

        ch_label = f"<#{aj_ch}> のみ" if aj_ch else "すべてのVC"
        await interaction.response.send_message(
            f"⚙️ **VC・通知設定を更新しました**\n"
            f"・VC自動参加 (Auto Join): **{'ON' if aj_on else 'OFF'}** ({ch_label})\n"
            f"・状態変化通知 (ミュート・配信等): **{'ON' if sn_on else 'OFF'}**\n"
            f"・毎時00分の時報: **{'ON' if ts_on else 'OFF'}**"
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Bot自身がVCから強制切断された場合はキューをクリーンアップ
        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                self.clear_queues()
            return

        if member.bot:
            return

        # 1. 入室・チャンネル移動イベント
        if before.channel != after.channel:
            if after.channel is not None:
                vc = member.guild.voice_client
                if vc is None:
                    auto_join_enabled = self.config.get("auto_join_enabled", True)
                    target_ch_id = self.config.get("auto_join_channel_id", None)

                    if auto_join_enabled:
                        if target_ch_id is None or after.channel.id == int(
                            target_ch_id
                        ):
                            try:
                                vc = await after.channel.connect()
                            except Exception as e:
                                logger.error(f"Auto join failed: {e}")
                                return

                if vc and vc.channel == after.channel:
                    text, emo = self.get_response_text_and_emotion("join_greet_normal")
                    self.enqueue_speech(vc, text or "こんにちは", emo)

            # 退室時の Auto Disconnect (VCに人間がいなくなったらキューをクリアして切断)
            vc = member.guild.voice_client
            if vc and before.channel == vc.channel:
                human_count = sum(1 for m in vc.channel.members if not m.bot)
                if human_count == 0:
                    self.clear_queues()
                    await vc.disconnect()
            return

        # 2. 同一VC内での状態変化イベント（ミュート・配信開始・カメラ等）
        vc = member.guild.voice_client
        if vc and after.channel == vc.channel:
            if self.config.get("voice_state_notify", True):
                events = [
                    (before.self_mute, after.self_mute, "mute_start", "mute_end"),
                    (before.self_deaf, after.self_deaf, "deaf_start", "deaf_end"),
                    (
                        before.self_stream,
                        after.self_stream,
                        "stream_start",
                        "stream_end",
                    ),
                    (before.self_video, after.self_video, "video_start", "video_end"),
                ]
                for b_state, a_state, k_start, k_end in events:
                    if b_state != a_state:
                        key = k_start if a_state else k_end
                        text, emo = self.get_response_text_and_emotion(key)
                        if text:
                            self.enqueue_speech(vc, text, emo)


async def setup(bot):
    await bot.add_cog(AudioSystem(bot))
