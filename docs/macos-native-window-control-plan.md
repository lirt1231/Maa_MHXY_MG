# macOS 原生窗口控制后端替换规划

状态：规划已确认，待实施（2026-07-28）

## 1. 目标与结论

本规划只替换当前 MaaFramework 承担的窗口截图、窗口绑定、坐标映射、点击和拖动控制，不重写现有 OCR、模板识别、任务状态机、风险门控和控制台协议。

目标是实现一套与 Codex Computer Use 底层能力同类型、但可由独立控制台本地运行的 macOS 原生窗口控制后端。正式实现不能依赖 Codex 的内部 `CUAService`，而应直接使用 macOS 公共框架。

推荐技术路线：

- 截图使用 `ScreenCaptureKit`，按明确的 `CGWindowID` 捕获游戏窗口。
- 窗口发现和校验使用 `CGWindowListCopyWindowInfo`、进程 PID、Bundle ID 和窗口 ID。
- 点击与拖动继续使用 `CGEvent`，但移除固定 28 点标题栏假设，统一使用截图返回的内容矩形进行映射。
- Maa 的 OCR、模板资源和 `post_recognition` 暂时保留，输入图像改由原生后端提供。
- 迁移期间保留旧后端并提供只读影子对比，不允许两个后端同时发送输入。

## 2. 当前基线与问题

当前执行链路为：

```text
精确窗口 ID
  -> Maa MacOSController(ScreenCaptureKit)
  -> Maa 截图失败时 WindowCapture 回退
  -> /usr/sbin/screencapture -l <window_id>
  -> 裁剪并缩放为 1020 x 718
  -> Maa OCR/模板识别
  -> NativeMacOSInput
  -> Objective-C++ CGEvent 点击或拖动
```

已确认的现状：

- 实机任务启动时，Maa `ScreenCaptureKit` 多次稳定出现 `screencap failed`，随后才能进入系统截图回退。
- 回退截图每帧启动一个 `screencapture` 子进程、写临时 PNG、重新读取并裁剪，延迟高且依赖黑边启发式。
- 输入层已经是本地 Objective-C++ `CGEvent`，并且会绑定单一窗口 ID，不需要完全重写。
- 输入坐标映射仍固定扣除 28 点标题栏高度，在不同窗口装饰、Retina 缩放和未来标定结果下容易漂移。
- 截图、输入和标定没有共享同一份内容矩形元数据，问题发生时难以判断是识别偏差还是坐标偏差。
- Computer Use 对同一游戏窗口的截图在本轮实机诊断中保持稳定，说明改成本地原生控制层有明确价值。

## 3. 范围

### 3.1 本期包含

- 单一游戏窗口的精确发现、绑定、存活校验和元数据读取。
- 原生窗口截图、帧元数据、超时、取消和错误分类。
- 截图坐标到全局屏幕坐标的统一映射。
- 单击、拖动和鼠标位置恢复。
- 屏幕录制与辅助功能权限的独立检查和可操作错误提示。
- 旧后端与新后端的影子对比、开关、指标和快速回退。
- 两种窗口尺寸、Retina 缩放、多显示器负坐标和移动窗口验证。
- 为后续窗口标定和多开提供稳定的 `targetId -> windowId` 边界。

### 3.2 本期不包含

- 不依赖或内嵌 Codex、Computer Use、Sky 或 `CUAService`。
- 不替换 Maa OCR、模板匹配和已有资源文件。
- 不引入视觉大模型直接决定点击。
- 不支持真正后台输入；点击前仍会激活目标游戏窗口。
- 不在本期开放多窗口并发执行，多开只保留接口和数据结构。
- 不改变任务的资产风险策略、结果协议和今日完成口径。

## 4. 目标架构

```text
Dashboard / Task Runner / Metrics Collector
                    |
                    v
             MacOSWindowBackend
       +------------+-------------+
       |            |             |
    target()      capture()    click()/drag()
       |            |             |
       +------------+-------------+
                    |
             Native SCK Backend
       +------------+-------------+
       |                          |
 ScreenCaptureKit frame      CGEvent input
       |                          |
 Frame + content rect      shared coordinate map
                    |
                    v
        Maa OCR / Template Recognition
                    |
                    v
       Existing task state machines and guards
```

旧 `WindowCapture` 和 `NativeMacOSInput` 在迁移期通过 `LegacyWindowBackend` 适配到同一接口。业务任务只依赖接口，不再自行选择截图方式或计算窗口坐标。

## 5. 接口契约

建议新增统一数据结构：

```python
@dataclass(frozen=True)
class WindowTarget:
    target_id: str
    window_id: int
    pid: int
    bundle_id: str
    title: str

@dataclass(frozen=True)
class CapturedFrame:
    image: np.ndarray
    pixel_size: tuple[int, int]
    window_bounds_points: tuple[float, float, float, float]
    content_rect_points: tuple[float, float, float, float]
    scale_factor: float
    capture_method: str
    captured_at: str

class MacOSWindowBackend(Protocol):
    def bind(self, target_id: str) -> WindowTarget: ...
    def health(self) -> dict: ...
    def capture(self, guard) -> CapturedFrame: ...
    def click(self, point, frame: CapturedFrame, guard) -> tuple[float, float]: ...
    def drag(self, start, end, frame: CapturedFrame, guard) -> None: ...
```

