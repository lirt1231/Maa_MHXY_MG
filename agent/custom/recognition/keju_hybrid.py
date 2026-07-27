import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from maa.context import Context
from maa.custom_recognition import CustomRecognition

from utils import logger

from .keju_answer_core import (
    VALID_CHOICES,
    build_verification_prompt,
    knowledge_fallback,
    local_fallback,
    looks_like_answer_result,
    parse_model_answer,
    scale_roi,
)
from .searchAnswer import search_candidates
from .keju_knowledge import search_knowledge


if os.environ.get("MAA_DIRECT_RESOURCE") != "1":
    from maa.agent.agent_server import AgentServer
else:
    AgentServer = None


def register_custom_recognition(name):
    if os.environ.get("MAA_DIRECT_RESOURCE") == "1":
        return lambda recognition: recognition
    return AgentServer.custom_recognition(name)


# 固定覆盖双行题干，底部仍停在选项区域之前。
QUESTION_ROI = [490, 170, 650, 125]
OPTION_ROIS = {
    "A": [509, 306, 269, 91],
    "B": [825, 304, 270, 95],
    "C": [506, 408, 268, 88],
    "D": [831, 404, 265, 96],
}
MAC_REFERENCE_SIZE = (923, 720)
MAC_QUESTION_ROI = [335, 225, 470, 85]
MAC_OPTION_ROIS = {
    "A": [340, 315, 225, 80],
    "B": [570, 315, 230, 80],
    "C": [340, 395, 225, 75],
    "D": [570, 395, 230, 75],
}
WAITING_CAPTURE_PATH = (
    Path(__file__).resolve().parent.parents[2]
    / "debug"
    / "custom"
    / "keju_waiting_capture.jsonl"
)
TIMING_STAGES = (
    "screenshot",
    "ocr",
    "local_question_bank",
    "knowledge_base",
    "model_request",
    "parse_decision",
)


def _build_timing_summary(status, stage_stats, total_ms):
    """Build a stable, configuration-free payload suitable for logs."""
    return {
        "status": status,
        "total_ms": max(0, int(total_ms)),
        "stages": {
            stage: {
                "elapsed_ms": max(
                    0, int(stage_stats.get(stage, {}).get("elapsed_ms", 0))
                ),
                "calls": max(0, int(stage_stats.get(stage, {}).get("calls", 0))),
            }
            for stage in TIMING_STAGES
        },
    }


class _StageTimings:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._started_at = clock()
        self._stats = {}

    @contextmanager
    def track(self, stage):
        started_at = self._clock()
        try:
            yield
        finally:
            elapsed_ms = max(0, round((self._clock() - started_at) * 1000))
            stats = self._stats.setdefault(stage, {"elapsed_ms": 0, "calls": 0})
            stats["elapsed_ms"] += elapsed_ms
            stats["calls"] += 1

    def summary(self, status):
        total_ms = round((self._clock() - self._started_at) * 1000)
        return _build_timing_summary(status, self._stats, total_ms)


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


def _recognition_image(context, argv):
    """Use the runner-supplied frame, with a fallback for legacy pipelines."""
    image = getattr(argv, "image", None)
    if image is None or getattr(image, "size", 0) == 0:
        image = context.tasker.controller.post_screencap().wait().get()
    if image is None or getattr(image, "size", 0) == 0:
        raise RuntimeError("科举识别没有可用截图")
    return image


def _clean_option_text(choice, text):
    text = re.sub(rf"^{choice}\s*[.、:：]?\s*", "", text, flags=re.IGNORECASE)
    # 当前会试界面会把 B 选项左侧的【中华历史】等分类标签一起 OCR。
    text = re.sub(r"^[【\[（(][^】\]）)]{1,16}[】\]）)]\s*[A-D]?\s*", "", text)
    return text.strip()


def _record_waiting_capture(recognizer, round_number, question, options, reason):
    """Persist the best OCR capture for each waiting-stage round."""
    valid_options = {choice: text for choice, text in options.items() if text}
    normalized_question = re.sub(r"\s+", "", question)
    quality = (len(valid_options), len(normalized_question))
    capture_quality = getattr(recognizer, "_waiting_capture_quality", {})
    previous_quality = capture_quality.get(round_number, (-1, -1))
    if quality <= previous_quality:
        return
    capture_quality[round_number] = quality
    recognizer._waiting_capture_quality = capture_quality

    payload = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "round": round_number,
        "reason": reason,
        "question": question,
        "options": valid_options,
    }
    log_payload = json.dumps(payload, ensure_ascii=False)
    logger.info(f"科举等待记录：{log_payload}")
    try:
        WAITING_CAPTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WAITING_CAPTURE_PATH.open("a", encoding="utf-8") as capture_file:
            capture_file.write(log_payload + "\n")
    except OSError as exc:
        logger.warning(f"科举等待记录写入失败：{exc}")


