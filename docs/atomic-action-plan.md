# 游戏原子操作规划

状态：两批低风险操作已实现（2026-08-07）。`OpenActivityCenter`、`SelectActivityTab`、`JoinIdentifiedActivity`、`AcceptIdentifiedDialogue`、`ContinueTrackedTask`、普通运镖确认、秘境进入/挑战、福利入口/确认、帮派福利/分红及趣闻点赞/刷新已落在 `tools/game_atomic_actions.py`，并迁移到对应工作流。答题和刮卡已有独立原子实现：`AnswerOptionClickAction` 统一三界奇缘/科举的答案选择，`ScratchWelfareCardAction` 处理有界刮卡。固定主界面、资源快照、队伍和考古操作仍待迁移。

## 边界

- 原子操作只做一个可验证意图，例如“打开背包”或“点击已识别的活动卡片”；不得把导航、等待、完成判断和多个点击塞进同一个操作。
- 纯读取不是输入操作。`ObserveScene`、`AwaitScene`、`AwaitStableValue`、`MonitorGameAutomation` 和 `StopOnUnsafeScene` 是可编排的只读步骤，负责给后续输入提供新鲜证据。
- 每个输入操作都必须声明：允许的页面身份、目标类型（固定坐标或动态识别框）、重复上限、成功后的观察条件和停止原因。所有输入继续经过取消守卫、共享输入预算和窗口标定。
- 固定布局控件使用归一化坐标作为目标；模板/OCR 只用于页面身份锚点，不决定点击点。活动列表、滚动内容、对话选项和奖励文本继续使用动态识别框。

## 共享步骤目录

### 观察、等待与安全门控

| 操作 | 类型 | 责任 |
| --- | --- | --- |
| `ObserveScene` | 只读 | 截取一帧并输出 OCR、模板、窗口标定和帧标识。 |
| `ConfirmMainScreen` | 只读 | 以跨区域锚点确认主界面；恢复完成时要求两帧不同 frameId。 |
| `AwaitScene` | 只读 | 在有限观察次数内等待声明的页面身份，不产生输入。 |
| `AwaitStableValue` | 只读 | 连续读取固定资源槽，数值稳定后才接受。 |
| `MonitorGameAutomation` | 只读 | 监视寻路、战斗、任务栏变化和明确完成证据；只读期间绝不推进游戏。 |
| `StopOnUnsafeScene` | 只读 | 战斗、购买、挑战失败、未知页或页面身份不足时停止并保留现场。 |

### 固定主界面与标准面板

| 操作 | 目标 | 页面身份与结果 |
| --- | --- | --- |
| `ToggleMainShortcutMenu` | 主界面固定加号/收起控件 | 已确认主界面和菜单状态；结果为菜单展开或收起。 |
| `OpenActivityCenter` | 主界面固定活动入口 | 已确认主界面；等待活动页锚点。 |
| `OpenBackpack` | 主界面固定包裹入口 | 已确认主界面；等待背包页身份。 |
| `OpenCharacterProfile` | 主界面固定头像入口 | 已确认主界面；等待人物属性页身份。 |
| `CloseStandardPanel` | 背包、人物属性等标准关闭区 | 已确认具体面板；等待主界面，不接受孤立 X。 |
| `CloseFixedOverlay` | 已登记的临时包裹、队伍快捷栏、聊天栏等 | 每个覆盖层独立身份锚点与坐标；同一关闭动作每轮最多一次。 |

### 动态活动与任务导航

| 操作 | 目标 | 页面身份与结果 |
| --- | --- | --- |
| `SelectActivityTab` | 已识别的日常/活动页签 | 活动页与页签身份同时成立；等待目标列表。 |
| `ScrollActivityList` | 活动卡片滚动区 | 卡片列表结构完整；有方向、次数和可见项变化预算。 |
| `JoinIdentifiedActivity` | 同一卡片内的参加按钮 | 任务名、卡片边界、按钮三者关联；等待任务对话或任务栏。 |
| `ContinueTrackedTask` | 已识别的右侧任务栏 | 任务名及任务栏区域完整；随后进入只读监视或明确下一页。 |
| `AcceptIdentifiedDialogue` | 已识别的对话选项/确认按钮 | 对话标题、选项文本和位置共同成立；等待对话状态变化。 |
| `SelectDynamicTabOrCard` | 页面内位置不固定的页签、关卡或卡片 | 目标文字/模板与容器结构共同成立；不使用固定坐标猜测。 |

### 答题、队伍与自动任务

| 操作 | 目标 | 页面身份与结果 |
| --- | --- | --- |
| `AnswerRecognizedQuestion` | 三界奇缘/科举的单选或多选答案 | 题目身份、答案证据和选项框完整；等待题号或完成页变化。 |
| `OpenTeamPanel` / `CloseTeamPanel` | 队伍固定入口和已确认面板 | 仅在完整队伍页证据成立时输入。 |
| `ConfigureGhostTeam` | 创建队伍、目标、等级、阵法和成员调整 | 拆成可单独重试的子操作；每项仅接受对应配置页。 |
| `StartGameAutoTask` | 师门、宝图、运镖等已确认任务栏 | 启动后转换为 `MonitorGameAutomation`，不再主动点寻路或资产页。 |
| `SelectEscortMode` / `ConfirmEscort` | 普通运镖模式和确认页 | 需要运镖页、普通镖银和确认控件的组合证据。 |
| `EnterDungeonStage` / `StartDungeonChallenge` | 秘境入口、关卡、挑战和入战 | 只处理已确认免费流程；模式选择、失败和购买页全部安全停止。 |

