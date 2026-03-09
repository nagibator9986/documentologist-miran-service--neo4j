import gc
import io
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import fitz
    import torch
    from PIL import Image
    SURYA_AVAILABLE = True
    # Use all available CPU cores for PyTorch operations.
    # This speeds up matrix multiplications in OCR models significantly.
    _cpu_count = os.cpu_count() or 4
    torch.set_num_threads(_cpu_count)
    torch.set_num_interop_threads(max(1, _cpu_count // 2))
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

        # --- ЗАКОММЕНТИРОВАНО ДЛЯ ЭКОНОМИИ RAM ---
        self.layout_predictor = None
        # try:
        #     from surya.layout import LayoutPredictor
        #     self.layout_predictor = LayoutPredictor(device="cpu")
        #     logger.info("  ✓ LayoutPredictor")
        # except Exception as e:
        #     self.layout_predictor = None

        self.table_predictor = None
        # try:
        #     from surya.table_rec import TableRecPredictor
        #     self.table_predictor = TableRecPredictor(device="cpu")
        #     logger.info("  ✓ TableRecPredictor")
        # except Exception as e:
        #     self.table_predictor = None

        logger.info("✅ Surya models ready (OCR Only).")

    @staticmethod
    def pdf_to_images(pdf_bytes, dpi=120):
        # DPI=120 снижает потребление памяти в 3-4 раза по сравнению с DPI=200
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
        doc.close()
        return images

    def process_document(self, file_bytes, filename):
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            images = self.pdf_to_images(file_bytes)
        elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            images = [Image.open(io.BytesIO(file_bytes)).convert("RGB")]
        else:
            raise ValueError(f"Unsupported: {ext}")

        page_count = len(images)
        logger.info(f"Processing {page_count} page(s) from '{filename}'")

        ocr_results = None
        if self.rec_predictor:
            try:
                logger.info("Running OCR (page-by-page to limit peak memory)…")
                ocr_results = []
                for idx, img in enumerate(images):
                    logger.info(f"  Page {idx + 1}/{page_count}…")
                    page_result = self.rec_predictor(
                        [img],
                        det_predictor=self.det_predictor,
                        sort_lines=True,
                    )
                    ocr_results.extend(page_result)
                    gc.collect()  # release page tensors before next page
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

_ocr_service = None

def get_ocr_service():
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = SuryaOCRService()
    return _ocr_service
