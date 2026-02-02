# Docs/Wiki 导出优化方案 v2.1（修订版）

## 背景与目标

目标是在不引入重依赖的前提下，显著提升 docs/wiki 类站点导出到单一 Markdown 的可读性与可用性，重点解决"导航/目录重复导致正文被淹没"的问题，同时保持当前脚本的轻量化与可控性。

## 问题定位

| 问题类型 | 具体表现 | 严重程度 |
|----------|----------|----------|
| **导航重复** | 每页都包含完整侧边栏（241 个链接 × 241 页） | 🔴 严重 |
| **页内 TOC 重复** | 每页的 "On this page" 目录被重复抓取 | 🔴 严重 |
| **锚点定位失准** | 跳转后需滚动 200+ 行才能看到正文 | 🟡 中等 |
| **内容密度低** | 链接占比过高，正文被"淹没" | 🟡 中等 |

## 设计原则（修订）

- **轻依赖**：保持无 BS4/LXML/Playwright 依赖，延续 HTMLParser 路线
- **默认不破坏**：新增能力默认关闭，避免误删正文
- **可观测**：每一步输出"移除/保留/命中规则"的可追溯信息
- **可扩展**：预设与自定义规则并存，覆盖常见 docs 站点
- **可回滚**：所有剥离逻辑均可通过参数关闭

## 解决方案架构（保持不变，细化实现）

```
┌─────────────────────────────────────────────────────────────┐
│                      抓取前检测层                            │
│  • 站点模板识别（Docusaurus/Mintlify/GitBook/MkDocs...）      │
│  • 正文容器探测（article/main/[role=main]）                   │
│  • 导航块特征识别（nav/aside/role=navigation/...）           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      单页处理层                              │
│  • 精准提取正文容器（--target-id/--target-class 多值支持）     │
│  • 移除导航元素（--strip-nav）                               │
│  • 移除页内目录（--strip-page-toc）                          │
│  • 连续锚点列表降噪（长度>阈值时自动移除）                     │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      合并处理层                              │
│  • 重复块去重（仅对"高链接密度块"生效）                        │
│  • 生成全局目录（仅一份）                                     │
│  • 每页仅保留：标题 + 正文                                    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      输出层                                  │
│  • 单文件模式：merged.md（全局目录 + 所有正文）               │
│  • 分文件模式：INDEX.md + 各页独立文件（可选）                │
│  • 可观测性报告：移除块数量、命中规则、节省字符数             │
└─────────────────────────────────────────────────────────────┘
```

## 抓取前检测要点（补充）

- **正文容器可靠性**：是否存在 `article/main/[role=main]` 或站点特定容器
- **目录/侧栏特征**：是否存在 `nav/aside`、类名含 `sidebar/toc/contents/menu`
- **链接密度与内容长度**：若链接数占比高且正文长度偏短，提示可能被目录污染
- **动态渲染风险**：正文过短时提示可能是 SPA（需要 `--local-html`）
- **锚点冲突风险**：合并时同名标题数量过多，需自动后缀

## 开发任务清单（修订）

### Phase 1: 目录/侧栏剥离（P0 - 核心功能）✅ 已完成

| 任务 ID | 任务描述 | 实现要点 | 状态 |
|---------|----------|----------|------|
| **T1.1** | 新增 `--strip-nav` | 在 HTML 预处理阶段移除 `<nav>`, `<aside>`, `[role=navigation]` 及常见类名 | ✅ |
| **T1.2** | 新增 `--strip-page-toc` | 移除 `.toc`, `.table-of-contents`, `.on-this-page`, `[data-toc]` | ✅ |
| **T1.3** | 新增 `--exclude-selectors` | 支持**简化选择器**（tag/.class/#id/[attr=val]/[attr*=val]），非完整 CSS | ✅ |
| **T1.4** | 连续锚点列表检测与移除 | 仅对"高链接密度块"生效，且优先在正文前部处理 | ✅ |
| **T1.5** | 可观测性输出 | 打印移除块数量、命中规则、节省字符数 | ✅ |
| **T1.6** | 单页模式支持 | 单页模式下 `--strip-nav` 等参数也能正常生效 | ✅ |

> 说明：由于当前实现不引入 DOM/CSS 解析，`--exclude-selectors` 为"简化选择器"，避免承诺完整 CSS 支持。

---

### Phase 2: 智能正文容器定位（P1 - 提升智能化）✅ 已完成

