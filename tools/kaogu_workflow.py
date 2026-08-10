"""Platform-neutral bounded archaeology workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

try:
    from .automation_actions import (
        ActionMetrics,
    )
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .kaogu_recognition import classify_kaogu_scene
    from .kaogu_atomic_actions import (
        OpenSkillsFromMainMenuAction,
        archaeology_actions,
        dig_archaeology_site_action,
        purchase_shovel_action,
    )
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import (
        ActionMetrics,
    )
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from kaogu_recognition import classify_kaogu_scene
    from kaogu_atomic_actions import (
        OpenSkillsFromMainMenuAction,
        archaeology_actions,
        dig_archaeology_site_action,
        purchase_shovel_action,
    )


KAOGU_TEMPLATES = {
    "menu_plus": ["zonghe/jiahao.png", "zonghe/jiahao_live_923.png"],
    "menu_close": ["zonghe/chenghao.png"],
    "battle": ["zhandou/quxiao.png"],
    "skills_icon": ["jineng/jineng.png"],
    "character_skills": ["jineng/renwujineng.png"],
    "clan_skills": ["jineng/bangpaijineng.png"],
    "training_skills": ["jineng/xiulianjineng.png"],
    "auxiliary_skills": ["jineng/fuzhujineng.png"],
    "auxiliary_tab": ["jineng/fuzhu.png", "jineng/fuzhu2.png"],
    "archaeology_entry": ["jineng/kaogu_rukou.png"],
    "archaeology_header": ["jineng/kaogu_ZJM.png"],
    "production_tab": ["jineng/jinengshengchan.png", "jineng/jinengshengchan2.png"],
    "tier_one": ["jineng/yijie.png"],
    "tier_two": ["jineng/erjie.png"],
    "tier_three": ["jineng/sanjie.png"],
    "tier_four": ["jineng/sijie.png"],
    "tier_five": ["jineng/wujie.png"],
    "tier_left_arrow": ["jineng/gongfang_jiantou_zuo.png"],
    "tool_plus": ["jineng/gongfang_jiahao.png"],
    "purchase_slot": ["jineng/kaogu_goumai.png"],
    "shovel": [
        "jineng/luoyangchan.png",
        "jineng/luoyangchan2.png",
        "jineng/luoyangchan_live_923.png",
    ],
    "shop_close": {
        "templates": [
            "jineng/zahuopu_guanbi.png",
            "jineng/zahuopu_guanbi_live_923.png",
        ],
        "threshold": 0.8,
        "roi_ratio": [0.82, 0.1, 0.16, 0.3],
    },
    "selected_shovel": [
        "jineng/yixuan_luoyangchan.png",
        "jineng/yixuan_luoyangchan_live_923.png",
    ],
    "inventory_page": {
        "templates": ["beibao/beibao_jiemian_panduan.png"],
        "threshold": 0.78,
    },
    "non_genuine_antique": {
        "templates": [
            "jineng/gd/gaofang.png",
            "jineng/gd/yanpin.png",
            "jineng/gd/gaofang_live_923.png",
            "jineng/gd/yanpin_live_923.png",
        ],
        "threshold": 0.82,
        "green_mask": True,
        # After the bounded inventory sort, archaeology antiques occupy the
        # lower rows. The upper rows contain unrelated event/reward badges
        # with similarly coloured corner ribbons.
        "roi_ratio": [0.1, 0.48, 0.79, 0.27],
    },
    "sale_non_genuine_antique": {
        "templates": [
            "jineng/gd/yanpin_sale_live_986.png",
            "jineng/gd/yanpin_live_923.png",
            "jineng/gd/yanpin.png",
            "jineng/gd/gaofang_live_923.png",
            "jineng/gd/gaofang.png",
        ],
        "threshold": 0.82,
        "green_mask": True,
        "roi_ratio": [0.1, 0.18, 0.5, 0.5],
    },
    "auto_pathing": [
        "zonghe/zidongxunlu1.png",
        "zonghe/zidongxunlu2.png",
        "zonghe/zidongxunlu3.png",
        "zonghe/zidongxunlu4.png",
    ],
    "antique_sale_page": {
        "templates": ["jineng/gudongshoumai.png"],
        "threshold": 0.78,
    },
    "antique_sale_complete": {
        "templates": [
            "jineng/shoumai_wancheng2.png",
        ],
        "threshold": 0.8,
    },
}

DEFAULT_ACTION_LIMITS = {
    "expand_menu": 1,
    "open_skills": 2,
    "open_auxiliary": 2,
    "open_archaeology": 2,
    "open_production": 2,
    "switch_tier": 4,
    "open_tool_selection": 2,
    # A LuoYang shovel can be consumed before the ten-dig target is reached.
    # Keep this aligned with MAX_PURCHASES: a second shop entry is permitted
    # only after the tool dialog explicitly proves the previous purchase is
    # no longer available.
    "open_shovel_shop": 2,
    "close_shop": 2,
    "select_shovel": 2,
    "use_shovel": 2,
    "start_archaeology": 1,
    "close_temporary_bag": 3,
    "close_inventory": 2,
    "open_inventory_for_sale": 1,
    "organize_inventory": 1,
    "use_antique": 1,
    "select_sale_antique": 12,
    "sell_antique": 12,
    "cancel_genuine_sale": 1,
    "close_sale_shop": 2,
}


class PurchaseGuard(Protocol):
    def is_reserved(self) -> bool: ...

    def purchase_count(self) -> int: ...

    def can_reserve(self, max_purchases: int) -> bool: ...

    def reserve(self, max_purchases: int = 1) -> None: ...

    def release(self) -> None: ...


def next_kaogu_phase(status: str, phase: str) -> str:
    return {
        "expand_menu": "skills",
        "open_skills": "skills",
        "open_auxiliary": "skills",
        "open_archaeology": "archaeology",
        "open_production": "production",
        "switch_tier": "production",
        "open_tool_selection": "tool_select",
        "open_shovel_shop": "shop",
    "buy_shovel": "shop_purchase_verify",
        "close_shop": "tool_select",
        "select_shovel": "tool_select",
        "use_shovel": "tool_ready",
        "start_archaeology": "digging",
    "dig": "digging",
    "dig_confirmed_on_main": "enter",
        "close_temporary_bag": phase,
        "open_inventory_for_sale": "sale_inventory_normalize",
        "normalize_inventory": phase,
        "organize_inventory": "sale_inventory_scan",
        "scan_inventory": phase,
        "activate_antique": "sale_pathing",
        "use_antique": "sale_pathing",
        "select_sale_antique": "sale_sell",
        "sell_antique": "sale_select",
        "cancel_genuine_sale": "sale_closing",
        "close_sale_shop": "sale_confirm_main",
    }.get(status, phase)


@dataclass(frozen=True)
class KaoguWorkflow:
    target_digs: int = 10
    initial_digs: int = 0
    tool_mode: str = "luoyang_shovel"
    max_steps: int = 80
    max_purchases: int = 2
    max_unknown_polls: int = 3
    max_wait_polls: int = 18
    max_dig_attempts_per_round: int = 3
    start_main_confirmations: int = 2
    max_start_recoveries: int = 1
    max_start_pathing_polls: int = 300
    max_final_observations: int = 360
    max_sale_wait_polls: int = 300
    sale_empty_confirmations: int = 3
    inventory_normalize_drags: int = 3
    max_inventory_scan_drags: int = 20
    final_main_confirmations: int = 2
    final_confirmation_wait_seconds: float = 5.0
    action_limits: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_ACTION_LIMITS))
    purchase_guard: PurchaseGuard | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_kaogu_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        if self.tool_mode not in {"selected", "luoyang_shovel"}:
            raise ValueError("考古工具方式必须是 selected 或 luoyang_shovel")
        values = (
            self.target_digs,
            self.max_steps,
            self.max_purchases,
            self.max_unknown_polls,
            self.max_wait_polls,
            self.max_dig_attempts_per_round,
            self.start_main_confirmations,
            self.max_start_recoveries,
            self.max_start_pathing_polls,
            self.max_final_observations,
            self.max_sale_wait_polls,
            self.sale_empty_confirmations,
            self.final_main_confirmations,
            self.inventory_normalize_drags,
            self.max_inventory_scan_drags,
        )
        if any(value <= 0 for value in values) or any(
            limit <= 0 for limit in self.action_limits.values()
        ):
            raise ValueError("考古 Workflow 上限必须是正整数")
        if not 0 <= self.initial_digs <= self.target_digs:
            raise ValueError("考古初始完成次数必须在 0 到目标次数之间")
        if self.final_confirmation_wait_seconds < 0:
            raise ValueError("考古最终确认等待时间不能为负数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        phase = "sale_wait_main" if self.initial_digs >= self.target_digs else "enter"
        metrics = ActionMetrics()
        digs = self.initial_digs
        purchases = (
            self.purchase_guard.purchase_count()
            if self.purchase_guard is not None
            else 0
        )
        unknown_polls = 0
        wait_polls = 0
        final_observations = 0
        confirmed_main_polls = 0
        sale_wait_polls = 0
        sale_empty_polls = 0
        sale_clicks = 0
        sale_batches = 0
        sale_outcome = None
        normalize_drags = 0
        scan_drags = 0
        pending_dig = False
        pending_dig_attempts = 0
        pending_dig_polls = 0
        start_main_polls = 0
        start_recoveries = 0
        # A persisted reservation alone does not prove that another shovel is
        # needed. Replenishment is allowed only after this workflow has seen
        # the explicit empty-tool dialog and opened the recognized shop.
        replenishment_authorized = (
            self.purchase_guard is None or not self.purchase_guard.is_reserved()
        )
        start_pathing_polls = 0
        purchase_verify_polls = 0
        purchase_shop_close_confirmations = 2

        actions = archaeology_actions(self.action_limits, normalize_drags=self.inventory_normalize_drags, scan_drags=self.max_inventory_scan_drags)
        actions["expand_menu"] = OpenSkillsFromMainMenuAction(
            repeat_limit=self.action_limits["expand_menu"]
        )
        buy_shovel = purchase_shovel_action(self.max_purchases)
        dig = dig_archaeology_site_action(self.max_dig_attempts_per_round, lambda: digs + 1)
        activate_antique = actions["activate_antique"]
        normalize_inventory = actions["normalize_inventory"]
        scan_inventory = actions["scan_inventory"]

        def result(status: str) -> WorkflowResult:
            if (
                status in {"completed", "already_completed"}
                and self.purchase_guard is not None
            ):
                self.purchase_guard.release()
            return WorkflowResult(
                status,
                {
                    "digs": digs,
                    "target_digs": self.target_digs,
                    "purchases": purchases,
                    "sale_clicks": sale_clicks,
                    "sale_batches": sale_batches,
                    "sale_outcome": sale_outcome,
                    "genuine_protected": True,
                    "actions": metrics.actions,
                    "clicks": metrics.clicks,
                    "drags": metrics.drags,
                },
            )

        total_step_limit = self.max_steps + self.max_final_observations
        for step in range(1, total_step_limit + 1):
            if digs < self.target_digs and step > self.max_steps:
                raise RuntimeError("考古流程超过最大识别步骤，已安全停止")
            scene = context.runtime.read_scene(KAOGU_TEMPLATES)
            decision = self.classifier(
                scene.texts,
                scene.templates,
                scene.image_size,
                phase=phase,
                tool_mode=self.tool_mode,
            )
            status = decision["status"]
            if (
                self.tool_mode == "selected"
                and digs < self.target_digs
                and status not in {
                    "completed",
                    "already_completed",
                    "blocked_resource",
                    "wait_for_battle",
                    "wait_for_busy_task",
                    "wait_for_dig",
                    "dig_confirmed",
                    "dig_confirmed_on_main",
                    "start_transition_main",
                    "wait_for_start_pathing",
                    "start_archaeology",
                    "dig",
                    "close_temporary_bag",
                    "close_inventory",
                    "unknown",
                }
            ):
                decision = {
                    "status": "manual_tool_required",
                    "reason": (
                        "请停留在工坊考古生产页，先手动选好工具并确认数量足够，"
                        "保持“考古”按钮可见后再启动"
                    ),
                }
                status = decision["status"]
            if status == "shop_resume":
                close_box = decision.get("close_box")
                if (
                    self.purchase_guard is not None
                    and self.purchase_guard.is_reserved()
                    and close_box
                ):
                    decision = {
                        "status": "close_shop",
                        "box": close_box,
                        "reason": "恢复已中断的洛阳铲购买后验证工具",
                    }
                else:
                    decision = {
                        "status": "purchase_blocked",
                        "reason": "未确认从空工具页进入洛阳杂货铺，已停止以避免无证据购买",
                    }
                status = decision["status"]
            if (
                status == "buy_shovel"
                and self.purchase_guard is not None
                and (
                    not replenishment_authorized
                    or not self.purchase_guard.can_reserve(self.max_purchases)
                )
            ):
                close_box = decision.get("close_box")
                if close_box:
                    # Keep the durable purchase reservation intact. A prior
                    # interrupted run may already have bought the shovel and
                    # left the shop open, so only close this positively
                    # identified dialog and verify the tool selection next.
                    decision = {
                        "status": "close_shop",
                        "box": close_box,
                    "reason": "洛阳铲购买次数已达上限，关闭商店后验证工具",
                    }
                else:
                    decision = {
                        "status": "purchase_blocked",
                        "reason": "本轮考古已经购买或尝试购买过洛阳铲，不再自动购买",
                    }
                status = decision["status"]
            if (
                status == "tool_selection_idle"
                and self.purchase_guard is not None
                and self.purchase_guard.is_reserved()
            ):
                # The current skin can show a purchased LuoYang shovel as an
                # icon-only first slot. Its identity is bounded by the recent
                # single-purchase reservation, the recognized tool dialog,
                # and the visible Use control; do not apply this fallback to
                # arbitrary tool dialogs.
                decision = {
                    "status": "select_shovel",
                    "box": [
                        round(scene.image_size[0] * 0.30),
                        round(scene.image_size[1] * 0.34),
                        max(1, round(scene.image_size[0] * 0.075)),
                        max(1, round(scene.image_size[1] * 0.10)),
                    ],
                    "reason": "recent_purchase_first_tool_slot",
                }
                status = decision["status"]
            context.emit(
                "scene",
                step=step,
                phase=phase,
                status=status,
                digs=digs,
                targetDigs=self.target_digs,
            )
            if inspect:
                return WorkflowInspection.from_scene(
                    scene,
                    decision,
                    context.inspection_evidence(scene, decision),
                )
            if digs >= self.target_digs:
                final_observations += 1
                if final_observations > self.max_final_observations:
                    raise RuntimeError(
                        "最后一次考古长时间未结束，已安全停止"
                    )
                if status == "antique_sale_main_confirmed":
                    confirmed_main_polls += 1
                    if confirmed_main_polls >= self.final_main_confirmations:
                        return result("completed")
                    context.waiter.wait(self.final_confirmation_wait_seconds)
                    continue
                confirmed_main_polls = 0
                if status == "sale_candidate_absent":
                    sale_empty_polls += 1
                    if sale_empty_polls >= self.sale_empty_confirmations:
                        phase = "sale_closing"
                    context.waiter.wait(0.8)
                    continue
                sale_empty_polls = 0
                if status == "antique_sale_finished":
                    phase = "sale_closing"
                    context.waiter.wait(0.5)
                    continue
                if status in {
                    "wait_for_antique_sale",
                    "wait_for_sale_pathing",
                    "wait_for_battle",
                }:
                    sale_wait_polls += 1
                    if sale_wait_polls > self.max_sale_wait_polls:
                        raise RuntimeError("古董售卖页面长时间未就绪，已安全停止并保留现场")
                    context.waiter.wait(1.0)
                    continue
                sale_wait_polls = 0
            if status in {"completed", "already_completed"}:
                return result(status)
            if status == "blocked_resource":
                raise RuntimeError("当前活力不足，考古已安全停止")
            if status == "purchase_blocked":
                raise RuntimeError(decision["reason"])
            if status == "manual_tool_required":
                raise RuntimeError(decision["reason"])
            if status in {"wait_for_battle", "wait_for_busy_task"}:
                context.waiter.wait(1.0)
                continue
            if status == "wait_for_purchase":
                purchase_verify_polls += 1
                close_box = decision.get("close_box")
                if (
                    purchase_verify_polls >= purchase_shop_close_confirmations
                    and self.purchase_guard is not None
                    and self.purchase_guard.is_reserved()
                    and close_box
                ):
                    # A successful purchase can leave the product page static.
                    # The reservation proves this run has already consumed its
                    # single purchase attempt; close only the recognized shop,
                    # then require the tool dialog to prove availability.
                    close_context = context.action_context(
                        scene,
                        {"status": "close_shop", "box": close_box},
                        "工坊考古",
                    )
                    metrics.record(actions["close_shop"].execute(close_context))
                    phase = "tool_select"
                    purchase_verify_polls = 0
                    continue
                if purchase_verify_polls >= self.max_wait_polls:
                    raise RuntimeError(
                        "购买洛阳铲后未确认工具到账，已安全停止并保留商店现场"
                    )
                context.waiter.wait(0.8)
                continue
            if status in {"tool_selection_idle", "production_idle"}:
                wait_polls += 1
                if wait_polls >= 4:
                    raise RuntimeError("考古页面连续未出现可执行按钮，已安全停止")
                context.waiter.wait(0.8)
                continue
            if status == "dig_confirmed":
                if pending_dig:
                    digs += 1
                    pending_dig = False
                    pending_dig_attempts = 0
                    pending_dig_polls = 0
                    dig.reset_repetition()
                    context.emit(
                        "dig_confirmed",
                        digs=digs,
                        targetDigs=self.target_digs,
                    )
                    phase = (
                        "sale_wait_main"
                        if digs >= self.target_digs
                        else "digging"
                    )
                context.waiter.wait(0.8)
                continue
            if status == "dig_confirmed_on_main":
                if not pending_dig:
                    # The settlement panel can confirm a dig before its
                    # personal-channel receipt reaches the main screen. Once
                    # that first signal has already cleared pending_dig, the
                    # delayed receipt is duplicate evidence, not a new dig.
                    context.emit(
                        "dig_receipt_ignored",
                        reason="duplicate_after_settlement_confirmation",
                        digs=digs,
                    )
                    context.waiter.wait(0.8)
                    continue
                digs += 1
                pending_dig = False
                pending_dig_attempts = 0
                pending_dig_polls = 0
                dig.reset_repetition()
                context.emit(
                    "dig_confirmed",
                    digs=digs,
                    targetDigs=self.target_digs,
                    evidence="main_screen_personal_receipt",
                )
                if digs >= self.target_digs:
                    phase = "sale_wait_main"
                else:
                    # The client has closed the panel after an evidenced
                    # completion. Restart only identity-checked navigation;
                    # the one-purchase guard is deliberately not reset.
                    for navigation_status in (
                        "expand_menu",
                        "open_skills",
                        "open_auxiliary",
                        "open_archaeology",
                        "open_production",
                        "switch_tier",
                        "open_tool_selection",
                        "select_shovel",
                        "use_shovel",
                        "start_archaeology",
                    ):
                        actions[navigation_status].reset_repetition()
                    phase = "enter"
                context.waiter.wait(0.8)
                continue
            if status == "start_transition_main":
                # A main screen can flash briefly while the client hands off
                # to the excavation panel. Observe it twice before replaying
                # the bounded navigation sequence, and never replay twice.
                start_main_polls += 1
                if start_main_polls < self.start_main_confirmations:
                    context.waiter.wait(1.0)
                    continue
                if start_recoveries >= self.max_start_recoveries:
                    raise RuntimeError(
                        "开始考古后未出现挖掘面板，已安全停止并保留现场"
                    )
                start_recoveries += 1
                for navigation_status in (
                    "expand_menu",
                    "open_skills",
                    "open_auxiliary",
                    "open_archaeology",
                    "open_production",
                    "switch_tier",
                    "open_tool_selection",
                    "select_shovel",
                    "use_shovel",
                    "start_archaeology",
                ):
                    actions[navigation_status].reset_repetition()
                phase = "enter"
                pending_dig = False
                pending_dig_attempts = 0
                pending_dig_polls = 0
                context.emit(
                    "start_recovery",
                    attempt=start_recoveries,
                    reason="main_screen_without_excavation_panel",
                )
                context.waiter.wait(1.0)
                continue
            if status == "wait_for_start_pathing":
                start_main_polls = 0
                start_pathing_polls += 1
                if start_pathing_polls > self.max_start_pathing_polls:
                    raise RuntimeError("前往考古地点长时间未结束，已安全停止并保留现场")
                context.waiter.wait(1.0)
                continue
            if status == "wait_for_dig":
                start_main_polls = 0
                start_pathing_polls = 0
                wait_polls += 1
                if pending_dig:
                    pending_dig_polls += 1
                    if pending_dig_polls >= self.max_wait_polls:
                        raise RuntimeError("本次考古长时间没有出现结算证据，已安全停止")
                if wait_polls >= self.max_wait_polls:
                    raise RuntimeError("长时间未出现下一次挖掘按钮，已安全停止")
                context.waiter.wait(0.6)
                continue
            if status == "unknown":
                unknown_polls += 1
                if unknown_polls >= self.max_unknown_polls:
                    raise RuntimeError("连续三次无法识别考古页面，已停止以避免无证据点击")
                context.waiter.wait(0.8)
                continue
            if status == "menu_open":
                wait_polls += 1
                if wait_polls >= 4:
                    raise RuntimeError("主界面菜单已展开，但连续未识别到技能入口")
                context.waiter.wait(0.8)
                continue

            unknown_polls = 0
            wait_polls = 0
            start_main_polls = 0
            start_pathing_polls = 0
            if status == "expand_menu" and phase == "tool_select":
                # Closing 洛阳杂货铺 can return the client to the main screen.
                # That transition is positively identified above, so a single
                # navigation replay is safe and must not consume the first
                # pass's repetition budget.
                for navigation_status in (
                    "expand_menu",
                    "open_skills",
                    "open_auxiliary",
                    "open_archaeology",
                    "open_production",
                    "switch_tier",
                    "open_tool_selection",
                ):
                    actions[navigation_status].reset_repetition()
            if (
                status == "open_shovel_shop"
                and self.purchase_guard is not None
                and self.purchase_guard.is_reserved()
            ):
                # `open_shovel_shop` is emitted only after the tool dialog
                # positively reports that the bag has no LuoYang shovel.
                replenishment_authorized = True
                # This is a new, evidence-bounded shop transaction. Its
                # eventual close must not inherit the prior shop's local
                # repetition count, while the purchase guard continues to
                # enforce the global two-purchase ceiling.
                actions["close_shop"].reset_repetition()
            action_context = context.action_context(scene, decision, "工坊考古")
            if status == "buy_shovel":
                if purchases >= self.max_purchases:
                    raise RuntimeError("洛阳铲购买已达到一次上限，已停止以避免重复购买")
                if self.purchase_guard is not None:
                    self.purchase_guard.reserve(self.max_purchases)
                metrics.record(buy_shovel.execute(action_context))
                purchases += 1
                purchase_verify_polls = 0
                replenishment_authorized = False
            elif status == "dig":
                if digs >= self.target_digs:
                    return result("completed")
                if pending_dig and pending_dig_attempts >= self.max_dig_attempts_per_round:
                    raise RuntimeError("本次挖掘按钮连续点击仍未进入结算，已安全停止")
                metrics.record(dig.execute(action_context))
                pending_dig = True
                pending_dig_attempts += 1
                pending_dig_polls = 0
                context.waiter.wait(1.8)
            elif status == "activate_antique":
                metrics.record(activate_antique.execute(action_context))
            elif status == "normalize_inventory":
                metrics.record(normalize_inventory.execute(action_context))
                normalize_drags += 1
                if normalize_drags >= self.inventory_normalize_drags:
                    phase = "sale_inventory_sort"
                    continue
            elif status == "scan_inventory":
                metrics.record(scan_inventory.execute(action_context))
                scan_drags += 1
                if (
                    sale_batches > 0
                    and scan_drags >= self.max_inventory_scan_drags
                ):
                    phase = "sale_inventory_verify_empty"
                    continue
            else:
                action = actions.get(status)
                if action is None:
                    raise RuntimeError(f"考古流程返回了不支持的动作：{status}")
                metrics.record(action.execute(action_context))
                if status == "sell_antique":
                    sale_clicks += 1
                elif status == "cancel_genuine_sale":
                    sale_outcome = "all_non_genuine_sold_genuine_retained"
                elif status == "close_sale_shop":
                    sale_batches += 1
                    normalize_drags = 0
                    scan_drags = 0
                    for batch_status in (
                        "open_inventory_for_sale",
                        "organize_inventory",
                        "use_antique",
                        "select_sale_antique",
                        "sell_antique",
                        "cancel_genuine_sale",
                        "close_sale_shop",
                    ):
                        actions[batch_status].reset_repetition()
                    activate_antique.reset_repetition()
                    normalize_inventory.reset_repetition()
                    scan_inventory.reset_repetition()
                    phase = "sale_rescan_main"
                    continue
                elif (
                    status == "close_inventory"
                    and phase == "sale_inventory_verify_empty"
                ):
                    if sale_outcome is None:
                        sale_outcome = "all_non_genuine_sold"
                    phase = "sale_confirm_main"
                    continue
                if status == "start_archaeology":
                    pending_dig = False
                    pending_dig_attempts = 0
                    pending_dig_polls = 0
            phase = next_kaogu_phase(status, phase)

        raise RuntimeError("考古流程超过最大识别步骤，已安全停止")


__all__ = [
    "DEFAULT_ACTION_LIMITS",
    "KAOGU_TEMPLATES",
    "KaoguWorkflow",
    "next_kaogu_phase",
]
