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

## Boundaries

### Open Source Scope

Web-Watcher is an open source software project.

This repository publicly releases Web-Watcher's reusable software implementation, test code, public documentation, configuration templates, and related engineering components for code review, development, testing, deployment, and secondary development.

The open source repository maintains a clear separation from the actual production environment.

#### Within Open Source Scope

The following content may be included in the public repository:

* Application source code
* Unit tests and integration tests
* Public technical documentation
* Configuration file templates
* Configuration schemas
* Database schemas and migration code
* Development and testing tools
* CI/CD configurations without sensitive information
* Deployment templates without production credentials
* Public architecture and engineering documentation
* Sample data specifically for development or testing
* Project dependency declarations and necessary lock files

#### Outside Open Source Scope

The following content must not be committed to the public repository:

* API Keys
* Access Tokens
* GitHub Personal Access Tokens
* SSH private keys
* TLS private keys
* Passwords
* Session cookies
* Authentication credentials
* Production environment secrets
* Private webhook URLs containing authentication information
* Database usernames and passwords
* Cloud platform and infrastructure credentials
* Personal access credentials
* Private or user data
* Production database content
* Production logs containing sensitive information
* Internal monitoring and operations credentials
* Machine-specific sensitive configurations
* Any information that may lead to unauthorized access

Environment-related configurations should be provided through environment variables, secret management systems, or other secure runtime injection mechanisms, rather than committing real credentials directly.

### Security Boundaries

The GitHub repository is the source code release boundary, not a storage location for production secrets, production data, or private runtime status.

Web-Watcher should maintain the following boundaries:

```
Public GitHub Repository
        │
        ├── Source Code
        ├── Tests
        ├── Documentation
        ├── Public Configuration Templates
        └── Deployment / CI Templates
                │
                ▼
            Runtime Environment
                │
                ├── Environment Variables
                ├── Secrets
                ├── Credentials
                ├── Production Database
                ├── Production Logs
                └── Runtime State
```

Code in the public repository should be reviewable and developable without submitting any real production credentials to Git.

### Secret Management

Sensitive information must be provided by the runtime environment and should not be hardcoded in source code.

Recommended approaches:

* Environment variables
* Secret Manager
* CI/CD Secrets
* VPS / container runtime secret injection
* Cloud platform-provided secret management mechanisms

Example configurations should use placeholders only:

```
API_KEY=<provided at runtime>
DATABASE_URL=<provided at runtime>
WEBHOOK_SECRET=<provided at runtime>
```

It is prohibited to commit after replacing real credentials into example configurations.

### Credential Leak Response

If tokens, API keys, passwords, or other secrets are accidentally committed to Git, simply deleting the file is not enough.

Immediately take the following steps:

1. Revoke or disable the compromised credentials.
2. Create new credentials.
3. Remove sensitive information from the current working tree.
4. Clean sensitive information from Git history as appropriate.
5. Check CI, logs, caches, build artifacts, and other possible leak locations.

Credentials that have entered public Git history should be considered compromised. Even if the corresponding file is later deleted, the credentials can no longer be trusted.

### Production Environment Boundaries

The public repository does not represent any specific production environment.

The following content belongs to specific deployment environments and is not part of the open source code release:

* Private VPS / cloud infrastructure
* Production environment credentials
* Production databases
* Private logs
* Monitoring runtime status
* Deployment environment-specific configurations
* Private network configurations
* Internal service addresses
* User data
* Other runtime private states

The same Web-Watcher source code can be deployed to different environments, with each environment providing its own runtime configuration.

### Data and Privacy

Web-Watcher may process external information and data generated during operation under different deployment configurations.

The public repository will not provide or include any specific deployment environment's:

* Private production data
* User data
* Private data source credentials
* Production historical data
* Internal runtime logs
* Other unauthorized data

Users deploying and using Web-Watcher should independently ensure their deployment complies with applicable laws, data protection requirements, third-party service terms, and their own data processing policies.

### Third-Party Components

Web-Watcher may depend on third-party software, libraries, APIs, models, services, or other external components.

These third-party components are still subject to their respective licenses, terms of service, and usage restrictions.

Web-Watcher's license does not automatically grant any additional rights to any third-party software, services, trademarks, APIs, datasets, models, or other external resources.

Users should independently verify and comply with the licenses and terms of service of relevant third-party components.

### License

Unless otherwise explicitly stated, Web-Watcher is released under the MIT License.

The full license text is located in the LICENSE file in the repository root.

The MIT License permits use, copying, modification, merging, publication, distribution, sublicensing, and sale of copies of the software under the conditions specified in the license.

The software is provided "AS IS" under the license, without any warranty beyond what is explicitly stated in the license.

For specific legal rights and obligations, please refer to the LICENSE file in the repository.

### Security Vulnerability Disclosure

If you discover a security vulnerability that may affect Web-Watcher users or deployment environments, please avoid publishing directly exploitable details before the vulnerability is fixed.

Security issue reports should also not contain:

* Real tokens
* API keys
* Passwords
* Private keys
* Production databases
* Private data
* Other sensitive information

### Pre-Commit Checklist

Before committing code to the public repository, confirm that the change does not contain:

* Tokens
* API keys
* Passwords
* Private keys
* Production environment configurations
* URLs containing secrets
* Personal information
* Production databases
* Sensitive logs
* Temporary files
* Local environment files
* Machine-specific configurations
* Other runtime information that should not be made public

For example, local environment files should typically remain untracked:

```
.env
.env.*
```

The public repository can provide templates without real secrets:

```
.env.example
```

Specific .gitignore rules should be adjusted according to the actual project structure.

### Final Definition of Open Source Scope

Web-Watcher's open source release scope includes only software code, tests, documentation, and other public resources intentionally committed to this public repository.

Publicly releasing Web-Watcher source code does not imply releasing its production environment, production data, secrets, credentials, infrastructure, or private runtime status.

Any private information not explicitly included in the public repository does not belong to the open source release scope of this project.

> Public software, not the secrets required to run it.
