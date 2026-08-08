import unittest
from types import SimpleNamespace

from tools.automation_calibration import CaptureSignature
from tools.macos_window_backend import CapturedFrame
from tools.return_to_main_workflow import RETURN_TO_MAIN_TEMPLATES, ReturnToMainWorkflow


def signature(source="maa"):
    return CaptureSignature(source, (1020, 718), (1020, 718), (0, 0, 1020, 718))


def scene(texts, templates=None, *, frame=None, capture_signature=None):
    capture_signature = capture_signature or signature()
    frame = frame or CapturedFrame(
        image=None, signature=capture_signature, capture_method=capture_signature.source
    )
    return SimpleNamespace(
        image_size=(1020, 718),
        texts=[{"text": text, "box": box} for text, box in texts],
        templates=templates or {},
        capture_signature=capture_signature,
        frame=frame,
    )


def main_scene(**kwargs):
    return scene(
        [
            ("任务", [780, 80, 45, 25]),
            ("队伍", [930, 95, 45, 25]),
            ("福利", [10, 135, 45, 25]),
            ("当前", [8, 640, 45, 25]),
        ],
        {"menu_plus": [960, 670, 30, 30]},
        **kwargs,
    )


def main_scene_with_task_tracker(**kwargs):
    current = main_scene(**kwargs)
    current.texts.extend(
        [
            {"text": "师门任务", "box": [830, 145, 80, 24]},
            {"text": "领取今天师门任务", "box": [830, 175, 150, 24]},
        ]
    )
    return current


class Guard:
    def checkpoint(self):
        return None

    def wait(self, _seconds):
        return None


class Runtime:
    target_id = "local-primary"

    def __init__(self, scenes):
        self.scenes = iter(scenes)
        self.clicks = []

    def read_scene(self, _templates):
        return next(self.scenes)

    def click_box(self, box, current_scene, reason):
        self.clicks.append((box, current_scene.frame.frame_id, reason))


