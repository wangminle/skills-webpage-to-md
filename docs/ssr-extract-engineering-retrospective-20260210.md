# SSR 数据提取方案：工程回顾

> **项目**：Web to Markdown Grabber · `ssr_extract.py` 模块  
> **日期**：2026-02-10  
> **参与者**：一线工程师（实现）、架构师 ×2（方案评审）、测试同学 ×3（Bug 发现与验证）  
> **最终产出**：945 行纯 Python，零新增依赖，覆盖 5 种 JSON 富文本 Schema + 2 种 SSR 框架

---

## 一、问题的起源

### 1.1 失败的第一次导出

2026 年 2 月 10 日的一次批量导出中，两个目标页面同时暴露了工具的盲区：

| 页面 | 现象 | 根因 |
|------|------|------|
| 腾讯云开发者文章 | "富媒体 content 根本没导出" | 正文存储为 ProseMirror JSON，嵌在 Next.js `__NEXT_DATA__` 中，纯 HTML 解析只能拿到空壳 |
| MetalManiax 日文站 | 全文乱码，目录错乱 | `requests` 默认 `ISO-8859-1`，HTML `<meta charset>` 声明的 Shift-JIS 被忽视 |

乱码问题是编码检测逻辑的缺陷，修复相对直接。但腾讯云文章的问题揭示了一个更深层的架构缺口——**我们的工具对 JavaScript 渲染的页面完全无能为力**。

### 1.2 问题的本质：SSR 数据 ≠ CSR 渲染

关键洞察：许多 JS 渲染站点并非"纯客户端渲染"。大量使用 Next.js、Nuxt、Modern.js 等 SSR 框架的站点，**服务端已经将文章数据序列化到 HTML 的 `<script>` 标签中**。数据就在那里，只是我们没去拿。

```
<script id="__NEXT_DATA__" type="application/json">
  {"props": {"pageProps": {"article": {"body": { /* ProseMirror JSON */ }}}}}
</script>
```

这不是"JavaScript 渲染"问题，这是**数据提取策略**问题。

---

## 二、架构讨论：三方观点的碰撞

在确认了问题本质后，团队围绕"如何支持 JS 渲染页面"展开了一场深入讨论。三方观点形成了完整的决策光谱。

### 2.1 架构师 1：问题域分析

架构师 1 从**技术全景**出发，梳理了 JS 富文本渲染的完整生态：

> **ProseMirror 和 Quill Delta JSON 确实是业界非常知名的两种富文本数据方案，但绝不是只有这两种。**

他列举了六种主流方案及其数据结构：

| 方案 | 数据格式 | 特征 |
|------|---------|------|
| **ProseMirror** / **Tiptap** | 嵌套 JSON Tree | `content` 作为子节点 key |
| **Slate.js** | 嵌套 JSON Tree | `children` 作为子节点 key |
| **Lexical** | JSON Tree + 位掩码 | `format` 字段用 bitmask 表示粗体/斜体 |
| **Editor.js** | 扁平 Block JSON | `blocks` 数组，每块有 `type` + `data` |
| **Quill Delta** | Operation 序列 | `insert` + `attributes` 的 ops 数组 |
| **Draft.js** | 扁平 Block + Entity Map | Facebook 旧版，已停维 |

**关键判断**：腾讯云和火山引擎的具体场景中，底层可能用的是 Cherry Markdown 和 MDX，而非直接的编辑器 JSON。对于**博客/技术文档展示**场景，Markdown 才是真正的主流存储格式。

### 2.2 架构师 2：工程权衡

架构师 2 从**投入产出比**出发，给出了明确的工程建议：

> **三套都"深度集成"会偏重，而且性价比不高。更简单有效的做法是走 80/20 方案。**

他提出的两阶段策略：

```
主路径：抓"渲染后 HTML"
  ↓ 正文缺失时
兜底路径：解析 <script> 中的 JSON
```

> **先不要全量集成 ProseMirror/Tiptap + Slate + Quill 运行时。优先"渲染后 HTML + JSON 兜底"的两阶段方案，最稳、最省成本。**

以及具体的轻量转换策略：

- 只支持常见节点：h1-h6 / p / list / code / blockquote / image / link / table
- 其余降级成纯文本或原始 HTML
- 做成插件式 extractor：`html_extractor`（默认） + `json_fallback_extractor`（按需触发）

