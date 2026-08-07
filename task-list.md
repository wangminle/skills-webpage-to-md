# 任务跟踪列表

记录本项目所有任务：代码 bug、bug 转需求、新增需求、需求调整、功能开发、代码审查、测试数据、文档维护、配置运维等。

> 说明：本文件是当前项目的任务清单。所有新增事项、状态变更和完成记录都应同步写入本文件。
> 字段说明：动作字段只允许以下 8 个固定枚举：修复、开发、优化、调整、规划、检查、文档、运维。
> 时间说明：发现时间和完成时间分开记录，格式为 YYYY-MM-DD HH:MM，使用机器本地时区的 24 小时制时间；未完成事项的完成时间填 -。
> 状态说明：Bug 未完成用待修复，通用未完成用待办（或待开发），进行中/已完成/已修复/已关闭/已解决按语义选用；条目互引用 [[BUG-001]] 语法。
> 归并规则：审计、复核、核查、审查、验证、评估统一记为“检查”；重构、清理统一记为“优化”；方案、梳理统一记为“规划”；记录类文档事项统一记为“文档”。
> 严重度说明：P0 崩溃或数据损坏；P1 功能错误；P2 边界隐患。备注中标注"存疑"的为未经充分复现确认、可能有意为之的项。

## 代码 Bug

