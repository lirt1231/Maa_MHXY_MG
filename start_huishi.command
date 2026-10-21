#!/bin/zsh
# 在游戏中手动进入科举会试等待页面后运行；Ctrl-C 停止。
set -eu

cd "${0:A:h}"

provider_args=()
while (( $# > 0 )); do
    if [[ "$1" == "--help" || "$1" == "-h" ]]; then
        print '用法：./start_huishi.command [--web-search-provider PROVIDER] ...'
        print '先手动进入科举会试等待页面，再运行此脚本。检测到第 1 题后自动答题。'
        print '模型读取 config/keju_model.json 的会试配置；Ctrl-C 停止。'
        print '搜索默认使用配置中的 DuckDuckGo/Bing RSS；可用 provider：duckduckgo、bing_rss、deepseek。'
        print '使用 deepseek 前请在环境变量 MAA_DEEPSEEK_API_KEY 中提供密钥。'
        print '仅用于开场启动，已开始答题后的恢复请使用运行器的 --wait-for-entry。'
        exit 0
    fi
    if [[ "$1" != "--web-search-provider" || $# -lt 2 ]]; then
        print -u2 '参数格式错误，请使用 --help 查看用法。'
        exit 2
    fi
    case "$2" in
        duckduckgo|bing_rss|deepseek) ;;
        *) print -u2 "不支持的搜索 provider：$2"; exit 2 ;;
    esac
    provider_args+=("$1" "$2")
    shift 2
done

if pgrep -f '[p]ython[^ ]* .*tools/run_keju_macos.py' >/dev/null; then
    print -u2 '已有科举运行器进程，请先停止原进程，避免重复答题。'
    exit 1
fi

print '正在只读检查会试页面，请保持游戏停留在会试等待页面。'
for attempt in 1 2; do
    inspection=$(uv run python tools/run_keju_macos.py --exam-type huishi --inspect-scene)
    if printf '%s\n' "$inspection" | uv run python -c '
import json, sys
lines = [line for line in sys.stdin if line.startswith("preflight_json=")]
if not lines:
    print("未取得页面检查结果。", file=sys.stderr)
    sys.exit(1)
decision = json.loads(lines[-1].split("=", 1)[1])["decision"]
print("页面检查：", json.dumps(decision, ensure_ascii=False))
sys.exit(0 if decision.get("examType") == "huishi" and decision.get("status") in
         {"waiting_for_question", "formal_question_ready"} else 1)
'; then
        print '已确认科举会试，等待第 1 题后自动答题；按 Ctrl-C 停止。'
        exec uv run python tools/run_keju_macos.py --exam-type huishi \
            "${provider_args[@]}" --wait-for-round-one
    fi
done

print -u2 '无法确认会试等待或答题页面，未启动答题。请手动进入会试后重试。'
exit 1
