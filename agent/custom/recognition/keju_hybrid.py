import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from maa.context import Context
from maa.custom_recognition import CustomRecognition

from utils import logger

from .keju_answer_core import (
    VALID_CHOICES,
    build_verification_prompt,
    local_fallback,
    parse_model_answer,
    scale_roi,
)
from .searchAnswer import search_candidates


if os.environ.get("MAA_DIRECT_RESOURCE") != "1":
    from maa.agent.agent_server import AgentServer
else:
    AgentServer = None


def register_custom_recognition(name):
    if os.environ.get("MAA_DIRECT_RESOURCE") == "1":
        return lambda recognition: recognition
    return AgentServer.custom_recognition(name)


QUESTION_ROI = [511, 186, 602, 107]
OPTION_ROIS = {
    "A": [509, 306, 269, 91],
    "B": [825, 304, 270, 95],
    "C": [506, 408, 268, 88],
    "D": [831, 404, 265, 96],
}
MAC_REFERENCE_SIZE = (923, 720)
MAC_QUESTION_ROI = [370, 245, 430, 55]
MAC_OPTION_ROIS = {
    "A": [340, 315, 225, 80],
    "B": [570, 315, 230, 80],
    "C": [340, 395, 225, 75],
    "D": [570, 395, 230, 75],
}


def keju_rois(image):
    image_height, image_width = image.shape[:2]
    if image_width / image_height < 1.5:
        reference_size = MAC_REFERENCE_SIZE
        question_roi = MAC_QUESTION_ROI
        option_rois = MAC_OPTION_ROIS
    else:
        reference_size = (1280, 720)
        question_roi = QUESTION_ROI
        option_rois = OPTION_ROIS
    return (
        scale_roi(question_roi, image_width, image_height, reference_size),
        {
            choice: scale_roi(roi, image_width, image_height, reference_size)
            for choice, roi in option_rois.items()
        },
    )


def _sorted_ocr_text(results, row_height_threshold=20):
    items = [item for item in results if getattr(item, "text", "")]
    items.sort(key=lambda item: (round(item.box[1] / row_height_threshold), item.box[0]))
    return "".join(item.text for item in items)


def _ocr_region(context, image, name, scaled_roi):
    detail = context.run_recognition(
        name,
        image,
        pipeline_override={
            name: {"roi": scaled_roi, "expected": [""], "recognition": "OCR"}
        },
    )
    if not detail or not detail.hit:
        return ""
    return _sorted_ocr_text(detail.all_results)


def _ocr_region_with_box(context, image, name, scaled_roi):
    detail = context.run_recognition(
        name,
        image,
        pipeline_override={
            name: {"roi": scaled_roi, "expected": [""], "recognition": "OCR"}
        },
    )
    if not detail or not detail.hit:
        return "", None
    return _sorted_ocr_text(detail.all_results), list(detail.box)


def _request_model(url, api_key, model, prompt, timeout):
    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的中文单选题答题助手，必须按要求返回 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 80,
            "stream": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]["content"]


def _request_codex_model(prompt, timeout):
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "enum": list(VALID_CHOICES)},
            "confidence": {"type": "number"},
            "used_question_bank": {"type": "boolean"},
        },
        "required": ["answer", "confidence", "used_question_bank"],
        "additionalProperties": False,
    }
    with tempfile.TemporaryDirectory(prefix="maa-keju-codex-") as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = temp_path / "answer-schema.json"
        output_path = temp_path / "answer.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        subprocess.run(
            [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-c",
                'model_reasoning_effort="low"',
                "--cd",
                temp_dir,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "你只能回答下面这道公开的游戏单选题，不得调用工具或执行命令。\n" + prompt,
            ],
            check=True,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return output_path.read_text(encoding="utf-8")