def _request_model(url, api_key, model, prompt, timeout, extra_body=None):
    request_body = {
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
    }
    request_body.update(extra_body or {})
    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json=request_body,
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
        timings = _StageTimings()
        self.last_status = "running"
        try:
            return self._analyze(context, argv, timings)
        finally:
            status = self.last_status
            if status == "running":
                status = "error"
                self.last_status = status
            summary = timings.summary(status)
            self.last_timings = summary
            logger.info(
                "科举识别耗时：" + json.dumps(summary, ensure_ascii=False, sort_keys=True)
            )

    def _analyze(self, context, argv, timings):
        with timings.track("screenshot"):
            image = _recognition_image(context, argv)
        question_roi, option_rois = keju_rois(image)
        with timings.track("ocr"):
            question = _ocr_region(
                context, image, "科举乡试题目-混合", question_roi
            )
        if not question:
            self.last_status = "no_question"
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="未识别到科举题目"
            )
        # 倒计时/活动说明页的装饰文字会落入同一 ROI。只有明确识别到
        # “第 N/M 题”时才允许查库或调用模型，避免等待阶段浪费请求，
        # 更重要的是杜绝把倒计时数字误当成选项后点击。
        round_match = re.match(r"^第\s*(\d+)\s*/\s*\d+\s*题", question)
        if not round_match:
            self.last_status = "waiting"
            self.last_question = question
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="会试尚未出现正式题目"
            )
        round_number = int(round_match.group(1))
        record_only_waiting = (
            os.environ.get("MAA_KEJU_REQUIRE_ROUND_ONE") == "1"
            and round_number != 1
        )

        options = {}
        option_boxes = {}
        with timings.track("ocr"):
            for choice, roi in option_rois.items():
                text, box = _ocr_region_with_box(
                    context, image, f"科举乡试答案{choice}-混合", roi
                )
                options[choice] = _clean_option_text(choice, text)
                option_boxes[choice] = box
        valid_options = {choice: text for choice, text in options.items() if text}
        if len(valid_options) < 2:
            if record_only_waiting:
                _record_waiting_capture(
                    self,
                    round_number,
                    question,
                    valid_options,
                    "等待第1题期间的部分OCR记录",
                )
                self.last_status = "waiting"
                self.last_question = question
                self.last_options = valid_options
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 0, 0), detail="等待第1题，已记录部分题面"
                )
            self.last_status = "unreliable"
            raise RuntimeError(f"科举选项识别不足，已识别：{valid_options}")
        is_answer_result = looks_like_answer_result(question, valid_options)
        if record_only_waiting:
            _record_waiting_capture(
                self,
                round_number,
                question,
                valid_options,
                "等待第1题期间捕获结果页" if is_answer_result else "等待第1题期间捕获答题页",
            )
            self.last_status = "waiting"
            self.last_question = question
            self.last_options = valid_options
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="等待正式会试从第1题开始，题面已记录"
            )
        if is_answer_result:
            _record_waiting_capture(
                self,
                round_number,
                question,
                valid_options,
                "答题结果或课堂复习页",
            )
            self.last_status = "waiting"
            self.last_question = question
            self.last_options = valid_options
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="当前为答题结果或课堂复习页"
            )

        # 一道题在提交后仍可能保留数秒活动态。去重必须发生在查库和
        # 模型请求之前，否则虽然外层不会重复点击，仍会浪费一次模型调用。
        round_answer_cache = getattr(self, "_round_answer_cache", {})
        cached_decision = round_answer_cache.get(round_number)
        if cached_decision:
            cached_answer, cached_source = cached_decision
            if cached_answer in valid_options:
                target = option_boxes.get(cached_answer) or option_rois[cached_answer]
                center_x = target[0] + target[2] // 2
                center_y = target[1] + target[3] // 2
                self.last_status = "ready"
                self.last_question = question
                self.last_options = valid_options
                self.last_answer = cached_answer
                self.last_source = cached_source
                self.last_click = (center_x, center_y)
                self.last_image_size = (image.shape[1], image.shape[0])
                logger.info(
                    f"科举同轮复用：round={round_number}, answer={cached_answer}, "
                    f"source={cached_source}"
                )
                return CustomRecognition.AnalyzeResult(
                    box=tuple(target), detail=f"复用本轮答案:{cached_answer}"
                )

        with timings.track("local_question_bank"):
            candidates = search_candidates(question, limit=3)
        with timings.track("knowledge_base"):
            knowledge_candidates = search_knowledge(question, limit=4)
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
        extra_body_raw = os.environ.get("MAA_KEJU_MODEL_EXTRA_BODY", "").strip()
        if extra_body_raw:
            try:
                model_extra_body = json.loads(extra_body_raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"模型 extra_body 不是有效 JSON: {exc}") from exc
        else:
            model_extra_body = attach.get("model_extra_body", {}) or {}
        fallback_threshold = int(attach.get("fallback_threshold", 92))
        knowledge_threshold = int(
            os.environ.get(
                "MAA_KEJU_KNOWLEDGE_THRESHOLD",
                attach.get("knowledge_threshold", 90),
            )
        )

        with timings.track("parse_decision"):
            local_answer = local_fallback(
                candidates,
                valid_options,
                question_threshold=fallback_threshold,
            )
            trusted_local_answer = (
                local_answer
                if candidates and candidates[0]["similarity"] == 100
                else None
            )
            trusted_knowledge_answer = None
            if not trusted_local_answer:
                trusted_knowledge_answer = knowledge_fallback(
                    knowledge_candidates,
                    valid_options,
                    knowledge_threshold=knowledge_threshold,
                )

        model_answer = None
        model_error = None
        model_source = None
        if trusted_local_answer:
            model_error = "题库精确命中，跳过远端模型"
        elif trusted_knowledge_answer:
            model_error = "知识库高置信度命中，跳过远端模型"
        elif api_key and url and model:
            try:
                with timings.track("parse_decision"):
                    prompt = build_verification_prompt(
                        question,
                        valid_options,
                        candidates,
                        knowledge_candidates,
                    )
                with timings.track("model_request"):
                    model_content = _request_model(
                        url,
                        api_key,
                        model,
                        prompt,
                        timeout,
                        extra_body=model_extra_body,
                    )
                with timings.track("parse_decision"):
                    model_answer = parse_model_answer(
                        model_content, tuple(valid_options)
                    )
                model_source = "openai_compatible_model"
                if model_answer is None:
                    model_error = "模型回复中没有有效选项"
            except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
                model_error = f"{type(exc).__name__}: {exc}"
        elif os.environ.get("MAA_KEJU_USE_CODEX") == "1":
            try:
                with timings.track("parse_decision"):
                    prompt = build_verification_prompt(
                        question,
                        valid_options,
                        candidates,
                        knowledge_candidates,
                    )
                codex_timeout = float(os.environ.get("MAA_KEJU_CODEX_TIMEOUT", "120"))
                with timings.track("model_request"):
                    model_content = _request_codex_model(
                        prompt, max(timeout, codex_timeout)
                    )
                with timings.track("parse_decision"):
                    model_answer = parse_model_answer(
                        model_content, tuple(valid_options)
                    )
                model_source = "codex_model"
                if model_answer is None:
                    model_error = "Codex 回复中没有有效选项"
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                model_error = f"{type(exc).__name__}: {exc}"
        else:
            model_error = "远端模型配置不完整"

        with timings.track("parse_decision"):
            final_answer = (
                trusted_local_answer
                or trusted_knowledge_answer
                or model_answer
                or local_answer
            )
            source = (
                "question_bank_exact"
                if trusted_local_answer
                else "knowledge_base"
                if trusted_knowledge_answer
                else model_source
                if model_answer
                else "question_bank_fallback"
            )
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

        round_answer_cache = getattr(self, "_round_answer_cache", {})
        round_answer_cache[round_number] = (final_answer, source)
        self._round_answer_cache = round_answer_cache

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
                    "knowledge_candidates": [
                        {
                            "title": item["title"],
                            "similarity": item["similarity"],
                            "answer": item["answer"],
                            "source": item["source_name"],
                        }
                        for item in knowledge_candidates
                    ],
                    "answer": final_answer,
                    "source": source,
                    "model_error": model_error,
                },
                ensure_ascii=False,
            )
        logger.info(f"科举混合答题：{log_payload}")
        return CustomRecognition.AnalyzeResult(
            box=tuple(target), detail=f"混合答题完成:{final_answer}"
        )
