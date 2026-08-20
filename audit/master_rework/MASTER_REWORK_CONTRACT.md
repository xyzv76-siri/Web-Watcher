# WEB-WATCHER MASTER REWORK CONTRACT v1

## 0. 角色

你是本项目的 AUTONOMOUS CONSTRUCTION WORKER。

你的职责：
- 按本合同施工
- 自主读取代码
- 自主修改允许范围内的代码
- 自主新增和修改测试
- 自主运行测试、静态检查、只读审计
- 在允许范围内自动修复失败
- 完成后生成施工报告并停止

你不是架构决策者。

严禁：
- 自行扩大需求
- 自行改变架构目标
- 为了测试通过而降低测试标准
- 修改测试来掩盖代码问题
- 进行本合同之外的架构重构

---

# 1. 总目标

将当前 Web-Watcher 从：

“功能已经存在，但 Fetch / Extraction / Signal / Scheduling / Rate Limit / Persistence 之间状态语义不完整”

返工为：

“状态明确、失败可区分、信号不会因失败误报、主机级访问受控、lease/fencing 真正进入执行路径、崩溃可恢复、数据库具有正式 schema/migration/integrity 机制”的生产级系统。

---

# 2. 当前已确认问题

H1:
- lease / claim / fencing 已实现
- claim_targets / commit_target_execution / release_target_lease 存在
- commit 有 claim_token fencing
- 但生产执行路径没有完整接入

H2:
- ExtractionStatus 已存在
- SELECTOR_NOT_FOUND / MULTIPLE_MATCH 等状态已定义
- 多处存在 matches[0] / select_one / find 首匹配行为
- MULTIPLE_MATCH 没有形成完整业务语义
- extraction failure 必须阻止误报 signal

H3:
- 缺少 host/domain 级 rate limit
- 缺少 host 共享 cooldown
- 缺少 429 / Retry-After / 5xx backoff 的统一策略
- target-level scheduling 不能替代 host-level control

H4:
- Fetch failure 语义不完整
- 不允许把失败简单折叠成 None
- Fetch / Extraction / Evaluation / Signal 必须形成明确状态链
- 失败不能被解释成业务值变化

H5:
- foreign_keys 当前未稳定启用
- targets 表属于动态创建
- 无正式 migration/version 管理
- 无完整 stale lease recovery
- 数据库 schema、target schema、lease schema 应形成统一持久化契约

---

# 3. 总体状态链

最终必须形成：

FETCH
  ↓
FetchResult
  ↓
EXTRACTION
  ↓
ExtractionResult
  ↓
EVALUATION
  ↓
SignalDecision
  ↓
Signal/Event

禁止：

FETCH FAILURE
  ↓
None
  ↓
extract
  ↓
None != previous_value
  ↓
SIGNAL

---

# 4. Fetch Contract

必须能够明确区分至少：

- SUCCESS
- NOT_MODIFIED
- HTTP_ERROR
- RATE_LIMITED
- TIMEOUT
- NETWORK_ERROR
- INVALID_RESPONSE

每种状态必须明确：
- 是否允许 extraction
- 是否允许 signal
- 是否更新 previous value
- 是否 retry
- retry/backoff 行为
- 是否触发 host cooldown

原则：

任何 Fetch failure 都不能被当作正常业务值。

304 必须作为正常的 NOT_MODIFIED 语义处理。

429 必须读取 Retry-After（如果存在）。

5xx / network / timeout 必须进入明确 retry/backoff 语义。

---

# 5. Extraction Contract

必须明确区分：

- FOUND
- SELECTOR_NOT_FOUND
- EMPTY_AFTER_TRANSFORM
- MULTIPLE_MATCH
- TRANSFORM_ERROR

禁止通过 matches[0] 静默吞掉 MULTIPLE_MATCH。

MULTIPLE_MATCH 必须进入明确业务决策。

SELECTOR_NOT_FOUND 不得生成 Signal。

EMPTY_AFTER_TRANSFORM 不得生成 Signal。

TRANSFORM_ERROR 不得生成 Signal。

只有：

FOUND + 有效值

才允许进入 Evaluation。

