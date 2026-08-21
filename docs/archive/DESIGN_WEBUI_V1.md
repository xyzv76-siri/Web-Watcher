# Web UI v1 Design Doc

> 日期：2026-08-21  
> 状态：DRAFT，等待用户确认后进入施工

---

## 一、目标

在现有 CLI 基础上，新增**轻量个人监控台**：
- 浏览器访问 `http://localhost:8080` 查看监控状态
- 无账号系统、无 SaaS、无外部依赖
- **v1 为严格只读**：所有 API 和页面仅查询数据，不修改 pipeline、不写入数据库、不触发调度或通知
- 基于 Python 标准库 `http.server`，零外部依赖

**非目标（v1 不实现）**
- 多用户/权限/登录
- 远程访问/HTTPS/反向代理配置
- 实时 WebSocket 推送（v1 仅轮询）
- 移动端适配
- 任何写入操作（POST/PUT/DELETE）—— v1 不提供任何修改接口

---

## 二、范围与边界

### 会改的

| 模块 | 改动 |
|------|------|
| `src/web_watcher/webui.py` | **新增** Flask/FastAPI 应用 |
| `src/web_watcher/cli.py` | 新增 `webui` 子命令 |
| `tests/test_webui.py` | **新增** 5-8 个测试 |
| `README.md` | 补充 Web UI 使用说明 |

### 不会改的

- `scheduled_runner.py` — core pipeline 不变
- `notify` 子命令 — dispatch 逻辑不变
- `repository.py` — 复用现有查询接口
- DB schema — 不新增表

---

## 三、技术选型

### 后端

- **Python 标准库 `http.server` + `json`**（零外部依赖）
- 路由：
  - `GET /` — 仪表盘首页
  - `GET /api/targets` — 目标列表
  - `GET /api/events` — 事件列表（支持分页/过滤）
  - `GET /api/events/<id>` — 事件详情（含 signals）
  - `GET /api/stats` — 统计信息
  - `GET /targets` — 目标列表页面
  - `GET /events/<id>` — 事件详情页面

### 前端

- **纯 HTML + CSS + JavaScript**（无 Node.js 依赖）
- 单页面应用风格，通过 fetch API 调用后端
- 静态资源：内联 CSS/JS，无需额外构建

---

## 四、页面设计

### 4.1 仪表盘首页 (`/`)

```
+------------------------------------------+
| Web-Watcher Dashboard                    |
+------------------------------------------+
| 统计卡片                                 |
| +------+ +------+ +------+ +------+      |
| | 目标数 | | 事件数 | | 通知数 | | 运行时间 |      |
| +------+ +------+ +------+ +------+      |
+------------------------------------------+
| 最近事件                                 |
| +--------------------------------------+ |
| | 时间 | 目标 | 类型 | 重要性 | 状态    | |
| +--------------------------------------+ |
| | ...  | ...  | ...  | ...    | ...    | |
| +--------------------------------------+ |
+------------------------------------------+
```

### 4.2 目标列表 (`/targets`)

```
+------------------------------------------+
| Targets                                  |
+------------------------------------------+
| + URL + 类型 + 状态 + 最后检查 + 操作    |
| | ... | ... | ... | ...    | ...    | |
| +--------------------------------------+ |
+------------------------------------------+
```

### 4.3 事件详情 (`/events/<id>`)

```
+------------------------------------------+
| Event #1234                              |
+------------------------------------------+
| 基本信息                                 |
| - 目标: target:alpha                     |
| - 类型: content_change                   |
| - 重要性: important                      |
| - 状态: open                             |
| - 创建时间: 2026-08-21 10:00             |
+------------------------------------------+
| Signals (2)                              |
| +--------------------------------------+ |
| | #1 content_change 2026-08-21 10:00  | |
| | {"url": "https://...", "diff": "..."}| |
| +--------------------------------------+ |
| | #2 content_change 2026-08-21 10:05  | |
| | {"url": "https://...", "diff": "..."}| |
| +--------------------------------------+ |
+------------------------------------------+
```