| ID | 动作 | 问题描述 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| BUG-001 | 修复 | 【P1】clean_wechat_noise 误删正文中出现的「取消/允许/Share」等同名词 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：噪音清理正则用子串匹配而非行锚定。建议：改为 ^...$ 行锚定，仅清理独占整行的按钮文本。影响文件：markdown_conv.py:883。⚠️ 状态更正：2026-08-07 12:29 复核，当前代码仍可复现（"该设置允许用户取消订阅"→"该设置用户订阅"），此前"已修复"记录有误；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-002 | 修复 | 【P1】合并模式标题降级正则破坏代码块内的 # 注释行 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：re.sub 对全文执行，未排除代码围栏。建议：新增围栏感知的分段处理，所有后处理在代码围栏外执行。影响文件：markdown_conv.py:828/831, output.py:307。⚠️ 状态更正：2026-08-07 12:29 复核仍可复现；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-003 | 修复 | 【P1】目标容器内 void 元素（br/img/hr）导致深度计数器永不归零，泄漏后续页脚内容 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：_TargetSectionExtractor 对 void 元素也 +1 深度但无对应 -1。建议：void 元素不计入深度。影响文件：extractors.py:834。⚠️ 状态更正：2026-08-07 12:29 复核仍可复现；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-004 | 修复 | 【P1】<a> 标签内仅含图片时输出回退裸链接 [url](url) | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：handle_endtag 的 <a> 分支在无文本时回退到 href 作为文本。建议：新增 a_has_image 标志，图片行内输出且不再追加回退链接。影响文件：markdown_conv.py:696-722。⚠️ 状态更正：2026-08-07 12:29 复核仍可复现；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-005 | 修复 | 【P1】Editor.js header 的 level 为非数字字符串时 int() 崩溃 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：int(data.get('level', 2)) 未做类型保护，且单页模式三处调用点无 try，整个工具 traceback。建议：改用 _safe_int 带默认值兜底。影响文件：ssr_extract.py:434。⚠️ 状态更正：2026-08-07 12:29 复核仍可复现；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-006 | 修复 | 【P1】Quill Delta ops 的 header/list 为行级属性，旧实现按行内属性处理导致标题和列表丢失 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：_convert_quill_ops 未按 \n 切分行级块格式；真实数据输出空 h2、标题留在段落里。建议：按 \n 切行并正确应用 header/list/blockquote/code-block 行级属性。影响文件：ssr_extract.py:612。⚠️ 状态更正：2026-08-07 12:29 复核仍可复现；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判；✅ 2026-08-07 12:42 补修无尾部 \\n 时标题双重 append 残留 |
| BUG-007 | 修复 | 【P1】Notion table + table_row block 未渲染为 HTML 表格，数据静默丢失 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：_render_single_block 未处理 table/table_row 类型，单元格文本在 properties 的列 ID 键下。建议：新增 _render_notion_table()，遍历 table_row 渲染 <table>/<tr>/<td>/<th>。影响文件：notion.py:470。⚠️ 状态更正：2026-08-07 12:29 复核确认无 table_row 分支；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-008 | 修复 | 【P1】JS 反爬检测中 'challenge'/'please wait' 关键词过于宽泛，误判 LeetCode 周赛等正常页面为反爬 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：challenge 作为子串匹配命中所有含 Challenge 的标题，且单信号即判 high。建议：移除宽泛关键词，只保留 Cloudflare/Akamai 等厂商特征和 'just a moment' 等特定标题；标题单信号最高 medium。影响文件：security.py:115-126,171-173。⚠️ 状态更正：2026-08-07 12:29 复核仍可复现（"Weekly Challenge 314" → high）；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-009 | 修复 | 【P1】批量模式 --browser-fetch 下 noscript 残留触发 JS 反爬检测，导致页面被误拦截 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已修复 | 根因：browser-fetch 已通过挑战，但渲染后 DOM 残留 noscript 触发误判；单页模式与爬取索引页均已跳过该检测，仅批量遗漏。建议：browser_fetch=True 时跳过 detect_js_challenge()。影响文件：grab_web_to_md.py:186。⚠️ 状态更正：2026-08-07 12:29 复核代码路径确认问题仍在；✅ 2026-08-07 12:35 运行时验证通过：工作区代码已包含修复，此前'复核仍可复现'系检查 HEAD 版本所致误判 |
| BUG-010 | 修复 | 【P1】纯 CSR 页面（如阿里云百炼控制台）HTML 中无 JSON 数据，内容由 API 动态加载，无法抓取 | 2026-08-07 12:25 | - | 待修复 | 触发条件：HTML 中无 __NEXT_DATA__/_ROUTER_DATA 等 SSR 数据。当前兜底：--local-html 手动保存。🆕 新方案（2026-08-07 调研）：Chrome --headless --dump-dom --virtual-time-budget=N 零依赖内置兜底，见 [[DEV-009]]；更强需求可用 nodriver 可选依赖，见 [[DEV-008]]。来源：ssr-extract-engineering-retrospective-20260210.md §7.3 |
| BUG-011 | 修复 | 【P2】auto-detect 置信度阈值单页(0.6)/批量(0.5)不一致，同页两模式行为不同 | 2026-08-07 12:29 | 2026-08-07 12:38 | 已修复 | 建议：统一为同一常量（0.6）。影响文件：grab_web_to_md.py:258,1402。✅ 2026-08-07 12:38 磁盘核查：已有 `AUTO_DETECT_CONFIDENCE_THRESHOLD=0.6`，单页/批量共用 |
| BUG-012 | 修复 | 【P2】--local-html 固定按 UTF-8 读取，Shift_JIS/GB2312 存档页静默乱码 | 2026-08-07 12:29 | 2026-08-07 12:38 | 已修复 | 建议：二进制读取后复用 http_client._detect_meta_charset 检测再 decode。影响文件：grab_web_to_md.py:1173。✅ 2026-08-07 12:38 磁盘核查：已改用 `read_local_html_file`（rb + meta charset） |
| BUG-013 | 修复 | 【P2】合并模式"文件已存在"检查在全部抓取和图片下载完成后才执行，浪费抓取配额 | 2026-08-07 12:29 | 2026-08-07 17:30 | 已修复 | ✅ 2026-08-07 17:30 抓取前提前检查 merge 输出文件是否存在，原检查保留作为安全兜底。影响文件：grab_web_to_md.py |
| BUG-014 | 修复 | 【P2】批量非合并模式 --validate 的 assets 目录统计恒为 0，输出误导 | 2026-08-07 12:29 | 2026-08-07 17:30 | 已修复 | ✅ 2026-08-07 17:30 非合并分支改为使用共享 assets 目录（output_dir/assets）。影响文件：grab_web_to_md.py |
| BUG-015 | 修复 | 【P2】图片下载失败（未开 --best-effort-images）以未捕获异常 traceback 退出 | 2026-08-07 12:29 | 2026-08-07 17:30 | 已修复 | ✅ 2026-08-07 17:30 三处 download_images/batch_download_images 调用均包 try/except，打印错误后返回 EXIT_ERROR。影响文件：grab_web_to_md.py |
| BUG-016 | 修复 | 【P2】--skip-errors 下存在失败页面仍返回退出码 0，CI 无法感知部分失败（存疑，可能有意） | 2026-08-07 12:29 | 2026-08-07 17:30 | 已修复 | ✅ 2026-08-07 17:30 新增 EXIT_PARTIAL_FAILURE=5，--skip-errors 下 fail_count>0 时返回非零退出码。影响文件：grab_web_to_md.py |
| BUG-017 | 修复 | 【P2】批量独立文件模式静默改名 _1 且 INDEX.md 无条件覆盖，与单页/合并模式冲突处理语义不一致 | 2026-08-07 12:29 | 2026-08-07 17:15 | 已修复 | ✅ 2026-08-07 17:15 改名时打印警告；INDEX.md 覆盖前检查并警告。影响文件：output.py, grab_web_to_md.py |
| BUG-018 | 修复 | 【P2】--max-workers 0 或负数直接抛 ValueError traceback | 2026-08-07 12:29 | 2026-08-07 17:15 | 已修复 | ✅ 2026-08-07 17:15 argparse 后校验 max_workers >= 1，否则 ap.error。影响文件：grab_web_to_md.py |
| BUG-019 | 修复 | 【P2】非法 --crawl-pattern 正则导致未捕获 re.error traceback | 2026-08-07 12:29 | 2026-08-07 17:15 | 已修复 | ✅ 2026-08-07 17:15 调用前预编译校验 re.error，失败给友好提示。影响文件：grab_web_to_md.py |
| BUG-020 | 修复 | 【P2】strip_anchor_lists 的"孤儿标题"全局清理误删与被删列表无关的标题 | 2026-08-07 12:29 | 2026-08-07 17:15 | 已修复 | ✅ 2026-08-07 17:15 磁盘核查+运行验证：orphan_title_pattern 已用 \Z 限制为仅删文档末尾标题。影响文件：extractors.py |
| BUG-021 | 修复 | 【P2】懒加载图 src="data:" 占位时 data-src 真实图片被丢弃，图片漏采 | 2026-08-07 12:29 | 2026-08-07 17:10 | 已修复 | ✅ 2026-08-07 17:10 磁盘核查+运行验证：ImageURLCollector 已过滤 data: 占位并回退到 data-src/data-original/data-lazy-src。影响文件：extractors.py |
| BUG-022 | 修复 | 【P2】stripper/extractor 对 script/style CDATA 内容做实体转义，污染 math/tex 公式 | 2026-08-07 12:29 | 2026-08-07 17:10 | 已修复 | ✅ 2026-08-07 17:10 HtmlStripper 和 _TargetSectionExtractor 增加 _raw_content_depth 跟踪，script/style 内 data 不转义。影响文件：extractors.py |
| BUG-023 | 修复 | 【P2】strip_anchor_lists 不识代码围栏，误删 fenced code block 内示例链接列表 | 2026-08-07 12:29 | 2026-08-07 17:10 | 已修复 | ✅ 2026-08-07 17:10 新增 _apply_regex_outside_fences 辅助函数，所有 re.sub 调用改为围栏外执行。影响文件：extractors.py |
| BUG-024 | 修复 | 【P2】redact_url 不剥离 URL 中的 userinfo（用户名/密码） | 2026-08-07 12:29 | 2026-08-07 17:00 | 已修复 | ✅ 2026-08-07 17:00 磁盘核查+运行验证：redact_url 已重建 netloc 剥离 userinfo。影响文件：security.py |
| BUG-025 | 修复 | 【P2】爬取模式对 fragment URL 不去重，同一页面重复抓取 | 2026-08-07 12:29 | 2026-08-07 17:00 | 已修复 | ✅ 2026-08-07 17:00 去重前先 urldefrag 剥离 fragment。影响文件：extractors.py, grab_web_to_md.py |
| BUG-026 | 修复 | 【P2】html_text_len 把 script/style 文本计入正文长度，SPA 空壳页预警失效 | 2026-08-07 12:29 | 2026-08-07 17:00 | 已修复 | ✅ 2026-08-07 17:00 _TextLenExtractor 跟踪 script/style 深度跳过其 data。影响文件：extractors.py |
| BUG-027 | 修复 | 【P2】validate_markdown 对带 title / 尖括号形式的图片引用误报 missing | 2026-08-07 12:29 | 2026-08-07 17:00 | 已修复 | ✅ 2026-08-07 17:00 剥离可选 title 和 <> 包裹。影响文件：security.py |
| BUG-028 | 修复 | 【P2】read_urls_file 遇非 UTF-8 文件 UnicodeDecodeError 崩溃 | 2026-08-07 12:29 | 2026-08-07 17:00 | 已修复 | ✅ 2026-08-07 17:00 open 增加 errors="replace"。影响文件：extractors.py |
| BUG-029 | 修复 | 【P2】未闭合的 katex span 吞掉其后全部文本 | 2026-08-07 12:29 | 2026-08-07 17:00 | 已修复 | ✅ 2026-08-07 17:00 遇块级标签起始时强制归零 katex_depth。影响文件：markdown_conv.py |
| BUG-030 | 修复 | 【P2】_append_text 空格启发式割裂 CJK 文本（你好<span>世界</span>->你好 世界）（存疑：英文站点是有意设计） | 2026-08-07 12:29 | 2026-08-07 16:50 | 已修复 | ✅ 2026-08-07 16:50 新增 _is_cjk() 辅助函数，两侧均为 CJK 时不插入空格。影响文件：markdown_conv.py |
| BUG-031 | 修复 | 【P2】rewrite_internal_links 改写代码围栏内的链接，破坏代码示例 | 2026-08-07 12:29 | 2026-08-07 16:50 | 已修复 | ✅ 2026-08-07 16:50 按代码围栏分段，仅处理围栏外链接。影响文件：markdown_conv.py |
| BUG-032 | 修复 | 【P2】<ol start="5"> 被忽略，编号始终从 1 开始 | 2026-08-07 12:29 | 2026-08-07 16:50 | 已修复 | ✅ 2026-08-07 16:50 读取 ol start 属性初始化编号。影响文件：markdown_conv.py |
| BUG-033 | 修复 | 【P2】data: URI 图片被原样内联进 Markdown（可达数 MB），与 ImageURLCollector 跳过行为不一致 | 2026-08-07 12:29 | 2026-08-07 16:50 | 已修复 | ✅ 2026-08-07 16:50 转换器跳过 data: URI 图片。影响文件：markdown_conv.py |
| BUG-034 | 修复 | 【P2】Windows 跨盘符 os.path.relpath 抛 ValueError，图片下载中断 | 2026-08-07 12:29 | 2026-08-07 16:45 | 已修复 | ✅ 2026-08-07 16:45 try/except ValueError 回退为绝对路径。影响文件：images.py |
| BUG-035 | 修复 | 【P2】_attrs_to_str 协议过滤可被嵌入空白/控制字符（java\tscript:）绕过；data:text/html href 未过滤 | 2026-08-07 12:29 | 2026-08-07 18:30 | 已修复 | ✅ 2026-08-07 16:45 _attrs_to_str 已修复（keep_html 表格路径）；⚠️ 2026-08-07 18:25 运行时验证发现正常 <a> 标签路径（handle_starttag/handle_endtag）仍绕过；✅ 2026-08-07 18:30 新增 _is_unsafe_link_url()，<a> 和表格内 <a> 开始标签处统一拦截 javascript/vbscript/file/data: 协议（含控制字符变体）。影响文件：markdown_conv.py |
| BUG-036 | 修复 | 【P2】sniff_ext SVG 启发式可能把开头含 <svg 的 HTML 错误页存成 .svg | 2026-08-07 12:29 | 2026-08-07 18:30 | 已修复 | ✅ 2026-08-07 16:45 移除宽松检查；⚠️ 2026-08-07 18:25 运行时验证发现 startswith(b"<svg") 仍误判内联 SVG 的 HTML；✅ 2026-08-07 18:30 SVG 检测增加 HTML 标志排除（<html>/<body>/<head>/<!doctype html>），含 HTML 标志的内容不判为 SVG。影响文件：images.py |
| BUG-037 | 修复 | 【P2】空响应体（200 但 0 字节）落盘为空文件并写入映射 | 2026-08-07 12:29 | 2026-08-07 16:40 | 已修复 | ✅ 2026-08-07 16:40 size==0 时删除空文件并抛 RuntimeError 走失败路径。影响文件：images.py |
| BUG-038 | 修复 | 【P2】富文本表格 colspan/rowspan 为字符串时 TypeError，经 Next.js 路径可崩溃 | 2026-08-07 12:29 | 2026-08-07 16:40 | 已修复 | ✅ 2026-08-07 16:40 colspan/rowspan 用 _safe_int 转换。影响文件：ssr_extract.py |
| BUG-039 | 修复 | 【P2】SSR 文本节点 text 非字符串时 AttributeError；content 为 dict 时迭代键名输出垃圾 | 2026-08-07 12:29 | 2026-08-07 16:40 | 已修复 | ✅ 2026-08-07 16:40 text 非 str 时转 str；children 非 list 时置空。影响文件：ssr_extract.py |
| BUG-040 | 修复 | 【P2】SSR 兜底解析策略 2 正则无 DOTALL，漏掉格式化多行 JSON；同行多个赋值只试第一个 | 2026-08-07 12:29 | 2026-08-07 16:40 | 已修复 | ✅ 2026-08-07 16:40 正则放宽为 =\s*(\{) 后交给 _extract_json_object_str 括号扫描。影响文件：ssr_extract.py |
| BUG-041 | 修复 | 【P2】Editor.js 清洗可被 href=" javascript:..."（前导空白）和 <img/onerror=...>（/ 分隔属性）绕过 | 2026-08-07 12:29 | 2026-08-07 16:35 | 已修复 | ✅ 2026-08-07 16:35 _EVENT_ATTR_RE 前缀改为 [\s/]+ 修复 <img/onerror=...> 绕过；href 前导空白由另一 agent 修复。影响文件：ssr_extract.py |
| BUG-042 | 修复 | 【P2】Notion 无法获取的 block id 导致 20 轮无效重复请求（含 sleep），missing 列表不去重 | 2026-08-07 12:29 | 2026-08-07 16:35 | 已修复 | ✅ 2026-08-07 16:35 missing 去重 + 连续一轮无进展提前退出。影响文件：notion.py |
| BUG-043 | 修复 | 【P2】火山引擎 Content 分节按字符串排序，数字键超 9 节正文乱序（存疑：无样本确认键形式） | 2026-08-07 12:29 | 2026-08-07 16:35 | 已修复 | ✅ 2026-08-07 16:35 键全为数字时按 int 排序，否则保持 JSON 原始顺序。影响文件：ssr_extract.py |
| BUG-044 | 修复 | 【P2】非法端口 URL（:99999/:abc）使合并输出阶段 parsed.port 抛 ValueError，整批成果丢失 | 2026-08-07 12:29 | 2026-08-07 16:35 | 已修复 | ✅ 2026-08-07 16:35 try/except ValueError 包裹 parsed.port 访问。影响文件：output.py |
| BUG-045 | 修复 | 【P2】browser-fetch Phase 2 subprocess.run(text=True) 未指定编码，中文 Windows 下乱码或 UnicodeDecodeError | 2026-08-07 12:29 | 2026-08-07 16:30 | 已修复 | ✅ 2026-08-07 16:30 已添加 encoding="utf-8", errors="replace"。影响文件：http_client.py |
| BUG-046 | 修复 | 【P2】browser-fetch Phase 1 抛异常时临时 profile 目录泄漏（可达数十 MB） | 2026-08-07 12:29 | 2026-08-07 16:30 | 已修复 | ✅ 2026-08-07 16:30 rmtree 提升为最外层 try/finally 覆盖 Phase 1+2。影响文件：http_client.py |
| BUG-047 | 修复 | 【P2】4xx 与"响应过大"等确定性失败被无意义重试 3 次，浪费流量拖慢批量 | 2026-08-07 12:29 | 2026-08-07 16:50 | 已修复 | ✅ 2026-08-07 16:50 4xx（除 429）和 RuntimeError 直接 raise 不进重试。影响文件：http_client.py |
| BUG-048 | 修复 | 【P2】cookies.txt 的 #HttpOnly_ 条目被当注释静默丢弃，登录态加载了仍 401 且无提示 | 2026-08-07 12:29 | 2026-08-07 12:42 | 已修复 | 建议：剥掉 #HttpOnly_ 前缀后按正常行解析。影响文件：http_client.py。✅ 2026-08-07 12:42 磁盘核查+复现：`_parse_cookies_file` 已识别 `#HttpOnly_` 并正确加载 session cookie |
| BUG-049 | 修复 | 【P2】文件名清洗不排除 Windows 保留名（CON/NUL/PRN/AUX/COM1-9/LPT1-9） | 2026-08-07 12:29 | 2026-08-07 16:30 | 已修复 | ✅ 2026-08-07 16:30 _sanitize_filename_part 增加保留名检测，命中时加下划线前缀。影响文件：output.py |
| BUG-050 | 修复 | 【P2】meta charset 正则不容忍 = 两侧空白（<meta charset = "gb2312"> 漏检） | 2026-08-07 12:29 | 2026-08-07 16:50 | 已修复 | ✅ 2026-08-07 16:50 charset 正则改为 charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)。影响文件：http_client.py |
| BUG-051 | 修复 | 【P2】单页模式 HTML 超限的 RuntimeError 未被捕获，直接 traceback（批量模式无此问题） | 2026-08-07 12:29 | 2026-08-07 16:50 | 已修复 | ✅ 2026-08-07 16:50 _fetch_page_html 增加 except RuntimeError 友好提示。影响文件：grab_web_to_md.py |