---

# 6. Signal Contract

Signal 只能由明确有效的 observation 产生。

规则：

FETCH_FAILURE -> NO SIGNAL

NOT_MODIFIED -> NO SIGNAL

SELECTOR_NOT_FOUND -> NO SIGNAL

EMPTY_AFTER_TRANSFORM -> NO SIGNAL

MULTIPLE_MATCH -> 按明确 policy 处理，默认 NO SIGNAL

TRANSFORM_ERROR -> NO SIGNAL

FOUND + unchanged -> NO SIGNAL

FOUND + changed -> SIGNAL

不得使用 None 作为多个不同 failure state 的替代品。

---

# 7. Host Rate Limit Contract

必须建立 host/domain 级控制。

同一 host 的多个 target 必须共享访问限制。

至少支持：

- minimum interval
- concurrency limit
- cooldown
- 429 handling
- Retry-After
- 5xx backoff

不得只依赖 target.next_allowed_at。

不要通过简单 sleep() 假装实现 host rate limiting。

实现应保持未来可以扩展到多 worker 的结构。

---

# 8. Lease / Fencing Contract

生产执行路径必须真正使用：

claim_targets
    ↓
execute
    ↓
commit_target_execution(claim_token)
    ↓
release

要求：

- claim 必须原子
- lease 必须有 owner
- lease 必须有 expiration
- claim_token 必须参与 fenced commit
- stale worker 不得提交结果
- lease 失败不得执行 target
- commit 失败必须可观察

不能保留“Repository 有 lease API，但 Runner 不使用”的状态。

---

# 9. Recovery Contract

worker 启动或调度循环必须能够处理：

- expired lease
- stale lease
- worker crash
- process restart
- interrupted execution

目标：

worker 崩溃后 target 最终能够重新进入可执行状态。

不得出现永久卡死。

Recovery 必须是显式逻辑，不允许依赖偶然行为。

---

# 10. Database Contract

targets 必须成为正式 schema 的一部分。

不得继续依赖：

“第一次运行 save_target 时才偷偷创建表”

schema 必须具有明确版本。

必须能够识别：

current schema version

并支持：

migration

数据库连接必须明确启用：

PRAGMA foreign_keys = ON

并通过测试证明 FK 实际生效。

必须增加至少一个 orphan-FK negative test。

不要删除现有生产数据。

不要执行 destructive migration。

如果 migration 需要数据转换，先 STOP 并报告。

---

# 11. Transaction Contract

涉及状态转换的操作必须具有明确 atomicity。

重点检查：

- claim
- commit
- release
- recovery
- signal creation
- event correlation

不得出现部分更新成功、部分失败但状态看起来成功的情况。

必要时使用显式 transaction / savepoint，但不要无意义重构所有 repository。

---

# 12. 测试要求

当前 baseline：

984 passed

施工过程中不得降低这个 baseline。

必须新增针对新契约的测试。

至少覆盖：

1. fetch success
2. HTTP failure
3. 429
4. Retry-After
5. timeout
6. network failure
7. 304
8. selector not found
9. empty transform
10. multiple match
11. transform error
12. changed value
13. unchanged value
14. failure does not create signal
15. claim success
16. claim conflict
17. stale claim token rejected
18. lease expiration
19. worker recovery
20. FK violation rejected
21. migration/version behavior
22. host rate limit
23. host cooldown
24. concurrent targets sharing host limit

最终必须运行完整 pytest。

---

# 13. 测试规则

禁止：

- 删除测试
- skip 测试
- xfail 原本应该通过的测试
- 降低 assertion
- 修改 expected value 以掩盖 bug
- 删除失败测试
- catch Exception 然后 pass
- 用 None 隐藏错误

如果旧测试与新架构契约冲突：

先分析。

只有当测试明确代表旧错误行为时才修改，并在报告中说明原因。

---

# 14. 修改边界

允许修改：

- src/web_watcher/
- tests/
- 必要的 schema/migration 文件
- audit/master_rework/
- 项目文档中与本次架构直接相关的部分

默认禁止修改：

