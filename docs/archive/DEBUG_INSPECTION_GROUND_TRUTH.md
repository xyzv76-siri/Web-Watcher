# DEBUG_INSPECTION v1 Ground Truth

## 验收结果：通过

| # | 检查点 | 状态 | 证据 |
|---|--------|------|------|
| 1 | `inspect` 子命令可用 | ✅ | `python -m web_watcher.cli inspect --help` 正常 |
| 2 | `--rule` + `--html-file` 离线执行 | ✅ | `test_inspect_with_local_html` PASSED |
| 3 | `--rule` + `--url` 远程执行 | ✅ | CLI 支持，手动验证通过 |
| 4 | scope miss 明确显示 | ✅ | `test_inspect_scope_miss` PASSED；输出包含 `scope_miss: True` |
| 5 | 无 scope 规则行为正常 | ✅ | `test_inspect_with_local_html` 无 scope 规则正常 |
| 6 | `--extractor` 过滤 | ✅ | CLI 参数已实现，handle_inspect 中过滤逻辑正确 |
| 7 | `--verbose` 详细输出 | ✅ | CLI 参数已实现，limit 控制正确 |
| 8 | 不修改数据库 | ✅ | handle_inspect 无 Database 操作 |
| 9 | 不持久化 observation/signal | ✅ | 仅内存执行，无 save 调用 |
| 10 | 全量测试不减少 | ✅ | 1444 passed（+3 new） |
| 11 | Git / origin / clean | ✅ | 已 push 8e2f71a；仅修改 cli.py + tests + fixtures |

## 结论
Debug / Inspection Mode v1 已完成并推送。
