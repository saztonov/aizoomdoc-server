"""
Evidence service for rendering PDF crops to PNG and generating preview/quadrants/ROI.

Uses LRU/versioned cache for efficient PDF render caching with:
- Version-based invalidation (etag/last_modified from S3)
- Size-limited cache with LRU eviction
- TTL-based expiration
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable, Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image

from app.config import settings
from app.services.render_cache import get_render_cache, RenderCacheManager

logger = logging.getLogger(__name__)


@dataclass
class RenderedImage:
    """Rendered image bytes and metadata."""

    kind: str
    png_bytes: bytes
    width: int
    height: int
    scale_factor: float
    bbox_norm: Optional[list[float]] = None


class EvidenceService:
    """Render PDF crops to PNG and generate preview/quadrants/ROI."""

    def __init__(self, cache_manager: Optional[RenderCacheManager] = None) -> None:
        """
        Initialize EvidenceService.
        
        Args:
            cache_manager: Optional custom cache manager (uses global by default)
        """
        self.cache = cache_manager or get_render_cache()

    def _compute_content_hash(self, pdf_bytes: bytes) -> str:
        """Compute SHA256 hash of PDF content as fallback version."""
        return hashlib.sha256(pdf_bytes).hexdigest()[:16]

    def render_pdf_page(
        self,
        pdf_bytes: bytes,
        *,
        source_id: str,
        source_version: Optional[str] = None,
        page: int = 0,
        dpi: int = 150,
    ) -> Image.Image:
        """
        Render a PDF page to PIL Image with LRU/versioned caching.
        
        Args:
            pdf_bytes: PDF file bytes
            source_id: Source identifier (r2_key or URL)
            source_version: Version of source (etag/last_modified), computed if None
            page: Page number to render
            dpi: DPI for rendering
            
        Returns:
            PIL Image of the rendered page
        """
        # Use content hash if no version provided
        if source_version is None:
            source_version = self._compute_content_hash(pdf_bytes)
        
        # Try cache first
        cached = self.cache.get(source_id, source_version, page, dpi)
        if cached is not None:
            logger.debug(f"Cache hit: {source_id}:{page}@{dpi}")
            return Image.open(BytesIO(cached)).convert("RGB")

        # Render PDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page_obj = doc.load_page(page)
            zoom = dpi / 72.0
            pix = page_obj.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # Cache the result
            png_bytes = self._to_png_bytes(img)
            self.cache.put(source_id, source_version, page, dpi, png_bytes)
            logger.debug(f"Cache miss, stored: {source_id}:{page}@{dpi}")
            
            return img
        finally:
            doc.close()
    
    def render_pdf_page_legacy(
        self,
        pdf_bytes: bytes,
        *,
        cache_key: str,
        page: int = 0,
        dpi: int = 150,
    ) -> Image.Image:
        """
        Legacy render method for backward compatibility.
        Uses cache_key as both source_id and computes version from content.
        """
        return self.render_pdf_page(
            pdf_bytes,
            source_id=cache_key,
            source_version=None,  # Will compute hash
            page=page,
            dpi=dpi,
        )

    def _scale_to_max_side(self, img: Image.Image, max_side: int) -> tuple[Image.Image, float]:
        w, h = img.size
        max_dim = max(w, h)
        if max_dim <= max_side:
            return img, 1.0
        scale = max_dim / float(max_side)
        new_w = max(1, int(w / scale))
        new_h = max(1, int(h / scale))
        resample = getattr(Image, "Resampling", Image).LANCZOS
        resized = img.resize((new_w, new_h), resample=resample)
        return resized, scale

    def build_preview_and_quadrants(
        self,
        pdf_bytes: bytes,
        *,
        source_id: str,
        source_version: Optional[str] = None,
        page: int = 0,
        dpi: int = 150,
    ) -> list[RenderedImage]:
        """
        Generate preview and optional quadrants from a PDF crop.
        
        Args:
            pdf_bytes: PDF file bytes
            source_id: Source identifier (r2_key or URL)
            source_version: Version of source (etag/last_modified)
            page: Page number to render
            dpi: DPI for rendering
            
        Returns:
            List of RenderedImage (overview + optional quadrants)
        """
        base_img = self.render_pdf_page(
            pdf_bytes,
            source_id=source_id,
            source_version=source_version,
            page=page,
            dpi=dpi,
        )
        w, h = base_img.size

        preview_img, scale_factor = self._scale_to_max_side(base_img, settings.preview_max_side)
        preview_bytes = self._to_png_bytes(preview_img)
        results = [
            RenderedImage(
                kind="overview",
                png_bytes=preview_bytes,
                width=preview_img.size[0],
                height=preview_img.size[1],
                scale_factor=scale_factor,
                bbox_norm=None,
            )
        ]

        if scale_factor > settings.auto_quadrants_threshold:
            quadrants = [
                ([0.0, 0.0, 0.55, 0.55], "quadrant"),
                ([0.45, 0.0, 1.0, 0.55], "quadrant"),
                ([0.0, 0.45, 0.55, 1.0], "quadrant"),
                ([0.45, 0.45, 1.0, 1.0], "quadrant"),
            ]
            for bbox_norm, kind in quadrants:
                crop = self._crop_norm(base_img, bbox_norm)
                crop_img, crop_scale = self._scale_to_max_side(crop, settings.zoom_preview_max_side)
                crop_bytes = self._to_png_bytes(crop_img)
                results.append(
                    RenderedImage(
                        kind=kind,
                        png_bytes=crop_bytes,
                        width=crop_img.size[0],
                        height=crop_img.size[1],
                        scale_factor=crop_scale,
                        bbox_norm=bbox_norm,
                    )
                )
        return results

    def build_roi(
        self,
        pdf_bytes: bytes,
        *,
        source_id: str,
        source_version: Optional[str] = None,
        bbox_norm: Iterable[float],
        page: int = 0,
        dpi: int = 300,
    ) -> RenderedImage:
        """
        Render ROI from PDF at requested DPI and return PNG bytes.
        
        Args:
            pdf_bytes: PDF file bytes
            source_id: Source identifier (r2_key or URL)
            source_version: Version of source (etag/last_modified)
            bbox_norm: Normalized bounding box [x1, y1, x2, y2]
            page: Page number to render
            dpi: DPI for rendering
            
        Returns:
            RenderedImage with ROI PNG
        """
        bbox_tuple: Tuple[float, float, float, float] = tuple(bbox_norm)[:4]  # type: ignore
        
        # Check ROI cache first
        cached_roi = self.cache.get(source_id, source_version or self._compute_content_hash(pdf_bytes), page, dpi, bbox_tuple)
        if cached_roi is not None:
            img = Image.open(BytesIO(cached_roi)).convert("RGB")
            return RenderedImage(
                kind="roi",
                png_bytes=cached_roi,
                width=img.size[0],
                height=img.size[1],
                scale_factor=1.0,
                bbox_norm=list(bbox_norm),
            )
        
        # Compute version if not provided (for caching)
        if source_version is None:
            source_version = self._compute_content_hash(pdf_bytes)
        
        base_img = self.render_pdf_page(
            pdf_bytes,
            source_id=source_id,
            source_version=source_version,
            page=page,
            dpi=dpi,
        )
        crop = self._crop_norm(base_img, list(bbox_norm))
        crop_img, crop_scale = self._scale_to_max_side(crop, settings.zoom_preview_max_side)
        crop_bytes = self._to_png_bytes(crop_img)
        
        # Cache the ROI
        self.cache.put(source_id, source_version, page, dpi, crop_bytes, bbox_tuple)
        
        return RenderedImage(
            kind="roi",
            png_bytes=crop_bytes,
            width=crop_img.size[0],
            height=crop_img.size[1],
            scale_factor=crop_scale,
            bbox_norm=list(bbox_norm),
        )

    def _crop_norm(self, img: Image.Image, bbox_norm: list[float]) -> Image.Image:
        x1, y1, x2, y2 = bbox_norm
        w, h = img.size
        x1 = max(0.0, min(1.0, x1))
        y1 = max(0.0, min(1.0, y1))
        x2 = max(0.0, min(1.0, x2))
        y2 = max(0.0, min(1.0, y2))
        if x2 <= x1 or y2 <= y1:
            raise ValueError("Invalid bbox_norm for ROI")
        left = int(x1 * w)
        top = int(y1 * h)
        right = int(x2 * w)
        bottom = int(y2 * h)
        return img.crop((left, top, right, bottom))

    def _to_png_bytes(self, img: Image.Image) -> bytes:
        from io import BytesIO

        output = BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

