import os

from google import genai
from google.genai import types

from llm import LLM


class Gemini(LLM):
    """Gemini 2.5 Flash on Google Vertex AI.

    Vertex serves Gemini as a fully-managed model, so there is nothing to
    deploy — we authenticate via gcloud ADC and call generate_content. Config
    comes from the environment to mirror the Together provider:

        GOOGLE_CLOUD_PROJECT   (default: evertune-tests)
        GOOGLE_CLOUD_LOCATION  (default: us-central1)
        GEMINI_MODEL           (default: gemini-2.5-flash)
        GEMINI_PARALLELISM     (default: 30)
    """

    def __init__(self):
        self.__client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT", "evertune-tests"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        self.__model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.__parallelism = int(os.getenv("GEMINI_PARALLELISM", "30"))

    def parallelism(self):
        return self.__parallelism

    async def ask_generic_question(
        self,
        system_prompt: str,
        question: str,
        temperature: float,
        thinking_budget: int | None = None,
        enable_web: bool = False,
        logprobs: int | None = None,
    ) -> LLM.SimpleResponse:
        """Ask one question.

        Beyond the base contract we accept three Gemini-specific knobs, all
        optional so the base-class signature still holds:

          thinking_budget  -1 dynamic (model decides) · 0 off · N cap the
                           reasoning tokens. Trades latency/cost against answer
                           quality; proven to be a per-request knob in
                           scripts/probe_thinking_budget.py.
          enable_web       attach the Google Search grounding tool. Flips the
                           measurement from "base-model knowledge" to the live
                           retrieval path real consumer apps use — a different
                           regime, not just a different answer.
          logprobs         request top-N token log-probs. Lets us read the
                           model's probability of naming a brand directly
                           instead of sampling for it (the recall shortcut the
                           Together provider gets via logprobs=1). Vertex support
                           is model-dependent; surfaced as-is so failures are
                           visible rather than hidden.
        """
        config_kwargs = dict(
            system_instruction=system_prompt,
            temperature=temperature,
        )
        if thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget
            )
        if enable_web:
            config_kwargs["tools"] = [
                types.Tool(google_search=types.GoogleSearch())
            ]
        if logprobs is not None:
            config_kwargs["response_logprobs"] = True
            config_kwargs["logprobs"] = logprobs

        response = await self.__client.aio.models.generate_content(
            model=self.__model,
            contents=question,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        usage = response.usage_metadata
        # Gemini 2.5 Flash is a thinking model: it spends "thoughts" tokens
        # reasoning before the answer. Those are billed at the output rate but
        # live in a separate field, so fold them into output_tokens — otherwise
        # cost is under-reported by 2-4x (and the load test would be optimistic).
        reasoning_tokens = usage.thoughts_token_count or 0
        output_tokens = (usage.candidates_token_count or 0) + reasoning_tokens

        grounded, sources, search_queries, avg_logprob = self.__extract_metadata(response)

        return LLM.SimpleResponse(
            answer=response.text,
            input_tokens=usage.prompt_token_count,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            grounded=grounded,
            sources=tuple(sources),
            search_queries=tuple(search_queries),
            avg_logprob=avg_logprob,
        )

    @staticmethod
    def __extract_metadata(response):
        """Pull grounding + logprob signals off the candidate, defensively.

        These fields are only present when the corresponding feature is on, and
        their shape varies by model/version, so every access is guarded.
        """
        grounded, sources, search_queries, avg_logprob = False, [], [], None
        candidate = (response.candidates or [None])[0]
        if candidate is None:
            return grounded, sources, search_queries, avg_logprob

        gm = getattr(candidate, "grounding_metadata", None)
        if gm is not None:
            search_queries = list(getattr(gm, "web_search_queries", None) or [])
            for chunk in getattr(gm, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web and getattr(web, "uri", None):
                    sources.append(getattr(web, "title", None) or web.uri)
            # "Grounded" means search actually fired, not merely that we allowed it.
            grounded = bool(search_queries or sources)

        lp = getattr(candidate, "logprobs_result", None)
        if lp is not None:
            vals = [
                c.log_probability
                for c in (getattr(lp, "chosen_candidates", None) or [])
                if getattr(c, "log_probability", None) is not None
            ]
            if vals:
                avg_logprob = sum(vals) / len(vals)

        return grounded, sources, search_queries, avg_logprob
