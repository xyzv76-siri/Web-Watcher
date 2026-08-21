# Diff Scope v1 — Design (frozen)

## 1. 问题

Extractor 决定"取什么"，但不决定"比哪部分"。  
页面里常混有动态噪声区域（时间戳、广告、页脚等），导致即使后续有 dynamic noise 过滤，仍然可能在证据层面制造干扰。

## 2. 目标

在 **Extractor → Diff** 之间增加唯一一层 **Diff Scope**：

```
Target
  ↓
Extractor       取什么
  ↓
Diff Scope      比哪部分
  ↓
Dynamic Noise   变化是不是噪声
  ↓
Alert Silencer  这个事件现在要不要通知
  ↓
Event Correlator
  ↓
Notification
```

## 3. v1 精确边界

### 3.1 只支持 `scope_selector`

`ExtractorConfig` 新增可选字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `scope_selector` | `str` | CSS 选择器，对提取到的 HTML 片段再次匹配子元素 |

v1 不加入：
- XPath
- text start/end marker
- line range
- 正则 scope
- AI / 自动推断
- evidence 大改
- rule pipeline 重构

### 3.2 行为语义

| 情况 | 行为 |
|------|------|
| 没有 `scope_selector` | 保持现有行为，100% 兼容 |
| selector 找到内容 + scope 找到区域 | 只比较 scope 区域的内容 |
| scope 找不到元素 | **不静默回退**，明确标记 scope miss，阻止误报扩散 |
| scope 匹配多个元素 | 合并这些元素后比较 |
| scope selector 非法 | 配置错误，明确失败 |
| 页面结构变化导致 scope 消失 | 产生可观测的 scope failure，而不是偷偷扩大比较范围 |

### 3.3 关键原则

**不回退**。  
如果用户明确指定了 `scope_selector`，而它消失了，我们不应该偷偷退回整个 extractor 范围。  
那样会突然把广告、推荐文章、评论、时间戳全部纳入 diff，制造一堆误报，违背 Web-Watcher "有礼貌" 的原则。

## 4. 实现计划

1. 扩展 `ExtractorConfig`，增加 `scope_selector` 字段
2. 实现 `apply_diff_scope()` 工具函数
3. 在 `GenericWebTarget.execute()` 中集成 scope 裁剪，位于 normalization 之后、diff 之前
4. 明确 scope miss 的失败路径，阻止后续 diff / signal
5. 补充测试：
   - scope_selector 正确裁剪 HTML
   - scope miss 不静默回退
   - scope 多个元素合并比较
   - scope selector 非法时明确失败
   - 无 scope 时保持现有行为

## 5. 待确认

1. 设计是否按此冻结边界进入施工？
