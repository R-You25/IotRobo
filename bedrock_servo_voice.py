import asyncio
import base64
import contextlib
import json
import logging
import os
import queue
import threading
import uuid

import boto3
import pyaudio
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient, InvokeModelWithBidirectionalStreamOperationInput
from aws_sdk_bedrock_runtime.config import Config
from aws_sdk_bedrock_runtime.models import (
    BidirectionalInputPayloadPart,
    InvokeModelWithBidirectionalStreamInputChunk,
)
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

from arm_control import TOOLS, build_system_prompt_en, dispatch_tool, open_serial, setup_logging

logger = logging.getLogger("bedrock_servo_voice")

MODEL_ID = "amazon.nova-sonic-v1:0"
REGION   = "us-east-1"  # Nova Sonic は us-east-1 のみ対応

INPUT_SAMPLE_RATE  = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS_AUDIO     = 1
CHUNK_SIZE         = 1024


@contextlib.contextmanager
def suppress_alsa_stderr():
    """PyAudio初期化時にALSAが標準エラーへ吐く大量の警告を抑制する。"""
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_stderr_fd = os.dup(2)
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_stderr_fd, 2)
        os.close(devnull_fd)
        os.close(saved_stderr_fd)


def load_credentials_into_env():
    """boto3(MFA/assume-role対応)で解決した一時credentialsを環境変数にセットする。"""
    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()
    os.environ["AWS_ACCESS_KEY_ID"] = creds.access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
    if creds.token:
        os.environ["AWS_SESSION_TOKEN"] = creds.token

# Nova Sonic のツール定義形式（Bedrock Converse の toolSpec と近いが inputSchema.json は文字列で渡す）
NOVA_TOOLS = [
    {
        "toolSpec": {
            "name": t["toolSpec"]["name"],
            "description": t["toolSpec"]["description"],
            "inputSchema": {"json": json.dumps(t["toolSpec"]["inputSchema"]["json"])},
        }
    }
    for t in TOOLS
]


def build_client() -> BedrockRuntimeClient:
    load_credentials_into_env()
    config = Config(
        endpoint_uri=f"https://bedrock-runtime.{REGION}.amazonaws.com",
        region=REGION,
        aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
    )
    return BedrockRuntimeClient(config=config)