### 2.3 一线工程师：基于实现经验的判断

作为实际编写了提取代码的一线工程师，最终方案从代码实践中提炼出一个核心洞察：

> **虽然编辑器架构天差地别，但当它们用于博客/技术文档展示时，序列化出来的 JSON 数据在语义层面高度趋同。**

五种框架的"段落"节点，仅仅是 key 名不同：

```
ProseMirror:  {"type": "paragraph", "content": [{"type": "text", "text": "..."}]}
Slate.js:     {"type": "paragraph", "children": [{"text": "..."}]}
Editor.js:    {"type": "paragraph", "data": {"text": "..."}}
Lexical:      {"type": "paragraph", "children": [{"type": "text", "text": "..."}]}
Quill Delta:  {"insert": "...\n", "attributes": {"header": false}}
```

> **万变不离其宗：段落、标题、列表、代码块、图片、表格、链接。一个博客文章不会用到编辑器的实时协同、光标同步、OT 算法这些复杂能力。**

由此得出结论——**不需要为每种编辑器写单独的转换器，只需一个兼容多种 Schema 的通用递归转换器**。

### 2.4 决策收敛

三方观点互为补充，最终收敛为一个清晰的方案比较表：

| 方案 | 新增代码 | 新增依赖 | 覆盖率 |
|------|---------|---------|--------|
| 集成 ProseMirror + Slate + Quill 运行时 | 数千行 | 3 个 npm 包 + Node.js | ~40% |
| **通用 JSON 转换器（最终选择）** | **~200 行 Python** | **零** | **~35%** |
| Playwright `--js-render` | ~200 行 | playwright | ~95% |
| `--local-html`（手动兜底） | 已实现 | 零 | 100% |

**最终决策**：通用化现有方案，不引入新依赖。这完全符合项目"只用 `requests` + 纯 Python 实现轻量导出"的初衷。

---

## 三、工程实现：通用转换器的设计

### 3.1 两阶段兜底策略

`try_ssr_extract()` 是模块的入口函数，实现了"精确打击 → 地毯式搜索"的双层策略：

```
第一阶段：精确匹配（已知 SSR 框架）
  ├── __NEXT_DATA__     → Next.js → ProseMirror JSON → HTML
  └── _ROUTER_DATA      → Modern.js → MDContent → 直接使用 Markdown
        ↓ 以上均未命中
第二阶段：通用兜底（扫描全页 <script> 标签）
  ├── 发现结构化 JSON → richtext_json_to_html → HTML
  └── 发现 Markdown 文本 → 直接使用
```

这种设计兼顾了**性能**（精确命中时一次即停）和**覆盖率**（未知站点也有机会提取）。

### 3.2 通用转换器：`richtext_json_to_html`

核心设计理念是**集合映射**——用 Python `set` 归并不同框架对同一语义类型的命名差异：

```python
_TYPE_PARAGRAPH = {"paragraph", "p"}
_TYPE_HEADING   = {"heading", "header"}
_TYPE_BULLET_LIST = {"bulletList", "bullet_list", "bulleted-list", "unordered-list"}
_TYPE_CODE_BLOCK  = {"codeBlock", "code-block", "code_block", "code"}
# ... 17 种语义类型
```

转换器本身是一个约 200 行的递归函数，核心逻辑极其简洁：

1. **识别子节点**：`node.get("content") or node.get("children") or []`（ProseMirror 用 `content`，Slate/Lexical 用 `children`）
2. **处理文本节点**：调用 `_apply_marks()` 应用格式标记
3. **语义映射**：通过 `set` 匹配节点类型 → 输出对应 HTML 标签
4. **安全回退**：未知节点直接输出子内容，绝不报错

### 3.3 格式标记统一：`_apply_marks`

不同框架对"粗体"的表示方式完全不同：

| 框架 | 表示方式 |
|------|---------|
| ProseMirror | `"marks": [{"type": "bold"}]` |
| Slate.js | `"bold": true` |
| Lexical | `"format": 1`（位掩码 0b0001） |

`_apply_marks` 的策略是**先收集后应用**——无论数据源是哪种格式，统一收敛为一组布尔标志，最后一次性包裹 HTML 标签。这避免了双重包装的问题。

