#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""回归测试集执行器。

用途：
1) 读取 tests/回归测试集.md
2) 按场景调用 skills/webpage-to-md/scripts/grab_web_to_md.py
3) 生成通过/失败汇总（终端 + Markdown + JSON）

说明：
- 该脚本是“在线回归执行器”，与 tests/test_grab_web_to_md.py（单元测试）职责不同。
- 默认会真实访问网络 URL，请在可联网环境下运行。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE_MD = ROOT / "tests" / "回归测试集.md"
GRABBER = ROOT / "skills" / "webpage-to-md" / "scripts" / "grab_web_to_md.py"
DEFAULT_OUTPUT_ROOT = ROOT / "tests" / "regression_output"


@dataclass
class Case:
    index: int
    section: str
    url: str


@dataclass
class CaseResult:
    index: int
    section: str
    url: str
    command: str
    exit_code: int
    duration_s: float
    status: str
    note: str
    output_md: str
    output_size: int
    log_file: str


def _slug(text: str, limit: int = 64) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", text.strip())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "case"
    return s[:limit].rstrip("-")


def _case_stem(case: Case) -> str:
    parsed = urlparse(case.url)
    host = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/").replace("/", "-")
    if parsed.query:
        path = (path + "-" + parsed.query) if path else parsed.query
    base = f"{case.index:02d}-{host}-{path}" if path else f"{case.index:02d}-{host}"
    return _slug(base, 88)


def _parse_suite(md_path: Path) -> List[Case]:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    cases: List[Case] = []
    section = ""
    idx = 1
    h_re = re.compile(r"^##\s+\d+\.\s*(.+?)\s*$")
    u_re = re.compile(r"^\s*-\s*\[[^\]]*]\((https?://[^)]+)\)")

    for line in lines:
        hm = h_re.match(line)
        if hm:
            section = hm.group(1).strip()
            continue
        um = u_re.match(line)
        if um and section:
            cases.append(Case(index=idx, section=section, url=um.group(1).strip()))
            idx += 1
    return cases


def _build_command(case: Case, output_dir: Path) -> tuple[List[str], Path]:
    out_md = output_dir / f"{_case_stem(case)}.md"
    section = case.section
    url = case.url

    base_single = [
        sys.executable,
        str(GRABBER),
        url,
        "--out",
        str(out_md),
        "--overwrite",
        "--validate",
        "--best-effort-images",
    ]

    if section == "docs或wiki类多页面导出为单一md":
        if "metalmaniax.com" in url:
            cmd = [
                sys.executable,
                str(GRABBER),
                url,
                "--crawl",
                "--merge",
                "--toc",
                "--merge-output",
                str(out_md),
                "--target-id",
                "body",
                "--clean-wiki-noise",
                "--rewrite-links",
                "--download-images",
                "--skip-errors",
                "--overwrite",
                "--validate",
            ]
        elif "docs.openclaw.ai" in url:
            cmd = [
                sys.executable,
                str(GRABBER),
                url,
                "--crawl",
                "--merge",
                "--toc",
                "--docs-preset",
                "mintlify",
                "--merge-output",
                str(out_md),
                "--download-images",
                "--skip-errors",
                "--overwrite",
                "--validate",
            ]
        else:
            cmd = [
                sys.executable,
                str(GRABBER),
                url,
                "--crawl",
                "--merge",
                "--toc",
                "--auto-detect",
                "--merge-output",
                str(out_md),
                "--download-images",
                "--skip-errors",
                "--overwrite",
                "--validate",
            ]
        return cmd, out_md

    return base_single, out_md


def _expect_failure(case: Case) -> bool:
    return case.section == "可能有免爬机制的页面，目前无法处理的页面给出用户提示"


def _evaluate(case: Case, exit_code: int, merged_output: str, out_md: Path) -> tuple[str, str, int]:
    expected_fail = _expect_failure(case)
    size = out_md.stat().st_size if out_md.exists() else 0

    if expected_fail:
        if exit_code == 0:
            return "FAIL", "该场景预期失败并提示，但实际返回成功", size
        hints = ("--local-html", "反爬", "HTTP 403", "JavaScript 反爬")
        if any(h in merged_output for h in hints):
            return "PASS", "符合预期：失败并给出可操作提示", size
        return "FAIL", "失败了但未检测到明确提示文案", size

    if exit_code != 0:
        return "FAIL", f"预期成功，但退出码={exit_code}", size
    if not out_md.exists():
        return "FAIL", "预期成功，但输出 md 文件不存在", 0

    text = out_md.read_text(encoding="utf-8", errors="replace")
    if size < 500 and ("# Untitled" in text or text.count("\n") < 8):
        return "FAIL", f"输出过小（{size} bytes），疑似空壳页面", size

    if case.section == "Notion公开链接" and "Notion 页面标题" not in merged_output:
        return "WARN", "导出成功，但日志未看到 Notion API 标题提示", size

    if case.section == "docs或wiki类多页面导出为单一md" and "失败的 URL：" in merged_output:
        return "WARN", "合并成功，但存在部分子页面抓取失败", size

    return "PASS", "符合预期", size


