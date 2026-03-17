# Notion 公开页面保存为 Markdown 完整方案

## 背景与挑战

Notion 公开页面（如 `https://www.notion.so/Kiro-29cbd3b8020080d5a1e5f7cd300576dd`）是纯 SPA（单页应用），所有内容通过 JavaScript 动态渲染。直接用 HTTP 请求或常规爬虫抓取只能拿到空壳 HTML，无法获取实际内容。

常规的 `webpage-to-md` 工具（基于 HTTP fetch）在这种场景下会失败。

---

## 解决方案概览

```
Notion 公开链接
    │
    ▼
Step 1: 自动检测 Notion URL（notion.so / *.notion.site）
    │
    ▼
Step 2: 通过 Notion 内部 API 递归获取所有 Block 数据（JSON）
    │
    ▼
Step 3: Python 脚本将 Block JSON 转换为结构化 HTML
    │
    ▼
Step 4: HTML → Markdown 转换 + 图片下载（全部由 grab_web_to_md.py 自动完成）
    │
    ▼
最终产出: Markdown 文件 + 本地图片资源
```

> **当前状态**：Notion 功能已集成到 `grab_web_to_md.py` CLI 中，传入 Notion 链接即可自动
> 走 API 路径，无需手动分步操作。以下文档保留原始方案设计供参考。

---

## Step 1：通过 Notion API 获取 Block 数据

### 关键发现

Notion 公开页面虽然不提供 SSR 内容，但其前端使用的内部 API 是公开可访问的（无需认证）。

### 使用的 API 端点

| API | URL | 用途 |
|-----|-----|------|
| loadPageChunk | `https://www.notion.so/api/v3/loadPageChunk` | 获取页面的初始 Block 数据（最多 300 个） |
| syncRecordValues | `https://www.notion.so/api/v3/syncRecordValues` | 批量获取指定 Block ID 的详细数据 |
| getSignedFileUrls | `https://www.notion.so/api/v3/getSignedFileUrls` | 将 `attachment:` 或 S3 内部 URL 转为可下载的签名 URL |

### Page ID 提取

从 Notion URL 中提取 Page ID：
```
URL: https://www.notion.so/Kiro-29cbd3b8020080d5a1e5f7cd300576dd
                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Page ID: 29cbd3b8-0200-80d5-a1e5-f7cd300576dd（插入连字符）
```

### 递归获取逻辑

1. 调用 `loadPageChunk` 获取初始 Block 数据
2. 遍历所有 Block 的 `content` 字段，收集子 Block ID
3. 找出尚未获取的 Block ID（missing）
4. 通过 `syncRecordValues` 批量获取（每批 50 个）
5. 重复步骤 2-4 直到没有缺失的 Block

本次实际获取了 **148 个 Block**。


---

## Step 2：Block JSON → HTML 转换

### Notion Block 类型映射

脚本需要处理的 Block 类型及其 HTML 映射：

| Notion Block Type | HTML 输出 | 说明 |
|-------------------|-----------|------|
| `text` | `<p>` | 普通段落 |
| `header` | `<h1>` | 一级标题 |
| `sub_header` | `<h2>` | 二级标题 |
| `sub_sub_header` | `<h3>` | 三级标题 |
| `bulleted_list` | `<ul><li>` | 无序列表（需合并连续项） |
| `numbered_list` | `<ol><li>` | 有序列表（需合并连续项） |
| `to_do` | `<p>☑/☐` | 待办事项 |
| `toggle` | `<details><summary>` | 折叠块 |
| `quote` | `<blockquote>` | 引用 |
| `callout` | `<div class="callout">` | 提示框（含 emoji 图标） |
| `code` | `<pre><code>` | 代码块（含语言标识） |
| `image` | `<figure><img>` | 图片（需处理签名 URL） |
| `divider` | `<hr/>` | 分割线 |
| `bookmark` | `<a href>` | 书签链接 |
| `column_list` / `column` | `<div class="columns">` | 多列布局 |
| `embed` / `video` | `<a href>` | 嵌入内容 |

### 富文本格式处理

Notion 的富文本存储为嵌套数组，格式标记包括：

| 格式码 | 含义 | HTML 输出 |
|--------|------|-----------|
| `b` | 粗体 | `<b>` |
| `i` | 斜体 | `<i>` |
| `s` | 删除线 | `<s>` |
| `c` | 行内代码 | `<code>` |
| `a` | 链接 | `<a href>` |
| `_` | 下划线 | `<u>` |
| `h` | 高亮/颜色 | `<mark>` |

### 列表合并的关键处理

Notion 中每个列表项是独立的 Block，但 HTML 需要将连续的同类列表项合并到一个 `<ul>` 或 `<ol>` 中。脚本通过遍历时检测连续同类型 Block 来实现合并。

