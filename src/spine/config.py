"""Spine configuration and enacted runtime defaults."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from `SPINE_*` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SPINE_",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    token: SecretStr
    owner_principal_id: str = Field(default="local", min_length=1)
    openai_api_key: SecretStr | None = None

    tau: float = Field(default=0.55, ge=0.0, le=1.0)
    top_k: int = Field(default=8, gt=0)
    near_miss_k: int = Field(default=3, ge=0)
    memory_context_share: float = Field(default=0.10, ge=0.01, le=0.50)
    half_life_time_days: int = Field(default=14, gt=0)
    half_life_hist_days: int = Field(default=7, gt=0)
    dedup_dup: float = Field(default=0.92, ge=0.0, le=1.0)
    dedup_sim: float = Field(default=0.80, ge=0.0, le=1.0)
    never_bias_step: float = -0.15
    quarantine_kills: int = Field(default=3, gt=0)
    candidate_pool: int = Field(default=50, gt=0)
    embed_base_url: str = "https://openrouter.ai/api/v1"
    embed_model: str = "openai/text-embedding-3-small"
    embed_dim: Literal[1536] = 1536
    memory_max_tokens: int = Field(default=128, gt=0)
    label_max: int = Field(default=64, gt=0)
    chat_model: str = "anthropic:claude-sonnet-4-6"
    chat_base_url: str = "https://openrouter.ai/api/v1"
    spend_view_refresh_seconds: int = Field(default=60, gt=0)
    reconciliation_hours: float = Field(default=24, gt=0)
    reconciliation_tolerance_usd: Decimal = Field(
        default=Decimal("0.000001"), gt=0, max_digits=20, decimal_places=12
    )
    learner_min_dispositions: int = Field(default=25, gt=0)
    learner_holdout_fraction: float = Field(default=0.20, gt=0.0, lt=0.5)
    learner_passive_discount: float = Field(default=0.25, gt=0.0, le=1.0)
    learner_pair_margin: float = Field(default=0.05, gt=0.0)
    learner_bias_l2: float = Field(default=1.0, gt=0.0)
    learner_win_margin: float = Field(default=1.0, gt=0.0)
    retrain_signal_stride: int = Field(default=25, gt=0)
    optimization_corpus_max_dispositions: int = Field(default=1000, gt=0)
    graph_edge_sim: float = Field(default=0.75, ge=0.0, le=1.0)
    curator_write_trigger: int = Field(default=25, gt=0)
    curator_pressure_trigger: int = Field(default=3, gt=0)
    curator_stale_days: int = Field(default=180, gt=0)
    curator_poll_seconds: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def validate_dedup_bands(self) -> "Settings":
        """Keep the similar band strictly below the hard-duplicate band."""

        if self.dedup_sim >= self.dedup_dup:
            raise ValueError("dedup_sim must be less than dedup_dup")
        if self.optimization_corpus_max_dispositions < self.learner_min_dispositions:
            raise ValueError(
                "optimization_corpus_max_dispositions must be at least learner_min_dispositions"
            )
        return self
