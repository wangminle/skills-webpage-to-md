# Cookie 边界修复实施计划

**目标：** 修复 BUG-067～069，同时保持 BUG-061/065 的服务器更新、批量克隆与显式请求头行为。

**范围：** 当前工作区中的 `http_client.py`、相关回归测试、台账和说明文档；保留已有未提交改动。

**方案：** 对 Cookie 对象按来源执行 host-only 匹配。导入条目使用原始精确域名；服务器响应记录真实来源主机；未记录来源的原生 Cookie 兼容 Python CookieJar 的有效请求主机表示，支持 localhost/intranet/IPv6，同时防止 localhost 与 localhost.local 被混为一域。请求路径先过滤 CookieJar，再由 requests 按 domain/path/secure/expires 规则生成自动 Cookie 头，保留同名同值的合法 Cookie。用户显式配置的头保留用户输入；重定向重新生成的头继续受过滤。拒绝导入的非法 Cookie 名值，不再靠分号拆分猜测自动 Cookie 的来源。

不选择继续扩展头部名值对黑名单：它无法可靠区分同名同值 Cookie，也无法处理含分号的值。不选择仅修改 CookiePolicy：requests 合并 jar 与重定向会丢失自定义策略，仍需请求出口过滤。

## 执行记录

- [x] 为 BUG-067 新增同主机重定向失败测试，覆盖 localhost、内网单标签主机与 IPv6，并验证子域仍被拒绝。
- [x] 为 BUG-068 新增同名同值的 host-only/共享 Cookie 对照，覆盖 path、secure、expires 与显式头优先。
- [x] 为 BUG-069 新增非法导入值拒绝测试及服务器异常值跨主机重定向测试；保留合法引号、等号与空值。
- [x] 运行新增测试，确认修复前因目标行为失败；保存当前实现快照用于后续检出复核。
- [x] 实现 CookieJar 对象过滤、有效请求主机匹配与导入验证，保留显式头来源信息，验证 requests 重定向的 PreparedRequest 副本。
- [x] 运行新测试和全量测试；必要时补充失败边界，不重复执行无关测试。
- [x] 同步 BUG-067～069、验证条目、审查报告与未发布日志；检查台账与差异格式。

## 验证命令

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 /Users/fenix-macmini/.agents/skills/task-list-initialization/scripts/task_list_cli.py check --file task-list.md
git diff --check -- skills/webpage-to-md/scripts tests task-list.md skills/webpage-to-md/references/full-guide.md
```

Cookie 重定向使用离线 HTTPAdapter 与真实 Cookie 提取接口，测试仅使用合成值。历史 BUG-010/052/053/054/064 属于其他模块或能力需求，未纳入本次三项 Cookie 修复。

执行结果：新增 13 项测试；修复前工作区快照 21 个断言失败、0 个运行错误；最终全量 295 项通过。台账登记 CHK-015，BUG-067～069 转已修复。