## 调整事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |

## 检查事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| CHK-001 | 检查 | 全量代码审查：6 路并行逐行审查 10 个源码文件 + 文档一致性核对 + 关键发现人工复核 | 2026-08-07 12:29 | 2026-08-07 12:29 | 已完成 | 测试基线 146 项全绿。产出：复核确认 9 个 P1（[[BUG-001]]~[[BUG-009]]，此前误记为已修复）+ 新增 41 个 P2（[[BUG-011]]~[[BUG-051]]）。审查范围：grab_web_to_md.py、extractors.py、security.py、markdown_conv.py、images.py、ssr_extract.py、notion.py、http_client.py、output.py、models.py、SKILL.md、full-guide.md |
| CHK-002 | 检查 | 磁盘代码核查 [[BUG-011]]~[[BUG-030]]（不以 git HEAD 为准） | 2026-08-07 12:38 | 2026-08-07 12:38 | 已完成 | 结论：2 项 FIXED_IN_WT（011/012→已修复）；18 项 CONFIRMED 仍存在（013–030，其中 016/030 仍标存疑）；0 项 FALSE_POSITIVE |
| CHK-003 | 检查 | 会话全量 bug 存在性复核：[[BUG-001]]~[[BUG-051]] + [[OPT-001]]（磁盘代码+最小复现） | 2026-08-07 12:42 | 2026-08-07 12:42 | 已完成 | 结论：001–009/OPT-001 修复有效；011/012/048 工作区已修；041 部分修（js:空白已清、img/onerror 仍在）；010 能力缺口属实；013–040/042–047/049–051 均仍可复现（016/030/043 存疑合理）；0 误报 |
| CHK-004 | 检查 | task-list 全量 bug 复核 + 收尾修复：逐条核查 [[BUG-013]]/014/015/016/030/041/023/047/050/051 当前磁盘代码状态 | 2026-08-07 18:00 | 2026-08-07 18:10 | 已完成 | 结论：015/016/030/041/023/047/050/051 共 8 项工作区代码已修复（此前 CHK-003 后由后续会话修复）；013 本次补修（抓取前提前检查合并输出存在性）；014 工作区已修复（validate 用共享 output_dir/assets）。191 项测试全绿（含新增 BUG-013/030 回归测试）。唯一剩余 BUG-010 为能力缺口（纯 CSR 页面），待 [[DEV-009]] 配合。 |
| CHK-005 | 检查 | task-list 全量 bug 运行时复现验证：BUG-022~040/042~044/049（最小复现脚本，非代码阅读） | 2026-08-07 18:20 | 2026-08-07 18:35 | 已完成 | 结论：022/025/026/027/028/029/031/032/033/037/038/039/040/042/043/044/049 共 17 项运行时确认已修复；❌ [[BUG-035]] 残留：正常 <a> 路径（非 keep_html 表格）仍放行 javascript:/控制字符变体/data:text/html；❌ [[BUG-036]] 残留：startswith(b"<svg") 仍误判内联 SVG 的 HTML 错误页。两项均于本次修复（见 BUG-035/036 备注）。199 项测试全绿。 |