---

## 五、API 设计

### `GET /api/targets`

```json
{
  "targets": [
    {
      "id": "target:alpha",
      "url": "https://example.com",
      "type": "generic_web",
      "status": "normal",
      "last_checked_at": "2026-08-21T10:00:00Z"
    }
  ]
}
```

### `GET /api/events`

查询参数：
- `since` — ISO 时间戳
- `until` — ISO 时间戳
- `importance` — 逗号分隔的重要性级别
- `status` — 逗号分隔的状态
- `limit` — 每页数量（default: 50）
- `offset` — 偏移量

```json
{
  "events": [
    {
      "id": 1234,
      "entity_id": 1,
      "entity_key": "target:alpha",
      "event_type": "content_change",
      "importance": "important",
      "status": "open",
      "created_at": "2026-08-21T10:00:00Z",
      "signal_count": 2
    }
  ],
  "total": 100,
  "limit": 50,
  "offset": 0
}
```

### `GET /api/events/<id>`

```json
{
  "event": {
    "id": 1234,
    "entity_id": 1,
    "event_type": "content_change",
    "importance": "important",
    "status": "open",
    "created_at": "2026-08-21T10:00:00Z"
  },
  "signals": [
    {
      "id": "sig_1234",
      "signal_type": "content_change",
      "observed_at": "2026-08-21T10:00:00Z",
      "value": "{\"url\": \"https://...\", \"diff\": \"...\"}",
      "fingerprint": "abc123"
    }
  ]
}
```

### `GET /api/stats`

```json
{
  "targets_count": 10,
  "events_24h": 25,
  "events_7d": 120,
  "notifications_pending": 3,
  "notifications_failed": 1,
  "by_importance": {
    "critical": 2,
    "important": 10,
    "interesting": 13
  }
}
```

---

## 六、CLI 设计

```bash
# 启动 Web UI（默认 localhost:8080）
python -m web_watcher.cli webui

# 指定端口和主机
python -m web_watcher.cli webui --host 0.0.0.0 --port 8080

# 指定数据库
python -m web_watcher.cli webui --db /path/to/web_watcher.db
```

参数：
- `--host` — 绑定地址（default: 127.0.0.1）
- `--port` — 端口号（default: 8080）
- `--db` / `--db-path` — 数据库路径（default: web_watcher.db）

---

## 七、验收标准

1. `python -m web_watcher.cli webui` 成功启动
2. 浏览器访问 `http://localhost:8080` 显示仪表盘
3. `/api/targets` 返回目标列表
4. `/api/events` 支持分页/过滤
5. `/api/events/<id>` 返回事件详情 + signals
6. `/api/stats` 返回统计信息
7. 静态资源内联，无需额外构建步骤
8. 不修改 core pipeline / DB schema
9. 全量测试回归 **1525+ passed**

---

## 八、测试计划

| 测试 | 场景 |
|------|------|
| `test_webui_starts` | Flask 应用启动 |
| `test_api_targets` | `/api/targets` 返回 JSON |
| `test_api_events` | `/api/events` 分页/过滤 |
| `test_api_event_detail` | `/api/events/<id>` 返回 signals |
| `test_api_stats` | `/api/stats` 返回统计 |
| `test_dashboard_page` | `GET /` 返回 HTML |
| `test_targets_page` | `GET /targets` 返回 HTML |
| `test_event_detail_page` | `GET /events/<id>` 返回 HTML |

---

## 九、后续扩展（不在 v1）

- WebSocket 实时推送
- 多用户/登录
- 远程访问/HTTPS
- 移动端适配
- 事件操作（关闭/重新打开/添加备注）

---

## 十、确认项

请确认以下设计约束：
1. v1 仅本地访问，默认 `127.0.0.1:8080`
2. 无账号系统，无 HTTPS
3. 前端纯 HTML + Jinja2，无 Node.js 依赖
4. 不修改 DB schema
5. 不修改 core pipeline
