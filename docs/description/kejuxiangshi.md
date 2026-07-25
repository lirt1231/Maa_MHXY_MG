## 不使用 AI：选择 No

- 每题固定选择 A，仅保留作为兼容模式。

## 题库 + 远端模型：选择 Yes

1. OCR 读取当前题目和四个选项。
2. 从本地题库检索 Top 3 相似题。
3. 把当前题目、选项和相似题交给 OpenAI API 兼容模型复核。
4. 模型请求失败时，仅在题库问题相似度和答案选项匹配度均足够高时兜底；否则停止，避免盲选。

配置示例：

- `url=https://api.deepseek.com/v1/chat/completions`
- `model=deepseek-chat`（以服务商当前实际模型名为准）
- API Key 仅用于请求，不写入答题日志。

## macOS 客户端手动进入答题页

先在《梦幻西游》客户端中手动打开乡试或会试答题页面，再运行：

```bash
python tools/run_keju_macos.py --use-codex
```

该模式会使用独立的乡试两列布局 ROI，读取题干与 A/B/C/D，检索本地 Top 3，
再通过临时、只读且不保存会话的 Codex 子进程复核答案。若只想检查识别结果而不点击：

```bash
python tools/run_keju_macos.py --use-codex --recognize-only
```

也可以不使用 Codex，通过环境变量提供 OpenAI 兼容服务：

- `MAA_KEJU_API_KEY`
- `MAA_KEJU_URL`
- `MAA_KEJU_MODEL`

任一题模型失败且本地题库不足以高置信兜底时，运行器会保留当前页面并停止。

本机也可以在忽略的 `config/keju_model.json` 中保存 URL、模型名、超时和 Keychain
服务名；API Key 只存储在 macOS Keychain，不写入 JSON、仓库或运行日志。配置完成后可直接运行：

```bash
python tools/run_keju_macos.py
```
