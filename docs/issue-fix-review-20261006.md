# 最新 issue 与本地修复审查（2026-10-06）

> **最新修复验证（2026-10-08，CHK-015）：BUG-067～069 已在当前工作区修复，全量 295 项测试通过。新增 13 项测试在修复前工作区快照中出现 21 个断言失败、0 个运行错误，确认检出能力。BUG-059/061/065/066 的既有回归继续通过。详见文末「Cookie 边界补修与验证」。下面保留此前审查和补修的历史记录。**

结论：4 个 GitHub issue 的原始缺陷在当前工作区均已得到定向修复，但仍有 5 个可复现的相关遗漏。建议补修后再认定整个处理链路已完整解决。此次仅审查、复现并登记待修复项，未修改实现代码，也未提交、推送或关闭 issue。

## 审查范围与版本

- GitHub 仓库：wangminle/skills-webpage-to-md；查询全部状态、上限 100 条，实际返回 4 条，全部 OPEN，均创建于 2026-10-06。
- 本地 HEAD 与远端 main 均为 `159f88af94545fdcfd9cb4b13024a344e90fb0d3`（0.4.2）；远端无 PR。
- 修复位于未提交的 `extractors.py`、`markdown_conv.py`、`http_client.py` 和 `tests/test_grab_web_to_md.py`。
- 同时存在用户原有的 `.gitignore`、`task-list.md` 和 `.claude/` 改动，未改动或撤销其原有内容。
- 验证环境为本机 Python；未在 issue 报告中的 Windows 11 / Python 3.13 环境实跑。此次发现均通过离线函数调用或请求准备复现，未向外部站点发送 Cookie。

## 逐项结论