### 福利、社交与可重复互动

| 操作 | 目标 | 页面身份与结果 |
| --- | --- | --- |
| `OpenWelfare` / `OpenScratchCard` | 福利页和刮刮乐入口 | 固定入口或已识别控件；等待对应页。 |
| `ConfirmFreeReward` | 已确认的免费领取/签到确认 | 奖励页面与免费确认语义完整；不覆盖付费或累计奖励。 |
| `OpenClanWelfare` / `ClaimClanDividend` | 帮派福利及分红 | 帮派身份、福利页与收益按钮完整；每项一次性预算。 |
| `LikeVisibleFeedItem` | 趣闻鉴赏动态的心形按钮 | 当前动态卡片和未点赞状态完整；每卡仅一次。 |
| `RefreshFeed` | 动态列表滚动区 | 列表身份和新卡片/滚动预算完整。 |

### 背包、考古与资产边界

| 操作 | 目标 | 页面身份与结果 |
| --- | --- | --- |
| `NormalizeInventoryViewport` / `ScanInventoryViewport` | 背包物品滚动区 | 已确认背包；有限拖动并观察候选物品变化。 |
| `ActivateIdentifiedInventoryItem` | 明确识别的非真品古董 | 物品身份、背包页和目标框完整；双击后只读等待寻路。 |
| `SelectSaleCandidate` / `SellVerifiedCandidate` | 古董商页面的非真品和出售按钮 | 售卖页、候选物和价格/按钮共同成立；真品确认页只允许取消。 |
| `PurchaseBoundedTool` | 考古洛阳铲 | 商店、物品、价格和一次性购买锁完整；购买后必须确认到账。 |
| `StartArchaeology` / `DigArchaeologySite` | 已选工具和挖掘按钮 | 工坊考古页、工具状态和本轮次数完整；每次挖掘等待结算。 |

### 资源与统一收尾

| 操作 | 目标 | 页面身份与结果 |
| --- | --- | --- |
| `CaptureWallet` | 背包的固定金币/银币槽 | 通过 `OpenBackpack` 进入；只读取稳定数值。 |
| `CaptureExperience` | 人物属性的等级/经验槽 | 通过 `OpenCharacterProfile` 进入；只读取稳定数值。 |
| `CaptureResourceSnapshot` | 一组声明字段 | 编排 `Open* -> Capture* -> CloseStandardPanel -> ConfirmMainScreen`。 |
| `ReturnToMain` | 任务终态到主界面 | 编排已登记的 `CloseFixedOverlay` 与页面关闭步骤，最终双帧确认。 |

## 任务编排目标

每个任务应表达为“只读观察/条件分支 + 原子操作”的序列，不能直接调用平台点击：

| 任务 | 目标序列摘要 |
| --- | --- |
| 福利签到、帮派签到 | `ConfirmMainScreen -> Open* -> ConfirmFreeReward/Claim* -> ReturnToMain`。 |
| 三界奇缘、乡试、会试 | `OpenActivityCenter -> SelectActivityTab -> JoinIdentifiedActivity -> AnswerRecognizedQuestion* -> ReturnToMain`。 |
| 捉鬼 | `OpenTeamPanel -> ConfigureGhostTeam* -> StartGameAutoTask -> MonitorGameAutomation -> ReturnToMain`。 |
| 宝图、师门 | `OpenActivityCenter -> JoinIdentifiedActivity -> StartGameAutoTask -> MonitorGameAutomation -> ReturnToMain`。 |
| 趣闻鉴赏 | `OpenActivityCenter -> JoinIdentifiedActivity -> LikeVisibleFeedItem/RefreshFeed* -> ReturnToMain`。 |
| 普通运镖、秘境 | `OpenActivityCenter -> JoinIdentifiedActivity -> Select/Confirm/Enter* -> MonitorGameAutomation -> ReturnToMain`。 |
| 工坊考古 | `ToggleMainShortcutMenu -> StartArchaeology/DigArchaeologySite* -> OpenBackpack -> Activate/SellVerifiedCandidate* -> ReturnToMain`。 |

`*` 表示受任务声明次数、同一目标去重和共享输入预算约束的重复步骤。宝图不建立任务前后资源快照或收益记录，因为当前流程没有可归因的角色资源变化。

## 迁移顺序与验收

1. 先实现固定主界面/标准面板操作：`OpenBackpack`、`OpenCharacterProfile`、`CloseStandardPanel`、`CloseFixedOverlay`，并迁移资源采集与考古。
2. 再抽取动态活动导航：页签、滚动、任务卡片、任务栏和对话；迁移宝图、师门、运镖、秘境和趣闻鉴赏。
3. 最后拆分答题、队伍、考古资产和副本等领域操作；高风险资产操作维持独立购买锁和更严格预算。
4. 每个原子操作须有成功路径、页面身份不足零输入、取消后零输入、重复/预算耗尽、多尺寸标定五类离线测试；迁移任务后补一条完整序列回归。

完成标准：任务 Workflow 不再直接调用 `runtime.click`、`click_box` 或原生输入；同一语义操作只有一个实现、一个日志名和一组跨任务测试。
