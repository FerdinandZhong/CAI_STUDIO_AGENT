import json
import re
from typing import Any, Dict, List

import requests
from crewai.tools import tool

from .common import encode_image_to_data_url


def extract_paddle_lines(paddle_result: Any) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []

    def add_line(text: Any, conf: Any = None):
        text_str = str(text).strip() if text is not None else ""
        if text_str:
            lines.append(
                {
                    "text": text_str,
                    "confidence": float(conf) if isinstance(conf, (int, float)) else None,
                }
            )

    if isinstance(paddle_result, dict) and isinstance(paddle_result.get("data"), list):
        for item in paddle_result["data"]:
            if not isinstance(item, dict):
                continue
            detections = item.get("text_detections")
            if not isinstance(detections, list):
                continue
            for detection in detections:
                if not isinstance(detection, dict):
                    continue
                pred = detection.get("text_prediction", {})
                if isinstance(pred, dict):
                    add_line(pred.get("text"), pred.get("confidence"))
    elif isinstance(paddle_result, list):
        for item in paddle_result:
            if isinstance(item, dict) and "text" in item:
                add_line(item.get("text"), item.get("confidence"))
            elif isinstance(item, list) and len(item) >= 2:
                candidate = item[1]
                if isinstance(candidate, (list, tuple)) and len(candidate) >= 1:
                    conf = candidate[1] if len(candidate) > 1 else None
                    add_line(candidate[0], conf)

    return lines


def extract_paddle_candidates(ocr_text: str) -> Dict[str, Dict[str, Any]]:
    candidates: Dict[str, Dict[str, Any]] = {}

    def add_candidate(field: str, value: Any, confidence: float):
        if value is None or str(value).strip() == "":
            return
        prev = candidates.get(field)
        if prev is None or confidence > prev["confidence"]:
            candidates[field] = {
                "value": value,
                "confidence": round(float(confidence), 3),
                "source": "paddle_regex",
            }

    amount_re = r"[-+]?\$?\s?\d[\d,]*\.?\d{0,3}"

    def normalize_amount(raw_value: str) -> str:
        return re.sub(r"\s+", "", str(raw_value).replace("$", "").replace(",", "").strip())

    for pattern in [
        r"(?:invoice|inv)\s*(?:#|number|no\.?)?\s*[:\-]?\s*([A-Za-z0-9\-/]+)",
        r"(?:receipt)\s*(?:#|number|no\.?)?\s*[:\-]?\s*([A-Za-z0-9\-/]+)",
    ]:
        m = re.search(pattern, ocr_text, flags=re.IGNORECASE)
        if m:
            add_candidate("invoice_number", m.group(1), 0.90)
            break

    date_match = re.search(
        r"(\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        ocr_text,
    )
    if date_match:
        add_candidate("date", date_match.group(1), 0.85)

    currency_match = re.search(r"(USD|EUR|GBP|JPY|CNY|KRW|IDR)", ocr_text, flags=re.IGNORECASE)
    if currency_match:
        add_candidate("currency", currency_match.group(1).upper(), 0.90)
    elif re.search(r"[$€£¥]", ocr_text):
        symbol = re.search(r"[$€£¥]", ocr_text).group(0)
        symbol_map = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
        add_candidate("currency", symbol_map.get(symbol, symbol), 0.78)

    def extract_amount_by_label(labels: List[str], field_name: str):
        label_pattern = "|".join(labels)
        pattern = rf"(?:{label_pattern})\s*[:\-]?\s*({amount_re})"
        matches = re.findall(pattern, ocr_text, flags=re.IGNORECASE)
        if matches:
            add_candidate(field_name, normalize_amount(matches[-1]), 0.92)

    extract_amount_by_label(["grand\\s*total", "total\\s*due", "amount\\s*due", "total"], "total")
    extract_amount_by_label(["sub\\s*total", "subtotal"], "subtotal")
    extract_amount_by_label(["tax", "vat", "gst"], "tax")

    money_like = re.findall(amount_re, ocr_text)
    if "total" not in candidates and money_like:
        add_candidate("total", normalize_amount(money_like[-1]), 0.55)

    if len(re.findall(r"\d[\d,]*\.\d{2,3}", ocr_text.lower())) >= 3:
        add_candidate("items", "line_items_present", 0.72)

    return candidates


def create_paddle_retrieve_tool(paddle_url: str, jwt_token: str):
    @tool("paddle_retrieve_tool")
    def paddle_retrieve_tool(image_path: str) -> str:
        """
        Run PaddleOCR on one image and return JSON with lines and candidates.
        Input: image_path
        """
        image_data_url = encode_image_to_data_url(image_path)
        payload = {"input": [{"type": "image_url", "url": image_data_url}]}
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(paddle_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        lines = extract_paddle_lines(result)
        ocr_text = "\n".join(x["text"] for x in lines)
        candidates = extract_paddle_candidates(ocr_text)
        return json.dumps(
            {
                "ocr_line_count": len(lines),
                "ocr_text": ocr_text,
                "lines": lines,
                "candidates": candidates,
            },
            ensure_ascii=False,
        )

    return paddle_retrieve_tool