| Issue | 原始问题与修复判断 | 仍需继续处理 |
| --- | --- | --- |
| [#1 导航剥离失步](https://github.com/wangminle/skills-webpage-to-md/issues/1) | 换为实际支持的 `.menu` 或 `nav` 选择器后，省略导航内部的 li/p 结束标签已不会吞掉正文。标签栈修复方向正确。原 issue 使用 `nav.menu`，此复合选择器实际上不受支持，原样调用移除数为 0，报告的选择器与输出不一致。 | 被剥离元素自身省略结束标签、表格中多层隐式闭合仍失步，见 BUG-059/060。 |
| [#2 目标正文截断或超采](https://github.com/wangminle/skills-webpage-to-md/issues/2) | 原始游离 p 结束标签与 div 内省略 li 结束标签两种复现均已通过，简单目标 p 被后续同名元素隐式闭合也通过。 | 目标自身省略结束标签时父元素闭合未结束采集，多层表格隐式闭合也有遗漏，见 BUG-059/060。 |
| [#3 围栏识别失步](https://github.com/wangminle/skills-webpage-to-md/issues/3) | 四个指定助手共用 `_FenceTracker`，已正确处理围栏字符、长度与结束行尾内容；原始混用波浪号、空行、标题和 LaTeX 复现通过。 | HTML 代码块生成器仍固定三反引号；链接改写另有一套未跟踪长度的实现，见 BUG-062/063。 |
| [#4 Cookie 发往任意域名](https://github.com/wangminle/skills-webpage-to-md/issues/4) | 返回 CookieJar 后，正常 `.example.com` Cookie 已不再发往无关域名；path、secure、expires 的实际请求过滤通过，HttpOnly 条目保留。 | Netscape 第二列 FALSE 被忽略，host-only Cookie 仍发往子域名；空 domain 行仍生成跨域 Cookie，见 BUG-061。 |

## 可复现的待修复问题

以下脚本片段均在仓库根目录通过 Python 运行。公共导入：

```python
import sys
sys.path.insert(0, "skills/webpage-to-md/scripts")
from webpage_to_md.extractors import strip_html_elements, extract_target_html
from webpage_to_md.markdown_conv import html_to_markdown, rewrite_internal_links
```

### BUG-059 / P1：父元素闭合不能结束省略自身结束标签的目标

关联 #1/#2。定位：`extractors.py:648-653`、`extractors.py:1074-1077`。

```python
html = '<ul><li id="content" class="drop">FIRST</ul><p>TAIL</p>'
print(strip_html_elements(html, [".drop"])[0])
print(extract_target_html(html, target_id="content", target_class=None))
```

实际结果：

```text
<ul>
<li id="content" class="drop">FIRST<p>TAIL</p>
```

剥离结果丢失正文 TAIL，提取结果错误包含 TAIL。这是合法的 li 结束标签省略方式：[HTML 标准](https://html.spec.whatwg.org/multipage/syntax.html#optional-tags)允许父元素没有后续内容时省略 li 结束标签。

根因：stripper 的 `min_index=root` 排除了父元素 ul，目标提取器的栈则根本不记录目标外的 ul，两者均把该父结束标签当作游离标签忽略。

建议：根据祖先上下文和元素作用域，在合法父元素结束时隐式闭合被剥离/提取的可选元素。父元素闭合后，剥离器还需保留相应的父结束标签。新增 li/p/td/tr/tbody 等目标自身省略结束标签的测试，并保留真正游离标签的反例。

### BUG-060 / P1：隐式闭合仅查看栈顶，表格行和单元格仍吞掉正文

关联 #1/#2。定位：`extractors.py:503-509`。

```python
html = '<table><tr id="content" class="drop"><td>FIRST<tr><td>SECOND</table><p>TAIL</p>'
print(strip_html_elements(html, [".drop"])[0])
print(extract_target_html(html, target_id="content", target_class=None))

html = '<table><tr><td id="content" class="drop"><p>FIRST<td>SECOND</tr></table><p>TAIL</p>'
print(strip_html_elements(html, [".drop"])[0])
print(extract_target_html(html, target_id="content", target_class=None))
```

实际结果：

```text
<table>
<tr id="content" class="drop"><td>FIRST<tr><td>SECOND<p>TAIL</p>
<table><tr>
<td id="content" class="drop"><p>FIRST<td>SECOND<p>TAIL</p>
```

剥离时 SECOND/TAIL 均消失，提取时误收 SECOND/TAIL。[HTML 标准](https://html.spec.whatwg.org/multipage/syntax.html#optional-tags)允许这些 tr/td/p 结束标签省略。

根因：新的 tr 只尝试闭合栈顶 tr，栈顶实际为 td；新的 td 只尝试闭合 td/th，栈顶实际为 p。因此未找到仍在栈中的待闭合祖先。

建议：实现有作用域限制的隐式闭合，先闭合 td/th 与其内部元素，再结束前一行；不能仅扩展栈顶标签集合，也不能无作用域地向下弹栈。回归必须包含嵌套表格，避免内层 tr/td 错误闭合外层元素。

### BUG-061 / P1：host-only Cookie 仍被发往子域名

关联 #4。定位：`http_client.py:419-434`。

```python
import os
import tempfile
import requests
from webpage_to_md.http_client import _parse_cookies_file

with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write("login.example.com\tFALSE\t/\tFALSE\t0\tsession\tSYNTHETIC_TOKEN\n")
    path = f.name
try:
    jar = _parse_cookies_file(path)
finally:
    os.unlink(path)
s = requests.Session()
s.cookies.update(jar)
for url in ["https://login.example.com/", "https://untrusted.login.example.com/"]:
    print(url, s.prepare_request(requests.Request("GET", url)).headers.get("Cookie"))
```

两次请求均带 `session=SYNTHETIC_TOKEN`。第二次应不带 Cookie：[curl Netscape 格式文档](https://curl.se/docs/http-cookies.html)明确第二列控制是否包含子域名。

根因：解析器把第二列读取为 `_flag` 后丢弃，`jar.set(domain=...)` 使用 requests 默认域匹配，无法保持 host-only 语义。

另一个输入加固遗漏：第一列为空、其余列完整的行仍被接受，生成 domain 为空的超级 Cookie，准备发往无关域名的请求时仍附带该 Cookie。此项只在无效 Cookie 文件输入下触发。

建议：保留 include-subdomains / host-only 语义，并在实际 Session 请求准备和发送路径执行限制，拒绝空 domain。不能只以 Cookie 对象字段正确为验收标准；必须验证 `Session.prepare_request`、会话克隆及重定向后的 Cookie 头。保留 TRUE 域 Cookie 对合法子域的访问，以及 path/secure/expires 过滤测试。

### BUG-062 / P1：代码块内的反引号行提前关闭生成围栏，代码仍被删除

关联 #3。定位：`markdown_conv.py:734-738`。

```python
print(repr(html_to_markdown(
    '<pre><code>```\n###\n</code></pre><h1>AFTER</h1>',
    base_url="https://example.com/", url_to_local={})))
```

实际输出的 Python 表示：

```python
'```\n```\n```\n\n# AFTER\n'
```

代码中的 `###` 仍被删除。此处 `_FenceTracker` 按规则工作，但生成器固定使用三反引号，代码中的同字符围栏提前结束代码块，使后处理误把其余代码当正文。[CommonMark](https://spec.commonmark.org/0.31.2/#fenced-code-blocks)规定同字符且长度不小于开启围栏的行可以结束代码块。

建议：根据代码正文中反引号串的最大长度动态选择更长的外层围栏，或选择不会冲突的另一种围栏；新增含三/四/更多反引号、空标题行、LaTeX 与尾随正文的完整转换断言。

### BUG-063 / P2：链接改写仍提前结束较长代码围栏

关联 #3 与历史 BUG-031。定位：`markdown_conv.py:1154-1176`。

```python
md = "````\n```\n[x](https://example.com/page)\n````\n"
print(repr(rewrite_internal_links(md, {"https://example.com/page": "anchor"})[0]))
```

实际结果：

```python
'````\n```\n[x](#anchor)\n````\n'
```

代码示例中的链接被改写。根因：链接改写仍使用自己的围栏检测，只检查同字符三连前缀，未跟踪开启长度，也未验证关闭行的尾部内容。建议统一使用 `_FenceTracker` 并验证围栏外链接仍正常改写。

## 验证结果与测试覆盖

执行：

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

结果：248 项通过，耗时 4.194 秒。旧测试中 3 处未关闭测试文件产生 ResourceWarning，未导致失败。测试全绿并未覆盖上述 5 项遗漏。

另运行 `git diff --check`：原有 `.gitignore:184-185` 的 CRLF 行尾被报告为尾部空白，未改动该文件；实现、测试及此次台账改动的定向空白检查通过。

将当前测试复制到 `git archive HEAD` 的独立临时目录后，分别运行此次新增的 4 个测试类，以确认测试能检出旧版本问题：

| 测试类 | 用例数 | 旧 HEAD 结果 | 当前工作区结果 |
| --- | --- | --- | --- |
| TestStripHtmlElementsOmittedEndTags | 5 | 3 失败、2 通过 | 全通过 |
| TestTargetExtractorOmittedEndTags | 3 | 3 失败 | 全通过 |
| TestFenceTrackerOutsideCode | 7 | 4 失败、3 通过 | 全通过 |
| TestCookiesFileDomainScope | 2 | 1 失败、1 错误 | 全通过 |

其中两项本应检出缺陷的测试在旧 HEAD 上也通过，需加强：

- `test_backtick_fence_shell_comment_not_demoted`：`assertIn("# 这是 shell 注释", md)` 在错误输出 `## 这是 shell 注释` 中仍成立；应比较完整输出或精确整行。
- `test_fence_length_gte_close_rule`：只检查短围栏之前的 `###`，没有检查短围栏之后的内容；应在短围栏后放入空标题行，并同时断言完整关闭围栏后的正文仍正常处理。

另外已直接验证：四助手原始输入、围栏关闭后的正文标题降级、较短围栏后的代码保持、Cookie 对无关域名/path 不匹配/HTTP secure/已过期的实际请求过滤。均符合预期。

## 后续处理建议

1. 优先补齐 Cookie host-only 限制（BUG-061）。
2. 一并修复 HTML 目标自身省略结束标签和表格多层闭合（BUG-059/060），共享规则但分别覆盖剥离与提取。
3. 修复生成围栏与链接改写（BUG-062/063），并加强上述两个不能检出旧缺陷的测试。
4. 验证后提交、推送修复，按实际完成范围更新 issue；当前远端代码仍不含这些本地修复。

历史台账的 BUG-010、BUG-052、BUG-053、BUG-054 仍为待修复；此次审查范围为 4 个最新 GitHub issue 与其相关处理链路，未重做全部历史审查。

## 补修进展（2026-10-06 23:46）

上表 5 项遗漏（BUG-059～063）已全部在本地工作区修复并通过验收，实现文件为 `extractors.py`、`http_client.py`、`markdown_conv.py`：

- **BUG-059**：新增 `_ENDTAG_IMPLIED_CLOSE` 映射与 `_endtag_closes_tag()`。剥离器在结束标签匹配子树根之外的祖先且会隐式闭合子树根时（如 `</ul>` 闭合省略 `</li>` 的 `li.drop`），先弹出整个剥离子树、再按正常流程输出该祖先的结束标签；目标提取器在游离结束标签隐式闭合目标根时结束采集（含 `</div>` 闭合省略 `</p>` 目标的 p 变体）。
- **BUG-060**：`_TagStack.pop_implied` 改用 `_IMPLIED_END_SCOPED_RULES` 作用域规则——自栈顶向下搜索最近的 tr/td/th/li/dt/dd 等目标元素并连同其上未闭合内容一起弹出，途中遇屏障元素（`table`/`ul`/`ol`/`tr` 等）立即放弃。嵌套表格的内层 tr/td 不会误闭合外层元素（已加回归用例）。
- **BUG-061**：`_parse_cookies_file` 生效第二列标志（TRUE 或点前缀 → 子域共享并归一化点前缀；FALSE → host-only，Cookie 打 `_host_only_domain` 标记），拒绝空 domain 行，domain 归一小写。host-only 语义由新增 `_HostOnlyCookieSession`（`_create_session` 已换用）在 `prepare_request` 与 `send`（重定向的递归出口）强制精确域名匹配后重建 Cookie 头；复查 session 创建点时另修复批量模式绕过点——`grab_web_to_md._clone_session` 原固定克隆为普通 `requests.Session`，现按源类型克隆。探测确认 http.cookiejar 默认策略对 version=0 Cookie 仅做域名后缀匹配，且 requests 2.34 的 `prepare_request`/`resolve_redirects` 均把 Cookie 合并进新建的默认策略 jar，故无法仅靠 Cookie 字段或自定义 policy 表达。已按验收标准走真实 `prepare_request`、会话 deepcopy 克隆、批量 `_clone_session` 克隆、302 跨子域重定向与混合 Cookie 路径验证。
- **BUG-062**：新增 `_safe_backtick_fence_len()`，按代码正文行首（≤3 空格）反引号串的最长长度 +1（≥3）动态生成外层围栏；正文含 ``` 时生成 ````，普通代码块仍为三反引号。
- **BUG-063**：`rewrite_internal_links` 弃用自研围栏检测（仅查同字符三连前缀），统一改用 `_process_outside_code` + `_FenceTracker`，跟踪开启长度与关闭行尾内容。

验证：新增 22 项回归测试并加强 2 项断言过弱的旧测试（改为整串比较），全套 **270 项通过**；在 `git archive HEAD` 隔离目录复跑，32 项失败/错误（覆盖 5 项 bug 的全部场景及 2 项加强断言），检出能力确认。端到端 `--local-html` 冒烟（省略 `</li>` 列表、含 ``` 的代码块、目标边界）通过。台账登记 [[CHK-010]]。

冒烟中发现相邻新问题已登记 **BUG-064**（P2）：converter 层对省略结束标签的表格（`<tr><th>A<th>B<tr><td>1<td>2</table>`）整表丢失，HEAD 上即可复现，非本轮回归，待后续修复。

## 2026-10-07 独立复核

结论：不能确认五项问题全部完成。原始定向用例通过，追加复现仍发现 BUG-059/061 的遗漏，以及两项本轮改动引入的功能回归。此次仅检查、记录与修正台账预览，未改实现代码或测试代码。

| 项目 | 复核状态 | 证据 |
| --- | --- | --- |
| BUG-059 | 待修复 | menu 父元素遗漏；不存在的父结束标签会提前结束目标采集 |
| BUG-060 | 原始复现场景通过 | 两条表格隐式闭合路径及嵌套表格对照通过 |
| BUG-061 | 待修复 | 服务器 Set-Cookie 更新登录 Cookie 后，重定向仍泄漏到子域名 |
| BUG-062 | 原始复现场景通过 | 三/四反引号、缩进与尾随正文转换通过 |
| BUG-063 | 原始复现场景通过 | 长围栏内短围栏行后的链接保留，围栏外链接正常改写；另有跨行链接回归 BUG-066 |
| BUG-064 | 待修复，仍可复现 | 省略结束标签表格转换仍只输出表外 TAIL |
| BUG-065 | 新增待修复 / P2 | host-only 过滤会清空用户显式设置的 Cookie 请求头 |
| BUG-066 | 新增待修复 / P2 | 链接改写改为逐行后，跨行链接不再匹配 |

### BUG-061：服务器更新 Cookie 后绕过 host-only 过滤（P1）

定位：`http_client.py:476-485`。过滤只识别导入时打上的 `_host_only_domain` 私有标记。服务器返回不带 Domain 属性的同名 Set-Cookie 时，requests 创建的新 Cookie 的 `domain_specified=False`，但无私有标记；新 Cookie 替换旧 Cookie 后便绕过过滤。

下面为离线 HTTPAdapter 复现，不向外部服务器发送请求。服务器响应的 Cookie 使用合成值，响应 raw 对象提供真实 Cookie 提取所需的消息头接口：

```python
import os, sys, tempfile, requests
from http.client import HTTPMessage
from types import SimpleNamespace
from requests.adapters import HTTPAdapter
sys.path.insert(0, "skills/webpage-to-md/scripts")
from webpage_to_md.http_client import _HostOnlyCookieSession, _parse_cookies_file

with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write("login.example.com\tFALSE\t/\tFALSE\t0\tsession\tIMPORTED\n")
    path = f.name
try:
    jar = _parse_cookies_file(path)
finally:
    os.unlink(path)

seen = []
class Adapter(HTTPAdapter):
    def send(self, request, **kwargs):
        seen.append((request.url, request.headers.get("Cookie")))
        r = requests.Response()
        r.url, r.request = request.url, request
        r._content, r._content_consumed = b"", True
        headers = HTTPMessage()
        if request.url.endswith("/start"):
            r.status_code = 302
            r.headers["Location"] = "https://untrusted.login.example.com/end"
            headers.add_header("Set-Cookie", "session=REFRESHED; Path=/")
        else:
            r.status_code = 200
        r.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=headers))
        return r

s = _HostOnlyCookieSession()
s.cookies.update(jar)
s.mount("https://", Adapter())
s.get("https://login.example.com/start")
print(seen)
```

实际结果：源域收到 `session=IMPORTED`；子域收到 `session=REFRESHED`，仍泄漏。检查 session jar 可见新 Cookie 的 domain 为 `login.example.com`，`domain_specified=False`，私有标记为 None。

已有重定向测试没有提供 Set-Cookie/raw 消息头，故只验证导入 Cookie 的标记存活，未覆盖服务器更新路径。[requests 官方实现](https://requests.readthedocs.io/en/latest/_modules/requests/sessions/)确认响应与重定向都会重新提取 Cookie。建议同时识别响应 Cookie 的标准 host-only 属性或在提取边界保留其语义；增加刷新同名 Cookie、服务器新增 Cookie 和显式 Domain Cookie 的重定向对照。

### BUG-059：父元素遗漏与游离标签误判（P1）

公共导入同前文。两条复现：

```python
html = '<menu><li id="content" class="drop">FIRST</menu><p>TAIL</p>'
print(strip_html_elements(html, [".drop"])[0])
print(extract_target_html(html, target_id="content", target_class=None))

html = '<ul><li id="content">FIRST</ol>SECOND</li></ul>'
print(extract_target_html(html, target_id="content", target_class=None))
```

实际：第一条剥离仅剩 `<menu>`，提取错误包含 TAIL；第二条提取仅剩 FIRST，丢失 SECOND。

定位：`extractors.py:496-499` 未包含 menu；`extractors.py:1180-1182` 不核实结束标签是否对应真实祖先。menu 的内容模型允许 li，而 li 在父元素没有更多内容时可以省略结束标签，参见 [menu 元素](https://html.spec.whatwg.org/multipage/grouping-content.html#the-menu-element)和[可选结束标签](https://html.spec.whatwg.org/multipage/syntax.html#optional-tags)。不存在的 ol/section 结束标签不应被视为目标的父元素，参见 [HTML 解析规则](https://html.spec.whatwg.org/multipage/parsing.html#parsing-main-inbody)。建议记录目标外祖先与作用域，而非仅凭标签映射推断。

### BUG-065：清空显式 Cookie 请求头（P2，本轮回归）

定位：`http_client.py:490-491`。给上述 session 设置 `s.headers['Cookie'] = 'manual=EXPLICIT'`，然后准备发往 `https://unrelated.example.net/` 的请求，Cookie 头会变成 None；同样配置的普通 requests.Session 保留 `manual=EXPLICIT`。CLI 支持通过自定义 Header 设置该值。

根因：请求 jar 包含不匹配的导入 host-only Cookie，即使原有 Cookie 头完全来自用户显式配置，过滤仍无条件删头并仅按 jar 重建。建议区分显式头与 jar 自动生成的头，增加显式 Cookie 与 cookies-file 混用的回归，保持登录 Cookie 的限制。

### BUG-066：跨行链接不再改写（P2，本轮回归）

定位：`markdown_conv.py:1175-1177`。原实现对围栏外文本块执行正则，新实现对单行执行，跨行链接无法完整匹配。

```python
md = html_to_markdown(
    '<p><a href="https://example.com/p">first\nsecond</a></p>',
    base_url="https://example.com/", url_to_local={})
print(repr(md))
print(rewrite_internal_links(md, {"https://example.com/p": "p"}))
```

转换器自身会生成 `[first\nsecond](https://example.com/p)`。当前改写计数为 0，保留网络链接；独立 HEAD 版本改写计数为 1，结果为 `[first\nsecond](#p)`。建议复用围栏跟踪器分段后，对完整围栏外文本块应用链接正则。

### 独立验证与记账

- 当前工作区：`python3 -m unittest discover -s tests -p 'test_*.py'`，270 项通过（4.611 秒），有原有测试文件未关闭的 ResourceWarning。
- 将当前测试复制到 `git archive HEAD` 隔离目录：270 项中 25 项失败、7 项错误，共 32 项，验证用户所述检出计数；临时目录已清理。
- 上述原始修复路径与新增反例均单独复现；Cookie 只用离线 Adapter/PreparedRequest 验证。
- 实现与测试文件的 `git diff --check` 通过；台账原有 4 行预览问题（裸 HTML/未闭合反引号）做了最小文字修正，并重新执行台账校验。
- BUG-059/061 退回待修复；新增 BUG-065/066 与 CHK-011；BUG-060/062/063 保留已修复，BUG-064 保留待修复。本轮未改实现、未提交或推送。

## 补修进展（二轮，2026-10-07 09:14）

上节复核退回的 2 项遗漏与 2 项新回归已全部在工作区修复，台账已转已修复：

- **BUG-059（二轮）**：① `_ENDTAG_IMPLIED_CLOSE` 补 `menu → li` 映射（li 的作用域屏障同步加入 menu），`<menu><li class="drop">…</menu>` 的剥离与提取均正确止于父结束标签；② `_TargetSectionExtractor` 新增 `_context` 祖先链——目标开始前按同一套隐式闭合规则记录目标外祖先（结束标签同样弹出已匹配项），未匹配的结束标签须**同时**满足「按映射隐式闭合目标根」与「在祖先链中真实打开」才结束采集：`<ul><li id="content">FIRST</ol>SECOND</li></ul>` 中不存在的 `</ol>` 不再截断，SECOND 保留；目标开始前已闭合的 `</div>` 同样被忽略。
- **BUG-061（二轮）**：新增 `_cookie_host_only_domain()`，在私有 `_host_only_domain` 标记之外同时识别 RFC 6265 标准 host-only 属性——服务器响应未带 Domain 属性设置的 cookie（`domain_specified=False` 且 domain 非空）本就仅匹配精确域名。用本报告的离线 HTTPMessage 复现脚本验证：Set-Cookie 刷新同名后 302 子域不再收到 `session=REFRESHED`；对照用例确认显式 `Domain=example.com` 的 cookie 仍被子域共享、服务器新增的无 Domain cookie 回到精确域仍正常发送。
- **BUG-065**：`_enforce_host_only` 弃用「删头 + 按 jar 重建」，改为 Cookie 头**名值对级过滤**——仅剔除与违规 host-only cookie 完全一致的名值对，用户显式设置的 Cookie 请求头（`--header`）原样保留；显式头与 cookies-file 混用行为与普通 `requests.Session` 对齐。
- **BUG-066**：`rewrite_internal_links` 改为按 `_FenceTracker` 分段后对**完整的围栏外文本块整体**执行链接正则：跨行链接（转换器对 a 内换行文本生成 `[first\nsecond](url)`）恢复改写，围栏内的跨行链接示例仍不动。

验证：新增 12 项回归测试（menu 剥离/提取、游离 `</ol>` 与真实 `</ul>` 对照、已闭合 `</div>` 反例、Set-Cookie 刷新/新增/显式 Domain 三组重定向、显式头保留与混用、跨行链接改写与围栏内对照），全套 **282 项通过**；隔离 HEAD 复跑 29 夘认+12 错误=41，核心检出项全部命中。BUG-064 仍待修复（本轮范围外）。台账登记 [[CHK-012]]。仍未提交、推送或更新 GitHub issue。

仍未执行：提交、推送、按实际完成范围更新 GitHub issue（远端 main 仍不含任何本地修复）。

## 2026-10-08 当前工作区复核（CHK-014）

结论：CHK-011 的四项问题已完成二轮补修，不能继续用旧复核意见判断当前代码未修复。此次重新读取源码、运行测试并逐条执行原始复现，结果如下：

| 原问题 | 当前实际结果 |
| --- | --- |
| BUG-059：menu 剥离 | 输出为 `<menu></menu><p>TAIL</p>`，正文与父结束标签保留 |
| BUG-059：menu 提取 | 仅采集 FIRST，不含 TAIL |
| BUG-059：游离父结束标签 | 不存在的 ol 结束标签被忽略，FIRST、SECOND 均保留 |
| BUG-061：Set-Cookie 更新后重定向 | 源域收到 session=IMPORTED，子域 Cookie 为 None，REFRESHED 不再泄漏 |
| BUG-065：显式 Cookie 头 | 含不匹配 host-only Cookie 的会话仍保留 manual=EXPLICIT |
| BUG-066：跨行链接 | 改写计数为 1，输出为 `[first\nsecond](#p)` |

原始复现 **6/6 通过**。全量命令 `python3 -m unittest discover -s tests -p 'test_*.py'` 为 **282 项通过**（3.918 秒，有旧测试文件未关闭的 ResourceWarning）。实现、测试与台账的定向 `git diff --check` 通过，台账结构校验通过。未修改实现或测试。

但当前台账已有 CHK-013 后续登记的 BUG-067～069；“仅剩 BUG-064 和历史遗留项”已不符合实际。此次用离线 Adapter / PreparedRequest 确认这三项仍存在：

| 已登记的待修复项 | 本次复现证据 |
| --- | --- |
| BUG-067 / P1 | localhost 的响应设置 session=LOCAL 并重定向到同主机：普通 Session 下一跳发送 Cookie，当前子类下一跳 Cookie 为 None。jar 域为 localhost.local，与实际主机精确比较产生误删 |
| BUG-068 / P1 | jar 同时含 login.example.com 的 host-only session=SHARED 和 .example.com 的共享 session=SHARED，请求 www.example.com 时合法共享 Cookie 也被删，头为 None |
| BUG-069 / P1 | 当前解析器接受的导入值带尾部空格或分号时，请求子域仍有 Cookie：SECRET 加尾部空格输出 session=SECRET；FIRST;SECOND 输出 session=FIRST; SECOND。此项针对异常输入，验证未真实发送网络请求 |

BUG-059/061/065/066 保留已修复，不重复登记或重新打开。BUG-067/068/069 保留待修复并追加此次证据，新增 CHK-014。BUG-064 和 BUG-010/052/053/054 仍为台账待修复项，本次未重做全部历史项验证。


## Cookie 边界补修与验证（2026-10-08，CHK-015）

BUG-067～069 已修复，保留工作区此前的修复和未提交内容。本轮修改 `http_client.py` 与回归测试，并同步台账、使用说明和实施计划。

| 条目 | 修复方式 | 验证结果 |
| --- | --- | --- |
| BUG-067 | 服务器响应 Cookie 提取后、构造下一跳前记录真实 URL 主机；对未带来源标记的原生 Cookie 使用 CookieJar 的有效主机表示作兼容匹配 | localhost、intranet、IPv6 的同主机下一跳保留 Cookie；localhost 与 localhost.local 不互相共享；普通非重定向响应与批量 worker 克隆同样正确 |
| BUG-068 | 按每个 Cookie 的来源筛选请求 jar，再交给 requests 生成自动头，不再按名值对一并删除 | host-only 与共享 Cookie 同名同值时，合法共享条目仍发送；path、secure、expires 不匹配时不发送；服务器新增 Domain Cookie 的重定向场景通过 |
| BUG-069 | 导入名称和值按 RFC 6265 的 token/cookie-octet 规则检查，拒绝非法值及非七列行；请求出口直接筛选 Cookie 对象 | 尾部/前导空白、分号、控制字符、额外制表符与非法引号值不进入 jar；合法空值、等号、百分号和整体引号值原样发送；服务器宽松解析的异常值跨主机不泄漏 |

显式 Cookie 头按 requests 的优先级保留，包括与 jar 恰好同名同值、大小写不同的头名，以及请求准备后主动更改的头。请求 Cookie=None 可取消会话显式头并恢复自动过滤。重定向沿用 requests 删除显式头的规则，并在 `rebuild_auth` 入口明确标记重新生成的自动头，避免 Cookie 到期等序列化变化造成误判。过滤只改本次 PreparedRequest 的 jar，不删除会话中的其他主机登录态。

验证证据：

- 新增 `TestCookieBoundaryRegressions` 13 个测试方法，覆盖离线 HTTPAdapter 的真实响应 Cookie 提取、请求准备、重定向与批量克隆。
- 先加入测试再改实现：最初 10 项测试在原实现上出现 18 个断言失败。补充 .local 别名、普通响应克隆与准备后修改头等边界后，将完整新增测试放入本轮修改前的工作区快照：13 项中 9 个方法触发失败，共 **21 个断言失败、0 个运行错误**。快照包含此前各轮修复，区别于未修复的 Git HEAD。
- 当前最终实现：`python3 -m unittest discover -s tests -p 'test_*.py'`，**295 项通过**（3.348 秒）。旧测试仍有既有文件未关闭 ResourceWarning。
- 所有 Cookie 均使用合成值和离线 Adapter 验证，没有向第三方站点发送请求。名称和值规则参考 [RFC 6265 第 4.1.1 节](https://www.rfc-editor.org/rfc/rfc6265#section-4.1.1)。
- 台账 BUG-067/068/069 转已修复，新增 CHK-015；实施计划为 `docs/plans/2026-10-08-cookie-boundary-fixes.md`。台账结构与定向差异格式检查通过。

历史 BUG-010/052/053/054 及相邻 converter 缺陷 BUG-064 仍保持待修复，本轮未纳入这三项 Cookie 修复。尚未提交、推送或更新 GitHub issue。
