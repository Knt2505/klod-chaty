import logging

import anthropic
import openai

from .config import Config
from .models import Persona

log = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 2000


class LLM:
    """Клод говорить через Claude, Чаті — через GPT."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.anthropic = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        self.openai = openai.AsyncOpenAI(api_key=cfg.openai_api_key)

    async def generate(self, persona: Persona, system: str, prompt: str) -> str:
        if persona == "klod":
            return await self._claude(system, prompt)
        return await self._gpt(system, prompt)

    async def _claude(self, system: str, prompt: str) -> str:
        response = await self.anthropic.messages.create(
            model=self.cfg.klod_model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        log.info("claude usage: %s", response.usage)
        if response.stop_reason == "refusal":
            log.warning("claude refused: %s", response.stop_details)
            return ""
        return "".join(block.text for block in response.content if block.type == "text").strip()

    async def _gpt(self, system: str, prompt: str) -> str:
        kwargs = {}
        if self.cfg.chaty_reasoning_effort:
            kwargs["reasoning"] = {"effort": self.cfg.chaty_reasoning_effort}
        response = await self.openai.responses.create(
            model=self.cfg.chaty_model,
            instructions=system,
            input=prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            **kwargs,
        )
        log.info("gpt usage: %s", response.usage)
        return response.output_text.strip()