关键约束：

- `capture`、`click` 和 `drag` 必须使用同一个已绑定窗口 ID。
- 输入必须携带产生识别框的那一帧元数据，不能重新按窗口标题选择目标。
- 每次输入前检查取消令牌、窗口存活、Bundle ID 和内容矩形签名。
- 识别框使用当前帧像素坐标；后端只在发送输入时进行一次像素到屏幕点的转换。
- 内容尺寸、缩放或窗口 ID 发生变化时阻止输入，并要求重新捕获或重新标定。
- 所有错误返回稳定错误码，并区分权限、窗口消失、窗口最小化、截图超时、空帧和输入失败。

## 6. 原生实现方案

### 6.1 窗口发现

- 沿用现有 Bundle ID `com.netease.my`、窗口标题和窗口面积筛选。
- 首次绑定后保存 `window_id + pid + bundle_id`，后续不再按标题重新选择。
- 每次输入前通过 `CGWindowListCopyWindowInfo` 校验目标仍存在、处于 layer 0 且属于原进程。
- 为 Wrapper 临时路径和相同 Bundle ID 实例保存可见别名，但正式执行仍以窗口 ID 为唯一目标。

### 6.2 截图

首选在持久原生桥中使用 `ScreenCaptureKit`：

- 通过目标窗口建立 `SCContentFilter`。
- 使用 `SCScreenshotManager` 或持久 `SCStream` 获取单帧。
- 输出 BGRA/RGB 原始像素，不经过 PNG、临时文件或固定尺寸缩放。
- 同时返回窗口边界、内容矩形、像素尺寸、缩放因子和捕获时间。
- 捕获设置排除光标，保留实际游戏内容尺寸。
- 空帧、黑帧、超时和窗口最小化分别上报，不自动切换到其他窗口。

兼容策略：

- 第一阶段可把现有 `/usr/sbin/screencapture -l` 作为 `system` 后端，先消除 Maa 首次失败日志。
- 原生 `ScreenCaptureKit` 稳定后成为 `native` 默认后端。
- 系统命令后端只作为显式回退，不在同一帧内无限重试。

### 6.3 点击与拖动

- 复用现有 `CGEvent` 单次 down/up 和拖动实现。
- 把窗口激活、坐标映射和事件发送纳入统一后端。
- 删除固定 `title_bar_height = 28`，使用 `CapturedFrame.content_rect_points`。
- 映射公式统一为：截图像素归一化坐标映射到内容矩形屏幕点坐标。
- 对 Retina、外接显示器、负屏幕原点和窗口移动分别建立测试。
- 每个输入只允许一个平衡的 down/up 序列；停止确认后不得有延迟第二击。

### 6.4 权限与诊断

- 屏幕录制权限只影响截图，辅助功能权限只影响输入，提示分别展示。
- 控制台系统状态增加当前后端、最近截图方式、最近帧耗时、目标窗口 ID 和内容尺寸。
- 事件日志记录 `targetId`、`windowId`、帧 ID、源坐标、屏幕坐标、后端和耗时。
- 不在普通日志保存完整截图；失败样本必须由用户显式开启诊断后落盘并支持清理。

## 7. 分阶段实施与提交边界

### 阶段 0：基线测量

- 记录当前 Maa、系统回退和 Computer Use 辅助诊断下的截图成功率、尺寸和延迟。
- 选取主界面、活动页、战斗页和弹窗页作为固定样本。
- 明确最低支持 macOS 版本和 ScreenCaptureKit API 可用范围。

提交：`test: add macos capture baseline diagnostics`

### 阶段 1：统一后端接口

- 新增 `MacOSWindowBackend`、`WindowTarget` 和 `CapturedFrame`。
- 用 `LegacyWindowBackend` 包装现有 `WindowCapture + NativeMacOSInput`。
- `MacOSTaskRuntime`、科举、三界奇缘和指标采集改为依赖接口，行为不变。

提交：`refactor: abstract macos window backend`

### 阶段 2：原生截图后端

- 在原生桥中实现按窗口 ID 的 ScreenCaptureKit 单帧截图。
- 返回原始帧和完整坐标元数据。
- 增加权限、超时、空帧、黑帧、窗口消失和取消测试。
- 先通过环境开关启用，不改变默认后端。

提交：`feat: add native macos window capture backend`

### 阶段 3：统一坐标与输入

- 把现有 CGEvent 点击/拖动并入新后端。
- 使用内容矩形替代固定标题栏高度。
- 接入窗口标定签名和输入前漂移门控。
- 覆盖多显示器负坐标、1x/2x 缩放和窗口移动。

提交：`feat: use frame-aware native macos input mapping`

### 阶段 4：影子对比与灰度切换

