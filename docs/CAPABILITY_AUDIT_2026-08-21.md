# Web-Watcher 全局能力盘点与缺口判断

> 日期：2026-08-21  
> 版本：v1.0.6  
> 状态：基于当前代码库 / 测试 1540 passed / README 已同步

---

## 一、已锁定能力边界

| 能力域 | 已实现 | 明确不实现 / 排除 |
|--------|--------|-------------------|
| **Target 类型** | GenericWebTarget（CSS/XPath + scope_selector + transforms）、GitHubTarget（releases/stars/tags 子资源隔离）、RSSFeedTarget（RSS 2.0 / Atom 1.0） | XPath scope（scope_xpath 不实现）、Playwright/JS 渲染（js_fetcher 已移除） |
| **Fetch 层** | ETag/Last-Modified 条件请求、重定向记录（不自动跟随）、429 Retry-After、超时/网络错误分类、Playwright 版本探测回退 | 自动跟随重定向、JS 渲染、代理轮换、WAF/CAPTCHA 绕过 |
| **规则与评估** | YAML 解析、运行时启用/禁用/优先级/分组（RuleRegistry）、text_diff/numeric_delta/regex_match/node_changed、AND/OR condition_group、time_window_minutes | 复杂多 selector 组合规则（已有 condition_group 覆盖部分场景） |
| **Diff 与 Scope** | scope_selector（CSS）、scope miss 阻止后续 diff/signal、evidence 保留原始裁剪后文本、确定性 fingerprint | scope_xpath（不实现） |
| **Pipeline** | Signal → Event → Investigation → Evidence → Notification 端到端编排、CrossTargetCorrelator（跨 target 关联分析） | 历史趋势检测、AI 驱动的智能降噪 |
| **调查** | 确定性 Planner（单步 PlanStep）、Engine（budget 检查）、EventCorrelator（信号关联/事件聚合/自动调查调度） | InvestigationFinding 构造、INCONCLUSIVE 推断、重试/重规划 |
| **通知渠道** | Console、Webhook、Slack (Block Kit)、Lark、DingTalk、Email (SMTP/SSL/STARTTLS)、Telegram (Bot API)、Discord (Webhook Embed) | — |
| **通知机制** | 至少一次投递、指数退避重试、fencing、AlertSilencer（内容相似度抑制）、动态噪声（STANDARD/AGGRESSIVE）、notify --history / --stats / --retry、digest（daily/weekly/custom） | 恰好一次投递、富文本模板引擎（已有 card_formatters 基础） |
| **持久化** | SQLite 自动 schema 初始化、declaration 过期清理、Retention/Export 过滤（entity_id/event_type/importance/status/channel）、文件权限 0o600 | 分布式存储、外部数据库迁移 |
| **运维 CLI** | run/daemon/worker/inspect/reload/registry/template/targets/notify/digest/export/test-rule/doctor/webui | — |
| **监控模板** | 9 个内置 preset（github_release/blog_post/price/product_page/news_article/status_page/changelog/rss_feed 等）、template apply/list/show | — |
| **Web UI** | 轻量本地监控台（基于 Python stdlib http.server，零外部依赖），页面：/、/targets、/events/<id>；API：/api/targets、/api/events、/api/events/<id>、/api/stats | 远程无认证访问、多用户、复杂可视化 |
| **Ground Truth** | 1540 passed、9 个新增 Web UI 测试、6 个新增 Telegram/Discord 测试 | — |

---

## 二、真实缺口判断

### 高优先级缺口（产品能力核心）

1. **规则/目标导入导出**
   - **现状**：仅支持 YAML 文件手动编辑
   - **缺口**：无法批量迁移或备份监控配置
   - **影响**：配置迁移成本高
   - **建议**：提供 `rules import/export`、`targets import/export` CLI 子命令

2. **持久化存储迁移方案**
   - **现状**：SQLite 单节点，无迁移/升级工具
   - **缺口**：schema 变更或切换后端时无平滑迁移路径
   - **影响**：升级风险高
   - **建议**：提供 `db migrate` CLI 或版本化 schema 迁移脚本

### 中优先级缺口（体验/运维）

3. **灰度发布 / 回滚机制**
   - **现状**：Docker 部署仅有 `docker compose up -d`
   - **缺口**：无滚动更新、健康检查、自动回滚
   - **影响**：生产部署风险
   - **建议**：补充 Docker Compose healthcheck + restart_policy，或提供 deploy 脚本

4. **多 Target 批量操作增强**
   - **现状**：`targets delete` 支持按标签批量删除
   - **缺口**：缺少批量启用/禁用、批量修改规则
   - **影响**：运维效率
   - **建议**：新增 `targets enable/disable`、`targets set-rule` 等子命令

### 低优先级 / 暂不进入

5. **Playwright / JS 渲染** — 已明确排除
6. **分布式部署** — 与当前 SQLite + 单节点架构冲突，需独立 Phase
7. **AI 驱动的智能降噪** — 超出当前 Personal AI Agent 观察层定位

---

## 三、是否值得进入下一轮 Design

**结论：值得，但需先选一个具体方向。**

当前产品已完成：
- 核心监控闭环（Target → Fetch → Signal → Event → Investigation → Notification）
- 规则热加载、运行时注册表、模板生态、批量操作、过滤/保留策略
- 多渠道通知（Console/Webhook/Slack/Lark/DingTalk/Email/Telegram/Discord）
- 事件汇总（Digest daily/weekly/custom）
- 轻量本地监控台（Web UI v1）
- Ground Truth 验证（1540 passed）
- RSS/Atom Feed 监控、Email 通知、Cross-target 关联分析

**剩余工作已从「基础能力补齐」转向「场景深化」。**

建议下一轮 Design 的输入选项：
1. **规则/目标导入导出** — 提供配置迁移和备份能力
2. **持久化存储迁移方案** — 提供平滑 schema 升级路径
3. **多 Target 批量操作增强** — 提升运维效率
4. **Docker 部署增强** — healthcheck、restart_policy、日志收集

**不推荐**在 Design 前做「全量新功能 brainstorm」。应先选 1 个方向，出 Design Doc，再做验收标准与测试计划。

---

## 四、当前工作区状态

- 分支 `master` 与 `origin/master` 同步
- HEAD / origin/master：`a367d73` (docs: restore DESIGN_DIGEST_V1.md; bump tag to 1.0.6)
- 最新 tag：`web-watcher-1.0.6`
- 测试：1540 passed
- 已提交：`docs/CAPABILITY_AUDIT_2026-08-21.md`
- 已归档设计文档：`docs/DESIGN_TELEGRAM_DISCORD_V1.md`、`docs/DESIGN_WEBUI_V1.md`

---

## 五、建议下一步动作

1. 用户选择上述 1 个方向（导入导出 / 存储迁移 / 批量操作增强 / Docker 增强）
2. 输出该方向的 Design Doc（含边界、接口、验收、测试）
3. 按 Design Doc 施工 + Ground Truth 验证
4. 更新 README / TODO / 版本号
