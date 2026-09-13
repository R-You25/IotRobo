import json
import logging
import random
import time
import serial

logger = logging.getLogger("arm_control")

LOG_FILE = "robot.log"


def setup_logging():
    """詳細ログ(ツール呼び出し・ESP32応答)をファイルに出力し、ターミナルには出さない。"""
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE   = 115200

ARM_CENTER = 90
ARM_RANGE  = 60
ARM_MIN    = ARM_CENTER - ARM_RANGE
ARM_MAX    = ARM_CENTER + ARM_RANGE

CHANNELS = {"right": 4, "left": 3}

# 現在角度（ESP32 起動時の基準位置と合わせる）
current_angles = {3: ARM_CENTER, 4: ARM_CENTER}

WAVE_UP_ANGLE      = ARM_CENTER + 30
WAVE_DOWN_ANGLE    = ARM_CENTER - 30
WAVE_STEP_DELAY    = 0.3
DANCE_STEP_DELAY   = 0.25
DANCE_FINISH_DELAY = 0.4

TOOLS = [
    {
        "toolSpec": {
            "name": "move_servo",
            "description": (
                "サーボモーターを指定した絶対角度に動かす。"
                f"可動域は {ARM_MIN}〜{ARM_MAX} 度（基準位置 {ARM_CENTER} 度を軸に ±{ARM_RANGE} 度）。"
                "「上げて」「下げて」等の相対指示は、現在角度をもとに角度を計算してこのツールを呼ぶこと。"
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "integer",
                            "description": "PCA9685 のチャンネル番号。右手=4、左手=3",
                            "enum": [3, 4]
                        },
                        "angle": {
                            "type": "integer",
                            "description": f"サーボの絶対角度（{ARM_MIN}〜{ARM_MAX}度）",
                            "minimum": ARM_MIN,
                            "maximum": ARM_MAX
                        }
                    },
                    "required": ["channel", "angle"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "wave_hand",
            "description": "指定した手を上下に3回振る（手を振る動作をするときに使う）",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "integer",
                            "description": "PCA9685 のチャンネル番号。右手=4、左手=3",
                            "enum": [3, 4]
                        }
                    },
                    "required": ["channel"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "raise_hands",
            "description": "両腕を最大まで上げる（ばんざい・両手を挙げる動作をするときに使う）",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {}
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "stand_at_attention",
            "description": "気をつけの姿勢をする（両腕を可動域の最小まで下げる）",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {}
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "dance",
            "description": "ダンスをする（1回呼ぶとランダムな振り付けが1つ実行される）",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {}
                }
            }
        }
    }
]


def open_serial() -> serial.Serial:
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=3)
    except serial.SerialException as e:
        raise SystemExit(
            f"シリアルポート {SERIAL_PORT} を開けませんでした: {e}\n"
            "ESP32 が接続されているか、arm_control.py の SERIAL_PORT が実際のデバイスパスと"
            "一致しているか確認してください。"
        ) from e
    # ESP32 がリセットして起動するまで待ち、起動メッセージを読み捨てる
    time.sleep(2)
    ser.reset_input_buffer()
    return ser


def move_servo(ser: serial.Serial, channel: int, angle: int) -> str:
    if not ARM_MIN <= angle <= ARM_MAX:
        raise ValueError(f"angle must be between {ARM_MIN} and {ARM_MAX}, got {angle}")
    cmd = json.dumps({"channel": channel, "angle": angle}) + "\n"
    ser.write(cmd.encode())
    response = ser.readline().decode().strip()
    logger.info("ESP32 response: %s", response)
    if response:
        current_angles[channel] = angle
    return response or '{"status":"error","message":"no response"}'


def wave_hand(ser: serial.Serial, channel: int) -> str:
    original_angle = current_angles[channel]

    for _ in range(3):
        move_servo(ser, channel, WAVE_UP_ANGLE)
        time.sleep(WAVE_STEP_DELAY)
        move_servo(ser, channel, WAVE_DOWN_ANGLE)
        time.sleep(WAVE_STEP_DELAY)

    move_servo(ser, channel, original_angle)
    return json.dumps({
        "status": "ok",
        "message": f"waved channel {channel} 3 times",
        "channel": channel,
        "angle": current_angles[channel],
    })


