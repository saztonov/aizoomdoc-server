"""
Document-level extraction of generic facts and table structures.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from app.models.llm_schemas import DocumentFacts, SelectedBlock, AnalysisIntent, get_document_facts_schema
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class DocumentExtractService:
    """Extracts generic facts and tables from selected blocks."""

    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def extract_facts(
        self,
        *,
        system_prompt: str,
        user_message: str,
        selected_blocks: List[SelectedBlock],
        analysis_intent: Optional[AnalysisIntent] = None,
        max_chars: int = 18000,
    ) -> DocumentFacts:
        """
        Extract facts from selected blocks using a structured JSON schema.

        Args:
            system_prompt: Prompt guiding extraction.
            user_message: Original user question.
            selected_blocks: Blocks selected by flash collector.
            analysis_intent: Optional intent guidance.
            max_chars: Max input size for block context.
        """
        context = self._build_context(selected_blocks, max_chars=max_chars)
        intent_text = ""
        if analysis_intent:
            intent_text = (
                f"ANALYSIS_INTENT:\n{analysis_intent.model_dump()}\n\n"
            )

        extraction_prompt = (
            f"{intent_text}"
            "EXTRACTION_SOURCE:\n"
            f"{context}\n\n"
            f"USER QUESTION:\n{user_message}"
        )

        try:
            text = await self.llm_service.generate_json_response(
                system_prompt=system_prompt,
                user_message=extraction_prompt,
                response_schema=get_document_facts_schema(),
            )
            parsed = self.llm_service.parse_json(text)
            return DocumentFacts.model_validate(parsed)
        except Exception as exc:
            logger.warning(f"DocumentExtractService failed: {exc}")
            return DocumentFacts()

    def _build_context(self, blocks: List[SelectedBlock], *, max_chars: int) -> str:
        """Build a compact context from blocks with id metadata."""
        parts: List[str] = []
        total = 0
        for block in blocks:
            if block.block_kind not in ("TEXT", "TABLE"):
                continue
            header = f"[BLOCK {block.block_id} | page {block.page_number} | {block.block_kind}]\n"
            body = block.content_raw.strip()
            if not body:
                continue
            chunk = f"{header}{body}\n\n"
            if total + len(chunk) > max_chars:
                break
            parts.append(chunk)
            total += len(chunk)
        return "".join(parts).strip()

