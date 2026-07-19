from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import OnbClient


@dataclass
class OnbConfig:
    api_url: str = "http://127.0.0.1:5055"
    password: str = ""
    collection_dir: str = "/tmp/notellm-collections"
    obsidian_vault: str = "/home/ubuntu/notebooks"
    max_file_size: int = 5_000_000
    max_collection_age: int = 3600
    max_retries: int = 3
    retry_backoff: float = 1.0
    tool_timeout: int = 120
    cache_ttl: int = 30
    podcast_profiles: tuple[str, str] = ("tech_discussion", "tech_experts")
    podcast_output_dir: str = os.path.expanduser("~/播客")
    podcast_host1: str = "Maia"  # 女性 知性温柔
    podcast_host2: str = "Kai"   # 男性 耳朵SPA

    @classmethod
    def from_env(cls) -> OnbConfig:
        return cls(
            api_url=os.environ.get("NOTELLM_ONB_API", "http://127.0.0.1:5055"),
            password=os.environ.get("NOTELLM_ONB_PASSWORD", ""),
            collection_dir=os.environ.get("NOTELLM_COLLECTION_DIR", "/tmp/notellm-collections"),
            obsidian_vault=os.environ.get("NOTELLM_OBSIDIAN_VAULT", "/home/ubuntu/notebooks"),
            max_file_size=int(os.environ.get("NOTELLM_MAX_FILE_SIZE", "5000000")),
            max_collection_age=int(os.environ.get("NOTELLM_MAX_COLLECTION_AGE", "3600")),
            max_retries=int(os.environ.get("NOTELLM_MAX_RETRIES", "3")),
            retry_backoff=float(os.environ.get("NOTELLM_RETRY_BACKOFF", "1.0")),
            tool_timeout=int(os.environ.get("NOTELLM_TOOL_TIMEOUT", "120")),
            cache_ttl=int(os.environ.get("NOTELLM_CACHE_TTL", "30")),
            podcast_profiles=(
                os.environ.get("NOTELLM_PODCAST_EPISODE_PROFILE", "tech_discussion"),
                os.environ.get("NOTELLM_PODCAST_SPEAKER_PROFILE", "tech_experts"),
            ),
            podcast_output_dir=os.environ.get("NOTELLM_PODCAST_OUTPUT_DIR", os.path.expanduser("~/播客")),
            podcast_host1=os.environ.get("NOTELLM_PODCAST_HOST1", "Maia"),
            podcast_host2=os.environ.get("NOTELLM_PODCAST_HOST2", "Kai"),
        )


@dataclass
class Context:
    client: OnbClient
    config: OnbConfig
