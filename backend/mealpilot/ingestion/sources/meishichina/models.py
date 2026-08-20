"""Raw quarantine contracts for the MeishiChina importer."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from mealpilot.domain.models import CookingStep


class RawRecipeIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: Literal["main", "secondary", "seasoning", "other"]
    raw_name: str = Field(min_length=1, max_length=200)
    raw_amount: str = Field(default="", max_length=100)
    raw_text: str = Field(min_length=1, max_length=300)


class RawMeishiChinaRecipe(BaseModel):
    """Parsed source material that is never publication eligible by itself."""

    model_config = ConfigDict(extra="forbid", validate_default=True)
    schema_version: Literal["mealpilot.raw-meishichina.v1"] = "mealpilot.raw-meishichina.v1"
    staging_id: str = Field(pattern=r"^raw-mc-[a-f0-9]{24}$")
    captured_at: datetime
    source_id: str = Field(pattern=r"^meishichina:\d+$")
    source_url: HttpUrl
    source_terms_url: HttpUrl = "https://static.meishichina.com/v6/help/policy.html"
    raw_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    ingredients: list[RawRecipeIngredient] = Field(min_length=1)
    cooking_steps: list[CookingStep] = Field(min_length=1)
    taste: str | None = Field(default=None, max_length=50)
    technique: str | None = Field(default=None, max_length=50)
    source_time_label: str | None = Field(default=None, max_length=50)
    difficulty: str | None = Field(default=None, max_length=50)
    tips: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    copyright_notice: str | None = Field(default=None, max_length=500)
    warnings: list[str] = Field(default_factory=list)
    trust_status: Literal["UNTRUSTED"] = "UNTRUSTED"
    license_status: Literal["PENDING"] = "PENDING"
    review_status: Literal["PENDING"] = "PENDING"
    extraction_status: Literal["EXTRACTED"] = "EXTRACTED"
    publication_eligible: Literal[False] = False
