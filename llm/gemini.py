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

    async def ask_generic_question(self, system_prompt: str, question: str, temperature: float) -> LLM.SimpleResponse:
        response = await self.__client.aio.models.generate_content(
            model=self.__model,
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
            ),
        )

        usage = response.usage_metadata
        # Gemini 2.5 Flash is a thinking model: it spends "thoughts" tokens
        # reasoning before the answer. Those are billed at the output rate but
        # live in a separate field, so fold them into output_tokens — otherwise
        # cost is under-reported by 2-4x (and the load test would be optimistic).
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        return LLM.SimpleResponse(
            answer=response.text,
            input_tokens=usage.prompt_token_count,
            output_tokens=output_tokens,
        )
