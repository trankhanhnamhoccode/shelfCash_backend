import asyncio
import json
from typing import Any

from json_repair import repair_json

from app.core.rule_mapper import finalize_mapping
from app.llm.base import LLMProvider
from app.schemas.llm import MappingSuggestion


class LocalQwenProvider(LLMProvider):
    def __init__(self, settings):
        self.settings = settings
        self.model = None
        self.tokenizer = None
        self.torch = None
        self._semaphore = asyncio.Semaphore(1)

    @property
    def available(self) -> bool:
        return self.model is not None and self.tokenizer is not None

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.torch = torch
        quantization_config = None
        if self.settings.qwen_load_in_4bit:
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.float16,
            )
        self.tokenizer = AutoTokenizer.from_pretrained(self.settings.qwen_model_id)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.settings.qwen_model_id, device_map="auto", quantization_config=quantization_config,
        )

    def health(self) -> dict:
        cuda_available = bool(self.torch and self.torch.cuda.is_available())
        return {
            "provider": "local_qwen", "model_id": self.settings.qwen_model_id,
            "available": self.available, "loaded": self.available, "cuda_available": cuda_available,
            "gpu_name": self.torch.cuda.get_device_name(0) if cuda_available else None,
            "quantization_mode": "4bit_nf4" if self.settings.qwen_load_in_4bit else "none",
        }

    async def map_sheet(self, profile, canonical_schemas, rule_suggestion):
        async with self._semaphore:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._generate_sync, profile.model_dump(mode="json"), canonical_schemas, rule_suggestion.model_dump()),
                    timeout=self.settings.qwen_timeout_seconds,
                )
                return self._validate_result(result, profile)
            except Exception:
                fallback = rule_suggestion.model_copy(deep=True)
                fallback.source = "rule_fallback"
                fallback.requires_review = True
                fallback.warnings.append("LLM mapping failed; rule suggestion retained")
                return fallback

    def _generate_sync(self, profile: dict[str, Any], canonical_schemas: dict, rule_suggestion: dict):
        system = (
            "You map Excel sheet profiles to canonical schemas. Return exactly one JSON object, "
            "with sheet_type, confidence, column_mapping, warnings, errors. Map every source column "
            "to a valid schema field or null. Never add source columns."
        )
        user = json.dumps({"profile": profile, "canonical_schemas": canonical_schemas, "rule_suggestion": rule_suggestion}, ensure_ascii=False)
        prompt = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        output = self.model.generate(**inputs, max_new_tokens=self.settings.qwen_max_new_tokens, do_sample=False)
        generated = output[0][inputs["input_ids"].shape[-1]:]
        return json.loads(repair_json(self.tokenizer.decode(generated, skip_special_tokens=True)))

    def _validate_result(self, raw: dict, profile) -> MappingSuggestion:
        columns = set(profile.columns)
        mapping = raw.get("column_mapping", {})
        if set(mapping) != columns:
            raise ValueError("LLM must map every and only source column")
        raw["source"] = "llm"
        raw["requires_review"] = False
        suggestion = MappingSuggestion.model_validate(raw)
        return finalize_mapping(
            profile, suggestion, self.settings.rule_confidence_threshold
        )

    async def close(self) -> None:
        self.model = self.tokenizer = None
        if self.torch and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
