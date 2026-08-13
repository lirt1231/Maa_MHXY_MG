import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from tools.automation_workflows import WorkflowContext, WorkflowResult
from tools.bangpai_qiandao_workflow import ClanSignInWorkflow
from tools.baotu_workflow import BaotuWorkflow
from tools.fuli_qiandao_workflow import WelfareSignInWorkflow
from tools.kaogu_workflow import KaoguWorkflow
from tools.keju_workflow import KejuWorkflow
from tools.macos_cancellation import CancellationRequested
from tools.mijingxiangyao_workflow import MijingxiangyaoWorkflow
from tools.quiz_workflows import QuizObservation
from tools.sanjieqiyuan_workflow import (
    SanjieNavigationWorkflow,
    SanjieqiyuanWorkflow,
)
from tools.zhuogui_workflow import WorkflowState, ZhuoguiWorkflow


@dataclass
class _MemoryAdapterBase:
    scenes: list

    def __post_init__(self):
        self.index = 0
        self.trace = []
        self.requested_templates = []

    def read_scene(self, templates=None):
        self.requested_templates.append(templates)
        scene = self.scenes[self.index]
        self.index += 1
        return scene

    def click_box(self, box, scene, reason):
        self.trace.append(("click", tuple(box), scene.image_size, reason))

    def click(self, point, scene, reason):
        self.trace.append(("click", point, scene.image_size, reason))

    def drag(self, start, end, scene, reason):
        self.trace.append(("drag", start, end, scene.image_size, reason))

    def input_text(self, text, scene, reason):
        self.trace.append(("input_text", text, scene.image_size, reason))

    def wait(self, seconds):
        self.trace.append(("wait", seconds))


class _DesktopAdapterA(_MemoryAdapterBase):
    pass


class _DesktopAdapterB(_MemoryAdapterBase):
    pass


class _FailingSceneAdapter(_MemoryAdapterBase):
    def read_scene(self, templates=None):
        if self.index >= len(self.scenes):
            raise RuntimeError("capture geometry changed during cleanup")
        return super().read_scene(templates)


class _CancelledSceneAdapter(_MemoryAdapterBase):
    def read_scene(self, templates=None):
        raise CancellationRequested("stop requested")


def _scene(status, box=None):
    texts = [{"text": status, "box": [10, 20, 80, 24]}]
    if box is not None:
        texts[0]["box"] = list(box)
    return SimpleNamespace(
        image_size=(1280, 720),
        texts=texts,
        templates={},
    )


def _classifier(texts, templates, image_size, *, daily_tab_confirmed=False):
    status = texts[0]["text"]
    decision = {"status": status}
    if status in {"open_activity", "open_daily", "join_task", "accept_dialogue"}:
        decision["box"] = texts[0]["box"]
    return decision


def _mijing_classifier(
    texts,
    templates,
    image_size,
    phase,
    *,
    daily_tab_confirmed=False,
):
    status = texts[0]["text"]
    decision = {"status": status}
    if status in {
        "open_activity",
        "open_daily",
        "join_task",
        "enter_dungeon",
        "start_challenge",
        "select_stage",
        "enter_battle",
    }:
        decision["box"] = texts[0]["box"]
    if status == "wait":
        decision["reason"] = "battle"
    return decision


def _status_classifier(texts, templates, phase, image_size):
    status = texts[0]["text"]
    decision = {"status": status}
    if status not in {"completed", "already_completed", "sign_in_confirmed"}:
        decision["box"] = texts[0]["box"]
    return decision


def _kaogu_classifier(
    texts, templates, image_size, *, phase="enter", tool_mode="luoyang_shovel"
):
    status = texts[0]["text"]
    decision = {"status": status}
    if status not in {
        "completed",
        "already_completed",
        "unknown",
        "wait_for_dig",
        "dig_confirmed",
        "wait_for_sale_pathing",
        "sale_candidate_absent",
        "antique_sale_main_confirmed",
    }:
        decision["box"] = texts[0]["box"]
    return decision


class _QuizSource:
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = []

    def observe(self, selected_question_key=None, selected_answers=frozenset()):
        self.calls.append((selected_question_key, selected_answers))
        return self.observations.pop(0)


class _SanjieNavigationSource:
    def __init__(self, observations):
        self.observations = list(observations)
        self.confirmed_states = []

    def observe(self, daily_tab_confirmed):
        self.confirmed_states.append(daily_tab_confirmed)
        return self.observations.pop(0)


def _quiz_observation(status, **decision):
    return QuizObservation(
        _scene(status),
        {"status": status, **decision},
    )


def _quiz_observation_with_templates(status, templates, **decision):
    scene = _scene(status)
    scene.templates = templates
    return QuizObservation(scene, {"status": status, **decision})