---

## Step 3：图片 URL 处理

### 问题

Notion 图片存储在 S3 上，URL 格式为：
```
attachment:xxx/Screenshot_2025-10-31_at_12.36.57_PM.png
```
或
```
https://prod-files-secure.s3.us-west-2.amazonaws.com/...
```

这些 URL 无法直接下载，需要通过 Notion 的签名服务获取临时可访问的 URL。

### 解决方案

**方案 A（脚本内处理）：** 使用 Notion 图片代理 URL：
```
https://www.notion.so/image/{encoded_url}?table=block&id={block_id}
```

**方案 B（API 签名）：** 调用 `getSignedFileUrls` API：
```json
POST https://www.notion.so/api/v3/getSignedFileUrls
{
  "urls": [
    {
      "url": "attachment:xxx/image.png",
      "permissionRecord": {
        "table": "block",
        "id": "block-id"
      }
    }
  ]
}
```

本次实际使用了方案 A（代理 URL），在 `webpage-to-md` 下载图片时自动处理。

---

## Step 4：集成式 CLI 用法（当前版本）

Notion 功能已完整集成到 `grab_web_to_md.py` 中。传入 Notion 链接时，CLI 会自动检测并走 API 路径：

```bash
# 单页导出（自动检测 Notion URL，无需额外参数）
python grab_web_to_md.py "https://www.notion.so/Kiro-29cbd3b8020080d5a1e5f7cd300576dd" \
  --out output/kiro-guide.md

# 自动标题模式
python grab_web_to_md.py "https://team.notion.site/Guide-abcdef0123456789abcdef0123456789" \
  --auto-title

# 禁用 Notion 自动检测（强制走普通 HTTP）
python grab_web_to_md.py "https://notion.so/xxx" --no-notion
```

### 支持的域名

| 域名模式 | 示例 |
|----------|------|
| `notion.so` / `www.notion.so` | `https://www.notion.so/Page-29cbd3b8...` |
| `*.notion.site` | `https://team.notion.site/Page-29cbd3b8...` |

### 相关 CLI 参数

| 参数 | 说明 |
|------|------|
| `--no-notion` | 禁用 Notion 公开页面自动检测与 API 提取 |

### 错误处理

- Notion API 提取失败时，CLI **不会**静默回退到普通 HTTP（因为 Notion 空壳页面无有效内容）
- 单页模式：直接返回 `EXIT_FAILURE` 并输出错误信息
- 批量模式：抛出 `RuntimeError`，由批量框架的 `skip_errors` 机制决定是否继续

---

## 最终产出

```
1-Kiro官方文档/Kiro-产品经理使用指南/
├── Kiro-产品经理使用指南.md                    # 完整 Markdown 文档
├── Kiro-产品经理使用指南.md.assets.json         # 图片资源映射
└── Kiro-产品经理使用指南.assets/
    ├── 01-Screenshot_2025-10-31_at_12.36.57_PM.png
    ├── 02-Screenshot_2025-10-31_at_12.50.03_PM.png
    ├── 03-Screenshot_2025-10-31_at_12.58.38_PM.png
    └── 04-Screenshot_2025-10-31_at_1.32.28_PM.png
```

共 4 张截图全部成功下载，Markdown 内容与原始 Notion 页面截图对比验证完整。

---

## 集成代码位置

| 文件 | 位置 | 说明 |
|------|------|------|
| `notion.py` | `skills/webpage-to-md/scripts/webpage_to_md/` | Notion API 提取 + Block→HTML 转换模块 |
| `grab_web_to_md.py` | `skills/webpage-to-md/scripts/` | 主 CLI，含 Notion 自动检测逻辑 |

### 复用方法

直接传入 Notion 公开链接即可，CLI 会自动检测：

```bash
python grab_web_to_md.py "https://www.notion.so/Your-Page-ID" --out output.md
```

---

## 方案局限性与注意事项

1. **仅适用于公开页面**：Notion 内部 API 对公开页面无需认证，私有页面需要 Cookie 或 Integration Token
2. **API 稳定性**：使用的是 Notion 内部 API（`/api/v3/`），非官方公开 API，可能随时变更
3. **图片签名有效期**：签名 URL 有时效限制，建议生成 HTML 后尽快下载图片
4. **不支持的 Block 类型**：数据库视图（database）、嵌入式表格（collection_view）等复杂类型未完整支持
5. **依赖项**：Python 3 + `requests` 库

---

*本方案在 2026 年 3 月实际验证通过，成功保存了包含 148 个 Block、4 张截图的 Notion 公开页面。*
*2026 年 3 月集成到 `grab_web_to_md.py` CLI，支持 `notion.so` 和 `*.notion.site` 域名自动检测。*
