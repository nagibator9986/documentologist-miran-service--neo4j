import gc
import io
import json
import os
import threading
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

try:
    import fitz
    import torch
    from PIL import Image
    SURYA_AVAILABLE = True
    # Single-threaded PyTorch: eliminates parallel temporary tensor buffers.
    # With 4 threads the inference RSS spikes to 3+ GB on a 7.6 GB machine
    # alongside all other services, causing OOM kill (exit 137).
    # 1 thread keeps peak RSS predictable at ~1.5–2 GB; OCR is I/O-bound
    # on CPU anyway so the throughput difference is minimal.
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
except ImportError:
    SURYA_AVAILABLE = False
    logger.warning("PyMuPDF/Pillow not installed.")


class SuryaOCRService:
    def __init__(self):
        if not SURYA_AVAILABLE:
            raise RuntimeError("Required packages not installed.")

        logger.info("Loading Surya OCR models (Memory Optimized Mode)…")

        from surya.detection import DetectionPredictor
        self.det_predictor = DetectionPredictor(device="cpu")
        logger.info("  ✓ DetectionPredictor")

        try:
            from surya.foundation import FoundationPredictor
            self.foundation_predictor = FoundationPredictor(device="cpu")
            logger.info("  ✓ FoundationPredictor")
        except Exception as e:
            logger.warning(f"  ✗ FoundationPredictor: {e}")
            self.foundation_predictor = None

        try:
            from surya.recognition import RecognitionPredictor
            if self.foundation_predictor:
                self.rec_predictor = RecognitionPredictor(self.foundation_predictor)
            else:
                self.rec_predictor = RecognitionPredictor()
            logger.info("  ✓ RecognitionPredictor")
        except Exception as e:
            logger.error(f"  ✗ RecognitionPredictor: {e}")
            self.rec_predictor = None

        # Layout and Table predictors are optional — disabled by default to save
        # ~1.5 GB RAM.  Enable via OCR_ENABLE_LAYOUT=true / OCR_ENABLE_TABLE=true.
        # Useful for documents with multi-column layouts or complex tables.
        self.layout_predictor = None
        if os.getenv("OCR_ENABLE_LAYOUT", "false").lower() == "true":
            try:
                from surya.layout import LayoutPredictor
                self.layout_predictor = LayoutPredictor(device="cpu")
                logger.info("  ✓ LayoutPredictor (enabled via OCR_ENABLE_LAYOUT)")
            except Exception as e:
                logger.warning("  ✗ LayoutPredictor: %s", e)
        else:
            logger.info("  ⊘ LayoutPredictor skipped (OCR_ENABLE_LAYOUT=false)")

        self.table_predictor = None
        if os.getenv("OCR_ENABLE_TABLE", "false").lower() == "true":
            try:
                from surya.table_rec import TableRecPredictor
                self.table_predictor = TableRecPredictor(device="cpu")
                logger.info("  ✓ TableRecPredictor (enabled via OCR_ENABLE_TABLE)")
            except Exception as e:
                logger.warning("  ✗ TableRecPredictor: %s", e)
        else:
            logger.info("  ⊘ TableRecPredictor skipped (OCR_ENABLE_TABLE=false)")

        mode = "Full" if (self.layout_predictor or self.table_predictor) else "OCR Only"
        logger.info("✅ Surya models ready (%s).", mode)

    @staticmethod
    def _iter_pdf_images(pdf_bytes: bytes, dpi: int | None = None) -> Iterator["Image.Image"]:
        """Yield one PIL Image per PDF page, releasing each pixmap immediately.

        Using a generator instead of building a full list keeps peak memory
        proportional to a single page rather than the entire document.

        DPI is configurable via ``OCR_PDF_DPI`` env var (default: 150).
        Higher DPI improves accuracy on small text/tables at the cost of memory.
        """
        import os
        if dpi is None:
            raw_dpi = int(os.getenv("OCR_PDF_DPI", "150"))
            dpi = max(72, min(600, raw_dpi))  # clamp to safe range
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        try:
            for page in doc:
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                pix = None  # release pixmap buffer before yielding
                yield img
        finally:
            doc.close()

    def process_document(self, file_bytes: bytes, filename: str) -> dict:
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            page_source = self._iter_pdf_images(file_bytes)
            # Count pages without loading images (open doc once, close it)
            _doc = fitz.open(stream=file_bytes, filetype="pdf")
            page_count = len(_doc)
            _doc.close()
        elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            page_count = 1
            page_source = iter([img])
        else:
            raise ValueError(f"Неподдерживаемый формат файла: {ext}")

        logger.info(f"Processing {page_count} page(s) from '{filename}'")

        ocr_results = None
        if self.rec_predictor:
            try:
                logger.info("Running OCR (page-by-page to limit peak memory)…")
                ocr_results = []
                for idx, img in enumerate(page_source):
                    logger.info(f"  Page {idx + 1}/{page_count}…")
                    # inference_mode: disables autograd tape — saves ~30% RAM
                    # vs no_grad because it also skips version counter updates.
                    with torch.inference_mode():
                        page_result = self.rec_predictor(
                            [img],
                            det_predictor=self.det_predictor,
                            sort_lines=True,
                        )
                    ocr_results.extend(page_result)
                    del img          # release PIL image memory
                    gc.collect()     # release page tensors before next page
                logger.info(f"OCR done: {len(ocr_results)} pages")
            except Exception as e:
                logger.error(f"OCR failed: {e}")

        # --- ВЫЗОВЫ ЭТИХ МОДЕЛЕЙ ТАКЖЕ ОТКЛЮЧЕНЫ ---
        layout_results = None
        # if self.layout_predictor:
        #     try:
        #         layout_results = self.layout_predictor(images)
        #     except Exception as e:
        #         logger.warning(f"Layout failed: {e}")

        table_results = None
        # if self.table_predictor:
        #     try:
        #         table_results = self.table_predictor.batch_table_recognition(images)
        #     except Exception as e:
        #         logger.warning(f"Tables failed: {e}")

        return self._structure_output(filename, page_count, ocr_results, layout_results, table_results)

    def _structure_output(self, filename, page_count, ocr_results, layout_results, table_results):
        pages = []
        for page_idx in range(page_count):
            page_data = {"page_number": page_idx + 1, "blocks": [], "full_text": ""}

            if ocr_results and page_idx < len(ocr_results):
                ocr_page = ocr_results[page_idx]
                text_lines = []
                for line in getattr(ocr_page, "text_lines", []):
                    block = {"type": "text", "text": getattr(line, "text", ""), "confidence": round(getattr(line, "confidence", 0), 3)}
                    bbox = getattr(line, "bbox", None)
                    if bbox is not None:
                        block["bbox"] = bbox if isinstance(bbox, list) else list(bbox)
                    page_data["blocks"].append(block)
                    text_lines.append(block["text"])
                page_data["full_text"] = "\n".join(text_lines)

            if layout_results and page_idx < len(layout_results):
                layout_page = layout_results[page_idx]
                bboxes = getattr(layout_page, "bboxes", []) or getattr(layout_page, "layout_bboxes", [])
                layout_blocks = []
                for b in bboxes:
                    lb = {"label": getattr(b, "label", "unknown")}
                    bbox = getattr(b, "bbox", None)
                    if bbox: lb["bbox"] = bbox if isinstance(bbox, list) else list(bbox)
                    conf = getattr(b, "confidence", None)
                    if conf: lb["confidence"] = round(conf, 3)
                    layout_blocks.append(lb)
                if layout_blocks:
                    page_data["layout"] = layout_blocks

            if table_results and page_idx < len(table_results):
                table_page = table_results[page_idx]
                tables_list = getattr(table_page, "tables", [])
                if not tables_list and isinstance(table_page, list):
                    tables_list = table_page
                page_tables = []
                for t_idx, table in enumerate(tables_list):
                    md = self._table_to_markdown(table)
                    if md:
                        page_tables.append({"table_index": t_idx, "markdown": md})
                if page_tables:
                    page_data["tables"] = page_tables

            pages.append(page_data)

        full_text = "\n\n".join(p["full_text"] for p in pages if p["full_text"])
        return {"filename": filename, "page_count": page_count, "pages": pages, "full_text": full_text}

    @staticmethod
    def _table_to_markdown(table):
        try:
            cells = getattr(table, "cells", [])
            if not cells: return ""
            max_row = max(getattr(c, "row", 0) for c in cells) + 1
            max_col = max(getattr(c, "col", 0) for c in cells) + 1
            grid = [["" for _ in range(max_col)] for _ in range(max_row)]
            for cell in cells:
                grid[getattr(cell, "row", 0)][getattr(cell, "col", 0)] = getattr(cell, "text", "").strip()
            lines = []
            for r_idx, row in enumerate(grid):
                lines.append("| " + " | ".join(row) + " |")
                if r_idx == 0:
                    lines.append("| " + " | ".join("---" for _ in row) + " |")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Table→MD failed: {e}")
            return ""

_ocr_service: SuryaOCRService | None = None
_ocr_service_lock = threading.Lock()


def get_ocr_service() -> SuryaOCRService:
    """Return the module-level SuryaOCRService singleton.

    Thread-safe double-checked locking: the lock is only acquired once during
    initialization, so subsequent calls have zero synchronization overhead.
    Loading Surya models takes 10–30 seconds — concurrent init must be prevented.
    """
    global _ocr_service
    if _ocr_service is not None:
        return _ocr_service
    with _ocr_service_lock:
        if _ocr_service is None:
            _ocr_service = SuryaOCRService()
    return _ocr_service
