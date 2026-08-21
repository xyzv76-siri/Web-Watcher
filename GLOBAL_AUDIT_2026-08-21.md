# GLOBAL AUDIT 2026-08-21 — 十一阶段全项目审查

## 执行摘要

| 项目 | 状态 |
|------|------|
| 审查模式 | 只读审查，零修改 |
| 仓库 | web-watcher @ master / origin/master |
| 最新 commit | `a367d73` (refactor: split cli.py and repository.py) |
| 测试基线 | 1540 passed / 0 failed / 0 skipped |
| 生产文件 | 67 个 |
| 测试文件 | 80+ 个 |
| 总跟踪文件 | 159+ 个 |

## 十一阶段审查结果

### Phase 1 — Ground Truth 采集

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 测试执行 | PASS | pytest 1540 passed, 0 failed, 0 skipped |
| pytest 配置 | PASS | `pythonpath = ["src"]`, `testpaths = ["tests"]` |
| conftest.py | N/A | 未发现 |
| pytest.ini | N/A | 未发现 |
| skip/xfail | INFO | 23 处 parametrize，无 skip/xfail |
| 测试分布 | INFO | 1540 tests，含 unit / integration / e2e |

### Phase 2 — 架构全局审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 循环依赖 | PASS | 77 个模块，0 循环依赖 |
| 模块职责 | PASS | cli → cli_handlers，repository → repository_utils，职责清晰 |
| 全局状态 | PASS | 无 Singleton 模式 |
| 并发模型 | PASS | 单线程调度，无全局 ThreadPool/ProcessPool |
| SQLite 事务边界 | PASS | finalize_execution 使用 `with self.connection:` 确保原子性 |
| claim/lease 原子性 | PASS | UPDATE ... WHERE 条件确保原子 acquire/renew/release |
| finalize_execution 原子性 | PASS |  fencing + 更新 target + persist signals + 创建/更新 events + 创建 links 全部在单个事务中 |

### Phase 3 — 安全审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| SQLite 权限 | PASS | `chmod(0o600)` 设置 |
| URL 验证 | PASS | 仅允许 http/https，有 scheme 和 hostname 检查 |
| YAML 解析安全 | PASS | 全部使用 `yaml.safe_load` |
| 硬编码凭证 | PASS | 未发现硬编码密码/token/secret |
| 日志泄露 | PASS | 未发现日志中记录敏感信息 |
| 外部 HTTP timeout | PASS | Webhook/Email/Telegram/Discord 均设置 timeout |
| path traversal | PASS | 未发现未验证的用户路径输入 |

### Phase 4 — 配置真实性审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| js_render | PASS | parser 支持，runtime 使用，有测试覆盖 |
| cookies | PASS | parser 支持，runtime 使用，有测试覆盖 |
| basic_auth | PASS | parser 支持，runtime 使用，有测试覆盖 |
| proxy | PASS | parser 支持，runtime 使用，有测试覆盖 |
| condition_group/operator | PASS | parser 支持，runtime 使用，有测试覆盖 |
| time_window_minutes | PASS | parser 支持，runtime 使用，有测试覆盖 |
| 环境变量覆盖 | PASS | WEB_WATCHER_* 系列全部实现 |
| noise_reduction_level | PASS | 环境变量覆盖已实现 |

### Phase 5 — Cross-Target 全局审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| CrossTargetCorrelator | PASS | evaluate_signals / evaluate_events 已实现 |
| cross_target_rules.yaml | PASS | YAML 热加载已实现 |
| ScheduledRunner 集成 | PASS | 信号/事件均接入 CrossTargetCorrelator |
| dedup/merge | PASS | 已实现 |
| evaluate_events | PASS | 已实现 |
| Part 1-6 测试 | PARTIAL | Part 1, 2, 6 有独立测试文件；Part 3/4/5 分散在其他测试中 |

### Phase 6 — 测试覆盖质量审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| scheduled_runner | WARN | 无直接测试文件（tests/test_scheduled_runner.py 不存在） |
| mock 使用 | WARN | 1264 处 mock，部分测试可能未覆盖真实链路 |
| integration test | PASS | 存在 test_config_integration, test_pipeline_rules_integration, test_retention_integration |
| e2e test | INFO | 无独立 e2e 目录，但部分 integration 测试模拟完整链路 |

