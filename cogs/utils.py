import logging
import random
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import settings

from .consts import extract_emotion, load_json, save_json

# ロガーの設定
logger = logging.getLogger(__name__)


class Utilities(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.alarm_list = load_json("alarms.json", [])
        self.timer_list = self._load_timers()

        self.config = load_json("config.json", {})
        self.default_alarm_channel_id = int(self.config.get("alarmChannelId", 0))

        self.last_checked_minute = ""
        self.last_time_signal_hour = -1

        self.check_alarm_task.start()
        self.check_timer_task.start()

    def cog_unload(self):
        self.check_alarm_task.cancel()
        self.check_timer_task.cancel()

    def _load_timers(self) -> list[dict]:
        """timers.json から永続化されたタイマーを復元する"""
        raw_timers = load_json("timers.json", [])
        restored = []
        now = datetime.now()
        for t in raw_timers:
            try:
                end_time = datetime.fromisoformat(t["end_time"])
                if end_time > now - timedelta(minutes=5):
                    restored.append(
                        {
                            "end_time": end_time,
                            "minutes": t["minutes"],
                            "user_id": t["user_id"],
                            "channel_id": t["channel_id"],
                        }
                    )
            except Exception as e:
                logger.warning(f"Failed to restore timer entry: {e}")
        return restored

    def save_timers(self):
        """稼働中のタイマーを timers.json に永続化する"""
        serializable = [
            {
                "end_time": t["end_time"].isoformat(),
                "minutes": t["minutes"],
                "user_id": t["user_id"],
                "channel_id": t["channel_id"],
            }
            for t in self.timer_list
        ]
        save_json("timers.json", serializable)

    def save_alarms(self):
        save_json("alarms.json", self.alarm_list)

    def speak(self, guild, text: str, emotion: str | None = None):
        audio_cog = self.bot.get_cog("AudioSystem")
        if audio_cog and guild and guild.voice_client:
            if emotion is None:
                clean_text, emotion = extract_emotion(text)
            else:
                clean_text, _ = extract_emotion(text, default_emotion=emotion)
            audio_cog.enqueue_speech(guild.voice_client, clean_text, emotion)

    def get_random_text(self, key: str, **kwargs) -> str:
        """
        AudioSystem の Pydantic モデルと連携してレスポンスを取得する。
        """
        audio_cog = self.bot.get_cog("AudioSystem")
        raw_val = None
        if audio_cog and hasattr(audio_cog, "responses"):
            raw_val = getattr(audio_cog.responses, key, None)
        if raw_val is None:
            raw_val = settings.RESPONSES.get(key, "メッセージが見つかりません")

        template = random.choice(raw_val) if isinstance(raw_val, list) else raw_val

        try:
            return template.format(**kwargs)
        except Exception as e:
            logger.warning(f"Format Error in {key}: {e}")
            return template

    # --- Autocomplete Helpers for Deletion ---

    async def alarm_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        choices = []
        for i, alarm in enumerate(self.alarm_list):
            icon = "🔄" if alarm.get("repeat") else "1️⃣"
            label = f"{i + 1}: {alarm['time']} {icon} {alarm['message']}"[:100]
            if current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label, value=i + 1))
        return choices[:25]

    async def dict_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        audio_cog = self.bot.get_cog("AudioSystem")
        if not audio_cog or not audio_cog.word_dict:
            return []
        choices = []
        for i, (word, reading) in enumerate(audio_cog.word_dict.items()):
            label = f"{i + 1}: {word} → {reading}"[:100]
            if current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label, value=word))
        return choices[:25]

    async def timer_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        now = datetime.now()
        choices = []
        for i, timer in enumerate(self.timer_list):
            rem = max(0, int((timer["end_time"] - now).total_seconds()))
            rem_m, rem_s = rem // 60, rem % 60
            label = f"{i + 1}: 残り{rem_m}分{rem_s}秒 (元: {timer['minutes']}分)"[:100]
            if current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label, value=i + 1))
        return choices[:25]

    # --- アラーム機能 ---
    alarm_group = app_commands.Group(name="alarm", description="アラームの管理")

    @alarm_group.command(name="add", description="アラームを追加します")
    async def alarm_add(
        self,
        interaction: discord.Interaction,
        time: str,
        message: str,
        repeat: bool = False,
    ):
        clean_time = time.replace(" ", "").replace("：", ":")
        try:
            dt = datetime.strptime(clean_time, "%H:%M")
            formatted_time = dt.strftime("%H:%M")
        except ValueError:
            await interaction.response.send_message(
                "時間は `12:00` のように入力してください", ephemeral=True
            )
            return

        new_alarm = {
            "time": formatted_time,
            "message": message,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel_id,
            "repeat": repeat,
        }
        self.alarm_list.append(new_alarm)
        self.save_alarms()

        icon = "🔄" if repeat else "1️⃣"

        raw_text = self.get_random_text(
            "alarm_set_text", time=formatted_time, icon=icon, message=message
        )
        clean_text, _ = extract_emotion(raw_text)
        await interaction.response.send_message(clean_text)

        res_voice = self.get_random_text("alarm_set_voice", time=formatted_time)
        self.speak(interaction.guild, res_voice)

    @alarm_group.command(name="list", description="アラーム一覧")
    async def alarm_list_cmd(self, interaction: discord.Interaction):
        if not self.alarm_list:
            raw_text = self.get_random_text("alarm_list_empty_text")
            clean_text, _ = extract_emotion(raw_text)
            await interaction.response.send_message(clean_text)

            res_voice = self.get_random_text("alarm_list_empty_voice")
            self.speak(interaction.guild, res_voice)
            return

        text = "⏰ **アラーム一覧**\n"
        for i, alarm in enumerate(self.alarm_list):
            icon = "🔄" if alarm["repeat"] else "1️⃣"
            ch_name = ""
            if "channel_id" in alarm and interaction.guild:
                ch = interaction.guild.get_channel(alarm["channel_id"])
                if ch:
                    ch_name = f" (in {ch.name})"

            text += (
                f"`{i + 1}`. **{alarm['time']}** {icon} : {alarm['message']}{ch_name}\n"
            )

        res_voice = self.get_random_text("alarm_list_voice")
        await interaction.response.send_message(text)
        self.speak(interaction.guild, res_voice)

    @alarm_group.command(
        name="delete", description="アラームを削除します（候補から選択可）"
    )
    @app_commands.describe(index="削除するアラームを選択（または番号を入力）")
    @app_commands.autocomplete(index=alarm_delete_autocomplete)
    async def alarm_delete(self, interaction: discord.Interaction, index: int):
        if 1 <= index <= len(self.alarm_list):
            removed = self.alarm_list.pop(index - 1)
            self.save_alarms()

            raw_text = self.get_random_text(
                "alarm_delete_text", time=removed["time"], message=removed["message"]
            )
            clean_text, _ = extract_emotion(raw_text)

            res_voice = self.get_random_text("alarm_delete_voice")

            await interaction.response.send_message(clean_text)
            self.speak(interaction.guild, res_voice)
        else:
            await interaction.response.send_message(
                "その番号のアラームはありません", ephemeral=True
            )

    # --- 辞書機能 ---
    dict_group = app_commands.Group(name="dict", description="読み方辞書の管理")

    @dict_group.command(name="add", description="単語登録")
    async def dict_add(self, interaction: discord.Interaction, word: str, reading: str):
        audio_cog = self.bot.get_cog("AudioSystem")
        if not audio_cog:
            await interaction.response.send_message(
                "音声システムが起動していません", ephemeral=True
            )
            return

        audio_cog.word_dict[word] = reading
        save_json("dictionary.json", audio_cog.word_dict)

        raw_text = self.get_random_text("dict_add_text", word=word, reading=reading)
        clean_text, _ = extract_emotion(raw_text)

        res_voice = self.get_random_text("dict_add_voice", word=word, reading=reading)

        await interaction.response.send_message(clean_text)
        self.speak(interaction.guild, res_voice)

    @dict_group.command(
        name="delete",
        description="辞書から単語を削除します（候補から直接選択、または番号指定）",
    )
    @app_commands.describe(target="削除する単語を選択（または辞書番号を入力）")
    @app_commands.autocomplete(target=dict_delete_autocomplete)
    async def dict_delete(self, interaction: discord.Interaction, target: str):
        audio_cog = self.bot.get_cog("AudioSystem")
        if not audio_cog:
            return

        removed_word = None
        if target in audio_cog.word_dict:
            removed_word = target
            del audio_cog.word_dict[target]
        elif target.isdigit():
            idx = int(target)
            keys = list(audio_cog.word_dict.keys())
            if 1 <= idx <= len(keys):
                removed_word = keys[idx - 1]
                del audio_cog.word_dict[removed_word]

        if removed_word is not None:
            save_json("dictionary.json", audio_cog.word_dict)

            raw_text = self.get_random_text("dict_delete_text", word=removed_word)
            clean_text, _ = extract_emotion(raw_text)

            res_voice = self.get_random_text("dict_delete_voice", word=removed_word)

            await interaction.response.send_message(clean_text)
            self.speak(interaction.guild, res_voice)
        else:
            await interaction.response.send_message(
                f"単語 `{target}` は辞書に見つかりませんでした。", ephemeral=True
            )

    @dict_group.command(name="list", description="辞書一覧")
    async def dict_list(self, interaction: discord.Interaction):
        audio_cog = self.bot.get_cog("AudioSystem")
        if not audio_cog or not audio_cog.word_dict:
            raw_text = self.get_random_text("dict_empty_text")
            clean_text, _ = extract_emotion(raw_text)
            await interaction.response.send_message(clean_text)

            res_voice = self.get_random_text("dict_empty_voice")
            self.speak(interaction.guild, res_voice)
            return

        text = "📖 **辞書一覧**\n"
        for i, (k, v) in enumerate(audio_cog.word_dict.items()):
            text += f"`{i + 1}`. {k} → {v}\n"

        res_voice = self.get_random_text("dict_list_voice")
        await interaction.response.send_message(text[:1900])
        self.speak(interaction.guild, res_voice)

    # --- タイマー機能 ---
    timer_group = app_commands.Group(name="timer", description="タイマー管理")

    @timer_group.command(name="set", description="タイマーをセット(分)")
    async def timer_set(self, interaction: discord.Interaction, minutes: int):
        if minutes <= 0:
            await interaction.response.send_message(
                "1分以上で設定してください", ephemeral=True
            )
            return

        end_time = datetime.now() + timedelta(minutes=minutes)
        self.timer_list.append(
            {
                "end_time": end_time,
                "minutes": minutes,
                "user_id": interaction.user.id,
                "channel_id": interaction.channel_id,
            }
        )
        self.save_timers()

        raw_text = self.get_random_text("timer_set_text", minutes=minutes)
        clean_text, _ = extract_emotion(raw_text)

        res_voice = self.get_random_text("timer_set_voice", minutes=minutes)

        await interaction.response.send_message(clean_text)
        self.speak(interaction.guild, res_voice)

    @timer_group.command(name="list", description="稼働中のタイマー")
    async def timer_list_cmd(self, interaction: discord.Interaction):
        if not self.timer_list:
            raw_text = self.get_random_text("timer_list_empty_text")
            clean_text, _ = extract_emotion(raw_text)
            await interaction.response.send_message(clean_text)

            res_voice = self.get_random_text("timer_list_empty_voice")
            self.speak(interaction.guild, res_voice)
            return

        now = datetime.now()
        text = "⏳ **タイマー一覧**\n"
        for i, timer in enumerate(self.timer_list):
            remaining = timer["end_time"] - now
            if remaining.total_seconds() < 0:
                rem_m, rem_s = 0, 0
            else:
                rem_m = int(remaining.total_seconds() // 60)
                rem_s = int(remaining.total_seconds() % 60)
            text += (
                f"`{i + 1}`. 残り**{rem_m}分{rem_s}秒** (元: {timer['minutes']}分)\n"
            )

        res_voice = self.get_random_text("timer_list_voice")
        await interaction.response.send_message(text)
        self.speak(interaction.guild, res_voice)

    @timer_group.command(
        name="delete", description="タイマーをキャンセルします（候補から選択可）"
    )
    @app_commands.describe(index="キャンセルするタイマーを選択（または番号を入力）")
    @app_commands.autocomplete(index=timer_delete_autocomplete)
    async def timer_delete(self, interaction: discord.Interaction, index: int):
        if 1 <= index <= len(self.timer_list):
            removed = self.timer_list.pop(index - 1)
            self.save_timers()

            raw_text = self.get_random_text(
                "timer_delete_text", minutes=removed["minutes"]
            )
            clean_text, _ = extract_emotion(raw_text)

            res_voice = self.get_random_text("timer_delete_voice")

            await interaction.response.send_message(clean_text)
            self.speak(interaction.guild, res_voice)
        else:
            await interaction.response.send_message(
                "その番号のタイマーはありません", ephemeral=True
            )

    # --- ダイス機能 ---
    @app_commands.command(name="dice", description="ダイスを振ります (例: 2d6+1d4-5)")
    async def dice(self, interaction: discord.Interaction, notation: str = "1d100"):
        await interaction.response.defer()

        clean_notation = notation.replace(" ", "").replace("-", "+-")
        total = 0
        details = []

        try:
            parts = clean_notation.split("+")
            for part in parts:
                if not part:
                    continue

                if "d" in part.lower():
                    count_str, sides_str = part.lower().split("d")
                    count = int(count_str) if count_str else 1

                    sign = -1 if count < 0 else 1
                    count = abs(count)
                    sides = int(sides_str)

                    if count > 100 or sides <= 0 or sides > 10000:
                        await interaction.followup.send(
                            "ダイスの数は100個まで、面数は1〜10000までです",
                            ephemeral=True,
                        )
                        return

                    rolls = [random.randint(1, sides) for _ in range(count)]
                    part_sum = sum(rolls) * sign
                    total += part_sum

                    details.append(f"{part}({', '.join(map(str, rolls))})")

                else:
                    mod = int(part)
                    total += mod
                    details.append(str(mod))

            detail_str = " + ".join(details).replace("+-", "-")

            msg = f"🎲 **{notation}**\n結果: **{total}**\n内訳: `{detail_str}`"
            voice_text = self.get_random_text("dice_result_voice", total=total)

            if notation.strip().lower() == "1d100":
                if total <= 5:
                    msg += self.get_random_text("dice_critical_text")
                    voice_text = self.get_random_text(
                        "dice_critical_voice", total=total
                    )
                elif total >= 96:
                    msg += self.get_random_text("dice_fumble_text")
                    voice_text = self.get_random_text("dice_fumble_voice", total=total)

            clean_msg, _ = extract_emotion(msg)
            await interaction.followup.send(clean_msg)

            self.speak(interaction.guild, voice_text)

        except Exception as e:
            await interaction.followup.send(f"計算式のエラーです: {e}", ephemeral=True)

    # --- ヘルプコマンド ---
    @app_commands.command(name="help", description="コマンド一覧と使い方を表示します")
    async def help_cmd(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 TTS Discord Bot ヘルプ",
            description=(
                f"現在のエンジン: **{settings.TTS_ENGINE.upper()}**\n"
                f"現在のキャラクター人格: **{settings.CURRENT_PERSONA_DISPLAY_NAME}**\n"
                f"現在のTTSボイス: **{settings.STARTUP_CHARACTER or 'デフォルト'}**"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🔊 音声・読み上げ・専用ボイス",
            value=(
                "`/join` / `/bye` : ボイスチャンネルに接続・切断\n"
                "`/say <text> [emotion]` : 入力したテキストをVCで読み上げ（`[JOY]`等のタグも可）\n"
                "`/myvoice [voice] [gain_db] [reverb]` : **あなたの `/say` 専用ボイス・音量・エコーを設定**\n"
                "`/audio [gain_db] [reverb] [reverb_mix]` : Bot全体の基本音量・リバーブを調整\n"
                "`/stop` : 再生中の音声を停止＆キューを消去\n"
                "`/autojoin` : VC自動参加・ミュート等の状態通知・時報のON/OFF設定"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎭 キャラクター・AI対話",
            value=(
                "`/char [persona] [voice]` : **キャラクターの人格・セリフ・TTSボイスを一括または個別に変更**\n"
                "`/presets` : 利用可能なキャラクター人格とボイス一覧を表示\n"
                "`/reset` : AIとの会話履歴をリセット\n"
                "💡 **AIとの会話**: Botにメンション（`@Bot`）または返信すると対話し、VCで感情豊かに喋ります。"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ ユーティリティ（候補から選択して削除可）",
            value=(
                "`/dict add/list/delete` : 読み方辞書の登録・一覧・削除\n"
                "`/alarm add/list/delete` : 時刻指定アラームの管理\n"
                "`/timer set/list/delete` : 分単位タイマーの管理（再起動後も保持）\n"
                "`/dice [notation]` : ダイスロール（例: `1d100`, `2d6+3`）"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    # --- 定期タスク (アラーム ＆ 毎時00分の時報) ---
    @tasks.loop(seconds=10)
    async def check_alarm_task(self):
        now = datetime.now()
        now_str = now.strftime("%H:%M")

        if self.last_checked_minute == now_str:
            return

        self.last_checked_minute = now_str

        # 1. 毎時00分の時報チェック
        if now.minute == 0 and self.last_time_signal_hour != now.hour:
            self.last_time_signal_hour = now.hour
            audio_cog = self.bot.get_cog("AudioSystem")
            time_signal_on = True
            if audio_cog and hasattr(audio_cog, "config"):
                time_signal_on = audio_cog.config.get("time_signal_enabled", True)

            if time_signal_on:
                ts_voice = self.get_random_text("time_signal_voice", hour=now.hour)
                for guild in self.bot.guilds:
                    if guild.voice_client and guild.voice_client.is_connected():
                        self.speak(guild, ts_voice)

        # 2. アラームチェック
        remove_list = []
        for alarm in self.alarm_list:
            if alarm["time"] == now_str:
                target_channel_id = (
                    alarm.get("channel_id") or self.default_alarm_channel_id
                )

                channel = self.bot.get_channel(target_channel_id)
                if channel:
                    notify_text_raw = self.get_random_text(
                        "alarm_notify_text",
                        user_id=alarm["user_id"],
                        message=alarm["message"],
                    )
                    notify_text_clean, _ = extract_emotion(notify_text_raw)

                    notify_voice = self.get_random_text(
                        "alarm_notify_voice", message=alarm["message"]
                    )

                    try:
                        await channel.send(notify_text_clean)
                    except Exception as e:
                        logger.error(f"Failed to send alarm message: {e}")
                    self.speak(channel.guild, notify_voice)

                if not alarm["repeat"]:
                    remove_list.append(alarm)

        if remove_list:
            for r in remove_list:
                if r in self.alarm_list:
                    self.alarm_list.remove(r)
            self.save_alarms()

    @tasks.loop(seconds=1)
    async def check_timer_task(self):
        now = datetime.now()
        remove_list = []

        for timer in self.timer_list:
            if now >= timer["end_time"]:
                channel = self.bot.get_channel(timer["channel_id"])
                if channel:
                    notify_text_raw = self.get_random_text(
                        "timer_notify_text",
                        minutes=timer["minutes"],
                        user_id=timer["user_id"],
                    )
                    notify_text_clean, _ = extract_emotion(notify_text_raw)

                    notify_voice = self.get_random_text(
                        "timer_notify_voice", minutes=timer["minutes"]
                    )

                    try:
                        await channel.send(notify_text_clean)
                    except Exception as e:
                        logger.error(f"Failed to send timer message: {e}")
                    self.speak(channel.guild, notify_voice)
                remove_list.append(timer)

        if remove_list:
            for r in remove_list:
                if r in self.timer_list:
                    self.timer_list.remove(r)
            self.save_timers()

    @check_alarm_task.before_loop
    async def before_alarm_task(self):
        await self.bot.wait_until_ready()

    @check_timer_task.before_loop
    async def before_timer_task(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Utilities(bot))
