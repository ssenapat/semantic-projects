"""Parser request models (CONTRACT 4.2)."""
from pydantic import BaseModel, ConfigDict, Field

from .context import LlmSemanticContext


class ParserOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature: float = Field(default=0.0, ge=0.0, le=0.2)
    max_output_tokens: int = Field(default=1000, ge=200, le=2000)
    allow_clarification: bool = True
    allow_unsupported: bool = True


class IntentParserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    question: str = Field(min_length=1, max_length=4000)
    conversation_context: list[str] = Field(default_factory=list, max_length=10)
    semantic_context: LlmSemanticContext
    parser_options: ParserOptions = Field(default_factory=ParserOptions)
