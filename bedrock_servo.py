import logging

import boto3

from arm_control import TOOLS, build_system_prompt, dispatch_tool, open_serial, setup_logging

logger = logging.getLogger("bedrock_servo")

MODEL_ID = "us.anthropic.claude-sonnet-5"


def chat(client, ser, user_input: str):
    messages = [{"role": "user", "content": [{"text": user_input}]}]

    print(f"\n俺「{user_input}」")

    while True:
        resp = client.converse(
            modelId=MODEL_ID,
            system=[{"text": build_system_prompt()}],
            messages=messages,
            toolConfig={"tools": TOOLS},
        )

        output      = resp["output"]["message"]
        stop_reason = resp["stopReason"]
        messages.append(output)

        if stop_reason == "tool_use":
            tool_results = []
            for block in output["content"]:
                if "toolUse" not in block:
                    continue
                tool_name   = block["toolUse"]["name"]
                tool_input  = block["toolUse"]["input"]
                tool_use_id = block["toolUse"]["toolUseId"]

                logger.info("tool call: %s(%s)", tool_name, tool_input)
                result = dispatch_tool(ser, tool_name, tool_input)

                tool_results.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": result}]
                    }
                })

            messages.append({"role": "user", "content": tool_results})

        else:
            for block in output["content"]:
                if "text" in block:
                    print(f"\nロボットくん「{block['text']}」")
            break


def main():
    setup_logging()
    client = boto3.client("bedrock-runtime", region_name="us-west-2")
    ser = open_serial()

    try:
        while True:
            user_input = input("\n> ").strip()
            if user_input.lower() in ("exit", "quit", "q"):
                break
            if not user_input:
                continue
            chat(client, ser, user_input)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
