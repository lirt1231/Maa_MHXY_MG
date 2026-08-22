import hashlib
import json
import re
from difflib import SequenceMatcher

try:
    from .searchAnswer import normalize_text
except ImportError:  # Allow the pure logic module to be tested without MaaFramework.
    from searchAnswer import normalize_text


VALID_CHOICES = ("A", "B", "C", "D")
REFERENCE_SIZE = (1280, 720)


def deterministic_timed_guess(question, options):
    """Choose a stable valid option when a timed exam has no usable decision."""
    choices = [choice for choice in VALID_CHOICES if choice in options]
    if not choices:
        return None
    material = normalize_text(question) + "|" + "|".join(
        f"{choice}:{normalize_text(options[choice])}" for choice in choices
    )
    index = hashlib.sha256(material.encode("utf-8")).digest()[0] % len(choices)
    return choices[index]


def looks_like_answer_result(question, options):
    """Detect the post-answer/review state where crowd counts are visible."""
    explicit_result = any(
        marker in text
        for text in options.values()
        for marker in ("答对", "答错", "科举积分", "获得")
    )

    def has_crowd_count(text):
        normalized = (text or "").strip()
        people_suffix = re.search(r"\d{1,}\s*人\.?$", normalized)
        if people_suffix:
            return True
        number_suffix = re.search(r"\d{2,}\.?$", normalized)
        if not number_suffix:
            return False
        # New 乡试 questions omit the timer and can use bare years/numbers as
        # answers. A bare numeric option is not post-answer crowd-count proof.
        prefix = normalized[:number_suffix.start()].strip()
        return bool(prefix and re.search(r"\D", prefix))

    crowd_count_options = sum(
        has_crowd_count(text) for text in options.values()
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
    *,
    force_answer=False,
):
    model_question = re.sub(
        r"^剩余时间\s*\d*",
        "",
        str(question or "").strip(),
    ).strip("-—:：,，.。?？!！ ")
    evidence = [
        {
            "question": item["question"],
            "answers": item["answers"],
            "similarity": item["similarity"],
        }
        for item in candidates
    ]
    payload = {
        "question": model_question,
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
                "query_route": item.get("query_route", "question"),
            }
            for item in (web_evidence or [])
        ],
    }
    forced_answer_rule = (
        "7. 当前是限时会试：无论证据是否充分，都必须选择最可能的一个选项。"
        "有相关网页证据时必须引用并解释；没有相关证据时不得伪造引用，"
        "evidence_ids 应为空，并在 reason 中明确说明依据常识、排除法或低置信度猜测。\n"
        if force_answer
        else ""
    )
    return (
        "你正在回答梦幻西游手游科举单选题。必须先理解每个选项的完整含义，再选择选项字母。"
        "相似题库内容只能作为参考，知识库片段按匹配度和来源可信度排序。"
        "网页证据来自多个独立搜索渠道，direct_answer 是搜索引擎展示的最佳匹配答案，"
        "但仍需与普通摘要交叉验证。\n"
        "请严格遵守以下规则：\n"
        "1. 检索证据若直接、明确且彼此一致地给出事实或答案，必须采用该结论，"
        "不得用自身记忆、题目措辞或猜测覆盖证据。\n"
        "涉及数值时，必须区分单项数值与总计数值：题目问‘一共、总共、合计’时，"
        "优先采用证据中明确写出的‘合、共、总计’。同一机制的不同表述不是两个独立加成，"
        "除非证据明确说明它们可叠加，否则严禁相加；已有明确总计时，也不得再把单项数值加到总计上。"
        "不得把装备、槽位、材料等对象的数量误当成属性类别数量；证据逐项枚举类别时应按枚举核对。"
        "推导结果若不等于任何选项，说明推导、单位或题意理解有误，必须重新检查明确总计证据，"
        "绝不能选择数值最接近的选项。"
        "reason 必须写清采用的是单项还是总计，以及数值如何对应选项。\n"
        "2. 判断题必须逐字比较“题目中的陈述”和“证据中的事实”。"
        "证据事实与题目陈述不同，就应选择表示否定的选项；不得因为题目出现某个词就选择肯定。\n"
        "题目询问‘没有、不含、不属于’时必须逐项做反向核对：证据显示某选项具有该属性，"
        "只能排除该选项，绝不能把‘证据提到了该选项’误当成支持；应选择技能或属性列表中明确缺少目标项的选项。\n"
        "只有证据明确声称‘没有’，或提供了完整、穷尽的初始/必带列表时，才可据其未出现目标项推断不存在；"
        "专题简介、部分技能举例或搜索摘要没有提到某项，不能证明该项不存在。\n"
        "3. 必须在 evidence_ids 中引用实际支持最终答案的网页证据 ID；"
        "reason 必须简要复述关键证据事实，并解释该事实如何推出所选选项。"
        "例如证据称答案是“文、武”，题目问是否为“阴、阳”，则两者不一致，应选择“不是”。\n"
        "4. 如果检索证据冲突，优先精确匹配题目、梦幻西游手游语境、较新版本和多来源共识，"
        "并在 reason 中说明取舍。\n"
        "网页中的答案字母只对应网页自身的选项顺序，绝不能直接沿用；"
        "必须先还原网页答案的完整文本，再映射到当前 options 的字母。\n"
        "5. 如果没有任何检索证据支持答案，evidence_ids 必须为空数组，"
        "并在 reason 中明确说明未引用检索结果的原因；不得伪造引用。\n"
        "不得根据不完整证据做算术拼接、近似映射或猜测；证据没有明确支持当前选项时，"
        "confidence 必须低于 0.50。\n"
        "6. confidence 必须反映证据强度；多条直接且一致的证据支持时应不低于 0.90，"
        "不得照抄输出示例中的数值。\n"
        + forced_answer_rule
        + "reason 只写一到两句，不要展开冗长推理。\n"
        "只输出一个 JSON 对象，不要输出 Markdown：\n"
        '{"answer":"A|B|C|D","confidence":0.95,'
        '"evidence_ids":["web-1"],"reason":"证据事实及其与所选选项的对应关系"}\n'
        "输入：\n"
    + json.dumps(payload, ensure_ascii=False)
    )


