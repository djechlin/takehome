"""Exercise the new provider knobs end-to-end against Vertex, one at a time,
so each feature is proven (or its failure made visible) before we rely on it.

Covers: plain call, thinking off, logprobs, and web grounding.

Run:  GOOGLE_CLOUD_PROJECT=evertune-tests .venv/bin/python scripts/probe_knobs.py
"""
import asyncio

from llm import Gemini

BRANDS_Q = "Name one running shoe brand."


async def main():
    g = Gemini()

    # 1) Plain call — baseline tokens, including hidden thinking.
    r = await g.ask_generic_question("You are terse.", BRANDS_Q, 0.0)
    print("plain      :", repr(r.answer[:60]),
          "| in/out/think", r.input_tokens, r.output_tokens, r.reasoning_tokens)

    # 2) Thinking off — reasoning_tokens should drop to 0.
    r = await g.ask_generic_question("You are terse.", BRANDS_Q, 0.0, thinking_budget=0)
    print("think-off  :", repr(r.answer[:60]), "| think", r.reasoning_tokens)

    # 3) Logprobs — does Vertex return them for this model? avg_logprob or None.
    try:
        r = await g.ask_generic_question("You are terse.", BRANDS_Q, 0.0, logprobs=5)
        print("logprobs   :", r.avg_logprob)
    except Exception as e:
        print("logprobs   : ERROR", type(e).__name__, str(e)[:160])

    # 4) Web grounding — a time-sensitive question forces a real search.
    news_q = "What is the latest news about the Tour de France 2026?"
    try:
        r = await g.ask_generic_question("You are helpful.", news_q, 0.0, enable_web=True)
        print("web        : grounded", r.grounded,
              "| queries", list(r.search_queries[:3]),
              "| n_sources", len(r.sources))
    except Exception as e:
        print("web        : ERROR", type(e).__name__, str(e)[:160])


if __name__ == "__main__":
    asyncio.run(main())