class ReturnToMainWorkflowTests(unittest.TestCase):
    PAGE_LABELS = {
        "sanjieqiyuan": "三界奇缘",
        "keju": "科举乡试",
        "keju_huishi": "科举会试",
        "zhuogui": "创建队伍",
        "fuli_qiandao": "每日刮刮乐 福利",
        "bangpai_qiandao": "帮派福利 帮派分红",
        "baotu": "日常活动 宝图任务",
        "fun_news": "趣闻鉴赏",
        "yunbiao": "日常活动 押送普通镖银",
        "shimen": "日常活动 师门任务",
        "mijingxiangyao": "秘境降妖 第18关",
        "kaogu": "工坊考古 技能生产",
    }

    def test_main_screen_templates_include_current_fixed_menu_state(self):
        self.assertIn(
            "zonghe/jiahao_live_923.png",
            RETURN_TO_MAIN_TEMPLATES["menu_plus"],
        )

    def known_page(self, label):
        return scene(
            [(label, [350, 80, 260, 35])],
            {"close_generic": [920, 45, 35, 35]},
        )

    def test_all_verified_task_pages_recover_to_two_frame_main_screen(self):
        for task_id, label in self.PAGE_LABELS.items():
            with self.subTest(task_id=task_id):
                runtime = Runtime([self.known_page(label), main_scene(), main_scene()])
                result = ReturnToMainWorkflow(task_id, wait_seconds=0).execute(runtime, Guard())
                self.assertEqual(result.status, "confirmed")
                self.assertEqual(result.outcome, "recovered")
                self.assertEqual(result.clicks, 1)
                self.assertNotEqual(
                    result.evidence["firstFrameId"], result.evidence["lastFrameId"]
                )

    def test_fun_news_feed_close_requires_its_full_page_identity(self):
        feed = scene(
            [
                (channel, [20, 110 + index * 40, 48, 25])
                for index, channel in enumerate(("系统", "世界", "当前", "门派", "帮派", "队伍", "空间"))
            ] + [
                ("打开空间", [15, 75, 58, 16]),
                ("关注", [95, 66, 39, 19]), ("图文", [136, 65, 50, 21]),
                ("视频", [259, 68, 36, 15]), ("论坛", [302, 66, 39, 19]),
                ("+关注", [359, 118, 52, 20]),
            ],
        )
        runtime = Runtime([feed, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("fun_news", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(runtime.clicks[0][0], [454, 294, 41, 101])

    def test_completed_fun_news_activity_card_returns_to_main_screen(self):
        activity_page = scene(
            [
                ("活动", [472, 97, 75, 30]),
                ("日常活动", [130, 161, 88, 27]),
                ("趣闻鉴赏", [669, 314, 85, 25]),
                ("次数5/5", [670, 348, 62, 19]),
                ("活跃5/5", [743, 348, 62, 19]),
                ("完成", [827, 333, 66, 31]),
            ],
            {"close_generic": [892, 96, 39, 40]},
        )
        runtime = Runtime([activity_page, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("fun_news", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.scene, "main_screen")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [892, 96, 39, 40])

    def test_live_welfare_scratch_page_is_an_allowed_close_path(self):
        current = scene(
            [
                ("梦幻刮刮乐", [420, 134, 111, 23]),
                ("今日奖品", [551, 193, 109, 31]),
                ("当前累计：4/7次", [407, 580, 128, 19]),
            ],
            {"close_generic": [809, 131, 32, 37]},
        )

        runtime = Runtime([current, main_scene(), main_scene()])
        result = ReturnToMainWorkflow(
            "fuli_qiandao", wait_seconds=0
        ).execute(runtime, Guard())

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [809, 131, 32, 37])

    def test_welfare_scratch_and_parent_are_distinct_close_actions(self):
        scratch = scene(
            [
                ("梦幻刮刮乐", [420, 134, 111, 23]),
                ("今日奖品", [551, 193, 109, 31]),
            ],
            {"close_generic": [809, 131, 32, 37]},
        )
        welfare = scene(
            [
                ("福利", [435, 134, 70, 24]),
                ("每日福利", [140, 260, 115, 30]),
                ("打开刮刮乐", [650, 580, 140, 35]),
            ],
            {"close_welfare": [808, 137, 52, 53]},
        )

        runtime = Runtime([scratch, welfare, main_scene(), main_scene()])
        result = ReturnToMainWorkflow(
            "fuli_qiandao", wait_seconds=0
        ).execute(runtime, Guard())

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 2)
        self.assertEqual(len(runtime.clicks), 2)

    def test_already_main_uses_zero_input_and_two_distinct_frames(self):
        runtime = Runtime([main_scene(), main_scene()])
        result = ReturnToMainWorkflow("baotu").execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "already_main")
        self.assertEqual(result.clicks, 0)
        self.assertEqual(runtime.clicks, [])

    def test_expanded_chat_panel_uses_scaled_fixed_collapse_control(self):
        chat = scene(
            [
                ("系统", [20, 110, 48, 24]),
                ("世界", [20, 150, 48, 24]),
                ("当前", [20, 190, 48, 24]),
                ("门派", [20, 230, 48, 24]),
                ("帮派", [20, 270, 48, 24]),
                ("队伍", [20, 310, 48, 24]),
                ("点击这里输入", [120, 55, 120, 24]),
                ("发送", [355, 53, 48, 28]),
                ("任务", [780, 80, 45, 25]),
                ("队伍", [930, 95, 45, 25]),
            ],
            {"menu_plus": [960, 670, 30, 30]},
        )
        runtime = Runtime([chat, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("kaogu", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [474, 309, 41, 86])

    def test_team_quick_panel_uses_verified_spiral_collapse_control(self):
        team_panel = scene(
            [
                ("任务", [780, 80, 45, 25]),
                ("队伍", [930, 95, 45, 25]),
                ("福利", [10, 135, 45, 25]),
                ("当前", [8, 640, 45, 25]),
                ("请组建或加入队伍", [853, 248, 143, 19]),
                ("创建", [853, 351, 43, 20]),
                ("找队", [948, 349, 44, 24]),
            ],
            {"menu_plus": [960, 670, 30, 30]},
        )
        runtime = Runtime([team_panel, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("sanjieqiyuan", wait_seconds=0).execute(
            runtime,
            Guard(),
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [770, 86, 56, 54])

    def test_team_quick_panel_fixed_coordinate_needs_full_identity(self):
        partial_panel = scene(
            [
                ("任务", [780, 80, 45, 25]),
                ("队伍", [930, 95, 45, 25]),
                ("福利", [10, 135, 45, 25]),
                ("当前", [8, 640, 45, 25]),
                ("请组建或加入队伍", [853, 248, 143, 19]),
                ("创建", [853, 351, 43, 20]),
            ],
            {"menu_plus": [960, 670, 30, 30]},
        )
        runtime = Runtime([partial_panel])

        result = ReturnToMainWorkflow("baotu", wait_seconds=0).execute(
            runtime,
            Guard(),
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("team_quick_panel_identity_incomplete", result.reason)
        self.assertEqual(result.clicks, 0)
        self.assertEqual(runtime.clicks, [])

    def test_full_team_panel_closes_only_with_cross_region_identity(self):
        team_panel = scene(
            [
                ("组队", [55, 100, 89, 34]),
                (
                    "你尚未创建队伍，点击便捷组队，可快速匹配到目标队伍",
                    [236, 151, 451, 18],
                ),
                ("创建队伍", [86, 572, 109, 26]),
                ("我的助战", [651, 571, 109, 27]),
                ("队伍", [965, 178, 26, 42]),
                ("申请", [962, 272, 31, 48]),
                ("助战", [253, 201, 54, 28]),
            ],
            {"close_generic": [935, 96, 50, 48]},
        )
        runtime = Runtime([team_panel, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("sanjieqiyuan", wait_seconds=0).execute(
            runtime,
            Guard(),
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [935, 96, 50, 48])

    def test_full_team_panel_without_body_identity_is_not_closed(self):
        incomplete_panel = scene(
            [
                ("组队", [55, 100, 89, 34]),
                ("队伍", [965, 178, 26, 42]),
                ("申请", [962, 272, 31, 48]),
                ("我的助战", [651, 571, 109, 27]),
            ],
            {"close_generic": [935, 96, 50, 48]},
        )
        runtime = Runtime([incomplete_panel])

        result = ReturnToMainWorkflow("baotu", wait_seconds=0).execute(
            runtime,
            Guard(),
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("team_panel_identity_incomplete", result.reason)
        self.assertEqual(result.clicks, 0)
        self.assertEqual(runtime.clicks, [])

    def test_chat_coordinate_is_never_used_without_full_panel_identity(self):
        partial_chat = scene(
            [
                ("世界", [20, 150, 48, 24]),
                ("帮派", [20, 270, 48, 24]),
                ("发送", [355, 53, 48, 28]),
            ],
        )
        runtime = Runtime([partial_chat])

        result = ReturnToMainWorkflow("kaogu", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(runtime.clicks, [])

    def test_battle_cancel_blocks_chat_panel_close(self):
        battle_with_chat = scene(
            [
                ("系统", [20, 110, 48, 24]),
                ("世界", [20, 150, 48, 24]),
                ("当前", [20, 190, 48, 24]),
                ("门派", [20, 230, 48, 24]),
                ("帮派", [20, 270, 48, 24]),
                ("队伍", [20, 310, 48, 24]),
                ("点击这里输入", [120, 55, 120, 24]),
                ("发送", [355, 53, 48, 28]),
                ("取消", [960, 680, 45, 25]),
            ]
        )
        runtime = Runtime([battle_with_chat])

        result = ReturnToMainWorkflow("kaogu", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("battle", result.reason)
        self.assertEqual(runtime.clicks, [])

    def test_chat_message_keywords_do_not_fake_an_unsafe_page(self):
        chat = scene(
            [
                ("系统", [20, 110, 48, 24]),
                ("世界", [20, 150, 48, 24]),
                ("当前", [20, 190, 48, 24]),
                ("门派", [20, 230, 48, 24]),
                ("帮派", [20, 270, 48, 24]),
                ("队伍", [20, 310, 48, 24]),
                ("点击这里输入", [120, 55, 120, 24]),
                ("发送", [355, 53, 48, 28]),
                ("帮派药房上架了新的药品", [95, 480, 170, 20]),
            ]
        )
        runtime = Runtime([chat, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("kaogu", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.clicks, 1)

    def test_task_tracker_text_does_not_mask_the_main_screen(self):
        runtime = Runtime([main_scene_with_task_tracker(), main_scene_with_task_tracker()])
        result = ReturnToMainWorkflow("fuli_qiandao").execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "already_main")
        self.assertEqual(runtime.clicks, [])

    def test_center_overlay_title_still_blocks_main_screen_confirmation(self):
        current = main_scene()
        current.texts.append({"text": "福利", "box": [450, 120, 70, 24]})
        runtime = Runtime([current])
        result = ReturnToMainWorkflow("fuli_qiandao").execute(runtime, Guard())
        self.assertEqual(result.status, "failed")
        self.assertEqual(runtime.clicks, [])

    def test_top_activity_ocr_replaces_an_unstable_toolbar_template(self):
        current = main_scene()
        current.templates["menu_plus"] = None
        current.texts = [
            item for item in current.texts if item["text"] != "当前"
        ]
        current.texts.append({"text": "活动", "box": [242, 65, 30, 18]})
        second = main_scene()
        second.templates["menu_plus"] = None
        second.texts = [item for item in second.texts if item["text"] != "当前"]
        second.texts.append({"text": "活动", "box": [242, 65, 30, 18]})
        runtime = Runtime([current, second])
        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.evidence["anchors"], [
            "activity_toolbar", "left_menu", "task_team_tabs"
        ])

    def test_transient_completion_overlay_waits_without_input(self):
        transient = main_scene()
        transient.texts.extend(
            [
                {"text": "任务完成", "box": [410, 120, 200, 50]},
                {"text": "你从强盗身上搜出了1张藏宝图", "box": [350, 350, 300, 24]},
            ]
        )
        runtime = Runtime([transient, main_scene(), main_scene()])
        result = ReturnToMainWorkflow("baotu", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "already_main")
        self.assertEqual(result.actions, 3)
        self.assertEqual(runtime.clicks, [])

    def test_shimen_completion_uses_confirm_button_not_unrelated_close(self):
        completion = scene(
            [
                ("师门任务完成", [390, 170, 240, 50]),
                ("确定", [420, 520, 180, 48]),
            ],
            {"close_generic": [850, 480, 45, 45]},
        )
        runtime = Runtime([completion, main_scene(), main_scene()])
        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [420, 520, 180, 48])

    def test_generic_close_outside_upper_right_is_never_clicked(self):
        current = scene(
            [("日常活动 师门任务", [350, 80, 260, 35])],
            {"close_generic": [850, 480, 45, 45]},
        )
        runtime = Runtime([current])
        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "failed")
        self.assertEqual(runtime.clicks, [])

    def test_split_shimen_panel_title_uses_dedicated_upper_close(self):
        panel = scene(
            [
                ("师门", [140, 190, 115, 90]),
                ("任务", [190, 255, 55, 45]),
                ("自动完成任务", [590, 500, 110, 22]),
            ],
            {
                "close_shimen": [785, 190, 43, 57],
                "close_generic": [821, 466, 32, 37],
            },
        )
        runtime = Runtime([panel, main_scene(), main_scene()])
        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [785, 190, 43, 57])

    def test_live_shimen_panel_anchors_recover_when_title_ocr_is_corrupted(self):
        panel = scene(
            [
                ("师口", [151, 174, 129, 97]),
                ("住务", [216, 250, 48, 28]),
                ("完成五种不同的任", [143, 313, 149, 21]),
                ("自动选择每日任务", [442, 491, 146, 21]),
                ("自动完成任务", [657, 491, 118, 21]),
            ],
            {
                "close_shimen": [870, 174, 43, 57],
                "close_generic": [909, 458, 32, 37],
            },
        )
        runtime = Runtime([panel, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(
            runtime,
            Guard(),
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(runtime.clicks[0][0], [870, 174, 43, 57])

    def test_shimen_reward_item_popup_has_a_distinct_close_path(self):
        reward = scene(
            [
                ("绿芦羹", [790, 565, 65, 24]),
                ("使用", [780, 610, 70, 34]),
            ],
            {"close_generic": [821, 466, 32, 37]},
        )
        runtime = Runtime([reward, main_scene(), main_scene()])
        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [821, 466, 32, 37])

    def test_live_shimen_reward_popup_accepts_split_use_ocr(self):
        reward = scene(
            [
                ("绿芦羹", [841, 549, 58, 23]),
                ("使 用", [841, 589, 60, 28]),
            ],
            {"close_generic": [905, 439, 39, 40]},
        )
        runtime = Runtime([reward, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(
            runtime,
            Guard(),
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [905, 439, 39, 40])

    def test_shimen_temporary_bag_uses_fixed_scaled_close_without_title_ocr(self):
        temporary_bag = scene(
            [],
            {
                "menu_plus": [960, 670, 30, 30],
                "close_generic": [677, 152, 31, 42],
            },
        )
        runtime = Runtime([temporary_bag, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [668, 147, 46, 50])

    def test_temporary_bag_fixed_close_needs_menu_and_fixed_close_region(self):
        incomplete = scene([], {"close_generic": [677, 152, 31, 42]})
        runtime = Runtime([incomplete])

        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(
            runtime, Guard()
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(runtime.clicks, [])

    def test_live_red_luo_soup_reward_popup_has_a_distinct_close_path(self):
        reward = scene(
            [
                ("红罗羹", [842, 562, 56, 19]),
                ("使用", [841, 599, 60, 27]),
            ],
            {"close_generic": [909, 458, 32, 37]},
        )
        runtime = Runtime([reward, main_scene(), main_scene()])

        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(
            runtime,
            Guard(),
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(runtime.clicks[0][0], [909, 458, 32, 37])

    def test_full_shimen_terminal_chain_recovers_in_one_run(self):
        completion = scene(
            [
                ("师门任务完成", [390, 170, 240, 50]),
                ("确定", [420, 520, 180, 48]),
            ],
            {"close_generic": [850, 480, 45, 45]},
        )
        panel = scene(
            [
                ("师门", [140, 190, 115, 90]),
                ("任务", [190, 255, 55, 45]),
            ],
            {
                "close_shimen": [785, 190, 43, 57],
                "close_generic": [821, 466, 32, 37],
            },
        )
        reward = scene(
            [
                ("绿芦羹", [790, 565, 65, 24]),
                ("使用", [780, 610, 70, 34]),
            ],
            {"close_generic": [821, 466, 32, 37]},
        )
        runtime = Runtime(
            [completion, panel, reward, main_scene(), main_scene()]
        )
        result = ReturnToMainWorkflow("shimen", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 3)
        self.assertEqual(
            [click[0] for click in runtime.clicks],
            [[420, 520, 180, 48], [785, 190, 43, 57], [821, 466, 32, 37]],
        )

    def test_live_sanjie_completion_uses_fixed_close_after_cross_region_anchors(self):
        completion = scene(
            [
                ("三界奇缘国", [103, 251, 46, 189]),
                ("10/10", [281, 157, 48, 19]),
                ("已获得奖励", [207, 460, 127, 21]),
                ("恭喜少侠完成所有题目！", [536, 403, 192, 23]),
            ],
            {"close_shimen": [902, 224, 43, 57]},
        )
        runtime = Runtime([completion, main_scene(), main_scene()])

        result = ReturnToMainWorkflow(
            "sanjieqiyuan", wait_seconds=0
        ).execute(runtime, Guard())

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.clicks, 1)
        self.assertEqual(runtime.clicks[0][0], [882, 129, 51, 86])

    def test_sanjie_fixed_close_requires_all_terminal_anchors(self):
        incomplete = scene(
            [
                ("10/10", [281, 157, 48, 19]),
                ("恭喜少侠完成所有题目！", [536, 403, 192, 23]),
            ],
        )
        runtime = Runtime([incomplete])

        result = ReturnToMainWorkflow(
            "sanjieqiyuan", wait_seconds=0
        ).execute(runtime, Guard())

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.clicks, 0)
        self.assertEqual(runtime.clicks, [])

    def test_rejects_old_frame_for_second_confirmation(self):
        first = main_scene()
        runtime = Runtime([first, first])
        result = ReturnToMainWorkflow("baotu").execute(runtime, Guard())
        self.assertEqual(result.status, "failed")
        self.assertIn("旧帧", result.reason)
        self.assertEqual(runtime.clicks, [])

    def test_rejects_capture_calibration_change(self):
        runtime = Runtime([main_scene(), main_scene(capture_signature=signature("system"))])
        result = ReturnToMainWorkflow("baotu").execute(runtime, Guard())
        self.assertEqual(result.status, "failed")
        self.assertIn("标定", result.reason)
        self.assertEqual(runtime.clicks, [])

    def test_unknown_and_blocked_pages_never_input(self):
        for current in (
            scene([("陌生页面", [300, 100, 100, 30])], {"close_generic": [920, 45, 35, 35]}),
            scene([("自动寻路中", [400, 500, 120, 30])]),
            scene([("挑战失败", [400, 300, 120, 30])]),
        ):
            runtime = Runtime([current])
            result = ReturnToMainWorkflow("baotu").execute(runtime, Guard())
            self.assertEqual(result.status, "failed")
            self.assertEqual(runtime.clicks, [])

    def test_same_close_action_is_never_repeated(self):
        page = self.known_page("日常活动 宝图任务")
        runtime = Runtime([page, self.known_page("日常活动 宝图任务")])
        result = ReturnToMainWorkflow("baotu", wait_seconds=0).execute(runtime, Guard())
        self.assertEqual(result.status, "failed")
        self.assertIn("不得重复", result.reason)
        self.assertEqual(len(runtime.clicks), 1)

    def test_observation_budget_stops_without_extra_input(self):
        runtime = Runtime([main_scene()])
        result = ReturnToMainWorkflow("baotu", max_observations=1).execute(runtime, Guard())
        self.assertEqual(result.status, "failed")
        self.assertIn("观察预算", result.reason)
        self.assertEqual(runtime.clicks, [])

    def test_click_budget_stops_before_an_unbudgeted_input(self):
        pages = [
            scene([("page-one", [200, 80, 100, 30])]),
            scene([("page-two", [200, 80, 100, 30])]),
        ]

        def classifier(_task_id, current):
            label = current.texts[0]["text"]
            return {
                "status": "close_known_page",
                "scene": label,
                "action_id": f"close:{label}",
                "box": [920, 45, 35, 35],
            }

        runtime = Runtime(pages)
        result = ReturnToMainWorkflow(
            "baotu", max_clicks=1, wait_seconds=0, classifier=classifier
        ).execute(runtime, Guard())
        self.assertEqual(result.status, "failed")
        self.assertIn("点击预算", result.reason)
        self.assertEqual(len(runtime.clicks), 1)


if __name__ == "__main__":
    unittest.main()
