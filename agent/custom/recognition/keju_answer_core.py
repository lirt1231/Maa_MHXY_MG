import json
import re
from difflib import SequenceMatcher

try:
    from .searchAnswer import normalize_text
except ImportError:  # Allow the pure logic module to be tested without MaaFramework.
    from searchAnswer import normalize_text


VALID_CHOICES = ("A", "B", "C", "D")
REFERENCE_SIZE = (1280, 720)


def looks_like_answer_result(question, options):
    """Detect the post-answer/review state where crowd counts are visible."""
    explicit_result = any(
        marker in text
        for text in options.values()
        for marker in ("答对", "答错", "科举积分", "获得")
    )
    crowd_count_options = sum(
        bool(re.search(r"\d{2,}\s*人?\.?$", text)) for text in options.values()
    )
    return explicit_result or ("剩余时间" not in question and crowd_count_options >= 2)


def scale_roi(roi, image_width, image_height, reference_size=REFERENCE_SIZE):
    reference_width, reference_height = reference_size
    scale_x = image_width / reference_width
    scale_y = image_height / reference_height
    x, y, width, height = roi
    return [
        int(round(x * scale_x)),
        int(round(y * scale_y)),
        max(1, int(round(width * scale_x))),
        max(1, int(round(height * scale_y))),
    ]


def build_verification_prompt(
    question,
    options,
    candidates,
    knowledge_candidates=None,
    web_evidence=None,
):
    evidence = [
        {
            "question": item["question"],
            "answers": item["answers"],
            "similarity": item["similarity"],
        }
        for item in candidates
    ]
    payload = {
        "question": question,
        "options": options,
        "similar_questions": evidence,
        "knowledge_evidence": [
            {
                "title": item["title"],
                "content": item["content"],
                "answer": item.get("answer", ""),
                "similarity": item["similarity"],
                "source": item.get("source_name", ""),
                "source_url": item.get("source_url", ""),
                "trust": item.get("trust", 0.5),
            }
            for item in (knowledge_candidates or [])
        ],
        "web_evidence": [
            {
                "id": item["id"],
                "provider": item["provider"],
                "kind": item["kind"],
                "title": item["title"],
                "snippet": item["snippet"],
                "url": item["url"],
                "rank": item["rank"],
            }
            for item in (web_evidence or [])
        ],
    }
    return (
        "你正在回答梦幻西游手游科举单选题。相似题库内容只能作为参考，"
        "知识库片段按匹配度和来源可信度排序。网页证据来自多个独立搜索渠道，"
        "direct_answer 是搜索引擎展示的最佳匹配答案，但仍需与普通摘要交叉验证。"
        "网页可能包含旧版本或互相矛盾的说法，应优先精确匹配题目、梦幻西游手游语境、"
        "较新版本和多来源共识，不得仅凭自身记忆覆盖检索证据。"
        "只输出一个 JSON 对象，不要输出 Markdown：\n"
        '{"answer":"A|B|C|D","confidence":0.0,"evidence_ids":["web-1"]}\n'
        "输入：\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def parse_model_decision(content, valid_choices=VALID_CHOICES):
    text = (content or "").strip()
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    json_text = fenced.group(1) if fenced else None
    if json_text is None:
        object_match = re.search(r"\{.*\}", text, re.DOTALL)
        json_text = object_match.group(0) if object_match else None
    if json_text:
        try:
            payload = json.loads(json_text)
            answer = str(payload.get("answer", "")).strip().upper()
            if answer in valid_choices:
                try:
                    confidence = float(payload.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                evidence_ids = payload.get("evidence_ids", [])
                if not isinstance(evidence_ids, list):
                    evidence_ids = []
                return {
                    "answer": answer,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "evidence_ids": [
                        str(item)
                        for item in evidence_ids
                        if isinstance(item, (str, int))
                    ],
                }
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    return None


def parse_model_answer(content, valid_choices=VALID_CHOICES):
    decision = parse_model_decision(content, valid_choices)
    if decision:
        return decision["answer"]

    text = (content or "").strip()
    if not text:
        return None

    letter_match = re.search(r"(?:答案|answer)?\s*(?:是|为|:|：)?\s*([A-D])\b", text.upper())
    if letter_match and letter_match.group(1) in valid_choices:
        return letter_match.group(1)
    return None


def validate_model_decision(
    decision,
    valid_evidence_ids,
    minimum_confidence=0.62,
    label="模型",
):
    """Accept only a confident decision that cites retrieved web evidence."""
    if not decision:
        return None, f"{label}回复中没有有效选项"
    confidence = decision["confidence"]
    if confidence < minimum_confidence:
        return (
            None,
            f"{label}置信度不足:{confidence:.2f}<{minimum_confidence:.2f}",
        )
    if not set(valid_evidence_ids).intersection(decision["evidence_ids"]):
        return None, f"{label}没有引用有效的网页证据"
    return decision["answer"], None


def answer_to_option(answer_text, options):
    normalized_answer = normalize_text(answer_text)
    if not normalized_answer:
        return None, 0

    best_choice = None
    best_score = 0
    for choice, option_text in options.items():
        normalized_option = normalize_text(option_text)
        if not normalized_option:
            continue
        if normalized_answer == normalized_option:
            return choice, 100
        score = int(round(SequenceMatcher(None, normalized_answer, normalized_option).ratio() * 100))
        if score > best_score:
            best_choice = choice
            best_score = score
    return best_choice, best_score


def local_fallback(candidates, options, question_threshold=92, option_threshold=85):
    if not candidates or candidates[0]["similarity"] < question_threshold:
        return None

    for answer_text in candidates[0]["answers"]:
        choice, score = answer_to_option(answer_text, options)
        if choice and score >= option_threshold:
            return choice
    return None


def knowledge_fallback(candidates, options, knowledge_threshold=90, option_threshold=85):
    """Use a sourced knowledge answer only when retrieval and option mapping agree."""
    if not candidates or candidates[0]["similarity"] < knowledge_threshold:
        return None
    answer = candidates[0].get("answer", "")
    choice, score = answer_to_option(answer, options)
    return choice if choice and score >= option_threshold else None