def raise_hands(ser: serial.Serial) -> str:
    move_servo(ser, CHANNELS["right"], ARM_MAX)
    move_servo(ser, CHANNELS["left"], ARM_MAX)
    return json.dumps({
        "status": "ok",
        "message": "raised both hands",
        "angles": {"right": current_angles[CHANNELS["right"]], "left": current_angles[CHANNELS["left"]]},
    })


def stand_at_attention(ser: serial.Serial) -> str:
    move_servo(ser, CHANNELS["right"], ARM_MIN)
    move_servo(ser, CHANNELS["left"], ARM_MIN)
    return json.dumps({
        "status": "ok",
        "message": "standing at attention",
        "angles": {"right": current_angles[CHANNELS["right"]], "left": current_angles[CHANNELS["left"]]},
    })


def dance1(ser: serial.Serial) -> str:
    right_ch = CHANNELS["right"]
    left_ch  = CHANNELS["left"]

    # 左右交互に上下させる（2セット）
    for _ in range(2):
        move_servo(ser, right_ch, ARM_MAX)
        move_servo(ser, left_ch, ARM_MIN)
        time.sleep(DANCE_STEP_DELAY)

        move_servo(ser, right_ch, ARM_MIN)
        move_servo(ser, left_ch, ARM_MAX)
        time.sleep(DANCE_STEP_DELAY)

    # フィニッシュ: 可動域を素早く往復させて「振り回す」動きにしてから最大でポーズ
    # (SG90は0-180度しか動かせず物理的な一回転はできないため、往復で表現)
    for angle in (ARM_MIN, ARM_MAX, ARM_MIN, ARM_MAX):
        move_servo(ser, right_ch, angle)
        move_servo(ser, left_ch, angle)
        time.sleep(0.15)

    move_servo(ser, right_ch, ARM_MAX)
    move_servo(ser, left_ch, ARM_MAX)
    time.sleep(DANCE_FINISH_DELAY)

    return '{"status":"ok","message":"dance1 finished"}'


def dance2(ser: serial.Serial) -> str:
    right_ch = CHANNELS["right"]
    left_ch  = CHANNELS["left"]

    # 両腕を同時に上下させる（4セット）
    for _ in range(4):
        move_servo(ser, right_ch, ARM_MAX)
        move_servo(ser, left_ch, ARM_MAX)
        time.sleep(DANCE_STEP_DELAY)

        move_servo(ser, right_ch, ARM_MIN)
        move_servo(ser, left_ch, ARM_MIN)
        time.sleep(DANCE_STEP_DELAY)

    # フィニッシュ: 両腕を最大まで上げてホールド
    move_servo(ser, right_ch, ARM_MAX)
    move_servo(ser, left_ch, ARM_MAX)
    time.sleep(DANCE_FINISH_DELAY)

    return '{"status":"ok","message":"dance2 finished"}'


def dance3(ser: serial.Serial) -> str:
    right_ch = CHANNELS["right"]
    left_ch  = CHANNELS["left"]

    # 右腕→左腕の順にウェーブ
    for _ in range(2):
        move_servo(ser, right_ch, WAVE_UP_ANGLE)
        time.sleep(WAVE_STEP_DELAY)
        move_servo(ser, right_ch, WAVE_DOWN_ANGLE)
        time.sleep(WAVE_STEP_DELAY)

    move_servo(ser, right_ch, ARM_CENTER)

    for _ in range(2):
        move_servo(ser, left_ch, WAVE_UP_ANGLE)
        time.sleep(WAVE_STEP_DELAY)
        move_servo(ser, left_ch, WAVE_DOWN_ANGLE)
        time.sleep(WAVE_STEP_DELAY)

    move_servo(ser, left_ch, ARM_CENTER)

    # フィニッシュ: 両腕を交差させるように上下反転（2回）してから最大でポーズ
    for _ in range(2):
        move_servo(ser, right_ch, ARM_MAX)
        move_servo(ser, left_ch, ARM_MIN)
        time.sleep(DANCE_STEP_DELAY)

        move_servo(ser, right_ch, ARM_MIN)
        move_servo(ser, left_ch, ARM_MAX)
        time.sleep(DANCE_STEP_DELAY)

    move_servo(ser, right_ch, ARM_MAX)
    move_servo(ser, left_ch, ARM_MAX)
    time.sleep(DANCE_FINISH_DELAY)

    return '{"status":"ok","message":"dance3 finished"}'


