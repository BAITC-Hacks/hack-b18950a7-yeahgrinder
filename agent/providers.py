"""LLM clients. Missing keys disable the AI layer; the engine never depends on it."""
import os

from dotenv import load_dotenv
import yaml

from engine.models import ROOT

load_dotenv(ROOT / ".env")


def ai_config() -> dict:
    config = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    return config.get("ai", {})


def chat_model():
    """Main agent model (OpenAI). None when OPENAI_API_KEY is not set."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"), temperature=0, api_key=key, timeout=60)


def critic_model():
    """Independent critic on NVIDIA's OpenAI-compatible endpoint. None when disabled or no key."""
    key = os.environ.get("NVIDIA_API_KEY")
    if not key or not ai_config().get("enable_nvidia_critic", True):
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"), temperature=0,
                      api_key=key, base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                      timeout=60)


def status() -> dict:
    return {"chat": bool(os.environ.get("OPENAI_API_KEY")),
            "critic": bool(os.environ.get("NVIDIA_API_KEY")) and ai_config().get("enable_nvidia_critic", True)}