## 测试数据

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |

## 文档维护

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| DOC-001 | 文档 | SKILL.md 冒烟测试第 4 步（pytest ../../tests/）仅源码仓库布局成立，skill 安装到 ~/.claude/skills/ 后必失败 | 2026-08-07 12:29 | 2026-08-07 17:35 | 已完成 | ✅ 2026-08-07 17:35 注明"仅源码仓库可用"。影响文件：SKILL.md |

## 功能开发

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| DEV-001 | 开发 | 更多 SSR 框架适配（Nuxt.js / Remix 等） | 2026-08-07 12:25 | - | 待开发 | 🆕 2026-08-07 调研结论（[[RES-003]]）：Nuxt __NUXT_DATA__（devalue 格式）最易，~30 行 Python 解引用器即可；Next.js App Router RSC flight（__next_f.push）中等，可参考 njsparser；Remix/RR7 turbo-stream 建议只解析首个同步 chunk。建议排期顺序 Nuxt → RSC → Remix。来源：ssr-extract-engineering-retrospective-20260210.md §7.3 |
| DEV-002 | 开发 | 语雀公开知识库适配（yuque.py） | 2026-08-07 12:25 | - | 待开发 | URL 识别：yuque.com/{owner}/{repo}/{slug}。🆕 2026-08-07 调研确认（[[RES-003]]）：有免鉴权 Markdown API（GET /api/docs/{slug}?book_id={id}&mode=markdown 直接返回 MD 源码），公开页仍可纯 HTTP 抓取。参考：burpheart/yuque-crawl。预估 ~300 行。来源：[[RES-001]] |
| DEV-003 | 开发 | Coda 公开发布页面适配（coda.py） | 2026-08-07 12:25 | - | 待开发 | URL 识别：coda.io/d/xxx。抓取方式：公开页面 HTML + 可选 API Key。预估 ~250 行。来源：[[RES-001]] |
| DEV-004 | 开发 | FlowUs 息流公开页面适配（flowus.py） | 2026-08-07 12:25 | - | 待开发 | URL 识别：flowus.cn/xxx 或 {space}.flowus.cn。抓取方式：开发者 API，Block 模型类似 Notion。预估 ~300 行。来源：[[RES-001]] |
| DEV-005 | 开发 | Craft 公开页面适配（craft.py） | 2026-08-07 12:26 | - | 待开发 | URL 识别：craft.do/s/xxx 或 craft.me/s/xxx。抓取方式：API 直接返回 Markdown（Accept: text/markdown）。预估 ~100 行。来源：[[RES-001]] |
| DEV-006 | 开发 | HackMD 公开笔记适配（hackmd.py） | 2026-08-07 12:26 | - | 待开发 | URL 识别：hackmd.io/xxx 或 hackmd.io/@user/xxx。抓取方式：API 直接返回 Markdown。预估 ~80 行。来源：[[RES-001]] |
| DEV-007 | 开发 | curl_cffi 可选依赖（如 --impersonate 参数），对抗知乎类 TLS 指纹反爬 | 2026-08-07 12:29 | - | 待开发 | 🆕 来源：[[RES-002]]。纯 pip wheel（2-13MB），API 与 requests 几乎 drop-in（impersonate="chrome"），2026-07 实测 31 个反爬目标 26 OK。装了走 curl_cffi，没装回退 requests，保持 requests 唯一硬依赖。对知乎无直接实测报告，仅间接证据 |
| DEV-008 | 开发 | nodriver 作为 --browser-fetch 可选增强层，提升 Cloudflare Turnstile 通过率 | 2026-08-07 12:29 | - | 待开发 | 🆕 来源：[[RES-002]]。直接 WebSocket 驱动系统 Chrome（无浏览器下载），2026-07 实测唯一 28 OK / 0 blocked 的工具。注意 AGPL-3.0 许可。同类候选：DrissionPage（国内生态友好）。headless=new 仍可被检测，无免费轻量稳定方案 |
| DEV-009 | 开发 | Chrome --headless --dump-dom --virtual-time-budget=N 内置 CSR 渲染兜底 | 2026-08-07 12:29 | - | 待开发 | 🆕 来源：[[RES-002]]。零新依赖（复用现有系统 Chrome 调用），覆盖部分纯 CSR 页面；短板是不等待异步 XHR。关联 [[BUG-010]] |
| DEV-010 | 开发 | 微信"环境异常"验证墙检测与提示分支 | 2026-08-07 12:29 | - | 待开发 | 🆕 来源：[[RES-003]]。2025-2026 年非微信环境访问 mp.weixin.qq.com 概率性触发"当前环境异常"人机验证墙，抓取会拿到验证墙 HTML 而非正文。建议：检测验证墙特征 → 提示用户浏览器验证后导 cookie 或 --local-html |
| DEV-011 | 开发 | Notion 内部 API 失败兜底提示增强 | 2026-08-07 12:29 | - | 待开发 | 🆕 来源：[[RES-002]]。2025 年起 loadPageChunk 出现间歇 400 个案（react-notion-x #675），接口不稳定但未全面封死。建议：API 失败时提示"接口可能变动，可改用 --local-html 或官方 API token" |

