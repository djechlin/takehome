import os

from google import genai
from google.genai import types

from llm import LLM


class GeminiLoadTestHarness:
    """
    Gemini client for the load-test console. Exposes the full knob set the
    console drives — thinking budget, web grounding, logprobs — and surfaces the
    extra response metadata those knobs produce (grounding sources, search
    queries, avg logprob).

    Deliberately NOT an LLM subclass: its ask_generic_question breaks the generic
    provider signature to take those extra options, so it stands on its own rather
    than masquerading as a drop-in provider. For a thin, LLM-compatible Gemini,
    use llm.gemini.Gemini instead.
    """

    def __init__(self):
        self.__client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT", "evertune-tests"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        self.__model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    async def ask_generic_question(
        self,
        system_prompt: str,
        question: str,
        temperature: float,
        thinking_budget: int | None = None,
        enable_web: bool = False,
        logprobs: int | None = None,
    ) -> LLM.SimpleResponse:
        config_kwargs = dict(
            system_instruction=system_prompt,
            temperature=temperature,
        )
        if thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget
            )
        if enable_web:
            config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        if logprobs is not None:
            config_kwargs["response_logprobs"] = True
            config_kwargs["logprobs"] = logprobs

        response = await self.__client.aio.models.generate_content(
            model=self.__model,
            contents=question,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        usage = response.usage_metadata
        reasoning_tokens = usage.thoughts_token_count or 0
        output_tokens = (usage.candidates_token_count or 0) + reasoning_tokens

        grounded, sources, search_queries, avg_logprob = self.__extract_metadata(
            response
        )

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
