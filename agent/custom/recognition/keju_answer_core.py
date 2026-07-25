import json
import re
from difflib import SequenceMatcher

try:
    from .searchAnswer import normalize_text
except ImportError:  # Allow the pure logic module to be tested without MaaFramework.
    from searchAnswer import normalize_text


VALID_CHOICES = ("A", "B", "C", "D")
REFERENCE_SIZE = (1280, 720)


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


def build_verification_prompt(question, options, candidates):
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
    }
    return (
        "你正在回答梦幻西游手游科举单选题。相似题库内容只能作为参考，"
        "必须结合当前题目与当前选项独立复核。只输出一个 JSON 对象，不要输出 Markdown：\n"
        '{"answer":"A|B|C|D","confidence":0.0,"used_question_bank":true}\n'
        "输入：\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def parse_model_answer(content, valid_choices=VALID_CHOICES):
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
            answer = str(json.loads(json_text).get("answer", "")).strip().upper()
            if answer in valid_choices:
                return answer
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    letter_match = re.search(r"(?:答案|answer)?\s*(?:是|为|:|：)?\s*([A-D])\b", text.upper())
    if letter_match and letter_match.group(1) in valid_choices:
        return letter_match.group(1)
    return None


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
