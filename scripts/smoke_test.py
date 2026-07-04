"""Quick check that we can reach Gemini 2.5 Flash on Vertex before building anything.

Run:  GOOGLE_CLOUD_PROJECT=evertune-tests python3 scripts/smoke_test.py
"""
import asyncio
import os

from google import genai
from google.genai import types

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "evertune-tests")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")


async def main():
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    resp = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents="Reply with exactly: pong",
        config=types.GenerateContentConfig(
            system_instruction="You are a terse test probe.",
            temperature=0,
        ),
    )
    print("answer:", repr(resp.text))
    print("tokens in/out:",
          resp.usage_metadata.prompt_token_count,
          resp.usage_metadata.candidates_token_count)


if __name__ == "__main__":
    asyncio.run(main())
