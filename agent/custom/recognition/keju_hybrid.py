import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import requests
from maa.context import Context
from maa.custom_recognition import CustomRecognition

from utils import logger

from .keju_answer_core import (
    VALID_CHOICES,
    build_xiangshi_prompt,
    build_verification_prompt,
    deterministic_timed_guess,
    knowledge_fallback,
    local_fallback,
    looks_like_answer_result,
    parse_model_decision,
    resolve_model_decision,
    scale_roi,
    validate_forced_model_decision,
    validate_xiangshi_model_decision,
)
from .searchAnswer import search_candidates
from .keju_knowledge import search_knowledge
from .keju_web_search import clean_search_question, search_web_evidence


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
EXAM_TITLE_ROI = [528, 65, 244, 67]
MAC_EXAM_TITLE_ROI = [350, 120, 230, 80]
MAC_EXAM_TITLE_FALLBACK_ROI = [270, 80, 380, 140]
MAC_QUESTION_ROI = [335, 190, 470, 120]
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
    "web_search",
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


def keju_exam_title_rois(image):
    image_height, image_width = image.shape[:2]
    if image_width / image_height < 1.5:
        reference_size = MAC_REFERENCE_SIZE
        rois = [MAC_EXAM_TITLE_ROI, MAC_EXAM_TITLE_FALLBACK_ROI]
    else:
        reference_size = (1280, 720)
        rois = [EXAM_TITLE_ROI]
    return [
        scale_roi(roi, image_width, image_height, reference_size)
        for roi in rois
    ]


def keju_exam_title_roi(image):
    """Retain the primary ROI API for older callers and tests."""
    return keju_exam_title_rois(image)[0]


def _exam_type_from_title(text):
    normalized = re.sub(r"\s+", "", text or "")
    matches = {
        exam_type
        for exam_type, label in {
            "xiangshi": "科举乡试",
            "huishi": "科举会试",
            "dianshi": "科举殿试",
        }.items()
        if label in normalized
    }
    return matches.pop() if len(matches) == 1 else None


def _uses_huishi_answer_policy(exam_type):
    return exam_type in {"huishi", "dianshi"}


def _sorted_ocr_text(results, row_height_threshold=20):
    items = [item for item in results if getattr(item, "text", "")]
    items.sort(key=lambda item: (round(item.box[1] / row_height_threshold), item.box[0]))
    return "".join(item.text for item in items)


def _question_ocr_text(results):
    """Drop tiny decorative OCR fragments before assembling the question area."""
    items = []
    for item in results:
        text = unicodedata.normalize("NFKC", getattr(item, "text", "") or "")
        compact_text = re.sub(r"\s+", "", text)
        score = float(getattr(item, "score", 1.0))
        if score < 0.6 and len(compact_text) <= 2:
            continue
        items.append(item)
    return _sorted_ocr_text(items)


