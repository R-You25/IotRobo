# ESP32 + PCA9685 + SG90 + Bedrock Tool Use

ESP32 から PCA9685 経由で SG90 サーボ（右手=チャンネル4, 左手=チャンネル3）を操作し、
Amazon Bedrock（Claude / Nova Sonic）の Tool Use で自然言語からサーボを動かすサンプルです。

- `bedrock_servo.py`: テキストチャットで動かす版（Claude の Tool Use）
- `bedrock_servo_voice.py`: リアルタイム音声対話で動かす版（Amazon Nova Sonic）

## 配線

| PCA9685 | ESP32 |
|---|---|
| SDA | GPIO21 |
| SCL | GPIO22 |
| VCC | 3.3V |
| GND | GND |
| V+ | ESP32 VIN（5V） |
| OE | GND（ジャンパで直結。プルダウン抵抗が無い安価なボードは必須） |

| SG90 | PCA9685 |
|---|---|
| 信号線（橙） | チャンネル4（右手） / チャンネル3（左手） の PWM |
| 赤（+5V） | 同チャンネルの V+ |
| 茶（GND） | 同チャンネルの GND |

## WSL2 で USB を使う場合（毎回必要）

WSL2 は USB を直接認識できないため、ESP32 を挿すたびに以下が必要（Linux / macOS 直挿しの場合は不要）。

**1. Windows PowerShell（管理者）で共有・接続**

```powershell
usbipd list
# BUSID を確認（例: 2-2）

usbipd bind --busid 2-2       # 初回のみ
usbipd attach --wsl --busid 2-2
```

**2. WSL ターミナルで認識確認**

```bash
ls /dev/ttyUSB0
# 環境によっては /dev/ttyACM0 になる場合もある
```

`/dev/ttyUSB0`（または `/dev/ttyACM0`）が出れば OK。出ない場合は PowerShell で `usbipd attach` を再実行。

> ESP32 を抜き差ししたり PC を再起動した場合は、この手順を毎回やり直す。

## ESP32 へのビルド・書き込み

[PlatformIO](https://platformio.org/) を使用します。VS Code の PlatformIO 拡張から：

1. **Build**（チェックマークアイコン）
2. **Upload**（矢印アイコン）

CLI から行う場合：

```bash
source ~/.platformio/penv/bin/activate  # または pio コマンドがある場所
pio run --target upload
```

## シリアルモニターで動作確認

```bash
~/.platformio/penv/bin/platformio device monitor --baud 115200
```

`READY` が表示されれば ESP32 側の準備完了（Ctrl+C で終了）。

## Bedrock 連携スクリプトの実行

**1. Python 仮想環境の作成（初回のみ）**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`bedrock_servo.py` のみを使う場合は `boto3` と `pyserial` だけで動きます。
音声版（`bedrock_servo_voice.py`）を使う場合はマイク・スピーカー入出力に `PyAudio` が必要です
（Linux では事前に `sudo apt install portaudio19-dev` 等が必要になることがあります）。
WSL2 + WSLg の場合は、加えて以下も必要です。

```bash
sudo apt-get install -y pulseaudio-utils
```

**2. AWS 認証確認**

```bash
aws configure list
```

Bedrock（Claude Sonnet 5 / `us-west-2`）と Nova Sonic（`us-east-1`）を呼び出せる権限が必要です。

**3. シリアルポートの調整**

[arm_control.py](arm_control.py) の `SERIAL_PORT = "/dev/ttyUSB0"` を、実際に接続されている
デバイスパスに合わせて変更してください（macOS では `/dev/tty.usbserial-*` など）。

**4. 実行**

テキスト版:

```bash
source .venv/bin/activate
python bedrock_servo.py
```

「右腕を45度にして」のように自然言語で入力すると、Bedrock（Claude）が
`move_servo` ツールを呼び出し、シリアル経由で ESP32 に角度指令が送られる。
終了するには `exit` / `quit` / `q` を入力。

音声版:

```bash
source .venv/bin/activate
python bedrock_servo_voice.py
```

マイクに向かって話しかけると、Amazon Nova Sonic が音声認識・応答生成を行い、
必要に応じてツールを呼び出してサーボを動かします（Ctrl+C で終了）。

## ファイル構成

| ファイル | 役割 |
|---|---|
| [src/main.cpp](src/main.cpp) | ESP32 側。シリアルから JSON（`{"channel":3,"angle":90}`）を受け取りサーボを動かす |
| [arm_control.py](arm_control.py) | PC 側共通処理。シリアル通信・ツール定義（move_servo, wave_hand, dance など）・システムプロンプト |
| [bedrock_servo.py](bedrock_servo.py) | PC 側。テキスト入力を Bedrock（Claude）の Tool Use で解釈し、シリアル経由で ESP32 に指令を送る |
| [bedrock_servo_voice.py](bedrock_servo_voice.py) | PC 側。Amazon Nova Sonic とのリアルタイム音声対話でサーボを動かす |
| [platformio.ini](platformio.ini) | PlatformIO 設定（ライブラリ: Adafruit PWM Servo Driver, ArduinoJson） |
| [requirements.txt](requirements.txt) | Python 依存パッケージ |

## トラブルシューティング

- **`Error: Please specify upload_port`** → USB が認識されていない。WSL2 の場合は上記「WSL2 で USB を使う場合」を実施。
- **`Could not open /dev/ttyUSB0, ... Permission denied`** → `sudo usermod -a -G dialout $USER` 後、再ログイン（WSL2 は `wsl --shutdown`）。
- **`Could not exclusively lock port`** → シリアルモニターや他プロセスがポートを掴んでいる。VS Code のモニターを閉じるか `sudo fuser -k /dev/ttyUSB0`。
- **SG90 が動かない** → PCA9685 の OE ピンが GND に落ちているか確認（フローティングだと出力無効）。次にチャンネル番号の刺し間違いを確認。