## 配置运维

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |

## 规划事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |

## 优化事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| OPT-001 | 优化 | 超大 HTML（2MB+）的 <script> 扫描阶段 .*? 正则回溯性能问题 | 2026-08-07 12:25 | 2026-08-07 12:00 | 已完成 | 已实现：_iter_script_bodies() 用 str.find（_find_ci 大小写不敏感）替代 .*? 正则，docstring 注明不会触发灾难性回溯。✅ 2026-08-07 12:35 运行时确认工作区代码已包含此优化。来源：ssr-extract-engineering-retrospective-20260210.md §7.3。影响文件：ssr_extract.py |
| OPT-002 | 优化 | Phase 3-C: 合并模式重复块 hash 去重（--dedup-blocks） | 2026-08-07 12:25 | - | 待办 | 方案：仅对高链接密度块或跨页完全重复块生效，默认关闭。风险：误删正文概率较大，属于锦上添花。来源：docs-wiki-export-optimization-v2.1.md |
| OPT-003 | 优化 | raw_table_mode 是不可达死代码，建议删除或接上触发条件 | 2026-08-07 12:29 | 2026-08-07 17:35 | 已完成 | ✅ 2026-08-07 17:35 删除 raw_table_mode/raw_table_buf/raw_table_depth 全部代码（初始化+3处分支）。影响文件：markdown_conv.py |

