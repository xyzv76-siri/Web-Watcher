# PART 19-02 完成报告
## Extraction / Normalize / Fingerprint / Diff

---

## 一、修改文件

### 新增文件
| 文件 | 说明 |
|------|------|
| `src/web_watcher/diff.py` | Diff 计算模块，提供 `DiffResult` 与 `compute_diff` |
| `src/web_watcher/normalizer.py` | 内容标准化模块，提供 `normalize_extracted_text` 与 `normalize_html_text` |
| `src/web_watcher/observation.py` | Observation 结果模型，定义 `ObservationResult` 与 `ObservationStatus` |
| `src/web_watcher/web_fingerprint.py` | Web observation 指纹模块，提供 `observation_fingerprint` 与 `selector_config_fingerprint` |
| `tests/test_diff.py` | Diff 模块测试 |
| `tests/test_normalizer.py` | Normalizer 模块测试 |
| `tests/test_observation.py` | Observation 模型测试 |
| `tests/test_web_fingerprint.py` | Web fingerprint 模块测试 |
| `tests/test_generic_web_target_extraction.py` | GenericWebTarget 端到端 extraction/diff/fingerprint 测试 |

### 修改文件
| 文件 | 说明 |
|------|------|
| `src/web_watcher/generic_web_target.py` | 重构 `execute` 方法：selector → extract → normalize → fingerprint → diff → observation |
| `tests/test_generic_web_target.py` | 更新 4 个测试以匹配 first observation / normalized diff 新语义 |
| `tests/test_execution_semantics.py` | 更新 `test_success_unchanged_reason` 以匹配 normalized diff |
| `tests/test_pipeline_rules_integration.py` | 更新 `test_pipeline_run_generic_web_flow` 以匹配 first observation baseline 语义 |

---

## 二、Extraction Architecture

```
raw HTML response
       ↓
selector extraction (DOMExtractor)
       ↓
raw extracted value (text / float / str)
       ↓
normalize_extracted_text()
       ↓
normalized content (stable string)
       ↓
observation_fingerprint(target_id, normalized, selector_fp)
       ↓
fingerprint (SHA-256 hex)
       ↓
compute_diff(previous_normalized, current_normalized)
       ↓
DiffResult(before, after, changed, summary, regions)
       ↓
ObservationResult
```

**关键原则**：
- Raw HTML 与 extracted observation 概念分离。
- Raw HTML 仅用于 debugging / evidence / fallback，不参与 fingerprint。
- 只有 selector 对应内容被 normalize 和 fingerprint。
- 禁止对完整 raw HTML 做默认 hash。

---

## 三、Normalization Rules

`normalize_extracted_text(raw)` 执行以下步骤：

1. **类型转换**：非字符串输入先转换为字符串表示（如 float → "99.0"）。
2. **空值处理**：空字符串或 falsy 值返回 `""`。
3. **首尾空白去除**：`strip()`。
4. **内部空白压缩**：将连续的空格、制表符、换行符压缩为单个空格。

**边界**：
- 仅消除明显动态噪声（whitespace、formatting）。
- 保留文本顺序与语义内容。
- 不删除任意内容，不过度 normalize。

`normalize_html_text(html_fragment)` 是 lightweight fallback：
- 移除 HTML 标签（正则 `<[^>]+>`）。
- 调用 `normalize_extracted_text`。

---

## 四、Fingerprint Design

`observation_fingerprint(target_id, normalized_content, selector_fingerprint=None)`

**设计**：
- 输入：target_id + normalized_content + selector_fingerprint（可选）。
- 算法：SHA-256 于 `"\x1f".join(parts)`。
- **Deterministic**：相同输入始终产生相同输出。
- **Stable**：跨进程重启稳定。
- **No runtime state**：不包含 timestamp、random、process memory。

`selector_config_fingerprint(selector_type, selector)`：
- 对 selector 配置本身做 fingerprint，使不同 selector 配置产生不同指纹。

**目的**：
- 相同 observation → 相同 fingerprint。
- 不同有效 observation → 不同 fingerprint。

---

## 五、Diff Design

`compute_diff(before: str, after: str) -> DiffResult`

**设计**：
- 输入：两个 normalized text observation。
- 输出：`DiffResult` 包含：
  - `changed: bool`
  - `before: str`
  - `after: str`
  - `summary: str`（人类可读摘要）
  - `regions: List[str]`（区域级别差异，如 `before_len=...`, `after_len=...`）
  - `metadata: dict`（结构化元数据）

