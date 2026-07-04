"""Prove that thinking is a per-request knob: same prompt, three budgets.

  budget = -1  -> dynamic (default: model decides)
  budget =  0  -> thinking OFF
  budget = 512 -> capped

Run:  .venv/bin/python scripts/probe_thinking_budget.py
"""
import asyncio
import os
import time

from google import genai
from google.genai import types

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "evertune-tests")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
QUESTION = "A farmer has 17 sheep; all but 9 run away. How many are left?"


async def ask(client, budget):
    start = time.time()
    r = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=QUESTION,
        config=types.GenerateContentConfig(
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_budget=budget),
        ),
    )
    u = r.usage_metadata
    return time.time() - start, (u.thoughts_token_count or 0), (u.candidates_token_count or 0)


async def main():
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    print(f"{'budget':>7}  {'latency':>8}  {'thinking':>8}  {'answer':>6}")
    for budget in (-1, 0, 512):
        dt, think, answer = await ask(client, budget)
        label = {-1: "dynamic", 0: "off"}.get(budget, str(budget))
        print(f"{label:>7}  {dt:7.2f}s  {think:>8}  {answer:>6}")


if __name__ == "__main__":
    asyncio.run(main())