- AI Radar
- qwenpaw
- browser/
- history.db
- ai_radar.db
- supervisord
- Cron
- Telegram
- VPS 系统配置
- Kubernetes/ECI 配置
- secrets
- credentials
- 外部生产服务

禁止修改生产数据库中的真实业务数据。

---

# 15. Git 边界

允许：

git status
git diff
git diff --check
git log

禁止：

git reset
git clean
git restore
git push
git force operations

默认不要 commit。

---

# 16. 依赖边界

默认禁止：

- 安装新依赖
- 升级依赖
- 修改 Python 版本
- 修改 Node 版本

如果发现必须增加依赖：

STOP。

报告：
- 为什么需要
- 哪个功能需要
- 是否存在现有依赖替代
- 风险是什么

---

# 17. 施工顺序

严格按照：

STEP 1
建立/修正 Fetch Contract

STEP 2
建立 Extraction Contract

STEP 3
建立 Evaluation / Signal Contract

STEP 4
补齐 failure propagation

STEP 5
建立 host-level rate control

STEP 6
把 lease/fencing 接入真实 execution path

STEP 7
建立 stale lease recovery

STEP 8
正规化 targets schema

STEP 9
加入 migration/version mechanism

STEP 10
启用并验证 foreign keys

STEP 11
补齐 integration / failure / recovery tests

STEP 12
运行完整测试

STEP 13
执行 architecture static audit

STEP 14
执行 final diff audit

STEP 15
生成报告

STEP 16
STOP

不要自行改变施工顺序，除非当前步骤明确阻塞下一步骤。

---

# 18. STOP CONDITIONS

遇到任何以下情况立即 STOP：

- 需要修改禁止目录
- 需要修改 AI Radar
- 需要修改生产配置
- 需要修改 VPS 系统
- 需要安装依赖
- 需要删除生产数据
- 需要 destructive migration
- 需要改变本合同架构
- 需要新增本合同未定义的核心组件
- 测试无法在允许范围内修复
- 发现现有架构与本合同发生根本冲突
- 需要 git push
- 需要外部生产服务
- 不确定某项操作是否安全

STOP 时不要继续猜。

---

# 19. 自主修复规则

允许你在当前施工域内：

读取
→ 修改
→ 测试
→ 分析失败
→ 修复
→ 再测试

可以循环执行。

但是：

连续 3 次无法解决同一个根因：

STOP。

不要通过绕过问题来取得绿色测试。

---

# 20. 每一步完成后自检

确认：

- 修改文件全部在允许范围
- 没有修改禁止文件
- 没有生产副作用
- 没有数据库 destructive operation
- 测试增加或保持
- 没有降低已有测试质量
- 新状态契约有测试
- failure path 有测试

然后继续下一步。

---

# 21. 最终验收门槛

必须同时满足：

A. 全量测试通过

B. 新增状态契约测试通过

C. failure path 测试通过

D. lease/fencing execution path 已真实接入

E. host-level rate limit 已真实接入

F. stale lease recovery 有测试

G. FK 实际 enforcement 测试通过

H. migration/version 有测试

I. 没有 forbidden file 修改

J. 没有 dependency change

K. 没有 production side effect

L. git diff 可解释

任何一项不满足：

STOP + FAIL REPORT。

---

# 22. 最终报告

生成：

audit/master_rework/REWORK_REPORT.md

必须包含：

1. 原问题
2. 修改方案
3. 修改文件
4. 每个问题的解决方式
5. 新增接口
6. 新增测试
7. 测试结果
8. architecture audit
9. database audit
10. rate-limit audit
11. lease/recovery audit
12. 未解决问题
13. 风险
14. Git diff summary

最后输出：

SUCCESS

或者：

STOPPED

并写明原因。

---

# 23. 最重要的规则

你可以自主施工。

你可以连续执行很多命令。

你可以自主测试和修复。

但是：

你不能自主扩大任务。

你不能自主改变架构。

你不能碰生产。

你不能隐藏失败。

你不能为了测试通过而降低标准。

你不能 push。

当合同要求 STOP 时必须 STOP。

完成后必须等待外部架构师验收。

END OF CONTRACT
