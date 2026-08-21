# Web-Watcher 全局能力盘点与缺口判断

> 基于 v1.0.1 / commit 2908b07 的静态代码盘点，不预设立场，只陈述现状与可观察缺口。

## 1. 当前已形成的能力

### 1.1 监控目标类型
- 通用网页：CSS / XPath / 正则 / 原始文本提取
- GitHub 仓库：官方 API（releases/latest、stars、元数据）
- 条件请求：ETag + 304 短路

### 1.2 提取与归一化
- `selector_type`: css / xpath / regex / raw
- `transforms`: text / strip / to_float 等管道
- **Diff Scope v1**：`scope_selector` 裁剪 DOM，不回退，多元素合并
- Dynamic Noise：STANDARD / AGGRESSIVE 两级过滤

### 1.3 变更检测与信号
- 内容指纹 + 差异评估
- `content_change` / `text_diff` / `numeric_delta` trigger 类型
- Alert Silencer：基于内容相似度的重复/近重复抑制
- False Positive Guard

### 1.4 持久化与可靠性
- SQLite 单节点持久化（signals / events / investigations / notifications）
- 声明围栏 + 原子 finalization
- 主机级 rate limiter + 退避/冷却
- 过期租约自动恢复
- 确定性抖动（SHA-256 派生）

### 1.5 调查与证据
- Investigation Worker（后台自治）
- 证据链持久化
- Markdown / HTML 审计报告导出

### 1.6 通知渠道
- Console / Webhook / Slack / Lark / DingTalk
- 至少一次投递语义
- 通知去重/抑制

### 1.7 CLI 子命令
- `run --once` / `daemon` / `worker` / `notify`
- `export` / `doctor` / `retention`
- `test-rule`（支持本地 HTML 文件 / 远程 URL 沙盒评估）
- `template`（list / show / apply）：github_release / blog_post / price / noise_reduction

### 1.8 部署
- Docker Compose（非 root 用户，挂载 /data / /logs）
- `python -m pip install --no-deps -e .` 本地安装

## 2. 可观察的能力缺口

以下缺口均来自代码现状，不含推测。

### 2.1 规则生命周期管理
- **现状**：规则通过 YAML 文件 + `test-rule` 加载，无运行时管理
- **缺口**：没有 `rules list` / `rules show` / `rules enable` / `rules disable` / `rules remove`
- **影响**：用户无法在不编辑文件的情况下管理监控规则

### 2.2 通知可观测性
- **现状**：通知由 dispatcher 投递，持久化到 DB，无查询接口
- **缺口**：没有 `notify history` / `notify status` 命令
- **影响**：用户无法回溯"我收到过什么通知"

### 2.3 数据保留与导出
- **现状**：`retention` 子命令存在，`export` 可导出审计报告
- **缺口**：retention 策略简单（仅 max_age_days），无选择性导出；无备份/恢复
- **影响**：长期运行后数据管理能力有限

### 2.4 Preset 生态
- **现状**：4 个 preset，hard-coded registry
- **缺口**：无用户自定义 preset 保存、无导入/导出、无 preset 推荐
- **影响**：扩展监控模板需改代码

### 2.5 目标分组与批量操作
- **现状**：target 是扁平列表，无标签/分组
- **缺口**：无法按项目/优先级批量启停
- **影响**：目标增多后运维成本高

### 2.6 条件表达式能力
- **现状**：trigger `condition` 字段存在，但 evaluator 支持有限
- **缺口**：无 AND/OR 复合条件、无时间窗口条件（如"仅工作日"）
- **影响**：复杂通知策略需工作流层弥补

### 2.7 Web 监控增强
- **现状**：纯 HTTP 静态提取，无 JS 渲染
- **缺口**：无 Cookie / Basic Auth / 企业代理配置
- **影响**：SPA 或需认证的站点无法监控

### 2.8 GitHub 监控广度
- **现状**：仅 releases/latest + stars
- **缺口**：无 PR / Issue / Commit / Discussion 监控
- **影响**：GitHub 场景覆盖窄

### 2.9 实时调试
- **现状**：`test-rule` 是一次性沙盒评估
- **缺口**：无 `watch` 模式（持续观察并打印 diff）
- **影响**：调试 selector / scope / transform 需反复运行

## 3. 缺口优先级判断

| 缺口 | 用户价值 | 实现成本 | 与现有架构契合度 | 是否建议下一轮 Design |
|------|----------|----------|------------------|----------------------|
| 规则生命周期管理 | 高 | 中 | 高（已有 rule_parser / CLI / storage） | ✅ 建议 |
| 通知历史查询 | 高 | 低-中 | 高（已有 notification_dispatcher / DB） | ✅ 建议 |
| retention / export 增强 | 中 | 低 | 高（已有 retention / exporter） | ✅ 建议 |
| 更多 preset | 中 | 低 | 高（已有 preset registry） | ✅ 建议 |
| 目标分组/标签 | 中 | 中 | 中（需扩展 target schema） | ⚠️ 可考虑 |
| 条件表达式增强 | 中 | 高 | 中（需扩展 rule_evaluator） | ⚠️ 暂缓 |
| Web 监控增强（Auth/JS） | 中 | 高 | 低（偏离静态提取定位） | ❌ 不建议 |
| GitHub 场景扩展 | 中 | 中 | 中（已有 github_target） | ⚠️ 可考虑 |
| 实时 watch 模式 | 中 | 低 | 高（复用 run pipeline） | ✅ 建议 |

## 4. 建议的下一轮方向（按推荐顺序）

### 方向 A：规则与通知可观测性
- `rules list/show/enable/disable`（基于现有 YAML 文件 + DB 元数据）
- `notify history`（查询已投递通知）
- 不改变 core pipeline，只增加 CLI 查询/控制面

### 方向 B：Preset 扩展 + 用户 preset
- 增加 `rss_feed`、`status_page`、`sitemap` preset
- 支持 `template save` 将当前规则保存为自定义 preset
- 不改变 core pipeline

### 方向 C：实时调试工具
- `run --watch <rule>`：持续运行并打印每次 diff
- `test-rule --watch`：实时观察 selector / scope 效果
- 不改变 core pipeline，纯前端体验

### 方向 D：Retention / Export 增强
- 支持按 target / channel / date 范围导出
- 支持选择性清理（保留最近 N 条而非仅时间窗口）

## 5. 明确不推荐的下一轮方向

- **JS 渲染 / Playwright 集成**：偏离"简单、可控、可解释"定位，引入 heavy runtime
- **AI 过滤扩展**：已有 noise reduction + similarity suppression，再增加会复杂化
- **规则 DSL 重构**：当前 YAML + 简单 condition 够用，重构风险高
- **分布式 / 多节点**：SQLite 单节点是当前设计约束

## 6. 结论

当前 Web-Watcher 已形成完整的监控 → 提取 → 检测 → 调查 → 通知 → 持久化闭环。

**最值得进入下一轮 Design 的方向是：规则与通知的可观测性（方向 A）。**

理由：
1. 当前最大摩擦点是"规则只能通过文件管理，通知发出后无法回溯"
2. 与现有 storage / CLI / rule_parser 架构完全契合
3. 不改变 core pipeline 语义，风险低
4. 用户价值明确，可独立验收

其余方向（B/C/D）可作为后续 Phase，但建议先完成 A 的验收再并行。
