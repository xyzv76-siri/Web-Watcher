# Ground Truth — 812d2a2 / 027a9c3

## Baseline
- Previous GA: `7393347` (Retention / Export)
- Current HEAD: `027a9c3`
- master ↔ origin/master: synchronized
- Worktree: clean
- Full test suite: **1428 passed**
- Tracked files: **159** (production 67, tests 80)

## Scope of changes (7393347 → 027a9c3)
9 files changed, 484 insertions(+), 33 deletions(-)

| File | Change | Verdict |
|------|--------|---------|
| README.md | Preset 列表扩展；新增「按标签分组巡检」章节 | ✅ 文档边界内 |
| src/web_watcher/cli.py | `targets list --tag`；`rules list/show` 显示 tags；`template show` 输出 tags | ✅ CLI 边界内 |
| src/web_watcher/models.py | `Target.tags: List[str]` | ✅ 数据模型扩展 |
| src/web_watcher/rule_models.py | `WatcherRule.tags: List[str]` | ✅ 数据模型扩展 |
| src/web_watcher/rule_parser.py | 解析 `tags` 字段 | ✅ 解析层扩展 |
| src/web_watcher/repository.py | `tags` 列 + `list_targets(tags, require_all)` | ✅ 存储层扩展 |
| src/web_watcher/presets/registry.py | 4 个新 preset + 全部 8 个 preset 默认 tags | ✅ Preset 层扩展 |
| src/web_watcher/scheduled_runner.py | `sync_rules` 保持 rule→target tags 同步 | ✅ 已移除运行时过滤 |
| tests/test_presets.py | 20 个 preset 测试用例 | ✅ 测试覆盖 |

## Functional verification
- [x] `Target.tags` / `WatcherRule.tags` 可序列化/反序列化
- [x] SQLite `targets.tags` 列自动迁移（ALTER TABLE）
- [x] `list_targets(tags=..., require_all=...)` OR/AND 过滤正确
- [x] `RuleParser` 解析 YAML `tags` 字段
- [x] `template show` 输出包含 `tags`
- [x] `template apply` 生成的 YAML 包含 `tags`
- [x] `rules list` 显示 Tags 列
- [x] `rules show` 显示 Tags
- [x] `targets list --tag <name>` 过滤正确
- [x] 8 个 preset 全部附带默认 tags
- [x] README 包含标签分组巡检示例
- [x] ScheduledRunner 中 **无** 未完成的运行时过滤残留

## Backward compatibility
- 旧规则无 `tags` 字段 → 解析为 `[]`，不影响现有行为
- 旧数据库自动迁移添加 `tags` 列，默认 `'[]'`
- `list_targets()` 无参数时行为与之前一致
- `_rule_to_yaml` 仅在 `rule.tags` 非空时输出 `tags` 段

## Out of scope (deferred)
- run/daemon `--include-tags` / `--exclude-tags` 已从 CLI 移除
- ScheduledRunner 运行时标签过滤已移除，等待 Tag Filtering v1 Design

## Next step
Tag Filtering v1 Design (no implementation)