## 调研事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| RES-001 | 规划 | 平台公开分享机制调研：梳理国内外文档/笔记/知识库平台的公开链接与匿名访问能力 | 2026-08-07 12:25 | 2026-03-17 18:00 | 已完成 | 结论：A 类值得专用适配 5 个平台（语雀/Coda/FlowUs/Craft/HackMD）；B 类暂缓 6 个（飞书/腾讯文档/石墨/金山/wolai/Evernote）；C 类已覆盖。来源：docs/platform-support-analysis-20260317.md |
| RES-002 | 检查 | 反爬绕过新方案调研（curl_cffi / nodriver / patchright / camoufox / dump-dom / Notion API 现状） | 2026-08-07 12:29 | 2026-08-07 12:29 | 已完成 | 结论：curl_cffi 成熟轻量首推为可选依赖（→[[DEV-007]]）；nodriver 驱动系统 Chrome 过 Turnstile 最强但 AGPL（→[[DEV-008]]）；dump-dom 零依赖覆盖部分 CSR（→[[DEV-009]]）；patchright/camoufox/undetected-chromedriver 不建议；Notion 内部 API 2025 年起间歇 400 但未封死（→[[DEV-011]]）。未查到 curl_cffi×知乎直接实测 |
| RES-003 | 检查 | SSR 新框架与平台变化调研（RSC flight / Nuxt devalue / Remix turbo-stream / 微信反爬 / 语雀） | 2026-08-07 12:29 | 2026-08-07 12:29 | 已完成 | 结论：Nuxt devalue 最易（→[[DEV-001]]）；Next.js RSC flight 中等（njsparser 可参考）；Remix turbo-stream 只解析首段；微信 2025-2026 新增"环境异常"验证墙（→[[DEV-010]]）；语雀有免鉴权 Markdown API（→[[DEV-002]]） |