**原则**：
- 服务于 "哪里发生了变化？" 而非仅返回 `changed = true`。
- 保留 before/after 以便 downstream investigation。
- 轻量实现，不引入 heavy diff 库。

---

## 六、First Observation Semantics

**规则**：
- 第一次成功抓取：`status = FIRST_OBSERVATION`。
- 建立 baseline：将 `normalized_values` 与 `initialized=True` 写入 `updated_metadata`。
- **不** emit signal。
- **不** 当作 "fake change event"。

**为什么**：
- Baseline 需要先建立，后续 observation 才能 diff。
- 避免第一次抓取就产生噪声事件。

---

## 七、304 Behavior

HTTP 304 Not Modified：

```
304
 ↓
no extraction
 ↓
no fingerprint
 ↓
no Signal
 ↓
no Event
 ↓
no Investigation
 ↓
no Notification
```

**实现**：
- `GenericWebTarget.execute` 在检测到 304 或 `FetchStatus.NOT_MODIFIED` 时立即 short-circuit。
- 返回 `ObservationResult(status=UNCHANGED, status_code=304)`。
- 不执行 selector extraction、normalize、fingerprint、diff。
- 继续保留 PART 18 已建立的 etag/last_modified 语义。

---

## 八、Observation Result 结构

`ObservationResult` 包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `target_id` | str | 目标 ID |
| `status` | str | `unchanged` / `changed` / `extraction_failure` / `http_failure` / `first_observation` |
| `status_code` | Optional[int] | HTTP 状态码 |
| `extracted_results` | Dict[str, ExtractionResult] | 每个 extractor 的原始结果 |
| `normalized_values` | Dict[str, str] | 标准化后的值 |
| `fingerprints` | Dict[str, str] | 每个 extractor 的 fingerprint |
| `diffs` | Dict[str, DiffResult] | 每个 extractor 的 diff |
| `previous_values` | Dict[str, str] | 上一次的 normalized 值 |
| `evidence` | Dict[str, Any] | 证据链（target, url, status_code, extractor_results, before/after） |
| `observed_at` | datetime | 观察时间 |
| `reason` | str | 状态原因 |

**关键约束**：
- Adapter 只返回 `ObservationResult`，不直接创建 Event。
- `evidence` 支持后续 investigation：target / event / before / after / diff / timestamp / source。

---

## 九、Tests

### 新增测试（72 个）
覆盖 Part 19-02 要求的 12 个场景 + 反例：

1. **same content → unchanged**（test_second_call_with_same_content_is_unchanged）
2. **real content change → changed**（test_price_change_detected）
3. **formatting-only change → unchanged**（test_whitespace_only_difference_is_unchanged）
4. **whitespace variation → unchanged**（test_extra_spaces_normalized_away, test_newlines_normalized_away）
5. **selector extraction**（test_css_selector_extracts_text, test_multiple_extractors, test_missing_selector_yields_not_found）
6. **first observation**（test_first_observation_establishes_baseline, test_first_observation_sets_initialized_flag, test_first_observation_stores_normalized_values）
7. **304 short-circuit**（test_304_returns_unchanged_without_extraction）
8. **empty extracted content**（test_empty_selector_content）
9. **malformed HTML**（test_malformed_html_does_not_crash）
10. **deterministic fingerprint**（test_fingerprint_stable_across_calls, test_fingerprint_changes_when_content_changes）
11. **process restart fingerprint stability**（test_same_observation_same_fingerprint_after_restart, test_fingerprint_independent_of_timestamp）
12. **before/after diff**（test_diff_preserves_before_and_after, test_diff_summary_present, test_diff_regions_present）

**反例测试**：
- `test_unknown_target_type_rejected`
- `test_invalid_url_rejected`
- `test_generic_web_target_construction_validates_url`
- `test_generic_web_target_construction_validates_selector`
- `test_all_extractors_failed_yields_extraction_failure`
- `test_first_observation_never_emits_signal`

### 更新现有测试
- `tests/test_generic_web_target.py`：4 个测试更新以匹配 first observation / normalized diff 语义。
- `tests/test_execution_semantics.py`：1 个测试更新。
- `tests/test_pipeline_rules_integration.py`：1 个测试更新。

