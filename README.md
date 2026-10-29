# TTS Discord Bot (A.I.VOICE & VOICEVOX + LLM)

A.I.VOICE Editor および VOICEVOX と連携する Discord 読み上げ＆AI対話 Bot です。
音声処理のバックエンドに Rust (`rust_core`) を使用し、GIL解放・完全オンメモリパイプライン・先行TTS合成（プリフェッチ）によって低遅延かつ自然な対話を実現しています。

---

## 主な機能

### 1. TTS エンジン連携 & 緻密な感情表現
* **A.I.VOICE**: Editor API を介した音声合成。感情タグ（`[JOY]`, `[SAD]`, `[ANGRY]`, `[SURPRISE]`, `[NORMAL]`）に応じたプリセット自動切り替え＋未作成プリセットのフォールバック調整に対応。
* **VOICEVOX**: HTTP Session プーリング＆完全オンメモリでの音声合成。スタイル切り替えによる露骨なキャラクター性の変化を避け、**全体のピッチ・抑揚・話速・間（前後無音長）に加え、アクセント句内の文末モーラピッチ・母音長・先頭子音長を緻密に制御**することで、キャラクターの声を維持したまま豊かな感情表現を行います。
* **ハイブリッド感情解析**:
  * 文頭タグ形式（`[JOY] こんにちは！`）・文末タグ形式（`こんにちは！[JOY]`）・日本語/小文字タグ揺れのすべてに対応。
  * LLMが感情タグを付け忘れた場合や `/say` でタグを省略した場合でも、文末記号（「！」「…」「！？」）や感情語彙から自動で感情を推定します。

### 2. Rust (`rust_core`) による低遅延オーディオパイプライン
* 無音カット（トリミング）、末尾フェードアウト、ゲイン調整、リバーブ、48kHz ステレオ PCM リサンプリング（線形補間）、Discord フレームアライメントをすべてオンメモリで一括処理します。
* 計算中は Python の GIL（グローバルインタプリタロック）を解放するため、Discord の非同期イベントループやハートビートを一切阻害しません。

### 3. ストリーミング LLM 対話 & 2段キュー（先行TTS合成）
* LLM からの応答をストリーミング（`stream=True`）で受信し、1文が区切られた瞬間に裏で TTS 合成を開始します。
* **合成キュー**と**再生キュー**を分離した2段パイプラインにより、1文目を再生している裏で2文目以降の音声を先行生成（プリフェッチ）するため、文と文の間の不自然な空白時間がありません。

### 4. キャラクター＆人格の一括管理
* `characters/` ディレクトリ内でキャラクターごとの人格プロンプト（`*.txt`）とシステムセリフ・推奨ボイス設定（`*.json`）を管理します。
* Discord 上の `/char` コマンド1つで、**LLMの人格プロンプト・システムセリフ・TTSボイスプリセットを一括（または声だけ個別に）切り替え**可能です。

### 5. 実運用向けの安定設計
* **スマート自動起動**: 前回起動時の設定（エンジン・ボイス・人格）を `config.json` に記憶し、次回起動時は **5秒間キー入力がなければ自動で前回の設定を使って起動**します（`--headless` や `--wizard` 等の CLI 引数にも対応）。
* **聞き専サポート (`/say` & `/myvoice`)**:
  * 他の Bot コマンドや通常チャットの邪魔にならないよう、明示的に `/say` スラッシュコマンドで入力したメッセージのみをサニタイズ＆最長一致辞書置換して読み上げます。
  * さらに `/myvoice` コマンドで **ユーザーごとに自分の専用ボイス・音量（Gain）・リバーブ（エコー）を登録可能**（A.I.VOICE / VOICEVOX 両対応）。誰が `/say` で話しているかを声で聞き分けられます。