def _clean_question_text(text):
    """Remove live round/score metadata while preserving the actual question."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", "", text)
    round_match = re.search(
        r"第\s*\d+(?:\s*/\s*\d+)?\s*题",
        text,
    )
    if round_match:
        text = text[round_match.end():]
    for pattern in (
        r"^正确率[:：]?\d+/\d+",
        r"^当前得分[:：]?\d+",
        r"^剩余时间[:：]?\d*",
        r"^圆(?=(?:我国|世界|在|被|目前|大运河|昆曲|电度表))",
    ):
        text = re.sub(pattern, "", text)
    return text.strip("-—:：,，.。 ")


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
    if name == "科举乡试题目-混合":
        return _question_ocr_text(detail.all_results)
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
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(rf"^{choice}\s*[.、:：]?\s*", "", text, flags=re.IGNORECASE)
    # 当前会试界面会把 B 选项左侧的【中华历史】等分类标签一起 OCR。
    text = re.sub(r"^[【\[（(][^】\]）)]{1,16}[】\]）)]\s*[A-D]?\s*", "", text)
    # 跨行选项偶尔会把左侧选项标记重复插入正文，例如“不妨碍A别人”。
    text = re.sub(
        rf"(?<=[\u3400-\u9fff]){choice}(?=[\u3400-\u9fff])",
        "",
        text,
        flags=re.IGNORECASE,
    )
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
                "content": (
                    "你是严谨的中文单选题答题助手。检索证据明确时必须服从证据，"
                    "并按要求返回包含引用和解释的 JSON。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": int(os.environ.get("MAA_KEJU_MAX_TOKENS", "220")),
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


def _request_huishi_models(
    url,
    api_key,
    primary_model,
    fallback_model,
    prompt,
    timeout,
    extra_body=None,
    *,
    valid_choices=VALID_CHOICES,
    response_observer=None,
):
    """Start both requests together; prefer a usable primary 会试 decision."""
    start_barrier = Barrier(2)

    def request(model_name):
        start_barrier.wait()
        return _request_model(
            url,
            api_key,
            model_name,
            prompt,
            timeout,
            extra_body,
        )

    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="keju-model")
    primary_future = executor.submit(request, primary_model)
    fallback_future = executor.submit(request, fallback_model)
    request_errors = (requests.RequestException, KeyError, IndexError, ValueError)

    def usable_response(future, model_name):
        content = future.result()
        if response_observer is not None:
            response_observer(content, model_name)
        decision = parse_model_decision(content, tuple(valid_choices))
        answer, error = validate_forced_model_decision(
            decision, tuple(valid_choices), f"模型 {model_name}"
        )
        if answer is None:
            raise ValueError(error)
        return content

    try:
        try:
            return usable_response(primary_future, primary_model), primary_model, None
        except request_errors as primary_exc:
            primary_error = f"{type(primary_exc).__name__}: {primary_exc}"
            try:
                return usable_response(fallback_future, fallback_model), fallback_model, primary_error
            except request_errors as fallback_exc:
                raise requests.RequestException(
                    f"主模型失败 ({primary_error}); 备用模型失败 "
                    f"({type(fallback_exc).__name__}: {fallback_exc})"
                ) from fallback_exc
    finally:
        # A successful primary response must not wait for a slower fallback.
        executor.shutdown(wait=False, cancel_futures=True)


def _request_codex_model(prompt, timeout):
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "enum": list(VALID_CHOICES)},
            "confidence": {"type": "number"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["answer", "confidence", "evidence_ids", "reason"],
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
        self.last_reason = ""
        self.last_manual_review = None
        try:
            return self._analyze(context, argv, timings)
        except RuntimeError as exc:
            self.last_reason = str(exc).strip() or exc.__class__.__name__
            raise
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
        self.last_terminal_result = None
        with timings.track("screenshot"):
            image = _recognition_image(context, argv)
        expected_exam_type = os.environ.get("MAA_KEJU_EXAM_TYPE", "xiangshi")
        wait_for_entry = os.environ.get("MAA_KEJU_WAIT_FOR_ENTRY") == "1"
        observed_exam_type = None
        for title_roi in keju_exam_title_rois(image):
            with timings.track("ocr"):
                title = _ocr_region(
                    context,
                    image,
                    "科举考试类型-混合",
                    title_roi,
                )
            observed_exam_type = _exam_type_from_title(title)
            if observed_exam_type is not None:
                break
        self.last_exam_type = observed_exam_type
        if observed_exam_type is None:
            if wait_for_entry:
                self.last_status = "waiting"
                self.last_reason = "等待手动进入科举正式答题页面"
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 0, 0), detail=self.last_reason
                )
            self.last_status = "unverified_exam_type"
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="未能从标题确认科举考试类型"
            )
        if observed_exam_type != expected_exam_type:
            if wait_for_entry:
                self.last_status = "waiting"
                self.last_reason = "等待进入与本任务匹配的科举页面"
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 0, 0), detail=self.last_reason
                )
            self.last_status = "wrong_exam_type"
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="当前科举考试类型与启动任务不匹配"
            )
        question_roi, option_rois = keju_rois(image)
        with timings.track("ocr"):
            raw_question = _ocr_region(
                context, image, "科举乡试题目-混合", question_roi
            )
        if not raw_question:
            if wait_for_entry:
                self.last_status = "waiting"
                self.last_reason = "等待科举正式题目出现"
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 0, 0), detail=self.last_reason
                )
            self.last_status = "no_question"
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="未识别到科举题目"
            )
        # 题号和实时得分与题干位于同一块面板。题号用于流程控制，
        # 但查库、联网检索和模型只接收清理后的实际题干。
        round_match = re.search(
            r"第\s*(\d+)(?:\s*/\s*(\d+))?\s*题",
            raw_question,
        )
        round_number = int(round_match.group(1)) if round_match else None
        round_total = (
            int(round_match.group(2))
            if round_match and round_match.group(2)
            else None
        )
        self.last_round_number = round_number
        self.last_question_round = (
            (round_number, round_total)
            if round_number is not None and round_total is not None
            else None
        )
        question = _clean_question_text(raw_question)
        if not question:
            if wait_for_entry:
                self.last_status = "waiting"
                self.last_reason = "等待科举题目正文出现"
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 0, 0), detail=self.last_reason
                )
            self.last_status = "no_question"
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 0, 0), detail="未识别到科举题目正文"
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
        if _uses_huishi_answer_policy(expected_exam_type):
            terminal_text = re.sub(
                r"\s+", "", question + "".join(valid_options.values())
            )
            correct_match = re.search(
                r"本次科举(?:您)?回答正确(\d+)", terminal_text
            )
            terminal_marker = re.search(
                r"切莫气馁|下次再战|再接再厉|殿试资格", terminal_text
            )
            if correct_match and terminal_marker:
                result = {"correctAnswers": int(correct_match.group(1))}
                reward_match = re.search(
                    r"本场(?:会试|殿试)?(?:获得)?奖金(\d+)", terminal_text
                )
                if reward_match:
                    result["settlementReward"] = {
                        "status": "confirmed",
                        "gold": int(reward_match.group(1)),
                        "evidence": {"sourcePage": "huishi_terminal_text"},
                    }
                self.last_terminal_result = result
                self.last_status = "completed"
                self.last_question = question
                self.last_options = valid_options
                self.last_reason = "已识别限时科举最终结果页"
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 0, 0), detail=self.last_reason
                )
        requires_round_one = os.environ.get("MAA_KEJU_REQUIRE_ROUND_ONE") == "1"
        live_formal_without_round = (
            round_number is None
            and len(valid_options) == 4
            and "剩余时间" in raw_question
        )
        if requires_round_one and (round_number == 1 or live_formal_without_round):
            os.environ.pop("MAA_KEJU_REQUIRE_ROUND_ONE", None)
            requires_round_one = False
        record_only_waiting = requires_round_one and round_number != 1
        if len(valid_options) < 2:
            if round_number is None:
                self.last_status = "waiting"
                self.last_question = question
                self.last_options = valid_options
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 0, 0), detail="科举尚未出现完整正式题目"
                )
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
            self.last_reason = f"科举选项识别不足，已识别：{valid_options}"
            raise RuntimeError(self.last_reason)
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
                box=(0, 0, 0, 0), detail="等待科举正式第1题开始，题面已记录"
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

        manual_override = getattr(self, "manual_review_override", None)
        if manual_override is not None:
            expected_question = str(manual_override.get("question", ""))
            expected_options = manual_override.get("options", {})
            selected_answer = str(manual_override.get("answer", "")).upper()
            self.manual_review_override = None
            if (
                question != expected_question
                or valid_options != expected_options
                or selected_answer not in valid_options
            ):
                self.last_status = "unreliable"
                self.last_question = question
                self.last_options = valid_options
                self.last_reason = "人工选择提交后题面或选项已变化，未执行点击"
                raise RuntimeError(self.last_reason)
            target = option_boxes.get(selected_answer) or option_rois[selected_answer]
            center_x = target[0] + target[2] // 2
            center_y = target[1] + target[3] // 2
            self.last_status = "ready"
            self.last_question = question
            self.last_options = valid_options
            self.last_answer = selected_answer
            self.last_source = "manual_review"
            self.last_click = (center_x, center_y)
            self.last_image_size = (image.shape[1], image.shape[0])
            logger.info(f"科举人工复核已重新确认当前题面，选择：{selected_answer}")
            return CustomRecognition.AnalyzeResult(
                box=tuple(target), detail=f"人工复核答题:{selected_answer}"
            )

        # 一道题在提交后仍可能保留数秒活动态。去重必须发生在查库和
        # 模型请求之前，否则虽然外层不会重复点击，仍会浪费一次模型调用。
        cache_key = round_number
        if cache_key is None:
            cache_key = "question:" + re.sub(
                r"剩余时间\s*\d*|\s+", "", question
            )
        round_answer_cache = getattr(self, "_round_answer_cache", {})
        cached_decision = round_answer_cache.get(cache_key)
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
                    f"科举同题复用：key={cache_key}, answer={cached_answer}, "
                    f"source={cached_source}"
                )
                return CustomRecognition.AnalyzeResult(
                    box=tuple(target), detail=f"复用本轮答案:{cached_answer}"
                )

        retrieval_question = clean_search_question(question)
        with timings.track("local_question_bank"):
            candidates = search_candidates(retrieval_question, limit=3)
        with timings.track("knowledge_base"):
            knowledge_candidates = search_knowledge(retrieval_question, limit=4)
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
        fallback_model = os.environ.get(
            "MAA_KEJU_FALLBACK_MODEL", ""
        ).strip() or str(attach.get("fallback_model", "")).strip()
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

        web_result = {
            "query": "",
            "queries": [],
            "evidence": [],
            "providers": {},
            "elapsed_ms": 0,
        }
        web_search_enabled = os.environ.get("MAA_KEJU_WEB_SEARCH_ENABLED", "1") != "0"
        if not trusted_local_answer and not trusted_knowledge_answer and web_search_enabled:
            web_timeout = float(
                os.environ.get(
                    "MAA_KEJU_WEB_SEARCH_TIMEOUT",
                    attach.get("web_search_timeout", 2.5),
                )
            )
            web_limit = int(os.environ.get("MAA_KEJU_WEB_SEARCH_LIMIT", "3"))
            provider_names = tuple(
                name.strip()
                for name in os.environ.get(
                    "MAA_KEJU_WEB_SEARCH_PROVIDERS", "duckduckgo,bing_rss"
                ).split(",")
                if name.strip()
            )
            with timings.track("web_search"):
                web_result = search_web_evidence(
                    question,
                    options=valid_options,
                    timeout=web_timeout,
                    limit_per_provider=web_limit,
                    provider_names=provider_names,
                )

        model_answer = None
        model_confidence = 0.0
        model_evidence_ids = []
        model_reason = ""
        model_error = None
        model_source = None
        model_responses = []
        xiangshi_mode = expected_exam_type == "xiangshi"
        force_model_answer = _uses_huishi_answer_policy(expected_exam_type)
        minimum_confidence = float(
            os.environ.get(
                "MAA_KEJU_MODEL_MIN_CONFIDENCE",
                attach.get(
                    "model_minimum_confidence",
                    0.70 if xiangshi_mode else 0.62,
                ),
            )
        )
        valid_web_ids = {item["id"] for item in web_result["evidence"]}

        def record_model_response(content, responding_model):
            decision = parse_model_decision(content, tuple(valid_options))
            response_record = {
                "model": responding_model,
                "raw_response": content,
                "parsed_decision": decision,
            }
            model_responses.append(response_record)
            # Persist before validation can replace the answer, reason or citations.
            # Also retain malformed replies and replies that end in manual review.
            logger.info("科举模型原始回复：" + json.dumps({
                "question": question,
                "options": valid_options,
                **response_record,
            }, ensure_ascii=False))
            return decision

        def accept_model_decision(decision, label):
            nonlocal model_answer, model_confidence, model_evidence_ids
            nonlocal model_reason, model_error, model_source
            if decision:
                model_confidence = decision["confidence"]
                model_evidence_ids = decision["evidence_ids"]
                model_reason = decision["reason"]
            if xiangshi_mode:
                model_answer, model_error = validate_xiangshi_model_decision(
                    decision,
                    tuple(valid_options),
                    minimum_confidence,
                    label,
                )
                forced_guess = False
            else:
                model_answer, forced_guess, model_error = resolve_model_decision(
                    decision,
                    valid_web_ids,
                    minimum_confidence,
                    label,
                    evidence=web_result["evidence"],
                    options=valid_options,
                    question=question,
                    force_answer=force_model_answer,
                )
            if forced_guess:
                model_source = "huishi_forced_model_guess"

        def request_codex_decision(prompt):
            nonlocal model_source
            codex_timeout = float(os.environ.get("MAA_KEJU_CODEX_TIMEOUT", "120"))
            with timings.track("model_request"):
                model_content = _request_codex_model(
                    prompt, max(timeout, codex_timeout)
                )
            with timings.track("parse_decision"):
                model_decision = record_model_response(model_content, "codex")
                accept_model_decision(model_decision, "Codex")
            if model_answer and model_source is None:
                model_source = "web_search_verified_codex"

        if trusted_local_answer:
            model_error = "题库精确命中，跳过远端模型"
        elif trusted_knowledge_answer:
            model_error = "知识库高置信度命中，跳过远端模型"
        elif api_key and url and model and (web_result["evidence"] or force_model_answer or xiangshi_mode):
            prompt = None
            try:
                with timings.track("parse_decision"):
                    prompt_builder = build_xiangshi_prompt if xiangshi_mode else build_verification_prompt
                    prompt = prompt_builder(
                        web_result["query"] or question, valid_options,
                        [item for item in candidates if item["similarity"] >= 50],
                        [item for item in knowledge_candidates if item["similarity"] >= 50],
                        web_result["evidence"],
                        **({} if xiangshi_mode else {"force_answer": force_model_answer}),
                    )
                with timings.track("model_request"):
                    if force_model_answer and fallback_model:
                        (
                            model_content,
                            responding_model,
                            primary_model_error,
                        ) = _request_huishi_models(
                            url,
                            api_key,
                            model,
                            fallback_model,
                            prompt,
                            timeout,
                            extra_body=model_extra_body,
                            valid_choices=tuple(valid_options),
                            response_observer=record_model_response,
                        )
                    else:
                        model_content = _request_model(
                            url,
                            api_key,
                            model,
                            prompt,
                            timeout,
                            extra_body=model_extra_body,
                        )
                        responding_model = model
                        primary_model_error = None
                with timings.track("parse_decision"):
                    if force_model_answer and fallback_model:
                        # The selector already logged both rejected and selected replies.
                        model_decision = parse_model_decision(model_content, tuple(valid_options))
                    else:
                        model_decision = record_model_response(model_content, responding_model)
                    decision_label = (
                        f"备用模型 {responding_model}"
                        if responding_model != model
                        else "模型"
                    )
                    accept_model_decision(model_decision, decision_label)
                    if responding_model != model and model_answer:
                        fallback_validation = model_error
                        model_source = "huishi_qwen_fallback"
                        model_error = f"主模型失败，已采用 {responding_model}: {primary_model_error}"
                        if fallback_validation:
                            model_error += f"; {fallback_validation}"
                if model_answer and model_source is None:
                    model_source = "web_search_verified_model"
            except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
                model_error = f"{type(exc).__name__}: {exc}"
                if (
                    prompt is not None
                    and os.environ.get("MAA_KEJU_USE_CODEX") == "1"
                ):
                    api_error = model_error
                    try:
                        request_codex_decision(prompt)
                    except (OSError, subprocess.SubprocessError, ValueError) as codex_exc:
                        model_error = (
                            f"{api_error}; Codex fallback "
                            f"{type(codex_exc).__name__}: {codex_exc}"
                        )
        elif os.environ.get("MAA_KEJU_USE_CODEX") == "1" and (web_result["evidence"] or force_model_answer or xiangshi_mode):
            try:
                with timings.track("parse_decision"):
                    prompt_builder = build_xiangshi_prompt if xiangshi_mode else build_verification_prompt
                    prompt = prompt_builder(
                        web_result["query"] or question, valid_options,
                        [item for item in candidates if item["similarity"] >= 50],
                        [item for item in knowledge_candidates if item["similarity"] >= 50],
                        web_result["evidence"],
                        **({} if xiangshi_mode else {"force_answer": force_model_answer}),
                    )
                request_codex_decision(prompt)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                model_error = f"{type(exc).__name__}: {exc}"
        else:
            model_error = (
                "联网搜索没有返回可校验证据"
                if not web_result["evidence"]
                else "远端模型配置不完整"
            )

        with timings.track("parse_decision"):
            timed_guess = None
            if _uses_huishi_answer_policy(expected_exam_type) and not (
                trusted_local_answer or trusted_knowledge_answer or model_answer
                or local_answer
            ):
                timed_guess = deterministic_timed_guess(question, valid_options)
                if timed_guess:
                    failure = model_error or "没有可用的题库或模型答案"
                    model_error = f"{failure}; 限时会试已执行确定性猜测"
                    model_reason = "主备用决策均不可用，按当前题面稳定选择有效选项"
            final_answer = (
                trusted_local_answer
                or trusted_knowledge_answer
                or model_answer
                or local_answer
                or timed_guess
            )
            source = (
                "question_bank_exact"
                if trusted_local_answer
                else "knowledge_base"
                if trusted_knowledge_answer
                else model_source
                if model_answer
                else "question_bank_fallback"
                if local_answer
                else "huishi_deterministic_guess"
                if timed_guess
                else "unavailable"
            )
        if final_answer not in VALID_CHOICES or final_answer not in valid_options:
            self.last_manual_review = {
                "question": question,
                "options": valid_options,
                "model": {
                    "answer": model_answer or "",
                    "confidence": model_confidence,
                    "evidenceIds": model_evidence_ids,
                    "reason": model_reason,
                    "error": model_error,
                    "source": model_source,
                    "rawResponse": locals().get("model_content", ""),
                },
                "webSearch": {
                    "query": web_result["query"],
                    "queries": web_result.get("queries", []),
                    "evidence": web_result["evidence"],
                },
                "reason": model_error or "没有可用于自动作答的答案",
            }
            self.last_status = "unreliable"
            self.last_question = question
            self.last_options = valid_options
            best_similarity = candidates[0]["similarity"] if candidates else 0
            self.last_reason = (
                "科举答题无法可靠决策，已停止避免盲选："
                f"model_error={model_error}, best_similarity={best_similarity}"
            )
            raise RuntimeError(self.last_reason)

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
        round_answer_cache[cache_key] = (final_answer, source)
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
                    "web_search": {
                        "query": web_result["query"],
                        "queries": web_result.get("queries", []),
                        "elapsed_ms": web_result["elapsed_ms"],
                        "providers": web_result["providers"],
                        "evidence": web_result["evidence"],
                    },
                    "answer": final_answer,
                    "source": source,
                    "model_confidence": model_confidence,
                    "model_evidence_ids": model_evidence_ids,
                    "model_reason": model_reason,
                    "model_error": model_error,
                    "model_responses": model_responses,
                },
                ensure_ascii=False,
            )
        logger.info(f"科举混合答题：{log_payload}")
        return CustomRecognition.AnalyzeResult(
            box=tuple(target), detail=f"混合答题完成:{final_answer}"
        )
