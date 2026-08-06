"""Platform-neutral bounded archaeology workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

try:
    from .automation_actions import (
        ActionMetrics,
        EvidenceClickAction,
        EvidenceDoubleClickAction,
        EvidenceThenRelativeClickAction,
    )
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .kaogu_recognition import classify_kaogu_scene
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import (
        ActionMetrics,
        EvidenceClickAction,
        EvidenceDoubleClickAction,
        EvidenceThenRelativeClickAction,
    )
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from kaogu_recognition import classify_kaogu_scene


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
    "shop_close": ["jineng/zahuopu_guanbi.png"],
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
        ],
        "threshold": 0.8,
        "green_mask": True,
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
            "jineng/shoumai_wancheng.png",
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
    "open_shovel_shop": 1,
    "close_shop": 2,
    "select_shovel": 2,
    "use_shovel": 2,
    "start_archaeology": 1,
    "close_temporary_bag": 3,
    "close_inventory": 2,
    "open_inventory_for_sale": 1,
    "use_antique": 1,
    "select_sale_antique": 12,
    "sell_antique": 12,
    "cancel_genuine_sale": 1,
    "close_sale_shop": 1,
}


class PurchaseGuard(Protocol):
    def is_reserved(self) -> bool: ...

    def reserve(self) -> None: ...

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
        "buy_shovel": "shop_closing",
        "close_shop": "tool_select",
        "select_shovel": "tool_select",
        "use_shovel": "tool_ready",
        "start_archaeology": "digging",
        "dig": "digging",
        "close_temporary_bag": phase,
        "open_inventory_for_sale": "sale_inventory",
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
    tool_mode: str = "luoyang_shovel"
    max_steps: int = 80
    max_purchases: int = 1
    max_unknown_polls: int = 3
    max_wait_polls: int = 18
    max_final_observations: int = 360
    max_sale_wait_polls: int = 300
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
            self.max_final_observations,
            self.max_sale_wait_polls,
            self.final_main_confirmations,
        )
        if any(value <= 0 for value in values) or any(
            limit <= 0 for limit in self.action_limits.values()
        ):
            raise ValueError("考古 Workflow 上限必须是正整数")
        if self.final_confirmation_wait_seconds < 0:
            raise ValueError("考古最终确认等待时间不能为负数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        phase = "enter"
        metrics = ActionMetrics()
        digs = 0
        purchases = 0
        unknown_polls = 0
        wait_polls = 0
        final_observations = 0
        confirmed_main_polls = 0
        sale_wait_polls = 0
        sale_clicks = 0
        sale_outcome = None

        actions = {
            status: EvidenceClickAction(
                name=f"kaogu_{status}",
                statuses=(status,),
                reason=(
                    (lambda _action_context: f"进行第 {digs + 1} 次挖掘")
                    if status == "start_archaeology"
                    else f"考古流程：{status}"
                ),
                wait_seconds=(1.8 if status == "start_archaeology" else 1.0),
                repeat_limit=(
                    self.target_digs if status == "start_archaeology" else limit
                ),
                repeat_error=f"考古步骤 {status} 重复达到上限，已安全停止",
                signature_factory=lambda action_context: action_context.decision_status,
            )
            for status, limit in self.action_limits.items()
        }
        actions["expand_menu"] = EvidenceThenRelativeClickAction(
            name="open_skills_from_main_menu",
            statuses=("expand_menu",),
            point_ratio=(0.849, 0.94),
            first_reason="展开主界面快捷菜单",
            second_reason="打开技能页面",
            between_seconds=1.2,
            wait_seconds=1.0,
            repeat_limit=self.action_limits["expand_menu"],
            repeat_error="主界面快捷菜单打开失败，已安全停止",
        )
        buy_shovel = EvidenceClickAction(
            name="buy_archaeology_shovel",
            statuses=("buy_shovel",),
            reason="购买一个洛阳铲",
            wait_seconds=1.8,
            repeat_limit=self.max_purchases,
            repeat_error="洛阳铲购买已达到一次上限，已停止以避免重复购买",
            signature_factory=lambda action_context: action_context.decision_status,
        )
        dig = EvidenceClickAction(
            name="dig_archaeology_site",
            statuses=("dig",),
            reason=lambda _action_context: f"进行第 {digs + 1} 次挖掘",
            wait_seconds=0.0,
            repeat_limit=self.target_digs,
            repeat_error="考古挖掘已达到目标次数，已停止额外输入",
            signature_factory=lambda action_context: action_context.decision_status,
        )
        activate_antique = EvidenceDoubleClickAction(
            name="activate_non_genuine_antique",
            statuses=("activate_antique",),
            reason="双击非真品古董并启动自动寻路",
            click_interval_seconds=0.12,
            wait_seconds=1.0,
            repeat_limit=1,
            repeat_error="古董已双击一次，未确认页面变化前不会重复双击",
        )

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
                    "sale_outcome": sale_outcome,
                    "genuine_protected": True,
                    "actions": metrics.actions,
                    "clicks": metrics.clicks,
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
            if (
                status == "buy_shovel"
                and self.purchase_guard is not None
                and self.purchase_guard.is_reserved()
            ):
                decision = {
                    "status": "purchase_blocked",
                    "reason": "本轮考古已经购买或尝试购买过洛阳铲，不再自动购买",
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
                if status == "antique_sale_finished":
                    sale_outcome = "all_non_genuine_sold"
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
            if status in {"tool_selection_idle", "production_idle"}:
                wait_polls += 1
                if wait_polls >= 4:
                    raise RuntimeError("考古页面连续未出现可执行按钮，已安全停止")
                context.waiter.wait(0.8)
                continue
            if status == "wait_for_dig":
                wait_polls += 1
                if digs >= self.target_digs:
                    return result("completed")
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
            action_context = context.action_context(scene, decision, "工坊考古")
            if status == "buy_shovel":
                if purchases >= self.max_purchases:
                    raise RuntimeError("洛阳铲购买已达到一次上限，已停止以避免重复购买")
                if self.purchase_guard is not None:
                    self.purchase_guard.reserve()
                metrics.record(buy_shovel.execute(action_context))
                purchases += 1
            elif status == "dig":
                if digs >= self.target_digs:
                    return result("completed")
                metrics.record(dig.execute(action_context))
                digs += 1
                if digs >= self.target_digs:
                    context.waiter.wait(0.8)
                else:
                    context.waiter.wait(1.8)
            elif status == "activate_antique":
                metrics.record(activate_antique.execute(action_context))
            else:
                action = actions.get(status)
                if action is None:
                    raise RuntimeError(f"考古流程返回了不支持的动作：{status}")
                metrics.record(action.execute(action_context))
                if status == "sell_antique":
                    sale_clicks += 1
                elif status == "cancel_genuine_sale":
                    sale_outcome = "all_non_genuine_sold_genuine_retained"
                if status == "start_archaeology":
                    # The production-page button performs an excavation in both
                    # tool modes; subsequent digs may appear on the map or after
                    # navigating back to the production page.
                    digs += 1
                    if digs >= self.target_digs:
                        context.waiter.wait(0.8)
            phase = next_kaogu_phase(status, phase)
            if digs >= self.target_digs and status in {"dig", "start_archaeology"}:
                phase = "sale_wait_main"

        raise RuntimeError("考古流程超过最大识别步骤，已安全停止")


__all__ = [
    "DEFAULT_ACTION_LIMITS",
    "KAOGU_TEMPLATES",
    "KaoguWorkflow",
    "next_kaogu_phase",
]