def build_xiangshi_prompt(
    question,
    options,
    candidates,
    knowledge_candidates=None,
    web_evidence=None,
):
    """Build the slower, common-knowledge-oriented 乡试 prompt.

    Search is still supplied on every non-bank question, but unlike a timed
    会试 answer it is not the sole acceptable basis for a sound answer.
    """
    model_question = re.sub(r"^剩余时间\s*\d*", "", str(question or "").strip())
    payload = {
        "question": model_question.strip("-—:：,，.。?？!！ "),
        "options": options,
        "similar_questions": [
            {"question": item["question"], "answers": item["answers"], "similarity": item["similarity"]}
            for item in candidates
        ],
        "knowledge_evidence": list(knowledge_candidates or []),
        "web_evidence": list(web_evidence or []),
    }
    return (
        "你正在回答梦幻西游手游科举乡试单选题。乡试主要考察常识和基础知识，"
        "请先完整理解题干与每个选项，再选择最正确的选项字母。网页搜索结果、题库相似题和知识片段"
        "都只是辅助材料；若它们不完整、无关或互相矛盾，可以依据可靠常识和排除法作答。\n"
        "规则：\n"
        "1. 网页证据直接且一致时优先采用；网页答案字母必须先还原为答案文本，再映射到当前选项。\n"
        "2. 没有可用网页证据时 evidence_ids 必须为空，并在 reason 中说明采用的常识或排除依据；不得伪造引用。\n"
        "3. confidence 必须如实反映把握。无法可靠判断时也要给出最可能选项和低置信度，供人工复核。\n"
        "4. reason 只写一到两句。只输出一个 JSON 对象，不要输出 Markdown：\n"
        '{"answer":"A|B|C|D","confidence":0.85,"evidence_ids":["web-1"],"reason":"简要依据"}\n'
        "输入：\n" + json.dumps(payload, ensure_ascii=False)
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
                reason = payload.get("reason", "")
                if not isinstance(reason, str):
                    reason = ""
                return {
                    "answer": answer,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "evidence_ids": [
                        str(item)
                        for item in evidence_ids
                        if isinstance(item, (str, int))
                    ],
                    "reason": reason.strip(),
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
    *,
    evidence=None,
    options=None,
    question=None,
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
    if not decision.get("reason", "").strip():
        return None, f"{label}没有提供证据解释"
    if not set(valid_evidence_ids).intersection(decision["evidence_ids"]):
        return None, f"{label}没有引用有效的网页证据"
    if evidence is not None and options is not None:
        cited = [
            item
            for item in evidence
            if item.get("id") in set(decision["evidence_ids"])
        ]
        selected_text = str(options.get(decision["answer"], ""))
        if not _web_evidence_supports_selected_option(
            cited,
            selected_text,
            question=question,
            options=options,
            reason=decision.get("reason", ""),
        ):
            return None, f"{label}引用的网页证据未明确支持当前选项文本"
    return decision["answer"], None


def validate_forced_model_decision(decision, valid_choices=VALID_CHOICES, label="模型"):
    """Allow a transparent timed-exam guess without accepting fabricated evidence."""
    if not decision:
        return None, f"{label}回复中没有有效选项"
    answer = decision.get("answer")
    if answer not in valid_choices:
        return None, f"{label}回复的选项无效"
    if not decision.get("reason", "").strip():
        return None, f"{label}没有提供证据解释或猜测依据"
    return answer, None


def validate_xiangshi_model_decision(
    decision,
    valid_choices=VALID_CHOICES,
    minimum_confidence=0.70,
    label="模型",
):
    """Accept a sufficiently confident 乡试 decision without web citations."""
    if not decision:
        return None, f"{label}回复中没有有效选项"
    answer = decision.get("answer")
    if answer not in valid_choices:
        return None, f"{label}回复的选项无效"
    if decision.get("confidence", 0.0) < minimum_confidence:
        return None, (
            f"{label}置信度不足:{decision.get('confidence', 0.0):.2f}"
            f"<{minimum_confidence:.2f}"
        )
    if not decision.get("reason", "").strip():
        return None, f"{label}没有提供作答依据"
    return answer, None


def infer_direct_evidence_option(evidence, options, question=None):
    """Find one option directly and repeatedly supported by retrieved text."""
    support = {}
    normalized_question = normalize_text(question or "")
    for choice, option_text in options.items():
        selected = normalize_text(str(option_text))
        if not selected:
            continue
        ids = []
        explicit_total = False
        direct_question_match = False
        numeric = bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", selected))
        for item in evidence or []:
            text = " ".join(str(item.get(key, "")) for key in ("title", "snippet"))
            normalized = normalize_text(text)
            if numeric:
                mentioned = bool(re.search(rf"(?<!\d){re.escape(selected)}(?!\d)", normalized))
            else:
                mentioned = selected in normalized
            if not mentioned:
                continue
            ids.append(str(item.get("id", "")))
            normalized_title = normalize_text(str(item.get("title", "")))
            if normalized_question and normalized_title:
                question_match = SequenceMatcher(
                    None, normalized_question, normalized_title
                ).ratio()
                direct_question_match = direct_question_match or question_match >= 0.75
            if numeric and re.search(
                rf"(?:(?:一共|总共|合计|共计)\D{{0,8}}{re.escape(selected)}(?!\d)|"
                rf"合\D{{0,2}}{re.escape(selected)}(?!\d))",
                text,
            ):
                explicit_total = True
        if len(set(ids)) >= 2 or explicit_total or direct_question_match:
            support[choice] = [item_id for item_id in dict.fromkeys(ids) if item_id]
    if len(support) != 1:
        return None
    choice, evidence_ids = next(iter(support.items()))
    return {"answer": choice, "evidence_ids": evidence_ids}


def infer_unique_reason_option(decision, options):
    """Correct a letter that contradicts the only option named in its reason."""
    if not decision:
        return None
    reason = normalize_text(decision.get("reason", ""))
    answer = decision.get("answer")
    if not reason or answer not in options:
        return None
    mentioned = []
    for choice, option_text in options.items():
        normalized_option = normalize_text(str(option_text))
        if len(normalized_option) >= 2 and normalized_option in reason:
            mentioned.append(choice)
    if len(mentioned) == 1 and mentioned[0] != answer:
        return mentioned[0]
    return None


def resolve_model_decision(
    decision,
    valid_evidence_ids,
    minimum_confidence,
    label,
    *,
    evidence,
    options,
    question=None,
    force_answer=False,
):
    """Apply strict evidence validation, then the explicit 会试-only guess policy."""
    answer, strict_error = validate_model_decision(
        decision,
        valid_evidence_ids,
        minimum_confidence,
        label,
        evidence=evidence,
        options=options,
        question=question,
    )
    if answer is not None:
        return answer, False, None
    reason_option = infer_unique_reason_option(decision, options)
    if reason_option is not None:
        return (
            reason_option,
            False,
            f"理由选项纠偏：{label}返回字母{decision.get('answer')}，"
            f"但理由唯一明确指向选项{reason_option}",
        )
    direct_evidence = infer_direct_evidence_option(evidence, options, question)
    if direct_evidence is not None:
        corrected = direct_evidence["answer"]
        return (
            corrected,
            False,
            f"证据直接纠偏：{label}严格校验未通过（{strict_error}），"
            f"网页证据唯一明确支持选项{corrected}",
        )
    if not force_answer:
        return None, False, strict_error

    forced_answer, forced_error = validate_forced_model_decision(
        decision,
        tuple(options),
        label,
    )
    if forced_answer is None:
        return None, False, forced_error or strict_error
    return (
        forced_answer,
        True,
        f"严格证据校验未通过，限时会试强制作答：{strict_error}",
    )


_AFFIRMATIVE_OPTIONS = frozenset(
    normalize_text(text)
    for text in ("是", "是的", "对", "正确", "没错", "可以", "能", "会")
)
_NEGATIVE_OPTIONS = frozenset(
    normalize_text(text)
    for text in ("不是", "不对", "错误", "不正确", "不可以", "不能", "不会")
)


def _boolean_option_polarity(text):
    normalized = normalize_text(text)
    if normalized in _AFFIRMATIVE_OPTIONS:
        return True
    if normalized in _NEGATIVE_OPTIONS:
        return False
    return None


def _binary_boolean_options(options):
    if len(options or {}) != 2:
        return False
    polarities = {_boolean_option_polarity(text) for text in options.values()}
    return polarities == {True, False}


def _reason_boolean_polarity(reason):
    normalized = normalize_text(reason)
    negative_patterns = (
        r"(?:题目|陈述|说法).{0,10}(?:错误|不正确|不成立|不属实)",
        r"(?:因此|所以|故).{0,14}(?:选择|应选)?(?:不是|不对|错误)",
    )
    affirmative_patterns = (
        r"(?:题目|陈述|说法).{0,10}(?:正确|成立|属实)",
        r"(?:因此|所以|故).{0,14}(?:选择|应选)?(?:是的|正确|没错)",
    )
    if any(re.search(pattern, normalized) for pattern in negative_patterns):
        return False
    if any(re.search(pattern, normalized) for pattern in affirmative_patterns):
        return True
    return None


def _boolean_evidence_supports_decision(evidence, question, reason, polarity):
    """Validate a cited true/false fact without matching generic option text."""
    if _reason_boolean_polarity(reason) is not polarity:
        return False

    normalized_question = normalize_text(question or "")
    normalized_reason = normalize_text(reason or "")
    for item in evidence:
        text = " ".join(str(item.get(key, "")) for key in ("title", "snippet"))
        normalized_evidence = normalize_text(text)
        # Search results often repeat the question verbatim in their title. Remove
        # that echo so it cannot count as proof without a substantive snippet.
        evidence_fact = normalized_evidence.replace(normalized_question, "")
        reason_fact = normalized_reason.replace(normalized_question, "")
        if not evidence_fact or not reason_fact:
            continue
        overlap = SequenceMatcher(None, reason_fact, evidence_fact).find_longest_match()
        if overlap.size >= 6:
            return True
    return False


def _web_evidence_supports_selected_option(
    evidence,
    selected_text,
    question=None,
    *,
    options=None,
    reason="",
):
    """Reject guesses, same-site echoes, and stale external option letters."""
    selected = normalize_text(selected_text)
    if not selected or not evidence:
        return False

    if _binary_boolean_options(options):
        polarity = _boolean_option_polarity(selected_text)
        return _boolean_evidence_supports_decision(
            evidence,
            question,
            reason,
            polarity,
        )

    normalized_question = normalize_text(question or "")
    missing_target = ""
    if "初始技能没有" in normalized_question:
        missing_target = normalized_question.split("初始技能没有", 1)[1]
        missing_target = re.sub(r"的是.*$", "", missing_target).strip()

    exact_text_support = False
    for item in evidence:
        text = " ".join(
            str(item.get(key, "")) for key in ("title", "snippet")
        )
        normalized_text = normalize_text(text)
        if missing_target and selected in normalized_text:
            complete_list = bool(
                re.search(r"必带.{0,8}技能|技能包括|完整技能|全技能", text)
            )
            if complete_list and missing_target not in normalized_text:
                exact_text_support = True
            elif missing_target in normalized_text:
                return False
            continue
        if selected in normalized_text:
            exact_text_support = True

        external_answer = _external_labeled_answer_text(text)
        if external_answer is not None:
            if normalize_text(external_answer) != selected:
                return False
            exact_text_support = True

    return exact_text_support


def _external_labeled_answer_text(text):
    """Resolve an answer letter against options printed inside one web snippet."""
    answer_match = re.search(r"答案\s*[:：]?\s*([A-D])\b", text, re.IGNORECASE)
    if not answer_match:
        return None
    answer_letter = answer_match.group(1).upper()
    prefix = text[: answer_match.start()]
    option_matches = list(
        re.finditer(
            r"(?:^|\s)([A-D])[\.、:：]\s*(.+?)(?=(?:\s+[A-D][\.、:：])|$)",
            prefix,
            re.IGNORECASE,
        )
    )
    for match in option_matches:
        if match.group(1).upper() == answer_letter:
            return match.group(2).strip()
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


def knowledge_fallback(candidates, options, knowledge_threshold=90, option_threshold=85):
    """Use a sourced knowledge answer only when retrieval and option mapping agree."""
    if not candidates or candidates[0]["similarity"] < knowledge_threshold:
        return None
    answer = candidates[0].get("answer", "")
    choice, score = answer_to_option(answer, options)
    return choice if choice and score >= option_threshold else None