### 3.4 树形 vs 扁平结构共存

ProseMirror/Slate/Lexical 的数据是**嵌套树**，而 Editor.js 的数据是**扁平块数组**。通用转换器在同一个入口中平滑处理了这种差异：

```python
# Editor.js 顶层结构: {"blocks": [...]}
blocks = node.get("blocks")
if isinstance(blocks, list) and "type" not in node:
    return _convert_editorjs_blocks(blocks)  # 走扁平路径

# 其他框架：递归处理嵌套树
children = node.get("content") or node.get("children") or []
inner = "".join(richtext_json_to_html(c) for c in children)
```

### 3.5 防御性编程细节

- **标题级别钳位**：`min(max(level, 1), 6)`，防止 `<h0>` 或 `<h99>`
- **递归深度限制**：`depth > 8` 时停止递归，防止恶意深层 JSON 导致栈溢出
- **HTML 转义**：所有文本内容经过 `html_escape`，防止 XSS
- **Editor.js 清洗器**：`_sanitize_editorjs_html` 移除危险标签和事件属性，保留安全格式

---

## 四、Bug 猎杀记：四轮测试同学的反馈

### 4.1 第一轮：核心流程验证

测试同学验证了腾讯云和火山引擎的导出效果：

- ✅ 腾讯云 ProseMirror 文章正文完整提取
- ✅ 火山引擎 MDContent 正文完整提取
- ⚠️ 火山引擎图片有部分 404（非标准 Markdown 尺寸提示 `=986x` 污染 URL）

**修复**：新增 `_MD_SIZE_HINT_RE` 正则，在图片 URL 提取和 Markdown 文本清理中剔除尺寸提示。

### 4.2 第二轮：边界条件暴露两个 P1 级 Bug

测试同学通过精心构造的 mock 数据，暴露了两个在真实页面上难以复现但确实存在的严重问题：

**P1-A：批量模式 SSR 提取被 JS 反爬提前拦截**

```
问题：process_single_url() 中 detect_js_challenge 检查在 try_ssr_extract 之前
影响：页面有 <noscript> 标签（触发 JS 反爬检测）但同时有 SSR 数据时，批量模式会误报失败
根因：单页模式已经实现了"SSR 可用则跳过反爬警告"，但批量模式的逻辑分支遗漏了
```

**修复**：将 `try_ssr_extract` 前移到 `detect_js_challenge` 之前。关键代码变更：

```python
# 修复前：先检测反爬 → 抛错 → SSR 提取永远执行不到
# 修复后：先 SSR 提取 → 再检测反爬 → 如果 SSR 成功则跳过
ssr_result = try_ssr_extract(page_html, url)
js_detection = detect_js_challenge(page_html)
if js_detection.is_challenge and not config.force:
    if ssr_result:
        pass  # SSR 数据可用，跳过反爬拦截
    else:
        raise RuntimeError(...)
```

**P1-B：`collect_md_image_urls` 丢弃相对 URL**

```
问题：函数仅保留 http/https 开头的 URL
影响：SSR Markdown 中的相对图片路径（/assets/a.png）被全部忽略
后果：下载映射不完整，导出文档离线查看时断图
```

**修复**：
1. `collect_md_image_urls` 新增 `base_url` 参数，用 `urljoin` 将相对 URL 解析为绝对 URL
2. 新增 `resolve_relative_md_images` 函数，在 Markdown 文本层面将相对路径统一替换为绝对路径

### 4.3 第三轮：架构师代码审查

一位架构师对 `ssr_extract.py` 进行了详细 Code Review，给出了高度评价和四个优化建议：

> **这份代码读起来非常令人愉悦！不仅逻辑清晰，而且在容错性和架构分层上下了很大功夫。这已经不再是一个简单的"脚本"，而是一个健壮的多策略提取引擎。**

> **评分：A+。可以直接合并/上线。**

四个优化建议及响应：

