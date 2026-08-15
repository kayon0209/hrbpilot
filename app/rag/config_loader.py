"""HRBP AI Workbench — ScenarioConfig data structure and YAML loader.

Each scenario has its own config file defining:
  - knowledge_base_id, retrieval strategy, prompt template, output schema
  - guardrail rules, eval metrics, fallback strategy
  - LLM parameters (temperature, max_tokens)

The prompt_template field in YAML can be either:
  1. A file path (relative to the scenario directory) — loaded and resolved at init
  2. Inline template text
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from app.shared.logger import get_logger

logger = get_logger(__name__)


class RetrievalStrategy(str, Enum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"


class FallbackStrategy(str, Enum):
    NO_EVIDENCE = "no_evidence"
    RAW_PRESERVE = "raw_preserve"
    DATA_INSUFFICIENT = "data_insufficient"
    MULTIPLE_VERSIONS = "multiple_versions"


@dataclass
class GuardrailRules:
    """Guardrail rules for a scenario — which checks to enable."""
    input: list[str] = field(default_factory=lambda: ["pii_detection", "prompt_injection"])
    output: list[str] = field(default_factory=lambda: ["citation_verification", "factuality_check"])


@dataclass
class ScenarioConfig:
    """Full configuration for one scenario — injected into the pipeline."""
    scenario_id: str
    knowledge_base_id: str
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    retrieval_top_k: int = 5
    rerank_enabled: bool = False
    prompt_template: str = ""
    output_schema: str = ""
    guardrail_rules: GuardrailRules = field(default_factory=GuardrailRules)
    eval_metrics: list[str] = field(default_factory=list)
    fallback_strategy: FallbackStrategy = FallbackStrategy.NO_EVIDENCE
    max_tokens: int = 1024
    temperature: float = 0.3
    required_role: str = "hrbp"


# Config directory (app/scenarios/)
CONFIG_DIR = Path(__file__).parent.parent / "scenarios"


def _resolve_prompt_template(raw_value: str, scenario_dir: Path) -> str:
    """Resolve prompt_template: if it's a file path, load the file content."""
    if not raw_value:
        return ""

    # Check if it looks like a file path (ends with .txt or .md, or contains /)
    if raw_value.endswith(".txt") or raw_value.endswith(".md") or "/" in raw_value:
        prompt_path = scenario_dir / raw_value
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        else:
            logger.warning("prompt_template_file_not_found", path=str(prompt_path))
            return raw_value  # Return the raw value as fallback

    # Inline template text
    return raw_value


def load_scenario_config(scenario_id: str) -> ScenarioConfig:
    """Load a ScenarioConfig from YAML file in the scenario directory."""
    config_path = CONFIG_DIR / scenario_id / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"ScenarioConfig not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Parse guardrail rules
    guardrail_raw = raw.get("guardrail_rules", {})
    guardrail_rules = GuardrailRules(
        input=guardrail_raw.get("input", ["pii_detection", "prompt_injection"]),
        output=guardrail_raw.get("output", ["citation_verification", "factuality_check"]),
    )

    # Resolve prompt template (file path → file content)
    scenario_dir = CONFIG_DIR / scenario_id
    prompt_template = _resolve_prompt_template(
        raw.get("prompt_template", ""), scenario_dir
    )

    config = ScenarioConfig(
        scenario_id=raw["scenario_id"],
        knowledge_base_id=raw["knowledge_base_id"],
        retrieval_strategy=RetrievalStrategy(raw.get("retrieval_strategy", "hybrid")),
        retrieval_top_k=raw.get("retrieval_top_k", 5),
        rerank_enabled=raw.get("rerank_enabled", False),
        prompt_template=prompt_template,
        output_schema=raw.get("output_schema", ""),
        guardrail_rules=guardrail_rules,
        eval_metrics=raw.get("eval_metrics", []),
        fallback_strategy=FallbackStrategy(raw.get("fallback_strategy", "no_evidence")),
        max_tokens=raw.get("max_tokens", 1024),
        temperature=raw.get("temperature", 0.3),
        required_role=raw.get("required_role", "hrbp"),
    )

    logger.info("scenario_config_loaded", scenario_id=scenario_id, prompt_len=len(prompt_template))
    return config
