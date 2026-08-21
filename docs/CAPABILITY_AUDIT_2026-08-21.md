# Web-Watcher 全局能力盘点与缺口判断

> 日期：2026-08-21  
> 版本：v1.0.2  
> 状态：基于当前代码库 / 测试 1508 passed / README 已同步

---

## 一、已锁定能力边界

| 能力域 | 已实现 | 明确不实现 / 排除 |
|--------|--------|-------------------|
| **Target 类型** | GenericWebTarget（CSS/XPath + scope_selector + transforms）、GitHubTarget（releases/stars/tags 子资源隔离） | RSS/Atom Feed、Playwright/JS 渲染（js_fetcher 已移除）、XPath scope（scope_xpath 不实现） |
| **Fetch 层** | ETag/Last-Modified 条件请求、重定向记录（不自动跟随）、429 Retry-After、超时/网络错误分类 | 自动跟随重定向、JS 渲染、代理轮换、WAF/CAPTCHA 绕过 |
| **规则与评估** | YAML 解析、运行时启用/禁用/优先级/分组（RuleRegistry）、text_diff/numeric_delta/regex_match/node_changed、AND/OR condition_group、time_window_minutes | 复杂多 selector 组合规则（已有 condition_group 覆盖部分场景） |
| **Diff 与 Scope** | scope_selector（CSS）、scope miss 阻止后续 diff/signal、evidence 保留原始裁剪后文本 | scope_xpath（不实现） |
| **Pipeline** | Signal → Event → Investigation → Evidence → Notification 端到端编排 | 跨 target 关联分析、历史趋势检测 |
| **调查** | 确定性 Planner（单步 PlanStep）、Engine（budget 检查）、EventCorrelator（信号关联/事件聚合/自动调查调度） | InvestigationFinding 构造、INCONCLUSIVE 推断、重试/重规划 |
| **通知渠道** | Console、Webhook、Slack (Block Kit)、Lark、DingTalk | 邮件、Telegram、Discord 原生集成 |
| **通知机制** | 至少一次投递、指数退避重试、fencing、AlertSilencer（内容相似度抑制）、动态噪声（STANDARD/AGGRESSIVE） | 恰好一次投递、富文本模板引擎（已有 card_formatters 基础） |
| **持久化** | SQLite 自动 schema 初始化、declaration 过期清理、Retention/Export 过滤（entity_id/event_type/importance/status/channel） | 分布式存储、外部数据库迁移 |
| **运维 CLI** | run/daemon/worker/inspect/reload/registry/template/targets/notify/export/test-rule/doctor | — |
| **Ground Truth** | 1508 passed、12 项运行时验证、设计文档归档 | — |

---

## 二、真实缺口判断

### 高优先级缺口（产品能力核心）

1. **RSS/Atom Feed 监控**
   - **现状**：仅支持 Web 页面和 GitHub API
   - **缺口**：缺少对 RSS/Atom 的原生解析，用户需通过 Web 抓取间接监控
   - **影响**：博客、新闻、论坛等常见场景覆盖不足
   - **建议**：新增 `RSSFeedTarget` 或扩展 `GenericWebTarget` 支持 `feed` selector_type

2. **通知渠道扩展（邮件 / Telegram / Discord）**
   - **现状**：5 个内置渠道，邮件/Telegram/Discord 需通过 Webhook 中转
   - **缺口**：原生集成缺失，用户需额外配置中转服务
   - **影响**：通知到达率和使用门槛
   - **建议**：优先实现邮件 SMTP（已有 himalaya skill 可参考），其次 Telegram Bot API / Discord Webhook

3. **跨 target 关联分析**
   - **现状**：EventCorrelator 仅按 `entity_id` 聚合同一目标的信号
   - **缺口**：无法发现「目标 A 变化 → 目标 B 变化」的因果或关联模式
   - **影响**：多源监控的价值未充分释放
   - **建议**：新增 `CorrelationRule` 或扩展 `EventCorrelator` 支持跨 entity 关联窗口

### 中优先级缺口（体验/运维）

4. **持久化存储迁移方案**
   - **现状**：SQLite 单节点，无迁移/升级工具
   - **缺口**：schema 变更或切换后端时无平滑迁移路径
   - **建议**：提供 `db migrate` CLI 或版本化 schema 迁移脚本

5. **灰度发布 / 回滚机制**
   - **现状**：Docker 部署仅有 `docker compose up -d`
   - **缺口**：无滚动更新、健康检查、自动回滚
   - **建议**：补充 Docker Compose healthcheck + restart_policy，或提供 deploy 脚本

### 低优先级 / 暂不进入

6. **Playwright / JS 渲染**
   - **决策**：已明确排除，不进入当前 Phase

7. **分布式部署**
   - **决策**：与当前 SQLite + 单节点架构冲突，需独立 Phase

---

## 三、是否值得进入下一轮 Design

**结论：值得，但需先选一个具体方向。**

当前产品已完成：
- 核心监控闭环（Target → Fetch → Signal → Event → Investigation → Notification）
- 规则热加载、运行时注册表、模板生态、批量操作、过滤/保留策略
- Ground Truth 验证（1508 passed）

**剩余工作已从「基础能力补齐」转向「场景深化」。**

建议下一轮 Design 的输入选项：
1. **RSS/Atom Feed 监控** — 扩展 Target 类型，覆盖最常见的内容监控场景
2. **邮件/Telegram/Discord 原生通知** — 扩展渠道，提升到达率
3. **跨 target 关联分析** — 从单点监控升级为关联 Intelligence

**不推荐**在 Design 前做「全量新功能 brainstorm」。应先选 1 个方向，出 Design Doc，再做验收标准与测试计划。

---

## 四、当前工作区状态

- 分支 `master` 与 `origin/master` 同步
- 最新提交：`91c9b91` (release: v1.0.2)
- 最新 tag：`web-watcher-1.0.2`
- 测试：1508 passed
- 未跟踪文件：`docs/CAPABILITY_AUDIT_2026-08-21.md`（本文件）
- 已归档设计文档：`docs/archive/`（6 份）

---

## 五、建议下一步动作

1. 用户选择上述 1 个方向（RSS / 通知渠道 / 关联分析）
2. 输出该方向的 Design Doc（含边界、接口、验收、测试）
3. 按 Design Doc 施工 + Ground Truth 验证
4. 更新 README / RELEASE_TODO / 版本号