def _write_summary(run_dir: Path, results: Sequence[CaseResult]) -> None:
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    payload = [asdict(r) for r in results]
    summary_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 回归测试汇总",
        "",
        f"- 运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总用例：{len(results)}",
        f"- PASS：{sum(1 for r in results if r.status == 'PASS')}",
        f"- WARN：{sum(1 for r in results if r.status == 'WARN')}",
        f"- FAIL：{sum(1 for r in results if r.status == 'FAIL')}",
        "",
        "| 序号 | 场景 | 状态 | 退出码 | 输出大小(bytes) | 备注 |",
        "|---:|---|---|---:|---:|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.index} | {r.section} | {r.status} | {r.exit_code} | {r.output_size} | {r.note} |"
        )
    lines.append("")
    summary_md.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    suite_md = Path(args.suite).resolve()
    if not suite_md.exists():
        print(f"错误：回归测试清单不存在：{suite_md}", file=sys.stderr)
        return 2
    if not GRABBER.exists():
        print(f"错误：找不到抓取脚本：{GRABBER}", file=sys.stderr)
        return 2

    cases = _parse_suite(suite_md)
    if args.only:
        k = args.only.strip().lower()
        cases = [c for c in cases if (k in c.section.lower() or k in c.url.lower())]
    if args.max_cases and args.max_cases > 0:
        cases = cases[: args.max_cases]
    if not cases:
        print("错误：没有匹配到可执行用例。", file=sys.stderr)
        return 2

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / ts
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"回归集：{suite_md}")
    print(f"输出目录：{run_dir}")
    print(f"用例数量：{len(cases)}")
    if args.dry_run:
        print("模式：DRY RUN（仅打印命令，不执行）")

    results: List[CaseResult] = []
    for case in cases:
        cmd, out_md = _build_command(case, run_dir)
        cmd_str = " ".join(subprocess.list2cmdline([p]) if " " in p else p for p in cmd)
        print(f"\n[{case.index:02d}] {case.section}")
        print(f"URL: {case.url}")
        print(f"CMD: {cmd_str}")
        log_file = logs_dir / f"{case.index:02d}-{_slug(case.section, 24)}.log"

        if args.dry_run:
            results.append(
                CaseResult(
                    index=case.index,
                    section=case.section,
                    url=case.url,
                    command=cmd_str,
                    exit_code=0,
                    duration_s=0.0,
                    status="DRYRUN",
                    note="未执行",
                    output_md=str(out_md),
                    output_size=0,
                    log_file=str(log_file),
                )
            )
            continue

        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        dt = time.perf_counter() - t0
        merged = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        log_file.write_text(merged, encoding="utf-8", errors="replace")

        status, note, size = _evaluate(case, proc.returncode, merged, out_md)
        print(f"结果: {status} (exit={proc.returncode}, {dt:.1f}s) - {note}")
        results.append(
            CaseResult(
                index=case.index,
                section=case.section,
                url=case.url,
                command=cmd_str,
                exit_code=proc.returncode,
                duration_s=round(dt, 3),
                status=status,
                note=note,
                output_md=str(out_md),
                output_size=size,
                log_file=str(log_file),
            )
        )

    _write_summary(run_dir, results)
    pass_n = sum(1 for r in results if r.status == "PASS")
    warn_n = sum(1 for r in results if r.status == "WARN")
    fail_n = sum(1 for r in results if r.status == "FAIL")
    print("\n=== 回归测试完成 ===")
    print(f"PASS={pass_n}, WARN={warn_n}, FAIL={fail_n}")
    print(f"详情: {run_dir / 'summary.md'}")
    print(f"日志: {run_dir / 'logs'}")

    if args.dry_run:
        return 0
    return 1 if fail_n > 0 else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="执行 tests/回归测试集.md 并输出汇总")
    p.add_argument("--suite", default=str(DEFAULT_SUITE_MD), help="回归测试清单（Markdown）")
    p.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="回归输出根目录")
    p.add_argument("--only", default="", help="仅执行包含关键字的场景或 URL")
    p.add_argument("--max-cases", type=int, default=0, help="最多执行前 N 条（0 表示不限制）")
    p.add_argument("--dry-run", action="store_true", help="仅打印命令，不实际执行")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