class NovaSonicSession:
    def __init__(self, ser):
        self.ser = ser
        self.client = build_client()
        self.stream = None
        self.prompt_name = str(uuid.uuid4())
        self.content_name_audio = str(uuid.uuid4())
        self.audio_out_queue: queue.Queue = queue.Queue()
        self.running = True
        self._pending_tool_name = None
        self._pending_tool_use_id = None
        self._pending_tool_input = None
        self._display_assistant_text = True

    async def start(self):
        operation_input = InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL_ID)
        self.stream = await self.client.invoke_model_with_bidirectional_stream(operation_input)

        await self._send_event({
            "event": {"sessionStart": {"inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7}}}
        })

        await self._send_event({
            "event": {
                "promptStart": {
                    "promptName": self.prompt_name,
                    "textOutputConfiguration": {"mediaType": "text/plain"},
                    "audioOutputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                        "sampleSizeBits": 16,
                        "channelCount": CHANNELS_AUDIO,
                        "voiceId": "matthew",
                        "encoding": "base64",
                        "audioType": "SPEECH",
                    },
                    "toolUseOutputConfiguration": {"mediaType": "application/json"},
                    "toolConfiguration": {"tools": NOVA_TOOLS},
                }
            }
        })

        system_content_name = str(uuid.uuid4())
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": system_content_name,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "SYSTEM",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        })
        await self._send_event({
            "event": {
                "textInput": {
                    "promptName": self.prompt_name,
                    "contentName": system_content_name,
                    "content": build_system_prompt_en(),
                }
            }
        })
        await self._send_event({
            "event": {"contentEnd": {"promptName": self.prompt_name, "contentName": system_content_name}}
        })

        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name_audio,
                    "type": "AUDIO",
                    "interactive": True,
                    "role": "USER",
                    "audioInputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": INPUT_SAMPLE_RATE,
                        "sampleSizeBits": 16,
                        "channelCount": CHANNELS_AUDIO,
                        "encoding": "base64",
                        "audioType": "SPEECH",
                    },
                }
            }
        })

    async def _send_event(self, event: dict):
        chunk = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=json.dumps(event).encode())
        )
        await self.stream.input_stream.send(chunk)

    async def send_audio_chunk(self, pcm_bytes: bytes):
        await self._send_event({
            "event": {
                "audioInput": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name_audio,
                    "content": base64.b64encode(pcm_bytes).decode(),
                }
            }
        })

    async def send_tool_result(self, tool_use_id: str, content_name: str, result: str):
        await self._send_event({
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": content_name,
                    "interactive": False,
                    "type": "TOOL",
                    "role": "TOOL",
                    "toolResultInputConfiguration": {
                        "toolUseId": tool_use_id,
                        "type": "TEXT",
                        "textInputConfiguration": {"mediaType": "text/plain"},
                    },
                }
            }
        })
        await self._send_event({
            "event": {
                "toolResult": {
                    "promptName": self.prompt_name,
                    "contentName": content_name,
                    "content": result,
                }
            }
        })
        await self._send_event({
            "event": {"contentEnd": {"promptName": self.prompt_name, "contentName": content_name}}
        })

    async def receive_loop(self):
        _, output_stream = await self.stream.await_output()
        while self.running:
            try:
                result = await output_stream.receive()
            except StopAsyncIteration:
                break
            if result is None:
                break
            if result.value and result.value.bytes_:
                event = json.loads(result.value.bytes_.decode())
                await self._handle_event(event)

    async def _handle_event(self, event: dict):
        ev = event.get("event", {})

        if "audioOutput" in ev:
            audio_bytes = base64.b64decode(ev["audioOutput"]["content"])
            self.audio_out_queue.put(audio_bytes)

        elif "contentStart" in ev:
            additional_fields_raw = ev["contentStart"].get("additionalModelFields")
            if additional_fields_raw:
                additional_fields = json.loads(additional_fields_raw)
                self._display_assistant_text = additional_fields.get("generationStage") != "SPECULATIVE"
            else:
                self._display_assistant_text = True

        elif "textOutput" in ev:
            text = ev["textOutput"]["content"]
            role = ev["textOutput"].get("role", "")
            if '{ "interrupted" : true }' in text:
                pass
            elif role == "USER":
                print(f"\nYou: {text}")
            elif role == "ASSISTANT" and self._display_assistant_text:
                print(f"\nRobot: {text}")

        elif "toolUse" in ev:
            tool_use = ev["toolUse"]
            self._pending_tool_name    = tool_use["toolName"]
            self._pending_tool_use_id  = tool_use["toolUseId"]
            self._pending_tool_input   = json.loads(tool_use["content"])

        elif "contentEnd" in ev and ev["contentEnd"].get("type") == "TOOL":
            tool_name   = self._pending_tool_name
            tool_use_id = self._pending_tool_use_id
            tool_input  = self._pending_tool_input
            logger.info("tool call: %s(%s)", tool_name, tool_input)

            result = await asyncio.to_thread(dispatch_tool, self.ser, tool_name, tool_input)
            await self.send_tool_result(tool_use_id, str(uuid.uuid4()), result)

        elif "completionEnd" in ev:
            self.running = False

    async def close(self):
        await self._send_event({"event": {"contentEnd": {"promptName": self.prompt_name, "contentName": self.content_name_audio}}})
        await self._send_event({"event": {"promptEnd": {"promptName": self.prompt_name}}})
        await self._send_event({"event": {"sessionEnd": {}}})
        await self.stream.input_stream.close()


def mic_capture_thread(pa: pyaudio.PyAudio, loop: asyncio.AbstractEventLoop, session: NovaSonicSession, stop_flag: threading.Event):
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS_AUDIO,
        rate=INPUT_SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE,
    )
    try:
        while not stop_flag.is_set():
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            asyncio.run_coroutine_threadsafe(session.send_audio_chunk(data), loop)
    finally:
        stream.stop_stream()
        stream.close()


def speaker_playback_thread(pa: pyaudio.PyAudio, session: NovaSonicSession, stop_flag: threading.Event):
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS_AUDIO,
        rate=OUTPUT_SAMPLE_RATE,
        output=True,
    )
    try:
        while not stop_flag.is_set():
            try:
                chunk = session.audio_out_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            stream.write(chunk)
    finally:
        stream.stop_stream()
        stream.close()


async def main():
    setup_logging()
    ser = open_serial()
    session = NovaSonicSession(ser)

    print("Connecting to Nova Sonic...")
    await session.start()
    print("Ready. Start talking (Ctrl+C to quit)")

    loop = asyncio.get_running_loop()
    stop_flag = threading.Event()

    with suppress_alsa_stderr():
        pa = pyaudio.PyAudio()

        mic_thread = threading.Thread(target=mic_capture_thread, args=(pa, loop, session, stop_flag), daemon=True)
        speaker_thread = threading.Thread(target=speaker_playback_thread, args=(pa, session, stop_flag), daemon=True)
        mic_thread.start()
        speaker_thread.start()

        try:
            await session.receive_loop()
        except KeyboardInterrupt:
            pass
        finally:
            stop_flag.set()
            mic_thread.join(timeout=2)
            speaker_thread.join(timeout=2)
            pa.terminate()

    await session.close()
    ser.close()


if __name__ == "__main__":
    asyncio.run(main())
