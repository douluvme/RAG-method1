"""App settings, read from the environment (and the optional .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "docqa.sqlite3"


@dataclass(frozen=True)
class ModelChoice:
    label: str
    model: str
    price_input: float  # USD per 1M input tokens
    price_cached: float  # USD per 1M cached input tokens
    price_output: float  # USD per 1M output tokens

    def cost(self, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        uncached = max(input_tokens - cached_tokens, 0)
        return (
            uncached * self.price_input
            + cached_tokens * self.price_cached
            + output_tokens * self.price_output
        ) / 1_000_000


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or None


def app_password() -> str | None:
    return os.getenv("APP_PASSWORD") or None


MODELS = {
    "economy": ModelChoice(
        label="Economy",
        model=os.getenv("ECONOMY_MODEL", "gpt-5-mini"),
        price_input=_f("ECONOMY_PRICE_INPUT", 0.25),
        price_cached=_f("ECONOMY_PRICE_CACHED", 0.025),
        price_output=_f("ECONOMY_PRICE_OUTPUT", 2.00),
    ),
    "best": ModelChoice(
        label="Best quality",
        model=os.getenv("BEST_MODEL", "gpt-5"),
        price_input=_f("BEST_PRICE_INPUT", 1.25),
        price_cached=_f("BEST_PRICE_CACHED", 0.125),
        price_output=_f("BEST_PRICE_OUTPUT", 10.00),
    ),
}

OCR_MODEL = os.getenv("OCR_MODEL", MODELS["economy"].model)

# Maximum size of all documents together, in tokens. Kept below the model's
# real limit to leave room for instructions, chat history and the answer.
CONTEXT_LIMIT_TOKENS = int(_f("CONTEXT_LIMIT_TOKENS", 350_000))

# How many previous question/answer pairs to include for follow-up questions.
HISTORY_TURNS = 4

# Typical answer length, used for the cost estimate shown before asking.
EXPECTED_OUTPUT_TOKENS = 1_500

# A page with fewer extractable characters than this is treated as scanned.
MIN_TEXT_CHARS_PER_PAGE = 25