* **VC 自動参加・状態通知・時報制御 (`/autojoin`)**: ユーザーが VC に入った際の自動参加（Auto Join）の ON/OFF、対象チャンネル限定、ミュート等の状態通知ボイスの ON/OFF、および毎時00分のキャラクター時報の ON/OFF が可能です。
* **永続化とロギング**: アラーム（`alarms.json`）・読み方辞書（`dictionary.json`）に加え、稼働中タイマー（`timers.json`）も永続化。削除時はプルダウン候補（Autocomplete）から直接選択して削除できます。ログは `logs/bot.log` にローテーション保存されます。

---

## 動作環境

* **OS**: Windows 10 / 11 (64bit)
* **Python**: 3.10 以上 (推奨: 3.11)
* **Rust**: 1.70 以上 (ビルドに必要)
* **TTS**: A.I.VOICE Editor (Ver.1 / 2) または VOICEVOX

---

## セットアップ

### 1. Rust の導入
[Rust 公式サイト](https://www.rust-lang.org/tools/install) から `rustup-init.exe` を取得し、デフォルト設定でインストールしてください。

### 2. 依存関係の構築
`install.bat` を実行してください。以下の処理が自動で行われます。
1. パッケージマネージャ `uv` のインストール
2. Python 3.11 仮想環境の作成
3. 依存ライブラリのインストール
4. Rust モジュール (`rust_core`) のリリースビルド

### 3. 設定ファイルの編集
`.env.example` を `.env` にリネーム（または新規作成）し、トークンおよびパスを設定してください。

```properties
DISCORD_TOKEN=your_token_here
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
LLM_MODEL=local-model
AIVOICE_DLL_PATH=C:\Program Files\AI\AIVoice\AIVoiceEditor\AI.Talk.Editor.Api.dll
AIVOICE_APP_PATH=C:\Program Files\AI\AIVoice\AIVoiceEditor\AIVoiceEditor.exe
VOICEVOX_URL=http://127.0.0.1:50021
VOICEVOX_APP_PATH=C:\Users\Username\AppData\Local\Programs\VOICEVOX\VOICEVOX.exe
```

---

## 使用方法

### 1. 起動
`start.bat` を実行してください。
* **初回起動時**（またはカウントダウン中にキーを押した場合）：ウィザード形式でエンジン・ボイス・人格設定を選択します。
* **2回目以降**：5秒間キーを押さなければ、前回保存された設定で自動的に起動します。
* **コマンドライン引数での起動**:
  ```bash
  python main.py --headless --engine voicevox --persona zundamon --voice "ずんだもん(ノーマル)"
  ```

### 2. 主要スラッシュコマンド

| コマンド | 内容 |
|---|---|
| `/join` | ボイスチャンネルに接続 |
| `/bye` | ボイスチャンネルから切断 |
| `/say <text> [emotion]` | 入力したテキストを VC で読み上げ（聞き専サポート・感情指定・個人ボイス対応） |
| `/myvoice [voice] [gain_db] [reverb]` | **自分が `/say` で話すときの専用ボイス・音量・リバーブを設定** |
| `/audio [gain_db] [reverb] [reverb_mix]` | Bot 全体の基本音量（ゲイン）やリバーブをリアルタイム調整 |
| `/char [persona] [voice]` | **キャラクターの人格設定・システムセリフ・TTSボイスを一括または個別に切り替え** |
| `/presets` | 利用可能なキャラクター人格と TTS ボイスの一覧を表示 |
| `/autojoin [enabled] [channel] [state_notify] [time_signal]` | VC 自動参加・ミュート等の状態通知・毎時00分の時報の ON/OFF 設定 |
| `/stop` | 再生停止・合成＆再生キューの消去 |
| `/reset` | LLM との会話履歴をリセット |
| `/dict add / list / delete` | 読み方辞書の登録・一覧・削除（候補から直接選択可） |
| `/alarm add / list / delete` | 時刻指定アラームの管理（候補から直接選択可） |
| `/timer set / list / delete` | 分単位タイマーの管理（候補から直接選択可・再起動後も保持） |
| `/dice [notation]` | ダイスロール（例: `1d100`, `2d6+1d4-2`） |
| `/help` | コマンド一覧と現在の設定を表示 |

---

## フォーマット・静的解析
```bash
ruff format
ruff check --fix
```