---

## 十、Full Pytest

```
1203 passed in 13.24s
```

全量测试通过，未破坏现有 GitHub target、scheduler、worker、repository、existing database 与 existing tests。

---

## 十一、Git Diff --stat

```
 .gitignore                                      |   1 +
 src/web_watcher/generic_web_target.py           | 242 +++++++++++++---
 tests/test_generic_web_target.py                |  35 ++-
 tests/test_execution_semantics.py                |  6 +-
 tests/test_pipeline_rules_integration.py         |   2 +-
 src/web_watcher/diff.py                         | 77 +++++++++
 src/web_watcher/normalizer.py                   | 43 ++++++
 src/web_watcher/observation.py                  | 72 ++++++++
 src/web_watcher/web_fingerprint.py              | 46 +++++++
 tests/test_diff.py                              | 74 +++++++++
 tests/test_normalizer.py                        | 52 +++++++++
 tests/test_observation.py                       | 53 +++++++++
 tests/test_web_fingerprint.py                   | 66 +++++++++
 tests/test_generic_web_target_extraction.py     | 400 +++++++++++++++++++++++++
```

---

## 十二、Known Limitations

1. **Normalization 边界**：当前仅做 whitespace 压缩。HTML 标签剥离（`normalize_html_text`）是 lightweight fallback，不处理复杂 DOM 结构。
2. **Diff 粒度**：当前 diff 是 token-level 摘要（before/after 长度 + 前 3 个 token），未实现行级或字符级 diff。后续可接入 `difflib` 或类似库增强。
3. **Selector missing 语义**：未定义 selector missing 的业务语义（如内容删除与重大 Event 触发逻辑），推迟至 PART 19-03。
4. **Dynamic noise policy**：未实现最终规则，推迟至后续 Part。
5. **Empty extracted content**：当前返回空字符串并标记 unchanged。后续可能需要区分 "truly empty" 与 "selector matched but no text"。
6. **Malformed HTML**：依赖 BeautifulSoup 容错，不保证 100% 解析正确。
7. **Fingerprint collision**：SHA-256 碰撞概率极低，但理论存在。当前未做额外防护。
8. **Metadata 存储**：`normalized_values` 存储在 target metadata（JSON）中。若 extractor 数量或内容体积极大，可能影响数据库性能。后续可考虑独立表或列。

---

## 十三、验收核心问题

**"系统判断网页变化时，到底比较的是什么？"**

**答案**：

系统比较的是 **selector 提取后的 normalized content**，而非 raw HTML hash。

完整链路：

```
raw HTML
    ↓
selector extraction（DOMExtractor）
    ↓
raw extracted value（文本 / 转换后值）
    ↓
normalize_extracted_text（去除首尾空白、压缩内部空白、类型安全转换）
    ↓
normalized content（稳定字符串）
    ↓
observation_fingerprint（SHA-256 of target_id + normalized + selector_fingerprint）
    ↓
fingerprint（deterministic, restart-stable）
    ↓
compute_diff(previous_normalized, current_normalized)
    ↓
DiffResult（before, after, changed, summary, regions）
    ↓
ObservationResult（包含 evidence 链）
```

**关键点**：
- **不** 对完整 raw HTML 做 hash。
- **不** 包含 timestamp / random / process state 在 fingerprint 中。
- **不** 将 first observation 当作 fake change。
- **不** 在 304 时执行 extraction 或 fingerprint。

---

## 十四、下一步

- PART 19-03：定义 selector missing 的业务语义（如内容删除与重大 Event 触发逻辑）。
- PART 19-04（可选）：增强 diff 粒度（行级 / 字符级 diff）。
- PART 19-05（可选）：dynamic noise policy 最终规则。

---

## 十五、锚点

- Generic Web Target 声明式配置：`src/web_watcher/targets.py`, `src/web_watcher/generic_web_target.py`
- Extraction 架构：`src/web_watcher/dom_extractor.py`, `src/web_watcher/normalizer.py`
- Fingerprint 设计：`src/web_watcher/web_fingerprint.py`
- Diff 设计：`src/web_watcher/diff.py`
- Observation 模型：`src/web_watcher/observation.py`
- 端到端测试：`tests/test_generic_web_target_extraction.py`
- 全量测试：`1203 passed`