### Phase 7 — 文档/代码一致性审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 版本号同步 | PASS | pyproject.toml / __init__.py / README / CAPABILITY_AUDIT 均为 1.0.6 |
| 测试基线同步 | PASS | README / CAPABILITY_AUDIT 均记录 1540 passed |
| Preset 数量 | PASS | 9 个 presets 已实现（github_release, blog_post, price, noise_reduction, product_page, news_article, status_page, changelog, rss_feed） |
| Web UI | PASS | webui.py 已实现，9 个测试通过 |
| Telegram/Discord | PASS | channel_senders.py 已实现，6 个测试通过 |
| Hot Reload | PASS | --include-tag / --exclude-tag 参数已实现 |
| Rule Registry | PASS | registry CLI 子命令已实现 |

### Phase 8 — Git/Release 审计

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 版本号同步 | PASS | 1.0.6 已打 tag |
| 提交边界 | PASS | 最近 5 个 commit 均未修改冻结的底层 schema/rule_models/rule_parser |
| 工作区状态 | PASS | 干净（除 GLOBAL_AUDIT 报告外） |

### Phase 9 — 历史变更审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 最近 5 commit | PASS | 均为小步提交，范围可控 |
| commit message | PASS | 清晰描述变更内容 |

### Phase 10 — 并发与一致性审查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| SQLite journal_mode | **WARN** | 当前为 `delete`，未启用 WAL |
| SQLite busy_timeout | **WARN** | 未设置，并发写入时可能报 `database is locked` |
| host_rate_limiter 持久化 | **WARN** | _active_claims 内存状态，进程重启丢失 |
| retry 机制 | WARN | 无通用 retry（仅通知重试） |
| crash recovery | WARN | 无显式 recovery 流程 |

### Phase 11 — 运行状态验证

| 检查项 | 状态 | 说明 |
|--------|------|------|
| doctor 检查 | PASS | HEALTHY WITH WARNINGS |
| 测试执行 | PASS | 1540 passed |
| 本地运行 | PASS | Python 3.11.2, SQLite 3.40.1 可用 |

## 问题分级汇总

### P0 — 阻塞性（无）

### P1 — 高优先级（2 项）

| # | 问题 | 影响 | 证据 |
|---|------|------|------|
| 1 | SQLite 未启用 WAL 模式 | 并发写入阻塞/失败 | `storage.py` 无 `PRAGMA journal_mode=WAL`，`doctor.py` 显示 `journal_mode: delete` |
| 2 | SQLite 未设置 busy_timeout | 锁冲突时立即失败 | `storage.py` 无 `busy_timeout` 配置 |

### P2 — 中优先级（4 项）

| # | 问题 | 影响 | 证据 |
|---|------|------|------|
| 1 | host_rate_limiter 内存状态不持久化 | 进程重启后 claim 丢失，可能导致重复请求 | `host_rate_limiter.py:27` `self._active_claims = {}` |
| 2 | scheduled_runner 无直接测试 | 核心调度链路缺少 unit test | `tests/` 无 `test_scheduled_runner.py` |
| 3 | mock 使用过多（1264 处） | 部分测试可能未覆盖真实 fetch 链路 | `test_generic_web_target.py` 等大量 mock fetcher |
| 4 | 无通用 retry / timeout 机制 | 网络抖动时任务直接失败 | `src/web_watcher/*.py` 无 retry decorator/utility |

### P3 — 低优先级（0 项）

### P4 — 建议（0 项）

## 推荐行动计划

1. **立即**：在 `storage.py` 的 `open_database()` 中添加 `PRAGMA journal_mode=WAL` 和 `PRAGMA busy_timeout=5000`
2. **立即**：将 `host_rate_limiter._active_claims` 持久化到 SQLite，或实现启动时 reaping
3. **短期**：为 `scheduled_runner.py` 添加 direct unit test
4. **短期**：为网络操作添加通用 retry 机制（如 tenacity）
5. **中期**：评估 mock 比例，增加真实链路测试

## 审查约束确认

- [x] 未修改任何源码/测试/配置
- [x] 未执行 commit/push/tag/release
- [x] 未自动格式化
- [x] 未顺手修复任何问题
- [x] 所有结论基于当前 VPS/Git 仓库真实状态
- [x] 不接受历史报告作为证据
- [x] 每项证据仅采集一次
- [x] 发现问题仅记录，未施工