| 任务 ID | 任务描述 | 实现要点 | 状态 |
|---------|----------|----------|------|
| **T2.1** | `--target-id/--target-class` 支持多值 | 逗号分隔，按优先级依次尝试 | ✅ |
| **T2.2** | 新增 `--docs-preset` 参数 | 内置框架预设（docusaurus/mintlify/gitbook/vuepress/mkdocs/readthedocs） | ✅ |
| **T2.3** | 站点模板自动识别 | 基于 HTML 关键特征做"置信度检测"，低置信度仅提示 | ✅ |
| **T2.4** | 内容密度检测 | 计算"链接数/总字符数"或"锚点列表密度"，超阈值提示 | ✅ |

**预设结构（保留并补充说明）**：

- `target_selectors` 与 `exclude_selectors` **使用简化选择器语法**
- 自动识别失败时，仅提示推荐预设，不自动启用
- 预设模式自动启用 `anchor_list_threshold=10`

---

### Phase 3: 合并优化与输出增强（架构评审后调整）

> **架构评审结论**：锚点冲突修复是"正确性问题"（功能不可用级别），应升为 P0；双版本输出是"可用性问题"（体验增强），为 P1；去重块风险较高，降为 P2/可选。

#### Phase 3-A: 正确性保障（P0 - 必做）✅ 已完成

| 任务 ID | 任务描述 | 实现要点 | 状态 |
|---------|----------|----------|------|
| **T3.A1** | 锚点冲突检测 | 构建全局 anchor map，统计重复锚点数量 | ✅ |
| **T3.A2** | 锚点自动去重 | 遇到重复时自动加后缀（`#install` → `#install-2`） | ✅ |
| **T3.A3** | 全局链接修复 | 同步修复 TOC 和页内链接指向新锚点 | ✅ |
| **T3.A4** | 冲突可观测性 | 输出冲突统计；新增 `--warn-anchor-collisions` | ✅ |

**价值**：不修复则"目录存在但跳不到正文"，属于功能性缺陷。

#### Phase 3-B: 体验提升（P1 - 高价值）✅ 已完成

| 任务 ID | 任务描述 | 实现要点 | 状态 |
|---------|----------|----------|------|
| **T3.B1** | 双版本输出 | `--split-output DIR` 同时输出 merged + split 版本 | ✅ |
| **T3.B2** | 分文件索引 | 生成 `INDEX.md`（结构索引）+ 各页独立文件 | ✅ |
| **T3.B3** | 统一 assets | 合并/分文件共用同一 assets 目录 | ✅ |
| **T3.B4** | 合并格式优化 | 结构统一：全局目录 → 分隔线 → 章节 | ✅ |

**价值**：适配 Obsidian、检索、协作编辑等场景。

#### Phase 3-C: 谨慎优化（P2 - 可选）

| 任务 ID | 任务描述 | 实现要点 | 预计工作量 |
|---------|----------|----------|------------|
| **T3.C1** | 重复块 hash 去重 | 仅对"高链接密度块"或"跨页完全重复块"生效 | 中 |
| **T3.C2** | 去重开关 | `--dedup-blocks` 默认关闭，避免误删正文 | 小 |

**风险**：误删正文概率较大，更像锦上添花，不是刚需。

## 参数汇总（修订）

```bash
# Phase 1（已完成 ✅）
--strip-nav                 # 移除导航元素（nav/aside/.sidebar 等）
--strip-page-toc            # 移除页内目录（.toc/.on-this-page 等）
--exclude-selectors STR     # 简化选择器（逗号分隔），非完整 CSS
--anchor-list-threshold N   # 连续锚点列表移除阈值（默认 0 关闭，预设模式自动 10）

# Phase 2（已完成 ✅）
--docs-preset NAME          # 使用框架预设（docusaurus/mintlify/gitbook/...）
--auto-detect               # 自动检测框架并按置信度提示/应用
--list-presets              # 列出所有可用预设

# Phase 3-A（已完成 ✅）
--warn-anchor-collisions    # 锚点冲突时输出警告（自动修复为 -2, -3...）

# Phase 3-B（已完成 ✅）
--split-output DIR          # 同时输出分文件版本（与 --merge 配合使用）

# Phase 3-C（可选）
--dedup-blocks              # 合并时对重复块去重（默认关闭）
```

## 命令示例

### 修复 OpenClaw 文档（Phase 1 完成后）

