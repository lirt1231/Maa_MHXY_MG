import datetime
import difflib
import re
from pathlib import Path


file_path = Path("agent") / "custom" / "recognition" / "tiku.txt"
file_path = file_path.resolve()
log_file_path = (Path("agent") / "custom" / "recognition" / "search_log.txt").resolve()

_question_bank_cache = None
_question_bank_cache_path = None


def normalize_text(value):
    """Normalize OCR and question-bank text before matching."""
    value = value or ""
    # 会试 OCR 会把实时题号、倒计时数字/字母和半个分类括号拼进题干。
    # 这些内容每秒变化，必须在相似度计算前移除。
    value = re.sub(
        r"^第\s*\d+\s*/\s*\d+\s*题(?:剩余时间)?[0-9a-zA-Z【\[（(]*",
        "",
        value,
    )
    value = value.lower()
    value = re.sub(r"[\s\t\r\n]", "", value)
    return re.sub(r"[，,、。！？?!：:；;“”\"'（）()【】\[\]\-·—…]", "", value)


def load_question_bank(path):
    """Load the existing loose JSON-like question bank."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        print(f"加载题库时出错: {exc}")
        return {}

    pattern = r'"\s*([^"]+)\s*"\s*:\s*\[\s*([^\]]*?)\s*\]'
    matches = re.findall(pattern, content)
    question_bank = {}
    for question, answers in matches:
        answer_list = re.findall(r'"([^"]+)"', answers)
        if not answer_list:
            answer_list = [answer.strip().strip('"') for answer in answers.split(",")]

        processed_answers = []
        for answer in answer_list:
            if "，" in answer:
                processed_answers.extend(
                    item.strip() for item in answer.split("，") if item.strip()
                )
            elif answer.strip():
                processed_answers.append(answer.strip())
        question_bank[question.strip()] = processed_answers
    return question_bank


def get_question_bank(path=file_path, force_reload=False):
    """Return a process-wide cached question bank."""
    global _question_bank_cache, _question_bank_cache_path
    resolved_path = Path(path).resolve()
    if (
        force_reload
        or _question_bank_cache is None
        or _question_bank_cache_path != resolved_path
    ):
        _question_bank_cache = load_question_bank(resolved_path)
        _question_bank_cache_path = resolved_path
    return _question_bank_cache


def search_candidates(query, limit=3, question_bank=None):
    """Return the most similar questions as evidence, without applying a cutoff."""
    bank = question_bank if question_bank is not None else get_question_bank()
    normalized_query = normalize_text(query)
    if not normalized_query or not bank:
        return []

    candidates = []
    for question, answers in bank.items():
        normalized_question = normalize_text(question)
        if normalized_query == normalized_question:
            similarity = 100
            match_type = "精确匹配"
        else:
            similarity = int(
                round(
                    difflib.SequenceMatcher(
                        None, normalized_query, normalized_question
                    ).ratio()
                    * 100
                )
            )
            match_type = "相似度匹配"
        candidates.append(
            {
                "question": question,
                "answers": list(answers),
                "similarity": similarity,
                "match_type": match_type,
            }
        )

    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    return candidates[: max(0, int(limit))]


def format_answer(answers):
    formatted = (f'"{answer.strip().strip(chr(34))}"' for answer in answers)
    return "[{}]".format(",".join(formatted))


def search_answer(question_bank, query, threshold=70):
    candidates = search_candidates(query, limit=1, question_bank=question_bank)
    if not candidates or candidates[0]["similarity"] < threshold:
        return "未找到匹配的问题", 0, "低于阈值"
    best = candidates[0]
    return format_answer(best["answers"]), best["similarity"], best["match_type"]


def log_search_result(query, result, confidence, match_type):
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = (
            f"[{timestamp}] 问题: {query} | 答案: {result} | "
            f"可信度: {confidence}% | 匹配类型: {match_type}\n"
        )
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(log_entry)
        return True
    except Exception as exc:
        print(f"记录日志时出错: {exc}")
        return False


def SearchQuestions(query):
    """Backward-compatible API used by the existing 三界奇缘 recognizer."""
    question_bank = get_question_bank()
    if not question_bank:
        print("题库加载失败或为空")
        return "未找到匹配的问题", 0, "题库为空"

    result, confidence, match_type = search_answer(question_bank, query)
    log_search_result(query, result, confidence, match_type)
    return result, confidence, match_type


__all__ = [
    "SearchQuestions",
    "get_question_bank",
    "load_question_bank",
    "normalize_text",
    "search_answer",
    "search_candidates",
]
