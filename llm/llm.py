from dataclasses import dataclass

class LLM:
    @dataclass
    class SimpleResponse:
        answer: str
        input_tokens: int
        output_tokens: int
        # Subset of output_tokens spent on internal reasoning before the answer
        # (thinking models like Gemini 2.5 Flash). 0 for models without it.
        reasoning_tokens: int = 0

    async def ask_generic_question(self, system_prompt: str, question: str, temperature: float) -> SimpleResponse:
        raise NotImplementedError()

    def parallelism(self):
        raise NotImplementedError()