| # | 建议 | 响应 |
|---|------|------|
| 1 | Editor.js `items` 可能已含 HTML，`html_escape` 会导致双重转义 | ✅ 实现了 `_sanitize_editorjs_html` 轻量清洗器 |
| 2 | 正则 `.*` + `re.DOTALL` 对超大 HTML 可能有性能问题 | 📝 记录为已知限制，普通网页无影响 |
| 3 | `collect_md_image_urls` 等函数在模块内无调用，需注明公共 API | ✅ 更新模块 docstring，明确列出公共 API |
| 4 | `_apply_marks` 中 `marks` 列表已做 `isinstance` 保护 | ✅ 确认已实现，无需修改 |

### 4.4 第四轮：正则边界精细化

测试同学通过精心构造的最小复现用例，又发现两个边界问题：

**P1-C：Markdown 图片 title 污染 URL**

```python
# 输入：![alt](https://example.com/a.png "img title")
# 期望：https://example.com/a.png
# 实际：https://example.com/a.png "img title"  ← title 被当作 URL 的一部分
```

**修复**：新增 `_MD_TITLE_RE` 正则剔除标准 Markdown title 文本。处理顺序：先剔除尺寸提示（始终在末尾）→ 再剔除 title → 得到纯净 URL。

**P2-D：HTML 清洗遗漏无引号属性**

```html
<!-- 带引号（已覆盖）-->
<a onclick="alert(1)">  →  <a>

<!-- 无引号（遗漏！）-->
<a onclick=alert(1)>    →  <a onclick=alert(1)>  ← 穿透了清洗
<a href=javascript:alert(1)>  →  未被清理
```

**修复**：事件属性和 `javascript:` 协议的正则新增无引号分支 `[^\s>]+`，以空白或 `>` 为边界阻止贪婪匹配。

---

## 五、轻依赖工程哲学

### 5.1 我们拒绝了什么

在整个实现过程中，有多个时刻可以选择"更重"的方案：

| 诱惑 | 拒绝理由 |
|------|---------|
| 引入 ProseMirror 编辑器运行时 | 只需要 JSON→HTML 转换，不需要编辑器的协同/光标/OT 能力 |
| 引入 Slate.js/Quill 运行时 | 同上。博客文章的 JSON 在语义层面高度趋同 |
| 引入 Playwright 做通用 JS 渲染 | 依赖重（npm + Chromium）、安装复杂、CI 不友好 |
| 引入 BeautifulSoup 做 HTML 解析 | 项目始终坚持 `HTMLParser`（标准库）路线 |
| 引入 bleach/ammonia 做 HTML 清洗 | 12 行正则即可覆盖 Editor.js 场景的安全需求 |

### 5.2 我们坚持了什么

```
唯一的外部依赖：requests
全部新增代码：945 行纯 Python
兼容的 JSON Schema：5 种（ProseMirror/Slate/Editor.js/Lexical/Quill Delta）
适配的 SSR 框架：2 种（Next.js/Modern.js）+ 通用 <script> 扫描兜底
测试覆盖：97 项单元测试全部通过
```

### 5.3 "不需要集成编辑器"的根本原因

这是本次工程中最重要的技术洞察，值得反复强调：

> **所谓的"ProseMirror 支持"，不是集成 ProseMirror 编辑器运行时，而只是一个递归 JSON → HTML 转换器。**
>
> **编辑器的复杂性在于实时编辑能力——协同、光标、OT 算法、Schema 校验。但当数据序列化为 JSON 用于展示时，这些复杂性全部消失了，只剩下十几种语义节点的树形结构。**

这个洞察直接决定了方案的轻量级——用 200 行代码覆盖了五种编辑器框架的展示数据，而不是引入五个重量级依赖。

---

## 六、数据驱动：测试覆盖与质量演进

### 6.1 测试用例增长曲线

| 阶段 | 新增测试 | 累计总数 | 覆盖范围 |
|------|---------|---------|---------|
| 初始状态 | — | 26 | HTML→Markdown 基础转换 |
| SSR 基础功能 | +21 | 47 | ProseMirror/Slate/Editor.js/Lexical/Quill 转换 |
| P1-A/B 修复 | +11 | 58 | 批量模式 SSR 旁路、相对 URL 解析 |
| 架构师建议实现 | +4 | 62 | Editor.js HTML 清洗 |
| 编码检测+其他 | +28 | 90 | Meta charset、自动标题等 |
| P1-C/P2-D 修复 | +7 | 97 | Title 剔除、无引号 XSS 清洗 |

### 6.2 Bug 的发现模式

