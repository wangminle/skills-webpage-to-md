# 平台公开分享机制调研与支持清单

> 调研日期：2026-03-17  
> 调研目标：梳理国内外具有"公开分享链接 + 匿名可访问"机制的文档/笔记/知识库平台，评估是否需要像 Notion 一样做成平台级专用适配（即新建 `xxx.py` 模块），为后续扩展提供决策依据。

---

## 目录

- [一、背景与现状](#一背景与现状)
- [二、中国大陆平台](#二中国大陆平台)
  - [2.1 文档/知识库平台](#21-文档知识库平台)
  - [2.2 内容/博客平台](#22-内容博客平台)
- [三、海外平台](#三海外平台)
- [四、综合分级](#四综合分级)
  - [A 类：值得专用适配](#a-类公开链接--可匿名抓取--值得专用适配)
  - [B 类：需授权或 SPA 逆向](#b-类公开链接--仍需授权-api-或-spa-逆向)
  - [C 类：通用抓取已覆盖](#c-类现有-docs-preset--通用-html-已基本覆盖)
- [五、实施建议](#五实施建议)
- [六、参考资料](#六参考资料)

---

## 一、背景与现状

当前仓库中，只有 **Notion** 做了"平台级专用适配"（`notion.py`，约 500 行），通过内部 API v3 递归获取所有 Block 并转换为 HTML，支持 `notion.so` 和 `*.notion.site` 两种域名。

其余平台的处理方式：
- **GitBook / Docusaurus / Mintlify / ReadTheDocs / VitePress** 等：通过 `--docs-preset` 参数使用文档框架预设，属于"导航剥离 + 通用 HTML 抓取"
- **微信公众号**：在主脚本中做了专用检测和处理（含传统长文 + 图文笔记/小绿书）
- **SSR 动态站点**：通过 `ssr_extract.py` 自动提取 `__NEXT_DATA__` / `_ROUTER_DATA` 等 SSR 数据
- **其他所有站点**：通用 HTML 抓取 → Markdown 转换

本次调研的核心问题是：**还有哪些平台具备类似 Notion 的"公开分享 + 匿名可访问"机制，值得做成专用适配？**

---

## 二、中国大陆平台

### 2.1 文档/知识库平台

| 平台 | 域名 | 公开分享链接 | 匿名访问 | 公开 API / 内部 API | 抓取难度 | 评估 |
|------|------|:---:|:---:|------|------|------|
| **语雀** | yuque.com | ✅ | ✅ | ✅ 官方 API（需 Token）；公开页可直接 HTTP | 🟢 低 | **最值得做** |
| **FlowUs 息流** | flowus.cn | ✅ | ✅ | ✅ 有开发者 API | 🟢 低 | 值得关注 |
| **飞书文档** | feishu.cn | ✅ | ✅（需管理员开启） | ⚠️ Open API 需 Token | 🟡 中 | SPA 渲染，暂缓 |
| **腾讯文档** | docs.qq.com | ✅ | ✅ | ❌ 无公开 API | 🟡 中 | SPA 渲染，暂缓 |
| **石墨文档** | shimo.im | ✅ | ✅（需勾选匿名） | ❌ 暂无公开 API | 🟡 中 | SPA 渲染，暂缓 |
| **金山文档/WPS** | kdocs.cn | ✅ | ✅ | ✅ 有匿名预览 API（24h 凭证） | 🟡 中 | 机制复杂，暂缓 |
| **wolai 我来** | wolai.com | ✅ | ✅ | ❌ 未见公开 API | 🟡 中 | 需逆向，暂缓 |
| **有道云笔记** | note.youdao.com | ✅ | ✅ | ⚠️ openAPI 文档较老 | 🟡 中 | 生态萎缩，不推荐 |
| **印象笔记** | yinxiang.com | ✅ | ✅ | ⚠️ 继承 Evernote SDK | 🟡 中 | 低优先 |

**各平台详细分析：**

#### 语雀（⭐⭐⭐ 第一优先）

- **公开机制**：支持知识库级别公开，公开知识库下所有文档可匿名浏览
- **URL 格式**：`https://www.yuque.com/{owner}/{repo}/{slug}`
- **抓取方式**：公开文档页面为标准 SSR HTML，可直接 HTTP 抓取；也可通过官方 API（需 Token）获取结构化数据
- **已有参考**：GitHub 上有 `yuque-crawl`、`YuqueExport` 等开源实现，技术路线成熟
- **注意事项**：2026 年起语雀要求超级会员才能创建新 Token，但已有 Token 仍可用；公开知识库的 HTTP 抓取不受此限制
- **建议**：新建 `yuque.py`，优先支持公开知识库的 HTTP 抓取，API Token 作为可选增强

#### FlowUs 息流（⭐⭐ 值得关注）

- **公开机制**：页面级公开分享，支持"可查看"（无需登录）和"可评论"两种权限
- **URL 格式**：`https://flowus.cn/xxx` 或自定义域名 `{space}.flowus.cn`
- **抓取方式**：有开发者 API（入口在「设置」→「集成应用」→「开发者中心」），Block 模型与 Notion 类似
- **建议**：产品形态最接近 Notion，如果用户需求明确，可做 `flowus.py`

#### 飞书文档（⭐ 暂缓）

- **公开机制**：管理员可开启"公开链接"或"密码保护链接"，外部人员无需登录即可查看
- **限制**：企业安全策略可能禁止公开链接；Open API 所有接口都需要 `tenant_access_token`
- **抓取难度**：页面为 SPA 渲染，纯 HTTP 请求拿不到正文内容
- **建议**：等有明确用户需求再做，优先级低于语雀和 FlowUs

#### 腾讯文档 / 石墨文档 / 金山文档（⭐ 暂缓）

- **共同特点**：都支持公开链接分享，"任何人可查看"模式无需登录
- **共同问题**：都是 SPA 重度渲染，内容通过 JS 动态加载，无公开 API（金山有受限的匿名预览 API）
- **建议**：投入产出比低，暂不做专用适配

### 2.2 内容/博客平台

知乎、CSDN、简书、掘金等中国大陆内容平台：

- **本质**：这些是**文章/博客平台**，公开文章本身就是标准 HTML 页面
- **现状**：现有通用 HTML 抓取能力已经覆盖
- **反爬趋势**：2025-2026 年反爬升级严重（TLS 指纹检测、AI 行为分析、WebGL/Canvas 硬件指纹），但这属于"反爬对抗"范畴，不是"平台专用适配"的问题
- **建议**：不做专用适配，反爬问题通过 `--cookie` / `--header` / `--ua` 参数由用户自行解决

---

## 三、海外平台

| 平台 | 域名 | 公开分享链接 | 匿名访问 | 公开 API / 内部 API | 抓取难度 | 评估 |
|------|------|:---:|:---:|------|------|------|
| **Notion** | notion.so / *.notion.site | ✅ | ✅ | ✅ 内部 API v3 | 🟢 | **✅ 已实现** |
| **Coda** | coda.io | ✅ | ✅ | ✅ REST API v1（需 Key） | 🟡 中 | **最值得做** |
| **Craft** | craft.do | ✅ | ✅ | ✅ API 返回 MD/JSON | 🟢 低 | 值得做 |
| **HackMD** | hackmd.io | ✅ | ✅ | ✅ API 返回 MD | 🟢 低 | 值得做 |
| **GitBook** | *.gitbook.io | ✅ | ✅ | ✅ | 🟢 低 | ✅ 已有 preset |
| **Evernote** | evernote.com | ✅ | ✅（浏览器） | ⚠️ SDK 老旧 | 🔴 高 | 低优先 |
| **Obsidian Publish** | publish.obsidian.md | ✅ | ✅ | ❌ | 🟢 低 | ✅ 通用已覆盖 |
| **GitHub** | github.com | ✅ | ✅ | ✅ REST API + raw | 🟢 低 | ✅ 通用已覆盖 |
| **Dropbox Paper** | paper.dropbox.com | ✅ | ⚠️ 需登录 | ✅ OAuth | 🔴 高 | 不推荐 |
| **OneNote** | onenote.com | ⚠️ | ⚠️ 需 MS 账号 | ✅ Graph API (OAuth) | 🔴 高 | 不推荐 |
| **Quip** | quip.com | ✅ | ⚠️ 受限 | ✅ API（需 Token） | 🟡 中 | 小众，不推荐 |

**各平台详细分析：**

#### Coda（⭐⭐⭐ 第一优先）

- **公开机制**：支持 "Publish your docs to the world"，发布后任何人可通过 URL 匿名浏览
- **官方 API**：REST API v1，支持读取行、发现页面、访问已发布文档（需 API Key）
- **公开页面**：已发布页面为标准 HTML，可直接 HTTP 抓取
- **产品定位**：与 Notion 最接近的海外竞品，功能上非常像"同一类问题"
- **建议**：新建 `coda.py`，优先支持公开发布页面的 HTTP 抓取

#### Craft（⭐⭐ 值得做）

- **公开机制**：Publish 功能，发布后通过 `craft.me/s/xxx` 或自定义域名访问，无需登录
- **API 亮点**：API 通过 `Accept: text/markdown` 直接返回 Markdown 格式内容，极其友好
- **安全选项**：支持密码保护、邮件域名限制、过期时间
- **建议**：做轻量 `craft.py`，利用 API 直接获取 Markdown，开发成本极低

#### HackMD / CodiMD（⭐⭐ 值得做）

- **公开机制**：笔记默认可设为公开，任何人可通过链接查看
- **天然优势**：原生 Markdown 平台，内容本身就是 Markdown，几乎无需转换
- **API**：支持通过 API 直接获取原始 Markdown 内容
- **建议**：做轻量 `hackmd.py`，主要解决 URL 识别和 API 调用

#### Evernote（⭐ 低优先）

- **公开机制**：公开分享笔记链接格式 `evernote.com/shard/{shardId}/sh/{noteGuid}/{shareKey}`
- **关键问题**：浏览器可正常访问，但 HTTP 程序化请求可能返回 404（有反自动化措施）
- **SDK 状态**：开发者文档和 SDK 较老，生态活跃度低
- **建议**：除非有明确用户需求，否则不做

---

## 四、综合分级

### A 类：公开链接 + 可匿名抓取 + 值得专用适配

适合新建 `xxx.py` 专用模块的平台：

| 优先级 | 平台 | 区域 | 预估工作量 | 核心理由 |
|:---:|------|:---:|------|------|
| 1 | **语雀** | 🇨🇳 | 中 (~300 行) | 中国最大技术文档平台，公开知识库 HTML 结构规范，已有开源参考 |
| 2 | **Coda** | 🌍 | 中 (~250 行) | 海外最像 Notion 的产品，有公开发布 + 官方 API |
| 3 | **FlowUs** | 🇨🇳 | 中 (~300 行) | 中国版 Notion，有开发者 API，Block 模型类似 |
| 4 | **Craft** | 🌍 | 小 (~100 行) | API 直接返回 Markdown，开发成本极低 |
| 5 | **HackMD** | 🌍 | 小 (~80 行) | 原生 MD 平台，API 直接返回 Markdown |

### B 类：公开链接 + 仍需授权 API 或 SPA 逆向

暂不做专用适配，等需求明确后再评估：

| 平台 | 区域 | 阻碍因素 |
|------|:---:|------|
| **飞书文档** | 🇨🇳 | SPA 渲染 + API 需 Token + 企业安全策略限制 |
| **腾讯文档** | 🇨🇳 | SPA 渲染 + 无公开 API |
| **石墨文档** | 🇨🇳 | SPA 渲染 + 无公开 API |
| **金山文档** | 🇨🇳 | 匿名预览 API 受限（24h 凭证） |
| **wolai** | 🇨🇳 | 无公开 API 文档，需逆向 |
| **Evernote/印象笔记** | 🌍/🇨🇳 | HTTP 请求被拦截（返回 404），SDK 老旧 |

### C 类：现有 docs preset / 通用 HTML 已基本覆盖

无需专用适配：

| 平台 | 覆盖方式 |
|------|---------|
| **GitBook** | `--docs-preset gitbook` |
| **Docusaurus / Mintlify / ReadTheDocs / VitePress** | 各自 docs preset |
| **GitHub** | 公开内容 raw 访问 / 通用 HTML |
| **Obsidian Publish** | 静态 HTML，通用抓取 |
| **知乎 / CSDN / 简书 / 掘金** | 标准博客 HTML，通用抓取 |
| **OneNote / Dropbox Paper** | 依赖 OAuth，不符合"匿名公开链接"定位 |

---

## 五、实施建议

### 第一批（最高 ROI）

1. **`yuque.py`** — 语雀公开知识库适配
   - URL 识别：`yuque.com/{owner}/{repo}/{slug}`
   - 抓取方式：公开页面 HTTP + 可选 API Token
   - 参考实现：`burpheart/yuque-crawl`
   - 预计工作量：2-3 天

2. **`coda.py`** — Coda 公开发布页面适配
   - URL 识别：`coda.io/d/xxx`
   - 抓取方式：公开页面 HTML + 可选 API Key
   - 预计工作量：2 天

### 第二批（中等 ROI）

3. **`flowus.py`** — FlowUs 息流适配
   - URL 识别：`flowus.cn/xxx` 或 `{space}.flowus.cn`
   - 抓取方式：开发者 API
   - 预计工作量：2-3 天

4. **`craft.py`** — Craft 公开页面适配
   - URL 识别：`craft.do/s/xxx` 或 `craft.me/s/xxx`
   - 抓取方式：API 直接返回 Markdown（`Accept: text/markdown`）
   - 预计工作量：0.5-1 天

5. **`hackmd.py`** — HackMD 公开笔记适配
   - URL 识别：`hackmd.io/xxx` 或 `hackmd.io/@user/xxx`
   - 抓取方式：API 直接返回 Markdown
   - 预计工作量：0.5-1 天

### 暂缓

- 飞书 / 腾讯文档 / 石墨 — SPA 重度渲染 + 无公开 API，投入产出比低
- Evernote / 印象笔记 — HTTP 反自动化 + SDK 老旧，生态萎缩
- OneNote / Dropbox Paper — 纯 OAuth 模型，不符合本工具"匿名公开链接"定位

### 架构建议

新增平台适配时，建议遵循 `notion.py` 已有的模式：

```
scripts/webpage_to_md/
├── notion.py      # 已有
├── yuque.py       # 新增
├── coda.py        # 新增
├── flowus.py      # 新增
├── craft.py       # 新增
└── hackmd.py      # 新增
```

每个模块需实现两个公共函数：
- `is_xxx_url(url: str) -> bool` — 判断是否为该平台 URL
- `fetch_xxx_page(url: str, **kwargs) -> Tuple[str, str]` — 返回 `(html_content, title)`

主脚本 `grab_web_to_md.py` 通过 URL 检测链自动路由到对应模块。

---

## 六、参考资料

### 中国大陆平台

- 语雀官网：https://www.yuque.com
- 语雀开放 API：https://www.yuque.com/yuque/developer
- yuque-crawl（开源爬虫）：https://github.com/burpheart/yuque-crawl
- YuqueExport：https://github.com/M1r0ku/YuqueExport
- FlowUs 息流：https://flowus.cn
- 飞书文档：https://docs.feishu.cn
- 飞书开放平台：https://open.feishu.cn
- 腾讯文档：https://docs.qq.com
- 石墨文档：https://shimo.im
- 金山文档开放平台：https://developer.kdocs.cn
- wolai：https://www.wolai.com

### 海外平台

- Notion Share your work：https://www.notion.so/help/share-your-work
- Notion API：https://developers.notion.com/reference/intro
- Coda Publish：https://help.coda.io/hc/en-us/articles/39555764981133
- Coda API：https://coda.io/developers/apis/v1
- Craft Publishing：https://support.craft.do/en/share-and-publish/publish
- HackMD：https://hackmd.io
- Evernote Sharing：https://dev.evernote.com/doc/articles/sharing.php
- Evernote Developer：https://dev.evernote.com/documentation/
- GitBook Public Publishing：https://gitbook.com/docs/publishing-documentation/publish-a-docs-site/public-publishing
- GitHub Contents API：https://docs.github.com/en/rest/repos/contents
- OneNote Graph API：https://learn.microsoft.com/en-us/graph/api/page-get