```bash
python grab_web_to_md.py "https://docs.openclaw.ai/" \
  --crawl \
  --merge --toc \
  --strip-nav \
  --strip-page-toc \
  --anchor-list-threshold 15 \
  --merge-output output/openclaw-clean.md \
  --download-images
```

### Phase 2 使用预设（推荐方式）

```bash
python grab_web_to_md.py "https://docs.openclaw.ai/" \
  --crawl --merge --toc \
  --docs-preset mintlify \
  --merge-output output/openclaw-clean.md
```

### Phase 2 自动识别（高置信度时）

```bash
python grab_web_to_md.py "https://docs.openclaw.ai/" \
  --crawl --merge --toc \
  --auto-detect \
  --merge-output output/openclaw-clean.md
```

### Phase 3-B 双版本输出（已实现）

```bash
python grab_web_to_md.py "https://docs.openclaw.ai/" \
  --crawl --merge --toc \
  --docs-preset mintlify \
  --merge-output output/openclaw-merged.md \
  --split-output output/openclaw-split/ \
  --download-images
```

**输出结构**：
```
output/
├── openclaw-merged.md          # 合并版（单文件，带全局目录）
├── openclaw-merged.assets/     # 图片目录（共享）
└── openclaw-split/             # 分文件版
    ├── INDEX.md                # 结构索引
    ├── Page-Title-1.md
    ├── Page-Title-2.md
    └── ...
```

## 实施计划（更新进度）

| 阶段 | 任务 | 优先级 | 状态 |
|------|------|--------|------|
| **Phase 1** | T1.1 `--strip-nav` | P0 | ✅ 完成 |
| **Phase 1** | T1.2 `--strip-page-toc` | P0 | ✅ 完成 |
| **Phase 1** | T1.3 `--exclude-selectors` | P0 | ✅ 完成 |
| **Phase 1** | T1.4 连续锚点列表检测 | P0 | ✅ 完成 |
| **Phase 1** | T1.5 可观测性输出 | P0 | ✅ 完成 |
| **Phase 1** | T1.6 单页模式支持 | P0 | ✅ 完成 |
| **Phase 2** | T2.1 多值 target 支持 | P1 | ✅ 完成 |
| **Phase 2** | T2.2 框架预设 | P1 | ✅ 完成 |
| **Phase 2** | T2.3 自动识别 | P1 | ✅ 完成 |
| **Phase 2** | T2.4 内容密度检测 | P1 | ✅ 完成 |
| **Phase 3-A** | T3.A1-A4 锚点冲突修复 | P0 | ✅ 完成 |
| **Phase 3-B** | T3.B1-B4 双版本输出 | P1 | ✅ 完成 |
| **Phase 3-C** | T3.C1-C2 重复块去重 | P2 | 📋 可选 |

## 验收标准（修订）

1. **Phase 1** ✅：OpenClaw 文档导出后，每页正文前不再包含重复导航/TOC
2. **Phase 1** ✅：输出日志包含命中规则与移除块数量
3. **Phase 2** ✅：预设与自动识别可降低手动配置成本
4. **Phase 3-A** ✅：合并版目录跳转正确，锚点冲突自动修复
5. **Phase 3-B** ✅：合并版与分文件版同时产出，共享 assets 目录
6. **回归检查**：对普通博客页导出不产生正文误删

## 风险与对策（新增）

- **误删正文风险**：默认关闭新特性；仅在"高链接密度 + 前部区域"触发
- **选择器能力不足**：明确"简化选择器"支持范围，避免误解
- **模板识别误判**：仅高置信度自动应用，其余只提示
- **锚点阈值语义**：`--anchor-list-threshold` 默认 0（关闭），预设模式自动启用 10

## 结论

方案可行，且在保持工具轻量化的前提下，能显著改善 docs/wiki 的可读性问题。**Phase 1/2/3-A/3-B 均已完成**，验证效果良好（OpenClaw 文档从 3.9MB 降至 1.7MB）。

**已实现功能汇总**：
- ✅ Phase 1：导航/目录剥离（`--strip-nav`、`--strip-page-toc`）
- ✅ Phase 2：智能正文定位（`--docs-preset`、`--auto-detect`）
- ✅ Phase 3-A：锚点冲突自动修复（`--warn-anchor-collisions`）
- ✅ Phase 3-B：双版本输出（`--split-output`）

**可选优化**（Phase 3-C）：重复块去重功能风险较高，降为可选实现。
