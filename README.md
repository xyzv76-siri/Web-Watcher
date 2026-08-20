# Web-Watcher

生产级、低频礼貌、可恢复、可追溯的 Web / GitHub 资源变化观测与自动化取证系统。

## What It Does

Web-Watcher 持续观测目标资源，按策略评估变化，并在必要时触发调查与通知。

核心处理链：

```
Target
→ Fetch
→ Signal
→ Event
→ Investigation
→ Evidence
→ Policy
→ Notification
```

它不是 crawler platform，不是 WAF bypass tool，不是 CAPTCHA bypass tool，不是 high-concurrency scraper。

## Architecture

### Target State

每个 Target 保存自己的调度状态：

- `status`：normal / backoff / cooldown / recovering
- `next_allowed_at`：下一次允许请求的时间
- `consecutive_failures`：连续失败计数
- `etag` / `last_modified` / `content_hash`：HTTP 缓存与指纹

### Host Authority

同一 host 的并发请求由 `HostRateLimiter` 统一管理：

- 原子 claim / renew / release
- 基于 `host_rate_limits` 表的 lease 语义
- 防止多 worker 同时冲击同一 host

### Fetch Policy

`FetchPolicy` 决定单次请求的放行与退避：

- 检查 Target 的 `next_allowed_at`
- 检查 Host authority
- 解析 HTTP 状态与 `Retry-After`
- 计算 bounded deterministic jitter
- 输出 `FetchResult` 与 `TargetExecutionResult`

### Signal

Fetch 产生 `Observation`，经 `EventCorrelator` 映射为 `Signal`：

- `content_change`
- `stars_changed`
- `release_published`

304 不产生 Signal。

### Event

Signal 被提升为 Event：

- 同一实体的 open Event 会在 24h 窗口内合并
- Importance 取最大值
- Event 状态由 `EventStatus` 管理：open / closed

### Investigation & Evidence

Eligible Event 触发 Investigation：

- `InvestigationWorker` 处理持久化 Event
- 结果写入 `investigation_results` 表
- `evidence_items` 以 JSON 形式持久化
- 支持 retry / backoff / state recovery

### Notification

最后 mile 投递：

- `NotificationDispatcher` 消费 pending Notification
- `AlertSilencer` 在发送前抑制重复/静音
- `dispatch_token` + `dispatch_owner` + `dispatch_until` 实现 claim fencing
- 外部发送与数据库 finalization 之间是 **at-least-once** 语义

### Target / Host 两层状态职责

- Target 层：单个 URL / API endpoint 的 resilience 与 backoff
- Host 层：同一域名/IP 的并发与 rate limit
- 两层独立持久化，无隐藏共享状态

## Reliability

### HTTP Cache & Conditional Request

- 保存 `ETag` 与 `Last-Modified`
- 后续请求携带条件头
- `304 Not Modified` 直接短路，不提取、不比对、不产生 Signal/Event/Investigation/Notification

### Retry-After

- 支持秒数与 HTTP-Date
- 作为 server instruction 参与 `next_allowed_at` 计算
- 有上限保护，防止单次 429 把系统锁死一整天

### Bounded Deterministic Jitter

- 退避延迟使用 SHA-256 派生，而非随机数
- 同一 Target + 同一时刻的 jitter 可重现
- 避免随机 burst 造成 thundering herd

### Per-Target Backoff

- 阶梯：normal → backoff → cooldown → recovering
- 每次成功/failure 更新 `consecutive_failures` 与 `next_allowed_at`
- 持久化在 `targets` 表，重启不丢

### Host Claim Lease

- `host_rate_limits` 表记录 per-host claim
- 原子 acquire，带 `claim_until` 过期时间
- worker crash 后 lease 自动过期，新 worker 可重新 claim

### Claim Fencing

- 所有 claim 携带唯一 `claim_token`
- 更新/释放时校验 token
- 旧 worker 无法篡改新 worker 持有的 lease

### Stale Lease Recovery

- `ScheduledRunner.run_once()` 每次启动时 reap 过期 lease
- 不依赖外部 cron 或手动干预