- 增加 `legacy`、`native`、`compare` 三种模式。
- `compare` 只并行截图和识别，不允许旧后端发送第二套输入。
- 对比帧尺寸、锚点位置、OCR 结果、模板命中、截图延迟和空帧率。
- 控制台展示当前后端与回退原因。

提交：`feat: add macos backend comparison mode`

### 阶段 5：切换默认并清理 Maa 控制路径

- 在白名单任务逐项实机通过后，把 `native` 设为默认。
- 保留一个发布周期的 `legacy` 快速回退。
- 稳定后移除 Maa 截图和 Maa 输入初始化，但继续保留 Maa OCR/模板能力。
- 更新安装、权限、故障排查和窗口标定文档。

提交：`refactor: default to native macos window control`

## 8. 测试矩阵

### 8.1 离线与单元测试

- 窗口绑定后不会因相同标题或 Bundle ID 切换目标。
- 截图超时、空帧、黑帧、窗口消失和权限不足返回稳定错误。
- 取消发生在截图前、截图等待中、点击前和拖动前时均不产生后续输入。
- 像素坐标到屏幕点坐标在 1x、2x、负屏幕原点和窗口移动下正确。
- 同一帧只进行一次坐标变换，不重复缩放 OCR 框。
- 输入前窗口签名变化会阻止点击。
- 影子模式永远只允许一个后端执行输入。

### 8.2 实机矩阵

- 窗口尺寸至少覆盖 `1020 x 718` 和另一种声明尺寸。
- 内建屏幕 Retina 与外接显示器各验证一次。
- 游戏窗口位于主显示器、负坐标副显示器和移动后位置各验证一次。
- 主界面、活动页、战斗页、中央弹窗和长列表拖动各采集 200 帧。
- 三界奇缘、科举乡试、帮派签到、普通运镖和秘境降妖各完成一次端到端运行。
- 运行中停止，确认停止响应后零点击。
- 同 Bundle ID 临时 Wrapper 存在时仍绑定原窗口。

## 9. 验收标准

- 连续 200 帧原生截图无空帧、无错误窗口、无 Maa `screencap failed` 日志。
- 原生截图 p95 延迟不高于 250 ms；不得通过每帧启动子进程实现。
- 两种窗口尺寸和 1x/2x 缩放下，预测点击点落在目标识别框内，映射误差不高于 5 像素。
- 移动窗口后无需重新训练识别；内容尺寸或缩放变化能在输入前被发现。
- 所有白名单任务的 OCR/模板离线结果与旧后端等价，允许的差异必须有样本和说明。
- 权限失败、窗口消失和标定漂移均为零点击安全停止。
- `compare` 模式不产生双击，主动停止后不再产生输入。
- 新后端失败时可通过单一配置切回 `legacy`，无需修改任务代码。

## 10. 风险与回退

- ScreenCaptureKit API 存在系统版本差异：构建时做可用性检查，低版本显式使用 `system` 后端。
- 原生桥异步回调可能在取消后返回：帧必须绑定请求 ID，过期帧直接丢弃。
- Retina 和内容矩形解释错误会导致全局偏移：在切换默认前强制通过可视化标定和点击预测测试。
- 游戏更新可能改变标题栏或渲染尺寸：只信任实时内容矩形和锚点，不保存固定 28 点偏移。
- 双后端对比可能增加 CPU：仅在诊断模式启用，并设置最长持续时间。
- 原生后端发布后保留 `legacy` 一个发布周期；发现窗口串绑、黑帧或坐标漂移时立即切回。

## 11. 预计文件调整

建议新增：

- `tools/macos_window_backend.py`
- `tools/macos_native_backend.py`
- `tools/macos_window_control.mm` 或等价 Swift 原生桥
- `tests/test_macos_window_backend.py`
- `tests/test_macos_native_backend.py`

预计修改：

- `tools/macos_task_runtime.py`
- `tools/run_sanjieqiyuan_macos.py`
- `tools/run_keju_macos.py`
- `dashboard/metrics.py`
- `dashboard/system.py`
- `docs/mac窗口运行教程.md`
- `docs/macos-dashboard-roadmap.md`

## 12. 与后续规划的关系

- 窗口标定直接消费 `CapturedFrame.content_rect_points`、像素尺寸和缩放因子，不再猜测标题栏高度。
- 任务编排继续保持单窗口串行，新后端只提供稳定控制能力，不改变编排语义。
- 多开后置，但每个实例必须拥有独立 `targetId`、窗口 ID、后端实例、标定和运行锁。
- 在原生控制层稳定前，不启动多开自动化开发，避免把单窗口坐标问题复制到多个实例。

## 13. 推荐实施顺序

先完成阶段 0 和阶段 1，以不改变行为的方式建立接口和基线；随后实现原生截图，并用影子模式验证。原生截图稳定后再改坐标映射和输入，最后逐任务灰度切换。不要一次性删除 Maa 控制路径，也不要把底层迁移与秘境、运镖等任务状态机修复合并提交。