## 统计摘要

| 分类 | 总数 | 已完成 | 待开发/待修复 | 完成率 |
| --- | --- | --- | --- | --- |
| 代码 Bug | 51 | 50 | 1 | 98% |
| 调整事项 | 0 | 0 | 0 | 0% |
| 检查事项 | 5 | 5 | 0 | 100% |
| 测试数据 | 0 | 0 | 0 | 0% |
<<<<<<< HEAD
| 文档维护 | 1 | 1 | 0 | 100% |
| 功能开发 | 11 | 0 | 11 | 0% |
| 配置运维 | 0 | 0 | 0 | 0% |
| 规划事项 | 0 | 0 | 0 | 0% |
| 优化事项 | 3 | 2 | 1 | 67% |
| 调研事项 | 3 | 3 | 0 | 100% |
| **总计** | 74 | 61 | 13 | 82% |
=======
| 文档维护 | 1 | 0 | 1 | 0% |
| 功能开发 | 11 | 0 | 11 | 0% |
| 配置运维 | 0 | 0 | 0 | 0% |
| 规划事项 | 0 | 0 | 0 | 0% |
| 优化事项 | 3 | 1 | 2 | 33% |
| 调研事项 | 3 | 3 | 0 | 100% |
| **总计** | 74 | 59 | 15 | 80% |
>>>>>>> 21b1e5e918bd02d7a0bf5640c46fb146ce4c60b2