### Atomic Finalization

- `finalize_execution()` 在单事务内完成：
  - fencing 检查
  - Target update
  - Signal insert
  - Event create/update
  - Link create
- 任意步骤失败触发 SQLite rollback，无 partial state

### Crash Recovery

- 所有关键状态持久化在 SQLite
- 重启后 `_validate_database()` 自动初始化 schema
- `reap_stale_claims()` 清除过期 lease
- `claim_targets()` 根据 `targets` 表状态恢复调度

### Multi-Worker Safety

- SQLite 保留锁保证 `claim_targets()` 串行
- 两个 worker 同时 claim 同一 Target，仅一个成功
- 失败方返回 `rowcount == 0`，不降级执行

## HTTP Semantics

| Status | Handling |
|--------|----------|
| 200 | 提取、normalize、fingerprint、diff、可能产生 Signal |
| 304 | 短路，保留 ETag/Last-Modified，不产生 Signal |
| 301/302/307/308 | 记录 redirect 元数据，不自动跟随，由 `updated_url` 承载 |
| 403 | 视为 forbidden，不重试，不产生 Signal |
| 404 | 视为 not found，不重试，不产生 Signal |
| 429 | 解析 Retry-After，更新 `next_allowed_at`，阶梯 cooldown |
| 5xx | 计入 `consecutive_failures`，进入 backoff/cooldown |
| timeout | 同 5xx 处理 |
| DNS 失败 | 同 5xx 处理 |

### 304 Semantics

304 表示资源未改变。Web-Watcher 在此情况下：

- 不调用 extractor
- 不计算 diff
- 不创建 Signal
- 不提升 Event
- 不触发 Investigation
- 不发送 Notification

仅更新 `last_fetched_at` 与缓存头。

## Supported Sources

### Generic Web

- CSS / XPath selector 提取
- 内容 normalization
- canonical fingerprint
- dynamic noise suppression（timestamp、random token、tracking param）
- partial selector failure safety：任一 selector 失败不触发 false positive

### GitHub API

- 官方 API，非 HTML scraping
- `releases/latest`
- repository metadata / stars
- ETag + 304
- 共享 `HostRateLimiter`

## Investigation & Evidence

Signal → Event → Investigation → persisted Evidence

- Investigation 只处理已持久化 Event
- 结果与证据写入 `investigation_results` 表
- 支持 retry / backoff / crash recovery
- 不会对未持久化 Event 产生 uncontrolled side effects

## Notification Delivery Semantics

**AT-LEAST-ONCE**

**NOT EXACTLY-ONCE**

外部投递流程：

1. worker claim pending Notification
2. 外部渠道发送成功
3. 进程在 DB finalize 前 crash
4. lease 过期
5. 另一个 worker 可能再次发送相同通知

DB fencing 防止 stale worker finalize 不属于自己的 lease，但无法让外部系统与 SQLite transaction 形成 exactly-once distributed transaction。

## Configuration

配置文件：`config/watcher.json`

- Target 声明式配置
- URL / selector / interval 校验
- 环境变量覆盖

## Docker

基于 `python:3.11-slim` 的生产镜像：

- 非 root 用户运行
- `/data` 持久化 SQLite
- `/logs` 持久化日志
- graceful shutdown（SIGTERM/SIGINT）
- `entrypoint.sh` 轻量 guard
- `docker-compose.yml` 本地编排

### Health

```bash
docker compose exec web-watcher python -m web_watcher.cli doctor
```

## Development

```bash
python -m pip install --no-deps -e .
pytest -q
```

## Testing

```bash
pytest -q
```

## Security Boundaries

- 无 WAF bypass
- 无 CAPTCHA bypass
- 无 TLS fingerprint spoofing
- 无 proxy rotation for evasion
- 仅通过官方 GitHub API 访问 GitHub

## Limitations

- SQLite 单机持久化，非分布式
- 外部通知为 at-least-once，非 exactly-once
- GitHub API rate limits 受 upstream 约束
- extraction failure 不表示 deletion
- 无跨进程 exactly-once distributed transaction