class PlatformNeutralWorkflowContractTests(unittest.TestCase):
    def test_same_workflow_has_identical_behavior_on_independent_adapters(self):
        scene_sequence = [
            _scene("open_activity", (100, 50, 80, 30)),
            _scene("open_daily", (120, 140, 90, 30)),
            _scene("join_task", (900, 300, 100, 36)),
            _scene("accept_dialogue", (820, 420, 120, 36)),
            _scene("completed"),
        ]
        outcomes = []

        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type(list(scene_sequence))
            workflow = BaotuWorkflow(max_steps=10, classifier=_classifier)

            result = workflow.execute(WorkflowContext(adapter, adapter))
            self.assertIsInstance(result, WorkflowResult)
            outcomes.append((result, adapter.trace, adapter.requested_templates))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "completed")
        self.assertEqual(outcomes[0][0].payload, {"actions": 4, "clicks": 4})
        self.assertEqual(
            [entry[0] for entry in outcomes[0][1]],
            ["click", "wait", "click", "wait", "click", "wait", "click", "wait"],
        )

    def test_inspection_is_read_only_for_any_adapter(self):
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([_scene("open_activity", (100, 50, 80, 30))])
            workflow = BaotuWorkflow(classifier=_classifier)

            inspection = workflow.execute(
                WorkflowContext(adapter, adapter),
                inspect=True,
            )

            self.assertEqual(inspection.decision["status"], "open_activity")
            self.assertEqual(adapter.trace, [])

    def test_inspection_accepts_optional_platform_evidence(self):
        adapter = _DesktopAdapterA([_scene("open_activity", (100, 50, 80, 30))])
        workflow = BaotuWorkflow(classifier=_classifier)
        evidence_calls = []

        def evidence_provider(current_scene, decision):
            evidence_calls.append((current_scene, decision))
            return {"anchorValidation": "pending", "frameId": "frame-1"}

        inspection = workflow.execute(
            WorkflowContext(adapter, adapter, inspection_evidence_provider=evidence_provider),
            inspect=True,
        )

        self.assertEqual(
            inspection.to_payload()["evidence"],
            {"anchorValidation": "pending", "frameId": "frame-1"},
        )
        self.assertEqual(len(evidence_calls), 1)
        self.assertEqual(adapter.trace, [])

    def test_baotu_completes_after_active_task_disappears_on_stable_main(self):
        scenes = [
            _scene("open_activity", (100, 50, 80, 30)),
            _scene("open_daily", (120, 140, 90, 30)),
            _scene("join_task", (900, 300, 100, 36)),
            _scene("accept_dialogue", (820, 420, 120, 36)),
            _scene("continue_task"),
            *[_scene("open_activity", (100, 50, 80, 30)) for _ in range(5)],
        ]
        adapter = _DesktopAdapterA(scenes)

        result = BaotuWorkflow(max_steps=len(scenes), classifier=_classifier).execute(
            WorkflowContext(adapter, adapter)
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            result.payload["completionEvidence"],
            "stable_main_after_task_disappeared",
        )
        self.assertEqual(result.payload["confirmationPolls"], 5)
        self.assertEqual(
            [entry[0] for entry in adapter.trace].count("click"),
            4,
        )

    def test_baotu_does_not_complete_from_main_without_observed_task_activity(self):
        scenes = [
            _scene("accept_dialogue", (820, 420, 120, 36)),
            *[_scene("open_activity", (100, 50, 80, 30)) for _ in range(5)],
        ]
        adapter = _DesktopAdapterA(scenes)

        with self.assertRaisesRegex(RuntimeError, "超过最大识别步骤"):
            BaotuWorkflow(max_steps=len(scenes), classifier=_classifier).execute(
                WorkflowContext(adapter, adapter)
            )

    def test_mijing_workflow_is_identical_on_independent_adapters(self):
        scene_sequence = [
            _scene("open_activity", (100, 50, 80, 30)),
            _scene("open_daily", (120, 140, 90, 30)),
            _scene("join_task", (900, 300, 100, 36)),
            _scene("enter_dungeon", (700, 220, 120, 30)),
            _scene("start_challenge", (800, 460, 60, 50)),
            _scene("enter_battle", (700, 410, 140, 35)),
            _scene("wait"),
            _scene("completed"),
        ]
        outcomes = []

        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type(list(scene_sequence))
            workflow = MijingxiangyaoWorkflow(
                max_battles=3,
                max_steps=12,
                classifier=_mijing_classifier,
            )

            result = workflow.execute(WorkflowContext(adapter, adapter))
            self.assertIsInstance(result, WorkflowResult)
            outcomes.append((result, adapter.trace, adapter.requested_templates))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "completed")
        self.assertEqual(
            outcomes[0][0].payload,
            {
                "battles": 1,
                "max_battles": 3,
                "actions": 6,
                "clicks": 6,
            },
        )
        self.assertEqual(
            [entry[0] for entry in outcomes[0][1]],
            [
                "click",
                "wait",
                "click",
                "wait",
                "click",
                "wait",
                "click",
                "wait",
                "click",
                "wait",
                "click",
                "wait",
                "wait",
            ],
        )

    def test_keju_answering_is_identical_on_independent_adapters(self):
        outcomes = []
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource([
                _quiz_observation(
                    "ready",
                    question="第1/1题 示例题",
                    question_key="round:1",
                    question_round=(1, 1),
                    answer="甲",
                    click=(620, 360),
                )
            ])

            outcome = KejuWorkflow(
                source,
                max_answers=20,
                delay=0.25,
            ).execute(WorkflowContext(adapter, adapter))
            outcomes.append((outcome, adapter.trace))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "completed")
        self.assertEqual(
            outcomes[0][0].payload,
            {"answers": 1, "clicks": 1, "limit": 20},
        )
        self.assertEqual(
            [entry[0] for entry in outcomes[0][1]],
            ["click", "wait"],
        )

    def test_keju_completes_at_fixed_final_round_without_total_in_ocr(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource([
            _quiz_observation(
                "ready",
                question="第7题 示例题七",
                question_key="round:7",
                question_number=7,
                answer="甲",
                click=(620, 360),
            ),
            _quiz_observation(
                "ready",
                question="第8题 示例题八",
                question_key="round:8",
                question_number=8,
                answer="甲",
                click=(620, 360),
            ),
            _quiz_observation(
                "ready",
                question="第9题 示例题九",
                question_key="round:9",
                question_number=9,
                answer="甲",
                click=(620, 360),
            ),
            _quiz_observation(
                "ready",
                question="第10题 示例题十",
                question_key="round:10",
                question_number=10,
                answer="甲",
                click=(620, 360),
            ),
        ])

        outcome = KejuWorkflow(
            source,
            max_answers=10,
            final_question_number=10,
            delay=0,
        ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.payload, {"answers": 4, "clicks": 4, "limit": 10})
        self.assertEqual(
            [entry[0] for entry in adapter.trace],
            ["click", "click", "click", "click"],
        )

    def test_keju_same_question_never_causes_a_second_click(self):
        adapter = _DesktopAdapterA([])
        repeated = _quiz_observation(
            "ready",
            question="第1/2题 示例题",
            question_key="round:1",
            question_round=(1, 2),
            answer="甲",
            click=(620, 360),
        )
        source = _QuizSource([repeated, repeated, repeated])

        with self.assertRaisesRegex(RuntimeError, "同一道科举题连续未变化"):
            KejuWorkflow(
                source,
                max_answers=2,
                delay=0,
                max_same_question_polls=2,
            ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual([entry[0] for entry in adapter.trace], ["click", "wait"])

    def test_keju_same_round_never_reclicks_when_ocr_question_key_changes(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource([
            _quiz_observation(
                "ready",
                question="剩余时间促进人体吸收钙的主要物质是",
                question_key="促进人体吸收钙的主要物质是",
                question_number=9,
                question_round=(9, 15),
                answer="维生素D",
                click=(420, 447),
            ),
            _quiz_observation(
                "ready",
                question="圆促进人体肠胃吸收钙的主要物质是",
                question_key="圆促进人体肠胃吸收钙的主要物质是",
                question_number=9,
                question_round=(9, 15),
                answer="维生素D",
                click=(421, 447),
            ),
            _quiz_observation(
                "ready",
                question="促进人体肠胃吸收钙的主要物质是",
                question_key="促进人体肠胃吸收钙的主要物质是",
                question_number=9,
                question_round=(9, 15),
                answer="维生素D",
                click=(421, 447),
            ),
        ])

        with self.assertRaisesRegex(RuntimeError, "同一道科举题连续未变化"):
            KejuWorkflow(
                source,
                max_answers=20,
                delay=0,
                max_same_question_polls=2,
            ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual([entry[0] for entry in adapter.trace], ["click", "wait"])

    def test_keju_retries_unreliable_frames_before_answering(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource([
            _quiz_observation("unreliable"),
            _quiz_observation("no_question"),
            _quiz_observation(
                "ready",
                question="第2/2题 示例题",
                question_key="round:2",
                question_round=(2, 2),
                answer="甲",
                click=(620, 360),
            ),
        ])

        outcome = KejuWorkflow(
            source,
            max_answers=2,
            delay=0.25,
            max_recognition_failures=4,
        ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(
            [entry[0] for entry in adapter.trace],
            ["wait", "wait", "click", "wait"],
        )

    def test_keju_retry_reports_the_recognition_reason(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource([
            _quiz_observation("unreliable", reason="模型没有引用有效网页证据"),
            _quiz_observation(
                "ready",
                question="第1/1题 示例题",
                question_key="round:1",
                question_round=(1, 1),
                answer="甲",
                click=(620, 360),
            ),
        ])
        events = []

        outcome = KejuWorkflow(source, max_answers=1, delay=0).execute(
            WorkflowContext(
                adapter,
                adapter,
                lambda event, **payload: events.append((event, payload)),
            )
        )

        self.assertEqual(outcome.status, "completed")
        retry = next(payload for event, payload in events if event == "recognition_retry")
        self.assertEqual(retry["polls"], 1)
        self.assertEqual(retry["limit"], 4)
        self.assertEqual(retry["reason"], "模型没有引用有效网页证据")

    def test_keju_stops_after_bounded_unreliable_frames_without_clicking(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource([
            _quiz_observation("unreliable"),
            _quiz_observation("unreliable"),
            _quiz_observation("unreliable"),
        ])

        with self.assertRaisesRegex(RuntimeError, "连续多帧无法可靠决策"):
            KejuWorkflow(
                source,
                delay=0.25,
                max_recognition_failures=3,
            ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual([entry[0] for entry in adapter.trace], ["wait", "wait"])

    def test_sanjie_multi_select_counts_choices_as_one_question_on_any_adapter(self):
        outcomes = []
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource([
                _quiz_observation(
                    "ready",
                    question="第一题",
                    question_key="第一题",
                    answer="甲",
                    click=(500, 300),
                ),
                _quiz_observation(
                    "ready",
                    question="第一题",
                    question_key="第一题",
                    answer="乙",
                    click=(700, 300),
                ),
                _quiz_observation(
                    "ready",
                    question="第二题",
                    question_key="第二题",
                    answer="丙",
                    click=(500, 420),
                ),
            ])

            with self.assertRaisesRegex(RuntimeError, "未识别到明确完成状态") as raised:
                SanjieqiyuanWorkflow(
                    source,
                    lambda: "completed",
                    max_questions=1,
                    initial_wait_seconds=0,
                    answer_wait_seconds=0,
                ).execute(WorkflowContext(adapter, adapter))
            outcomes.append((str(raised.exception), adapter.trace, source.calls))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertIn("固定 1 题", outcomes[0][0])
        self.assertEqual([entry[0] for entry in outcomes[0][1]], ["click", "click"])
        self.assertEqual(outcomes[0][2][1], ("第一题", frozenset({"甲"})))

    def test_sanjie_resumes_numbered_final_multi_select_without_recounting_ocr_drift(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource([
            _quiz_observation(
                "ready",
                question="找出2个与牛魔王有爱情纠葛的角色",
                question_key="第10题",
                question_number=10,
                answer="铁扇公主",
                click=(650, 360),
            ),
            _quiz_observation("finished"),
        ])

        result = SanjieqiyuanWorkflow(
            source,
            lambda: "completed",
            max_questions=10,
            initial_wait_seconds=0,
            answer_wait_seconds=0,
        ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["questions"], 10)
        self.assertEqual([entry[0] for entry in adapter.trace], ["click"])
        self.assertEqual(source.calls[1], ("第10题", frozenset({"铁扇公主"})))

    def test_sanjie_retries_transient_answer_ocr_failures_without_input(self):
        outcomes = []
        observations = [
            _quiz_observation(
                "answer_not_found",
                question="找出3个生活在地府的角色",
                question_key="第3题：找出3个生活在地府的角色",
                candidates=["千年僵尸", "孤魂野鬼", "吸血鬼"],
            ),
            _quiz_observation(
                "answer_not_found",
                question="找出3个生活在地府的角色",
                question_key="第3题：找出3个生活在地府的角色",
                candidates=["千年僵尸", "孤魂野鬼", "吸血鬼"],
            ),
            _quiz_observation(
                "ready",
                question="找出3个生活在地府的角色",
                question_key="第3题：找出3个生活在地府的角色",
                answer="千年僵尸",
                click=(500, 300),
            ),
            _quiz_observation("finished"),
        ]
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource(list(observations))

            outcome = SanjieqiyuanWorkflow(
                source,
                lambda: "completed",
                initial_wait_seconds=0,
                answer_wait_seconds=0,
            ).execute(WorkflowContext(adapter, adapter))
            outcomes.append((outcome, adapter.trace))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "completed")
        self.assertEqual(outcomes[0][0].payload, {"questions": 1, "clicks": 1})
        self.assertEqual(
            [entry[0] for entry in outcomes[0][1]],
            ["wait", "wait", "click"],
        )

    def test_sanjie_waits_ten_seconds_for_slow_multi_select_transition(self):
        adapter = _DesktopAdapterA([])
        awaiting = _quiz_observation(
            "awaiting_transition",
            question="找出2个示例角色",
            question_key="第4题",
        )
        source = _QuizSource([
            _quiz_observation(
                "ready",
                question="找出2个示例角色",
                question_key="第4题",
                answer="甲",
                click=(500, 300),
            ),
            *[awaiting for _ in range(8)],
            _quiz_observation("finished"),
        ])
        events = []

        result = SanjieqiyuanWorkflow(
            source,
            lambda: "completed",
            initial_wait_seconds=0,
            answer_wait_seconds=0,
        ).execute(
            WorkflowContext(
                adapter,
                adapter,
                lambda event, **payload: events.append((event, payload)),
            )
        )

        self.assertEqual(result.status, "completed")
        waits = [payload for event, payload in events if event == "answer_transition_wait"]
        self.assertEqual(len(waits), 8)
        self.assertEqual(waits[-1]["limit"], 20)
        self.assertEqual(waits[-1]["selectedAnswers"], ["甲"])
        self.assertEqual(
            [entry[0] for entry in adapter.trace],
            ["click", *["wait"] * 8],
        )

    def test_sanjie_stops_after_bounded_answer_ocr_retries(self):
        adapter = _DesktopAdapterA([])
        missed = _quiz_observation(
            "answer_not_found",
            question="示例题",
            question_key="第一题",
            candidates=["甲"],
        )

        with self.assertRaisesRegex(RuntimeError, "题库答案未在当前选项中找到"):
            SanjieqiyuanWorkflow(
                _QuizSource([missed, missed, missed, missed]),
                lambda: "completed",
                initial_wait_seconds=0,
            ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual([entry[0] for entry in adapter.trace], ["wait", "wait", "wait"])

    def test_sanjie_navigation_is_identical_on_independent_adapters(self):
        outcomes = []
        observations = [
            _quiz_observation("click_activity", click=(280, 58)),
            _quiz_observation("click_daily", click=(140, 105)),
            _quiz_observation("scroll_daily", reason="target_not_visible"),
            _quiz_observation("click_join", click=(840, 327)),
            _quiz_observation("answering"),
        ]
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _SanjieNavigationSource(list(observations))

            outcome = SanjieNavigationWorkflow(source).execute(
                WorkflowContext(adapter, adapter)
            )
            outcomes.append((outcome, adapter.trace, source.confirmed_states))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "answering")
        self.assertEqual(
            outcomes[0][0].payload,
            {"actions": 4, "clicks": 3, "drags": 1},
        )
        self.assertEqual(
            [entry[0] for entry in outcomes[0][1]],
            ["click", "wait", "click", "wait", "drag", "wait", "click", "wait"],
        )
        self.assertEqual(outcomes[0][2], [False, False, True, True, False])

    def test_quiz_preview_has_zero_input_on_any_adapter(self):
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource([
                _quiz_observation(
                    "ready",
                    question="第1/1题 示例题",
                    question_key="round:1",
                    answer="甲",
                    click=(620, 360),
                )
            ])

            inspection = KejuWorkflow(source).execute(
                WorkflowContext(adapter, adapter),
                preview=True,
            )

            self.assertEqual(inspection.decision["question_key"], "round:1")
            self.assertFalse(any(entry[0] in {"click", "drag"} for entry in adapter.trace))

    def test_zhuogui_workflow_is_identical_on_independent_adapters(self):
        outcomes = []
        observations = [
            _quiz_observation("main_screen", team=(1200, 100)),
            _quiz_observation("team_quick_panel", create=(900, 420)),
            _quiz_observation(
                "team_page",
                target_is_ghost=True,
                full=True,
                close=(1120, 80, 40, 40),
            ),
            _quiz_observation_with_templates(
                "main_screen",
                {"small_map": (80, 40, 40, 40)},
                team=(1200, 100),
            ),
            _quiz_observation("map_zhongkui", click=(400, 300)),
            _quiz_observation("zhongkui_dialog", click=(700, 500)),
            _quiz_observation("ghost_task", click=(1100, 250), progress=1),
            _quiz_observation(
                "round_complete",
                confirm=(700, 480),
                cancel=(560, 480),
            ),
        ]
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource(list(observations))
            clock_values = iter((0.0, 1.0, 2.0, 10.0, 11.0, 12.0, 13.0, 14.0))

            outcome = ZhuoguiWorkflow(
                source,
                target_rounds=1,
                clock=lambda: next(clock_values),
            ).execute(WorkflowContext(adapter, adapter))
            outcomes.append((outcome, adapter.trace))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "completed")
        self.assertEqual(outcomes[0][0].payload["rounds"], 1)
        self.assertEqual(outcomes[0][0].payload["ghost_progress"], 1)
        self.assertEqual(outcomes[0][0].payload["actions"], 8)
        self.assertEqual(
            [entry[0] for entry in outcomes[0][1]],
            [
                "click", "wait", "click", "wait", "click", "wait",
                "click", "wait", "click", "wait", "click", "wait",
                "click", "wait", "click",
            ],
        )

    def test_zhuogui_level_adjustment_is_an_atomic_drag_on_any_adapter(self):
        outcomes = []
        observations = [
            _quiz_observation(
                "target_dialog",
                target_is_ghost=True,
                current_min_level=20,
                current_max_level=115,
            ),
            _quiz_observation(
                "target_dialog",
                target_is_ghost=True,
                current_min_level=90,
                current_max_level=115,
                confirm=(509, 540),
            ),
            _quiz_observation("already_completed"),
        ]
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource(list(observations))
            clock_values = iter((0.0, 1.0, 2.0))

            outcome = ZhuoguiWorkflow(
                source,
                clock=lambda: next(clock_values),
            ).execute(WorkflowContext(adapter, adapter))
            outcomes.append((outcome, adapter.trace))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "already_completed")
        self.assertEqual(outcomes[0][0].payload["actions"], 2)
        self.assertEqual(outcomes[0][0].payload["clicks"], 1)
        self.assertEqual(outcomes[0][0].payload["drags"], 1)
        self.assertEqual(
            [entry[0] for entry in outcomes[0][1]],
            [
                "drag", "wait",
                "click", "wait",
            ],
        )

    def test_zhuogui_confirms_target_without_editing_description(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource(
            [
                _quiz_observation(
                    "target_dialog",
                    target_is_ghost=True,
                    current_min_level=90,
                    current_max_level=115,
                    confirm=(509, 540),
                ),
                _quiz_observation("already_completed"),
            ]
        )
        clock_values = iter((0.0, 1.0))

        outcome = ZhuoguiWorkflow(
            source,
            clock=lambda: next(clock_values),
        ).execute(WorkflowContext(adapter, adapter))

        self.assertEqual(outcome.status, "already_completed")
        self.assertEqual(
            [entry[0] for entry in adapter.trace],
            ["click", "wait"],
        )

    def test_zhuogui_selects_then_saves_formation_on_any_adapter(self):
        outcomes = []
        observations = [
            _quiz_observation(
                "team_page",
                current_formation="虎翼阵",
                formation=(180, 165),
            ),
            _quiz_observation(
                "formation_menu",
                formations={"天覆阵": (260, 252), "虎翼阵": (260, 187)},
                selected_formation="虎翼阵",
                save=(500, 512),
            ),
            _quiz_observation(
                "formation_menu",
                formations={"虎翼阵": (260, 187), "地载阵": (260, 317)},
                selected_formation="天覆阵",
                save=(500, 512),
            ),
            _quiz_observation("already_completed"),
        ]
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource(list(observations))
            clock_values = iter((0.0, 1.0, 2.0, 3.0))

            outcome = ZhuoguiWorkflow(
                source,
                formation="天覆阵",
                clock=lambda: next(clock_values),
            ).execute(WorkflowContext(adapter, adapter))
            outcomes.append((outcome, adapter.trace))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "already_completed")
        self.assertEqual(outcomes[0][0].payload["actions"], 3)
        self.assertEqual(
            [entry[3] for entry in outcomes[0][1] if entry[0] == "click"],
            ["打开阵法选择", "选择天覆阵", "保存天覆阵"],
        )

    def test_zhuogui_creates_team_before_configuring_formation(self):
        outcomes = []
        observations = [
            _quiz_observation(
                "team_page",
                create=(176, 543),
                current_formation="虎翼阵",
                formation=(185, 168),
            ),
            _quiz_observation(
                "team_page",
                exit=(176, 543),
                current_formation="虎翼阵",
                formation=(185, 168),
            ),
            _quiz_observation(
                "formation_menu",
                formations={"天覆阵": (260, 252), "虎翼阵": (260, 187)},
                selected_formation="虎翼阵",
                save=(507, 519),
            ),
            _quiz_observation(
                "formation_menu",
                formations={"天覆阵": (260, 252), "虎翼阵": (260, 187)},
                selected_formation="天覆阵",
                save=(507, 519),
            ),
            _quiz_observation("already_completed"),
        ]
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([])
            source = _QuizSource(list(observations))
            clock_values = iter((0.0, 1.0, 2.0, 3.0, 4.0))

            outcome = ZhuoguiWorkflow(
                source,
                formation="天覆阵",
                clock=lambda: next(clock_values),
            ).execute(WorkflowContext(adapter, adapter))
            outcomes.append((outcome, adapter.trace))

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0].status, "already_completed")
        self.assertEqual(
            [entry[3] for entry in outcomes[0][1] if entry[0] == "click"],
            ["创建队伍", "打开阵法选择", "选择天覆阵", "保存天覆阵"],
        )

    def test_zhuogui_target_confirmation_has_a_hard_repeat_limit(self):
        adapter = _DesktopAdapterA([])
        source = _QuizSource(
            [
                _quiz_observation(
                    "target_dialog", current_min_level=90,
                    current_max_level=115, confirm=(509, 540),
                ),
                _quiz_observation(
                    "target_dialog", current_min_level=90,
                    current_max_level=115, confirm=(509, 540),
                ),
                _quiz_observation(
                    "target_dialog", current_min_level=90,
                    current_max_level=115, confirm=(509, 540),
                ),
            ]
        )
        state = WorkflowState(
            target_daily_clicked=True,
            target_ghost_clicked=True,
            target_high_range_clicked=True,
        )

        with self.assertRaisesRegex(RuntimeError, "确认队伍目标后弹窗仍未关闭"):
            ZhuoguiWorkflow(
                source,
                clock=iter((0.0, 1.0, 2.0)).__next__,
            ).execute(WorkflowContext(adapter, adapter), state)

        self.assertEqual(
            [entry[3] for entry in adapter.trace if entry[0] == "click"],
            ["确认90-115抓鬼目标", "确认90-115抓鬼目标"],
        )

    def test_mijing_inspection_is_read_only_on_any_adapter(self):
        for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
            adapter = adapter_type([_scene("open_activity", (100, 50, 80, 30))])
            workflow = MijingxiangyaoWorkflow(classifier=_mijing_classifier)

            inspection = workflow.execute(
                WorkflowContext(adapter, adapter),
                inspect=True,
            )

            self.assertEqual(inspection.decision["status"], "open_activity")
            self.assertEqual(adapter.trace, [])

    def test_mijing_navigation_budget_is_shared_with_task_actions(self):
        adapter = _DesktopAdapterA(
            [
                _scene("open_activity", (100, 50, 80, 30)),
                _scene("open_daily", (120, 140, 90, 30)),
                _scene("enter_dungeon", (700, 220, 120, 30)),
            ]
        )
        workflow = MijingxiangyaoWorkflow(
            max_steps=4,
            max_navigation_inputs=2,
            classifier=_mijing_classifier,
        )

        with self.assertRaisesRegex(RuntimeError, "活动导航达到输入上限"):
            workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(
            [entry[0] for entry in adapter.trace],
            ["click", "wait", "click", "wait"],
        )

    def test_sign_in_and_kaogu_workflows_are_platform_neutral(self):
        cases = (
            (
                lambda: WelfareSignInWorkflow(max_steps=5, classifier=_status_classifier),
                [_scene("open_welfare"), _scene("scratch_ready"), _scene("completed")],
                {"actions": 6, "clicks": 1},
            ),
            (
                lambda: ClanSignInWorkflow(max_steps=8, classifier=_status_classifier),
                [
                    _scene("expand_menu"),
                    _scene("open_clan"),
                    _scene("open_welfare"),
                    _scene("sign_in"),
                    _scene("sign_in_confirmed"),
                    _scene("completed"),
                ],
                {"actions": 4, "clicks": 4},
            ),
            (
                lambda: KaoguWorkflow(
                    target_digs=2,
                    max_steps=10,
                    max_inventory_scan_drags=1,
                    classifier=_kaogu_classifier,
                ),
                [
                    _scene("buy_shovel"),
                    _scene("close_shop"),
                    _scene("select_shovel"),
                    _scene("use_shovel"),
                    _scene("start_archaeology"),
                    _scene("dig"),
                    _scene("dig_confirmed"),
                    _scene("dig"),
                    _scene("dig_confirmed"),
                    _scene("open_inventory_for_sale"),
                    _scene("normalize_inventory"),
                    _scene("normalize_inventory"),
                    _scene("normalize_inventory"),
                    _scene("organize_inventory"),
                    _scene("activate_antique"),
                    _scene("wait_for_sale_pathing"),
                    _scene("sell_antique"),
                    _scene("cancel_genuine_sale"),
                    _scene("close_sale_shop"),
                    _scene("open_inventory_for_sale"),
                    _scene("normalize_inventory"),
                    _scene("normalize_inventory"),
                    _scene("normalize_inventory"),
                    _scene("organize_inventory"),
                    _scene("scan_inventory"),
                    _scene("close_inventory"),
                    _scene("antique_sale_main_confirmed"),
                    _scene("antique_sale_main_confirmed"),
                ],
                {
                    "digs": 2,
                    "target_digs": 2,
                    "purchases": 1,
                    "sale_clicks": 1,
                    "sale_batches": 1,
                    "sale_outcome": "all_non_genuine_sold_genuine_retained",
                    "genuine_protected": True,
                    "cleanup_status": "completed",
                    "cleanup_warning": None,
                    "actions": 23,
                    "clicks": 17,
                    "drags": 7,
                },
            ),
        )

        for workflow_factory, scenes, expected_payload in cases:
            outcomes = []
            for adapter_type in (_DesktopAdapterA, _DesktopAdapterB):
                adapter = adapter_type(list(scenes))
                result = workflow_factory().execute(WorkflowContext(adapter, adapter))
                outcomes.append((result, adapter.trace, adapter.requested_templates))

            self.assertEqual(outcomes[0], outcomes[1])
            self.assertEqual(outcomes[0][0].status, "completed")
            self.assertEqual(outcomes[0][0].payload, expected_payload)

    def test_kaogu_can_resume_at_sale_without_any_dig_or_purchase_input(self):
        adapter = _DesktopAdapterA([
            _scene("open_inventory_for_sale"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("organize_inventory"),
            _scene("activate_antique"),
            _scene("wait_for_sale_pathing"),
            _scene("sell_antique"),
            _scene("cancel_genuine_sale"),
            _scene("close_sale_shop"),
            _scene("open_inventory_for_sale"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("organize_inventory"),
            _scene("scan_inventory"),
            _scene("close_inventory"),
            _scene("antique_sale_main_confirmed"),
            _scene("antique_sale_main_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=10,
            initial_digs=10,
            max_inventory_scan_drags=1,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["digs"], 10)
        self.assertEqual(result.payload["purchases"], 0)
        reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertNotIn("购买一个洛阳铲", reasons)
        self.assertFalse(any("次挖掘" in reason for reason in reasons))

    def test_kaogu_preserves_completed_digs_when_cleanup_capture_fails(self):
        adapter = _FailingSceneAdapter([
            _scene("dig"),
            _scene("dig_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=1,
            max_steps=3,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["digs"], 1)
        self.assertEqual(result.payload["clicks"], 1)
        self.assertEqual(result.payload["sale_outcome"], "cleanup_incomplete")
        self.assertEqual(result.payload["cleanup_status"], "incomplete")
        self.assertEqual(
            result.payload["cleanup_warning"],
            "capture geometry changed during cleanup",
        )

    def test_kaogu_next_dig_button_confirms_a_prior_accepted_dig_with_missed_ocr(self):
        adapter = _DesktopAdapterA([
            _scene("dig"),
            _scene("wait_for_dig"),
            _scene("wait_for_dig"),
            _scene("dig"),
            _scene("dig_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=2,
            max_steps=5,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["digs"], 2)
        click_reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertEqual(click_reasons, ["进行第 1 次挖掘", "进行第 2 次挖掘"])

    def test_kaogu_retries_only_when_the_pending_dig_button_stays_visible(self):
        adapter = _DesktopAdapterA([
            _scene("dig"),
            _scene("dig"),
            _scene("dig_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=1,
            max_steps=3,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        click_reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertEqual(click_reasons, ["进行第 1 次挖掘", "进行第 1 次挖掘"])

    def test_kaogu_waits_for_pending_dig_before_closing_temporary_bag(self):
        adapter = _DesktopAdapterA([
            _scene("dig"),
            _scene("close_temporary_bag"),
            _scene("dig_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=1,
            max_steps=3,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        click_reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertEqual(click_reasons, ["进行第 1 次挖掘"])

    def test_kaogu_cleanup_closes_archaeology_before_selling(self):
        adapter = _DesktopAdapterA([
            _scene("close_archaeology_for_sale"),
            _scene("antique_sale_main_confirmed"),
            _scene("antique_sale_main_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=1,
            initial_digs=1,
            max_steps=3,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        click_reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertEqual(click_reasons, ["考古流程：close_archaeology_for_sale"])

    def test_kaogu_cleanup_closes_auxiliary_skills_before_selling(self):
        adapter = _DesktopAdapterA([
            _scene("close_skills_for_sale"),
            _scene("antique_sale_main_confirmed"),
            _scene("antique_sale_main_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=1,
            initial_digs=1,
            max_steps=3,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        click_reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertEqual(click_reasons, ["考古流程：close_skills_for_sale"])

    def test_kaogu_does_not_downgrade_capture_failure_before_target(self):
        adapter = _FailingSceneAdapter([])
        workflow = KaoguWorkflow(target_digs=1, classifier=_kaogu_classifier)

        with self.assertRaisesRegex(RuntimeError, "capture geometry changed"):
            workflow.execute(WorkflowContext(adapter, adapter))

    def test_kaogu_does_not_downgrade_cancellation_after_target(self):
        adapter = _CancelledSceneAdapter([])
        workflow = KaoguWorkflow(
            target_digs=1,
            initial_digs=1,
            classifier=_kaogu_classifier,
        )

        with self.assertRaisesRegex(CancellationRequested, "stop requested"):
            workflow.execute(WorkflowContext(adapter, adapter))

    def test_kaogu_rescans_and_sells_a_second_inventory_batch(self):
        adapter = _DesktopAdapterA([
            _scene("open_inventory_for_sale"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("organize_inventory"),
            _scene("activate_antique"),
            _scene("wait_for_sale_pathing"),
            _scene("sell_antique"),
            _scene("antique_sale_finished"),
            _scene("close_sale_shop"),
            _scene("open_inventory_for_sale"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("organize_inventory"),
            _scene("activate_antique"),
            _scene("wait_for_sale_pathing"),
            _scene("sell_antique"),
            _scene("antique_sale_finished"),
            _scene("close_sale_shop"),
            _scene("open_inventory_for_sale"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("organize_inventory"),
            _scene("scan_inventory"),
            _scene("close_inventory"),
            _scene("antique_sale_main_confirmed"),
            _scene("antique_sale_main_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=10,
            initial_digs=10,
            max_inventory_scan_drags=1,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["sale_batches"], 2)
        self.assertEqual(result.payload["sale_clicks"], 2)
        reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertEqual(
            reasons.count("双击非真品古董并启动自动寻路"),
            4,
        )
        self.assertEqual(reasons.count("考古流程：close_sale_shop"), 2)
        self.assertEqual(reasons.count("考古流程：close_inventory"), 1)

    def test_kaogu_sells_every_visible_antique_before_closing_shop(self):
        adapter = _DesktopAdapterA([
            _scene("sell_antique"),
            _scene("sell_antique"),
            _scene("sell_antique"),
            _scene("sale_candidate_absent"),
            _scene("sale_candidate_absent"),
            _scene("sale_candidate_absent"),
            _scene("close_sale_shop"),
            _scene("open_inventory_for_sale"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("organize_inventory"),
            _scene("scan_inventory"),
            _scene("close_inventory"),
            _scene("antique_sale_main_confirmed"),
            _scene("antique_sale_main_confirmed"),
        ])
        workflow = KaoguWorkflow(
            target_digs=10,
            initial_digs=10,
            max_inventory_scan_drags=1,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["sale_clicks"], 3)
        reasons = [entry[-1] for entry in adapter.trace if entry[0] == "click"]
        self.assertEqual(reasons.count("考古流程：sell_antique"), 3)
        self.assertEqual(reasons.count("考古流程：close_sale_shop"), 1)

    def test_kaogu_reports_incomplete_cleanup_after_bounded_inventory_scan(self):
        adapter = _DesktopAdapterA([
            _scene("open_inventory_for_sale"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("normalize_inventory"),
            _scene("organize_inventory"),
            _scene("scan_inventory"),
            _scene("scan_inventory"),
        ])
        workflow = KaoguWorkflow(
            target_digs=10,
            initial_digs=10,
            max_inventory_scan_drags=1,
            classifier=_kaogu_classifier,
        )

        result = workflow.execute(WorkflowContext(adapter, adapter))

        clicks = [entry for entry in adapter.trace if entry[0] == "click"]
        drags = [entry for entry in adapter.trace if entry[0] == "drag"]
        self.assertEqual(len(clicks), 2)
        self.assertEqual(len(drags), 4)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["cleanup_status"], "incomplete")
        self.assertIn("未找到非真品古董", result.payload["cleanup_warning"])


if __name__ == "__main__":
    unittest.main()