四轮 Bug 发现呈现清晰的"由粗到细"模式：

```
第 1 轮：功能级（能不能用）  → 正文缺失、URL 404
第 2 轮：流程级（不同路径）  → 批量 vs 单页、绝对 vs 相对 URL
第 3 轮：设计级（架构审查）  → 双重转义、公共 API 文档
第 4 轮：边界级（正则精度）  → Markdown title、无引号属性
```

每一轮都是在上一轮"能用"的基础上进一步追求"正确"和"安全"。这种渐进式质量提升是工程实践中非常健康的模式。

---

## 七、反思与总结

### 7.1 做对了什么

1. **问题分类先于方案选择**。先区分 SSR（数据已在 HTML 中）和 CSR（数据需要 JS 执行后加载），才能选择正确的技术路线。
2. **拒绝过度设计**。没有为"可能需要"的 Playwright 集成预留架构，而是用最简方案解决当前问题。
3. **测试驱动修复**。每个 Bug 修复都先写测试用例确认问题，再修改代码，再运行全量回归。
4. **安全左移**。从 `html_escape` 到 `_sanitize_editorjs_html`，安全措施是代码的一部分，而非事后补丁。

### 7.2 可以做得更好的地方

1. **批量模式与单页模式的逻辑一致性**应在第一次实现时就保证，而非靠测试同学发现不一致后回溯修复（P1-A）。
2. **正则的边界条件**（title、无引号属性）应在编写时就考虑完整，而非靠多轮测试逐步收紧。
3. **公共 API 的文档**应在函数编写时就标注 `@public`，而非等审查者指出。

### 7.3 留给未来的问题

| 待解决 | 触发条件 | 建议方案 |
|--------|---------|---------|
| 纯 CSR 页面（如阿里云百炼控制台） | HTML 中无 JSON 数据，内容由 API 动态加载 | `--local-html` 手动兜底；远期考虑 Playwright 作为可选依赖 |
| 超大 HTML（2MB+）正则性能 | `<script>` 扫描阶段 `.*?` 回溯 | 改用字符串查找 `html.find('<script')` 替代正则 |
| 更多 SSR 框架适配 | 遇到 Nuxt.js / Remix 等新框架 | 在精确匹配阶段添加新的 `_extract_xxx` 函数 |

### 7.4 最后的工程哲学

回望整个实现过程，有一句话可以概括我们的技术选择：

> **不要把编辑器的复杂性误认为是数据的复杂性。当 ProseMirror 的 JSON 离开编辑器、变成展示数据的那一刻，它就只是一棵普通的语义树。一个 200 行的递归函数足以驾驭它。**

这是轻依赖工程哲学的一个极好的实践案例：**理解数据的本质，比引入更多工具更重要。**

---

## 附录：关键代码索引

| 文件 | 函数/类 | 职责 |
|------|---------|------|
| `ssr_extract.py` | `try_ssr_extract()` | 入口：两阶段提取策略 |
| `ssr_extract.py` | `richtext_json_to_html()` | 通用 JSON→HTML 转换器（5 种 Schema） |
| `ssr_extract.py` | `_apply_marks()` | 格式标记统一（ProseMirror marks / Slate attrs / Lexical bitmask） |
| `ssr_extract.py` | `_sanitize_editorjs_html()` | Editor.js 内容 HTML 清洗（XSS 防护） |
| `ssr_extract.py` | `collect_md_image_urls()` | Markdown 图片 URL 提取（含相对路径解析） |
| `ssr_extract.py` | `resolve_relative_md_images()` | Markdown 文本中相对图片 URL → 绝对 URL |
| `ssr_extract.py` | `_extract_nextjs()` | Next.js `__NEXT_DATA__` 精确提取 |
| `ssr_extract.py` | `_extract_modernjs()` | Modern.js `_ROUTER_DATA` 精确提取 |
| `ssr_extract.py` | `_scan_scripts_for_richtext()` | 通用 `<script>` 扫描兜底 |
| `grab_web_to_md.py` | `_fetch_page_html()` | 单页模式 SSR 旁路逻辑 |
| `grab_web_to_md.py` | `process_single_url()` | 批量模式 SSR 旁路逻辑 |
| `test_grab_web_to_md.py` | 97 项测试 | 全覆盖回归测试 |
