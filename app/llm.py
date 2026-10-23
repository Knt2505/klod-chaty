import base64
import logging
import time

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
        self.reasoning_effort = cfg.chaty_reasoning_effort

    async def generate(
        self, persona: Persona, system: str, prompt: str, images: list[bytes]
    ) -> str:
        encoded = [base64.standard_b64encode(image).decode("ascii") for image in images]
        started = time.monotonic()
        if persona == "klod":
            text = await self._claude(system, prompt, encoded)
        else:
            text = await self._gpt(system, prompt, encoded)
        log.info("%s answered in %.1fs", persona, time.monotonic() - started)
        return text

    async def _claude(self, system: str, prompt: str, images: list[str]) -> str:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
            for data in images
        ]
        content.append({"type": "text", "text": prompt})
        response = await self.anthropic.messages.create(
            model=self.cfg.klod_model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            # Для чату thinking не потрібен — без нього відповідь у рази швидша.
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": content}],
        )
        log.info("claude usage: %s", response.usage)
        if response.stop_reason == "refusal":
            log.warning("claude refused: %s", response.stop_details)
            return ""
        return "".join(block.text for block in response.content if block.type == "text").strip()

    async def _gpt(self, system: str, prompt: str, images: list[str]) -> str:
        content = [
            {"type": "input_image", "detail": "low", "image_url": f"data:image/jpeg;base64,{data}"}
            for data in images
        ]
        content.append({"type": "input_text", "text": prompt})
        request = {
            "model": self.cfg.chaty_model,
            "instructions": system,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        if self.reasoning_effort:
            request["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = await self.openai.responses.create(**request)
        except openai.BadRequestError:
            if "reasoning" not in request:
                raise
            # Модель не приймає reasoning — далі працюємо без нього.
            log.warning("model %s rejected reasoning param, retrying without it", self.cfg.chaty_model)
            self.reasoning_effort = None
            del request["reasoning"]
            response = await self.openai.responses.create(**request)
        log.info("gpt usage: %s", response.usage)
        return response.output_text.strip()