@register_custom_recognition("KejuHybridAnswer")
class KejuHybridAnswer(CustomRecognition):
    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        started_at = time.monotonic()
        image = context.tasker.controller.post_screencap().wait().get()
        question_roi, option_rois = keju_rois(image)
        question = _ocr_region(context, image, "科举乡试题目-混合", question_roi)
        if not question:
            self.last_status = "no_question"
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="未识别到科举题目"
            )

        options = {}
        option_boxes = {}
        for choice, roi in option_rois.items():
            text, box = _ocr_region_with_box(
                context, image, f"科举乡试答案{choice}-混合", roi
            )
            options[choice] = re.sub(
                rf"^{choice}\s*[.、:：]?\s*", "", text, flags=re.IGNORECASE
            )
            option_boxes[choice] = box
        valid_options = {choice: text for choice, text in options.items() if text}
        if len(valid_options) < 2:
            raise RuntimeError(f"科举选项识别不足，已识别：{valid_options}")

        candidates = search_candidates(question, limit=3)
        node_data = context.get_node_data("活动-科举乡试-开始答题混合")
        attach = node_data.get("attach", {})
        api_key = os.environ.get("MAA_KEJU_API_KEY", "").strip() or str(
            attach.get("apikey", "")
        ).strip()
        url = os.environ.get("MAA_KEJU_URL", "").strip() or str(
            attach.get("url", "")
        ).strip()
        model = os.environ.get("MAA_KEJU_MODEL", "").strip() or str(
            attach.get("model", "")
        ).strip()
        timeout = float(
            os.environ.get("MAA_KEJU_TIMEOUT", attach.get("timeout", 12))
        )
        fallback_threshold = int(attach.get("fallback_threshold", 92))

        model_answer = None
        model_error = None
        model_source = None
        if api_key and url and model:
            try:
                prompt = build_verification_prompt(question, valid_options, candidates)
                model_content = _request_model(url, api_key, model, prompt, timeout)
                model_answer = parse_model_answer(model_content, tuple(valid_options))
                model_source = "openai_compatible_model"
                if model_answer is None:
                    model_error = "模型回复中没有有效选项"
            except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
                model_error = f"{type(exc).__name__}: {exc}"
        elif os.environ.get("MAA_KEJU_USE_CODEX") == "1":
            try:
                prompt = build_verification_prompt(question, valid_options, candidates)
                codex_timeout = float(os.environ.get("MAA_KEJU_CODEX_TIMEOUT", "120"))
                model_content = _request_codex_model(prompt, max(timeout, codex_timeout))
                model_answer = parse_model_answer(model_content, tuple(valid_options))
                model_source = "codex_model"
                if model_answer is None:
                    model_error = "Codex 回复中没有有效选项"
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                model_error = f"{type(exc).__name__}: {exc}"
        else:
            model_error = "远端模型配置不完整"

        final_answer = model_answer or local_fallback(
            candidates,
            valid_options,
            question_threshold=fallback_threshold,
        )
        source = model_source if model_answer else "question_bank_fallback"
        if final_answer not in VALID_CHOICES or final_answer not in valid_options:
            self.last_status = "unreliable"
            self.last_question = question
            self.last_options = valid_options
            best_similarity = candidates[0]["similarity"] if candidates else 0
            raise RuntimeError(
                "科举答题无法可靠决策，已停止避免盲选："
                f"model_error={model_error}, best_similarity={best_similarity}"
            )

        target = option_boxes.get(final_answer) or option_rois[final_answer]
        center_x = target[0] + target[2] // 2
        center_y = target[1] + target[3] // 2
        if os.environ.get("MAA_KEJU_RECOGNIZE_ONLY") == "1":
            self.last_status = "ready"
            self.last_question = question
            self.last_options = valid_options
            self.last_answer = final_answer
            self.last_source = source
            self.last_click = (center_x, center_y)
            self.last_image_size = (image.shape[1], image.shape[0])
        else:
            click_result = context.tasker.controller.post_click(center_x, center_y).wait()
            if not click_result.succeeded:
                self.last_status = "click_failed"
                raise RuntimeError(f"科举答案点击失败：{final_answer}")
            self.last_status = "clicked"

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        candidate_log = [
            {
                "question": item["question"],
                "similarity": item["similarity"],
                "answers": item["answers"],
            }
            for item in candidates
        ]
        log_payload = json.dumps(
                {
                    "question": question,
                    "options": valid_options,
                    "candidates": candidate_log,
                    "answer": final_answer,
                    "source": source,
                    "model_error": model_error,
                    "elapsed_ms": elapsed_ms,
                },
                ensure_ascii=False,
            )
        logger.info(f"科举混合答题：{log_payload}")
        return CustomRecognition.AnalyzeResult(
            box=tuple(target), detail=f"混合答题完成:{final_answer}"
        )
