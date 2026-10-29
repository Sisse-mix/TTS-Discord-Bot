import argparse
import asyncio
import logging
import os
import pathlib
import random
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# 設定ファイルの読み込み
load_dotenv()
import settings  # noqa: E402
from cogs.consts import load_json, save_json  # noqa: E402
from cogs.tts_engines import get_tts_provider  # noqa: E402

# ログディレクトリの作成とRotatingFileHandler設定
LOG_DIR = pathlib.Path("logs")
LOG_DIR.mkdir(exist_ok=True)

log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
file_handler = RotatingFileHandler(
    LOG_DIR / "bot.log",
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
    encoding="utf-8",
)
file_handler.setFormatter(log_formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler],
)
logger = logging.getLogger(__name__)

# 環境変数からトークンを取得
TOKEN = os.getenv("DISCORD_TOKEN")


def wait_enter_with_timeout(timeout_sec: int = 5) -> bool:
    """
    Windowsコンソール等で timeout_sec 秒間キー入力を待つ。
    何かキーが押されたら True（手動ウィザードへ）、タイムアウトしたら False（自動起動へ）を返す。
    """
    if not sys.stdin or not sys.stdin.isatty():
        return False

    if os.name == "nt":
        import msvcrt

        # 既存の入力バッファをクリア
        while msvcrt.kbhit():
            msvcrt.getwch()

        start = time.time()
        while time.time() - start < timeout_sec:
            if msvcrt.kbhit():
                msvcrt.getwch()
                print()
                return True
            remaining = int(timeout_sec - (time.time() - start)) + 1
            print(
                f"\r-> 変更する場合は何かキーを押してください（あと {remaining} 秒で自動起動）... ",
                end="",
                flush=True,
            )
            time.sleep(0.1)
        print(
            "\r-> 前回の設定を使用して自動起動します。                                      "
        )
        return False
    else:
        import select

        print(
            f"-> 変更する場合は Enter キーを押してください（{timeout_sec}秒で自動起動）..."
        )
        rlist, _, _ = select.select([sys.stdin], [], [], timeout_sec)
        if rlist:
            sys.stdin.readline()
            return True
        return False


def input_index(prompt, options, zero_label=None):
    """
    コンソールに一覧を表示し、ユーザーにインデックス番号を選択させる関数。
    """
    print(f"\n{prompt}")

    start_num = 0 if zero_label else 1

    if zero_label:
        print(f"  [0] {zero_label}")

    for i, opt in enumerate(options):
        print(f"  [{i + 1}] {opt}")

    while True:
        val = input(f"番号を入力してください ({start_num}-{len(options)}) > ").strip()
        if not val.isdigit():
            continue
        idx = int(val)

        if idx == 0 and zero_label:
            return None

        if 1 <= idx <= len(options):
            return options[idx - 1]


def select_character_interactive(presets):
    """
    プリセットリストを解析し、キャラクターごとにグループ化して選択させる関数
    """
    groups = {}
    for p in presets:
        if "(" in p:
            name = p.split("(")[0]
        elif "（" in p:
            name = p.split("（")[0]
        else:
            name = p

        if name not in groups:
            groups[name] = []
        groups[name].append(p)

    group_names = list(groups.keys())

    print(f"\n使用するキャラクターを選んでください (全{len(group_names)}名):")
    for i, name in enumerate(group_names):
        count = len(groups[name])
        suffix = f" ({count}種)" if count > 1 else ""
        print(f"  [{i + 1}] {name}{suffix}")

    while True:
        val = input(f"キャラクター番号を入力 (1-{len(group_names)}) > ").strip()
        if not val.isdigit():
            continue
        idx = int(val)
        if 1 <= idx <= len(group_names):
            selected_name = group_names[idx - 1]
            break

    variants = groups[selected_name]
    if len(variants) == 1:
        return variants[0]

    print(f"\n{selected_name} のスタイルを選択:")
    for i, v in enumerate(variants):
        display_style = v
        if selected_name in v:
            display_style = v.replace(selected_name, "").strip("()（）")
            if not display_style:
                display_style = "標準"

        print(f"  [{i + 1}] {display_style}")

    while True:
        val = input(f"スタイル番号を入力 (1-{len(variants)}) > ").strip()
        if not val.isdigit():
            continue
        idx = int(val)
        if 1 <= idx <= len(variants):
            return variants[idx - 1]


def try_launch_app(engine_name):
    """指定されたエンジンのアプリケーションを自動起動する"""
    app_path = None
    if engine_name == "aivoice":
        app_path = getattr(settings, "AIVOICE_APP_PATH", None)
    elif engine_name == "voicevox":
        app_path = getattr(settings, "VOICEVOX_APP_PATH", None)

    if app_path and os.path.exists(app_path):
        try:
            print(f"アプリを起動しています (バックグラウンド): {app_path}")
            startup_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                info = subprocess.STARTUPINFO()
                info.dwFlags |= 1
                info.wShowWindow = 7
                startup_kwargs["startupinfo"] = info

            subprocess.Popen(app_path, **startup_kwargs)
        except Exception as e:
            logger.error(f"アプリの自動起動に失敗しました: {e}")
            print("! 自動起動に失敗しました。手動でアプリを起動してください。")


def wait_for_launch(provider, max_retries=60):
    print("エンジンの起動を確認中", end="", flush=True)
    for _ in range(max_retries):
        try:
            provider.initialize()
            presets = provider.get_presets()
            if presets:
                print(" 完了！")
                return presets
        except Exception:
            pass
        time.sleep(1)
        print(".", end="", flush=True)

    print("\nタイムアウト: エンジンの起動が確認できませんでした。")
    return []


class TTSBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        prefix = getattr(settings, "COMMAND_PREFIX", "!")
        super().__init__(command_prefix=prefix, intents=intents, help_command=None)

    async def setup_hook(self):
        initial_extensions = ["cogs.audio", "cogs.chat", "cogs.utils"]
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
                logger.info(f"Loaded extension: {extension} ......成功")
            except Exception as e:
                logger.error(f"Failed to load extension {extension}: {e}")

        try:
            await self.tree.sync()
            logger.info("Synced commands (Global) ......成功")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Engine: {settings.TTS_ENGINE}")
        logger.info(f"Character Voice: {settings.STARTUP_CHARACTER}")
        logger.info(f"Persona: {settings.CURRENT_PERSONA_DISPLAY_NAME}")
        logger.info("System Ready.")
        if not self.change_status_loop.is_running():
            self.change_status_loop.start()

    @tasks.loop(minutes=10)
    async def change_status_loop(self):
        status_list = getattr(settings, "STATUS_MESSAGES", ["Running..."])
        await self.change_presence(
            activity=discord.Game(name=random.choice(status_list))
        )

    @change_status_loop.before_loop
    async def before_status_loop(self):
        await self.wait_until_ready()


def parse_args():
    parser = argparse.ArgumentParser(description="TTS Discord Bot")
    parser.add_argument(
        "--engine",
        choices=["aivoice", "voicevox"],
        help="使用するTTSエンジンを指定します",
    )
    parser.add_argument(
        "--voice",
        type=str,
        help="起動時に使用するTTSボイスプリセット名を指定します",
    )
    parser.add_argument(
        "--persona",
        type=str,
        help="起動時に使用するキャラクター人格ID (akari, yukari, zundamon, none) を指定します",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="対話ウィザードをスキップし、保存設定または引数で即座に起動します",
    )
    parser.add_argument(
        "--wizard",
        action="store_true",
        help="強制的に対話型起動ウィザードを表示します",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    config = load_json("config.json", {})

    saved_engine = args.engine or config.get("last_engine")
    saved_voice = args.voice or config.get("last_voice")
    saved_persona = (
        args.persona if args.persona is not None else config.get("last_persona")
    )

    use_wizard = args.wizard
    if not use_wizard and not args.headless:
        if saved_engine and saved_voice:
            print("=== 自動起動チェック ===")
            print(
                f"前回の設定: エンジン={saved_engine} / ボイス={saved_voice} / 人格={saved_persona or 'オフ'}"
            )
            use_wizard = wait_enter_with_timeout(5)
        else:
            use_wizard = True

    if use_wizard:
        print("=== 起動設定ウィザード ===")

        # 1. TTSエンジンの選択
        engines = ["A.I.VOICE", "VOICEVOX"]
        selected_engine = input_index("使用するTTSエンジンを選択してください:", engines)
        settings.TTS_ENGINE = "voicevox" if selected_engine == "VOICEVOX" else "aivoice"
        print(f"-> エンジン: {settings.TTS_ENGINE} を選択しました。")

        try_launch_app(settings.TTS_ENGINE)

        # 2. キャラクター選択 (TTS)
        temp_provider = get_tts_provider(settings.TTS_ENGINE)
        presets = wait_for_launch(temp_provider)
        temp_provider.terminate()

        if presets:
            char_choice = select_character_interactive(presets)
            if char_choice:
                settings.STARTUP_CHARACTER = char_choice
                print(f"-> キャラクター: {char_choice} を選択しました。")
        else:
            print("! プリセットが見つかりませんでした。")

        # 3. 人格設定 (charactersフォルダ) 選択
        personas = settings.get_available_personas()
        if personas:
            persona_keys = list(personas.keys())
            persona_labels = [
                f"{personas[k]['display_name']} ({k})" for k in persona_keys
            ]
            selected_label = input_index(
                "使用する人格設定(characters)を選択してください:",
                persona_labels,
                zero_label="LLMを使用しない (No Use)",
            )
            if selected_label:
                idx = persona_labels.index(selected_label)
                chosen_persona_id = persona_keys[idx]
                settings.load_character_config(personas[chosen_persona_id]["dir"])
                print(
                    f"-> 人格設定: {personas[chosen_persona_id]['display_name']} を読み込みました。"
                )
            else:
                chosen_persona_id = None
                settings.load_character_config(None)
        else:
            print(
                "\n※ charactersフォルダにキャラクター設定が見つかりません。LLM機能はオフで起動します。"
            )
            chosen_persona_id = None
            settings.load_character_config(None)

        # 選択内容を config.json に保存
        config["last_engine"] = settings.TTS_ENGINE
        config["last_voice"] = settings.STARTUP_CHARACTER
        config["last_persona"] = chosen_persona_id
        save_json("config.json", config)

    else:
        # 自動起動（またはCLI引数起動）
        settings.TTS_ENGINE = saved_engine or "aivoice"
        settings.STARTUP_CHARACTER = saved_voice
        try_launch_app(settings.TTS_ENGINE)

        temp_provider = get_tts_provider(settings.TTS_ENGINE)
        wait_for_launch(temp_provider, max_retries=30)
        temp_provider.terminate()

        if saved_persona and str(saved_persona).lower() != "none":
            settings.load_character_config(saved_persona)
        else:
            settings.load_character_config(None)

    if not TOKEN:
        logger.critical(
            "DISCORD_TOKEN が設定されていません。.env ファイルを確認してください。"
        )
        return

    print("\nBotを起動しています...")
    async with TTSBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        if os.name == "nt":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