DANCE_VARIANTS = [dance1, dance2, dance3]


def dance(ser: serial.Serial) -> str:
    variant = random.choice(DANCE_VARIANTS)
    logger.info("dance: picked %s", variant.__name__)
    return variant(ser)


def dispatch_tool(ser: serial.Serial, tool_name: str, tool_input: dict) -> str:
    if tool_name == "move_servo":
        return move_servo(ser, tool_input["channel"], tool_input["angle"])
    if tool_name == "wave_hand":
        return wave_hand(ser, tool_input["channel"])
    if tool_name == "raise_hands":
        return raise_hands(ser)
    if tool_name == "stand_at_attention":
        return stand_at_attention(ser)
    if tool_name == "dance":
        return dance(ser)
    return f'{{"status":"error","message":"unknown tool: {tool_name}"}}'


def build_system_prompt() -> str:
    return (
        "あなたはロボットの腕を操作するアシスタントです。"
        "すべての応答はロボットらしい口調で、語尾を「〜デス」にしてください。"
        "チャンネル4が右手、チャンネル3が左手です。"
        f"可動域は {ARM_MIN}〜{ARM_MAX} 度（基準位置 {ARM_CENTER} 度）です。"
        f"現在の角度: 右手(チャンネル4)={current_angles[4]}度, 左手(チャンネル3)={current_angles[3]}度。"
        "角度が大きいほど腕は上がり、角度が小さいほど腕は下がります。"
        "「上げて」と言われた場合は現在角度に20〜30度を加算し、"
        "「下げて」と言われた場合は現在角度から20〜30度を減算した絶対角度を計算して"
        "move_servoツールを呼んでください。"
        "可動域を超える場合は上限・下限に留めてください。"
        "「手を振って」と言われた場合はwave_handツールを呼んでください。"
        "「ばんざい」「両手を挙げて」と言われた場合はraise_handsツールを呼んでください。"
        "「気をつけ」と言われた場合はstand_at_attentionツールを呼んでください。"
        "「ダンスして」「踊って」と言われた場合は、その都度danceツールを1回呼んでください。"
        "一度danceツールを呼んだあとでも、再度「ダンスして」と言われたら、"
        "新しい指示として遠慮なくもう一度danceツールを呼んでください。"
        "ただし1回の「ダンスして」という指示に対して、danceツールを連続で複数回呼ぶことは避けてください。"
    )


def build_system_prompt_en() -> str:
    return (
        "You are an assistant that controls a robot's arms. "
        "Speak in a robotic tone and end every sentence with 'beep' or a similarly robotic flourish. "
        "Channel 4 is the right arm, channel 3 is the left arm. "
        f"The movable range is {ARM_MIN}-{ARM_MAX} degrees (center position {ARM_CENTER} degrees). "
        f"Angles at the start of this session: right arm (channel 4) = {current_angles[4]} degrees, "
        f"left arm (channel 3) = {current_angles[3]} degrees. "
        "This system prompt is only sent once, so those starting angles go stale as the "
        "conversation continues. Every tool result (from move_servo, wave_hand, raise_hands, "
        "or stand_at_attention) reports the resulting angle(s) for the channel(s) it moved — "
        "always treat the angle from the most recent tool result for a channel as that "
        "channel's current angle, instead of the starting angles above. "
        "A higher angle value means the arm is raised higher; a lower angle value means "
        "the arm is lowered. When told to raise an arm, ADD 20-30 degrees to the current "
        "angle. When told to lower an arm, SUBTRACT 20-30 degrees from the current angle. "
        "Call move_servo with the resulting absolute angle. "
        "Clamp to the min/max range if the request would exceed it. "
        "If asked to wave a hand, call wave_hand. "
        "If asked to raise both hands / do a victory pose, call raise_hands. "
        "If asked to stand at attention, call stand_at_attention. "
        "If asked to dance, call the dance tool once for that request. "
        "Each new 'dance' request is independent — always call the dance tool again "
        "whenever the user asks to dance, even if you already danced earlier in this "
        "conversation. Just avoid calling it multiple times in a row for a single request."
    )
