"""Measure per-request latency and FULL token usage, including hidden thinking
tokens, to explain latency spikes. Gemini 2.5 Flash reasons before answering;
those 'thoughts' tokens are billed and add latency but are NOT part of
candidates_token_count (what our provider currently reports).

Run:  make probe   (or)   .venv/bin/python scripts/probe_thinking.py
"""

import asyncio
import os
import time

from google import genai
from google.genai import types

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "evertune-tests")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

PROMPTS = [
    "Say hi.",
    "What is the capital of France?",
    "A farmer has 17 sheep; all but 9 run away. How many are left? Explain briefly.",
    "Explain why the sky is blue in 2 sentences.",
]


async def main():
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    print(
        f"{'latency':>9}  {'prompt':>7}  {'thoughts':>8}  {'answer':>6}  {'total':>6}"
    )
    for p in PROMPTS:
        start = time.time()
        r = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=p,
            config=types.GenerateContentConfig(temperature=0),
        )
        dt = time.time() - start
        u = r.usage_metadata
        print(
            f"{dt:8.2f}s  {u.prompt_token_count:>7}  "
            f"{(u.thoughts_token_count or 0):>8}  "
            f"{(u.candidates_token_count or 0):>6}  "
            f"{u.total_token_count:>6}   | {p[:40]}"
        )


if __name__ == "__main__":
    asyncio.run(main())
