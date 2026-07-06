import os

from google import genai
from google.genai import types

from llm import LLM


class Gemini(LLM):
    def __init__(self):
        self.__client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT", "evertune-tests"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        self.__model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    def parallelism(self):
        return int(os.getenv("GEMINI_PARALLELISM", "30"))

    async def ask_generic_question(
        self, system_prompt: str, question: str, temperature: float
    ) -> LLM.SimpleResponse:
        response = await self.__client.aio.models.generate_content(
            model=self.__model,
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
            ),
        )

        usage = response.usage_metadata
        reasoning_tokens = usage.thoughts_token_count or 0
        output_tokens = (usage.candidates_token_count or 0) + reasoning_tokens

        return LLM.SimpleResponse(
            answer=response.text,
            input_tokens=usage.prompt_token_count,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
