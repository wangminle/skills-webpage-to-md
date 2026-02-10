#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
抓取网页正文与图片，保存为 Markdown + 本地 assets 目录。

依赖说明：
- 必需依赖：requests（HTTP 请求）
- 可选依赖：markdown（用于 PDF 渲染时的 Markdown→HTML 转换，无则使用内置简易转换）
- PDF 生成：使用系统已安装的 Edge/Chrome 浏览器 headless 模式，无需额外安装工具
- 不依赖：pandoc、playwright、selenium、bs4、lxml

设计目标（来自之前四个站点的实践）：
- 优先提取 <article>（其次 <main>/<body>），减少导航/页脚噪音
- 仅用标准库 HTMLParser（不依赖 bs4/lxml），适配离线/受限环境
- 图片下载支持：src/data-src/srcset/picture/source；相对 URL；content-type 缺失时嗅探格式
- Ghost/Anthropic 等站点会把视频播放器/图标混进正文：跳过常见 UI 标签/类
- 处理 <tag/> 自闭合导致的 skip 栈不出栈：实现 handle_startendtag
- 简单表格转换为 Markdown table；并提供校验（引用数=文件数/文件存在）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Sequence, Tuple, Callable

import requests

# 支持通过 importlib 直接加载本脚本时导入同级 package
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from webpage_to_md.models import BatchConfig, BatchPageResult, JSChallengeResult, ValidationResult
from webpage_to_md.http_client import (
    UA_PRESETS,
    _DEFAULT_MAX_HTML_BYTES,
    _create_session,
    fetch_html,
)
from webpage_to_md.extractors import (
    DOCS_PRESETS,
    ImageURLCollector,
    detect_docs_framework,
    extract_h1,
    extract_links_from_html,
    extract_main_html,
    extract_target_html,
    extract_target_html_multi,
    extract_title,
    extract_wechat_title,
    get_available_presets,
    get_strip_selectors,
    html_text_len,
    is_wechat_article_html,
    is_wechat_article_url,
    read_urls_file,
    strip_anchor_lists,
    strip_html_elements,
    uniq_preserve_order,
)
from webpage_to_md.images import (
    _DEFAULT_MAX_IMAGE_BYTES,
    batch_download_images,
    download_images,
    replace_image_urls_in_markdown,
)
from webpage_to_md.markdown_conv import (
    clean_wechat_noise,
    clean_wiki_noise,
    html_to_markdown,
    rewrite_internal_links,
    strip_duplicate_h1,
)
from webpage_to_md.output import (
    _default_basename,
    _safe_path_length,
    _sanitize_filename_part,
    auto_wrap_output_dir,
    batch_save_individual,
    generate_frontmatter,
    generate_index_markdown,
    generate_merged_markdown,
)
from webpage_to_md.pdf_utils import (
    generate_pdf_from_markdown,
    strip_yaml_frontmatter,
)
from webpage_to_md.security import (
    _redact_url_to_local_map,
    detect_js_challenge,
    print_js_challenge_warning,
    redact_url,
    redact_urls_in_markdown,
    validate_markdown,
)


# ============================================================================
# 退出码定义
# ============================================================================
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_EXISTS = 2
EXIT_VALIDATION_FAILED = 3
EXIT_JS_CHALLENGE = 4  # 检测到 JS 反爬保护，无法获取内容



def process_single_url(
    session: requests.Session,
    url: str,
    config: BatchConfig,
    custom_title: Optional[str] = None,
    order: int = 0,
) -> BatchPageResult:
    """处理单个 URL，返回结果"""
    try:
        # 获取页面
        page_html = fetch_html(
            session=session,
            url=url,
            timeout_s=config.timeout,
            retries=config.retries,
            max_html_bytes=config.max_html_bytes,
        )

        # 批量模式同样检测 JS 反爬挑战页，避免把验证页误当正文导出
        js_detection = detect_js_challenge(page_html)
        if js_detection.is_challenge and not config.force:
            suggestions = js_detection.get_suggestions(url)
            suggestion_text = "\n".join(f"  {s}" for s in suggestions)
            raise RuntimeError(
                "检测到 JavaScript 反爬保护，当前页面无法通过纯 HTTP 请求获取完整内容。\n"
                f"置信度：{js_detection.confidence}\n"
                "建议操作：\n"
                f"{suggestion_text}\n"
                "如需跳过该检查并强制继续，请添加 --force。"
            )
        
        # 微信公众号文章自动检测
        is_wechat = config.wechat
        if not is_wechat and is_wechat_article_url(url):
            is_wechat = True
        elif not is_wechat and is_wechat_article_html(page_html):
            is_wechat = True
        
        # 确定正文提取策略
        target_id = config.target_id
        target_class = config.target_class
        exclude_selectors = config.exclude_selectors
        strip_nav = config.strip_nav
        strip_page_toc = config.strip_page_toc
        anchor_list_threshold = config.anchor_list_threshold
        
        # 微信模式下，如果未指定 target，自动使用 rich_media_content
        if is_wechat and not target_id and not target_class:
            target_class = "rich_media_content"
        
        # Phase 2: 自动检测文档框架
        detected_preset: Optional[str] = None
        if config.auto_detect and not config.docs_preset:
            detected_preset, confidence, signals = detect_docs_framework(page_html)
            if detected_preset and confidence >= 0.5:
                preset = DOCS_PRESETS.get(detected_preset)
                if preset:
                    # 高置信度时应用预设
                    if not target_id and preset.target_ids:
                        target_id = ",".join(preset.target_ids)
                    if not target_class and preset.target_classes:
                        target_class = ",".join(preset.target_classes)
                    # 批量模式下 auto-detect 也应尽量复用预设的“去导航”能力，保持与单页模式一致
                    preset_excludes = ",".join(preset.exclude_selectors) if preset.exclude_selectors else ""
                    if preset_excludes:
                        if exclude_selectors:
                            exclude_selectors = f"{exclude_selectors},{preset_excludes}"
                        else:
                            exclude_selectors = preset_excludes
                    strip_nav = True
                    strip_page_toc = True
                    if anchor_list_threshold == 0:
                        anchor_list_threshold = 10
        
        # 提取正文（支持多值 target，T2.1）
        if target_id or target_class:
            # 使用多值提取
            article_html, matched = extract_target_html_multi(
                page_html, 
                target_ids=target_id, 
                target_classes=target_class
            )
            if not article_html:
                # 回退到单值提取（兼容旧逻辑）
                article_html = extract_target_html(
                    page_html,
                    target_id=target_id.split(",")[0] if target_id else None,
                    target_class=target_class.split(",")[0] if target_class else None,
                ) or ""
            if not article_html:
                article_html = extract_main_html(page_html)
        else:
            article_html = extract_main_html(page_html)
        
        # Phase 1: HTML 导航元素剥离（在提取正文后、转换 Markdown 前）
        strip_selectors = get_strip_selectors(
            strip_nav=strip_nav,
            strip_page_toc=strip_page_toc,
            exclude_selectors=exclude_selectors,
        )
        if strip_selectors:
            article_html, _ = strip_html_elements(article_html, strip_selectors)
        
        # 提取标题（微信模式下优先使用专用提取函数）
        if custom_title:
            title = custom_title
        elif is_wechat:
            title = extract_wechat_title(page_html) or extract_h1(article_html) or extract_title(page_html) or "Untitled"
        else:
            title = extract_h1(article_html) or extract_title(page_html) or "Untitled"
        
        # 收集图片 URL（如果需要下载图片）
        image_urls: List[str] = []
        if config.download_images:
            collector = ImageURLCollector(base_url=url)
            collector.feed(article_html)
            image_urls = uniq_preserve_order(collector.image_urls)
        
        # 转换为 Markdown（批量模式先不替换图片路径，后续统一处理）
        md_body = html_to_markdown(
            article_html=article_html,
            base_url=url,
            url_to_local={},  # 先不替换图片路径
            keep_html=config.keep_html,
        )
        md_body = strip_duplicate_h1(md_body, title)
        
        # 清理噪音内容
        if is_wechat:
            md_body = clean_wechat_noise(md_body)
        if config.clean_wiki_noise:
            md_body = clean_wiki_noise(md_body)
        
        # Phase 1: Markdown 锚点列表剥离
        if anchor_list_threshold > 0:
            md_body, _ = strip_anchor_lists(md_body, anchor_list_threshold)
        
        return BatchPageResult(
            url=url,
            title=title,
            md_content=md_body,
            success=True,
            order=order,
            image_urls=image_urls,
        )
    
    except Exception as e:
        return BatchPageResult(
            url=url,
            title=custom_title or url,
            md_content="",
            success=False,
            error=str(e),
            order=order,
        )


def batch_process_urls(
    session: requests.Session,
    urls: List[Tuple[str, Optional[str]]],
    config: BatchConfig,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[BatchPageResult]:
    """
    批量处理 URL 列表
    
    Args:
        session: requests.Session
        urls: [(url, custom_title), ...]
        config: 批量处理配置
        progress_callback: 进度回调函数 (current, total, url)
    
    Returns:
        处理结果列表
    """
    results: List[BatchPageResult] = []
    total = len(urls)
    lock = threading.Lock()
    last_request_time = [0.0]  # 使用列表以便在闭包中修改
    
    def process_with_delay(args: Tuple[int, str, Optional[str]]) -> BatchPageResult:
        idx, url, custom_title = args
        
        # 控制请求间隔
        with lock:
            now = time.time()
            elapsed = now - last_request_time[0]
            if elapsed < config.delay:
                time.sleep(config.delay - elapsed)
            last_request_time[0] = time.time()
        
        if progress_callback:
            progress_callback(idx + 1, total, url)
        
        return process_single_url(
            session=session,
            url=url,
            config=config,
            custom_title=custom_title,
            order=idx,
        )
    
    # 使用线程池并发处理
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        args_list = [(i, url, title) for i, (url, title) in enumerate(urls)]
        futures = {executor.submit(process_with_delay, args): args for args in args_list}
        
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            
            if not result.success and not config.skip_errors:
                # 取消剩余任务
                for f in futures:
                    f.cancel()
                raise RuntimeError(f"处理失败：{result.url}\n错误：{result.error}")
    
    # 按原始顺序排序
    results.sort(key=lambda r: r.order)
    return results


def _batch_main(args: argparse.Namespace) -> int:
    """批量处理模式的主函数"""
    
    # 创建 Session
    session = _create_session(args, referer_url=args.url)
    
    # 收集要处理的 URL 列表
    urls: List[Tuple[str, Optional[str]]] = []
    source_url: Optional[str] = None
    
    if args.urls_file:
        # 从文件读取 URL
        if not os.path.isfile(args.urls_file):
            print(f"错误：URL 列表文件不存在：{args.urls_file}", file=sys.stderr)
            return EXIT_ERROR
        urls = read_urls_file(args.urls_file)
        print(f"从文件加载了 {len(urls)} 个 URL")
    
    if args.crawl:
        # 从索引页爬取链接
        if not args.url:
            print("错误：爬取模式需要提供索引页 URL", file=sys.stderr)
            return EXIT_ERROR
        
        source_url = args.url
        print(f"正在从索引页提取链接：{args.url}")
        
        try:
            index_html = fetch_html(
                session=session,
                url=args.url,
                timeout_s=args.timeout,
                retries=args.retries,
                max_html_bytes=args.max_html_bytes,
            )
        except Exception as e:
            print(f"错误：无法获取索引页：{e}", file=sys.stderr)
            return EXIT_ERROR

        # 索引页也可能命中 JS 反爬：提前给出解决方案，避免后续“提取 0 链接”的误导
        js_detection = detect_js_challenge(index_html)
        if js_detection.is_challenge:
            print_js_challenge_warning(js_detection, args.url)
            if not args.force:
                return EXIT_JS_CHALLENGE
            print("已添加 --force 参数，强制继续处理索引页...", file=sys.stderr)
        
        # 提取链接
        links = extract_links_from_html(
            html=index_html,
            base_url=args.url,
            pattern=args.crawl_pattern,
            same_domain=args.same_domain,
        )
        
        # 添加到 URL 列表（避免重复）
        existing_urls = {u for u, _ in urls}
        for link_url, link_text in links:
            if link_url not in existing_urls:
                urls.append((link_url, link_text))
                existing_urls.add(link_url)
        
        print(f"从索引页提取了 {len(links)} 个链接，总计 {len(urls)} 个 URL")
    
    if not urls:
        print("错误：没有要处理的 URL", file=sys.stderr)
        return EXIT_ERROR
    
    # 显示 URL 列表预览
    print("\n即将处理的 URL 列表：")
    for i, (url, title) in enumerate(urls[:10], 1):
        display = f"  {i}. {title or url}"
        if len(display) > 80:
            display = display[:77] + "..."
        print(display)
    if len(urls) > 10:
        print(f"  ... 共 {len(urls)} 个")
    print()
    
    # 配置批量处理
    config = BatchConfig(
        max_workers=args.max_workers,
        delay=args.delay,
        skip_errors=args.skip_errors,
        timeout=args.timeout,
        retries=args.retries,
        max_html_bytes=args.max_html_bytes,
        best_effort_images=args.best_effort_images,  # Bug fix: 使用用户参数而非硬编码
        keep_html=args.keep_html,
        target_id=args.target_id,
        target_class=args.target_class,
        clean_wiki_noise=args.clean_wiki_noise,
        download_images=args.download_images,
        wechat=args.wechat,
        # Phase 1: 导航剥离参数
        strip_nav=args.strip_nav,
        strip_page_toc=args.strip_page_toc,
        exclude_selectors=args.exclude_selectors,
        anchor_list_threshold=args.anchor_list_threshold,
        # Phase 2: 智能正文定位参数
        docs_preset=args.docs_preset,
        auto_detect=args.auto_detect,
        force=args.force,
    )
    
    # Phase 2: 应用文档框架预设
    if args.docs_preset:
        preset = DOCS_PRESETS.get(args.docs_preset)
        if preset:
            print(f"\n📦 使用文档框架预设：{preset.name} ({preset.description})")
            # 应用预设的 target 配置
            if not config.target_id and preset.target_ids:
                config.target_id = ",".join(preset.target_ids)
            if not config.target_class and preset.target_classes:
                config.target_class = ",".join(preset.target_classes)
            # 合并预设的 exclude_selectors
            preset_excludes = ",".join(preset.exclude_selectors)
            if config.exclude_selectors:
                config.exclude_selectors = f"{config.exclude_selectors},{preset_excludes}"
            else:
                config.exclude_selectors = preset_excludes
            # 自动启用导航剥离
            config.strip_nav = True
            config.strip_page_toc = True
            # 预设模式下，如果用户未显式设置 anchor_list_threshold，则自动启用（默认 10）
            if args.anchor_list_threshold == 0:
                config.anchor_list_threshold = 10
            print(f"  • 正文容器 ID：{config.target_id or '(未设置)'}")
            print(f"  • 正文容器 class：{config.target_class or '(未设置)'}")
            print(f"  • 排除选择器：{len(preset.exclude_selectors)} 个")
            if config.anchor_list_threshold > 0:
                print(f"  • 锚点列表阈值：{config.anchor_list_threshold} 行")
    
    # Phase 1: 打印导航剥离配置
    if args.strip_nav or args.strip_page_toc or args.exclude_selectors:
        selectors = get_strip_selectors(args.strip_nav, args.strip_page_toc, args.exclude_selectors)
        print(f"启用导航剥离：{len(selectors)} 个选择器")
        if args.anchor_list_threshold > 0:
            print(f"锚点列表移除阈值：{args.anchor_list_threshold} 行")
    
    # 进度回调
    def progress_callback(current: int, total: int, url: str) -> None:
        short_url = url if len(url) <= 50 else url[:47] + "..."
        print(f"[{current}/{total}] 处理中：{short_url}")
    
    # 执行批量处理
    print(f"开始批量处理（并发数：{config.max_workers}，间隔：{config.delay}s）...\n")
    
    try:
        results = batch_process_urls(
            session=session,
            urls=urls,
            config=config,
            progress_callback=progress_callback,
        )
    except RuntimeError as e:
        print(f"\n错误：{e}", file=sys.stderr)
        return EXIT_ERROR
    
    # 统计结果
    success_count = len([r for r in results if r.success])
    fail_count = len(results) - success_count
    print(f"\n处理完成：成功 {success_count}，失败 {fail_count}")
    
    # Phase 1: 导航剥离统计（T1.5 可观测性）
    if args.strip_nav or args.strip_page_toc or args.exclude_selectors:
        selectors = get_strip_selectors(args.strip_nav, args.strip_page_toc, args.exclude_selectors)
        print(f"\n📊 导航剥离已生效：")
        print(f"  • 应用选择器：{len(selectors)} 个")
        if args.strip_nav:
            print(f"  • --strip-nav: 移除导航元素（nav/aside/.sidebar 等）")
        if args.strip_page_toc:
            print(f"  • --strip-page-toc: 移除页内目录（.toc/.on-this-page 等）")
        if args.exclude_selectors:
            print(f"  • --exclude-selectors: {args.exclude_selectors}")
        if args.anchor_list_threshold > 0:
            print(f"  • 锚点列表阈值：>{args.anchor_list_threshold} 行自动移除")
    
    # 下载图片（如果启用）
    url_to_local: Dict[str, str] = {}
    if args.download_images:
        # 统计图片数量
        total_images = sum(len(r.image_urls) for r in results if r.success)
        unique_images = len(set(url for r in results if r.success for url in r.image_urls))
        
        if unique_images > 0:
            # 确定 assets 目录
            if args.merge:
                output_file = args.merge_output or "merged.md"
                # 自动创建同名上级目录（如果用户未指定目录）
                output_file = auto_wrap_output_dir(output_file)
                assets_dir = os.path.splitext(output_file)[0] + ".assets"
                md_dir = os.path.dirname(output_file) or "."
            else:
                assets_dir = os.path.join(args.output_dir, "assets")
                md_dir = args.output_dir
            
            print(f"\n发现 {unique_images} 张图片（去重后），开始下载到：{assets_dir}")
            
            def img_progress(current: int, total: int, url: str) -> None:
                short_url = url if len(url) <= 50 else url[:47] + "..."
                print(f"  [{current}/{total}] 下载：{short_url}")
            
            url_to_local = batch_download_images(
                session=session,
                results=results,
                assets_dir=assets_dir,
                md_dir=md_dir,
                timeout_s=args.timeout,
                retries=args.retries,
                best_effort=bool(args.best_effort_images),
                progress_callback=img_progress,
                redact_urls=args.redact_url,
                max_image_bytes=args.max_image_bytes,
            )
            
            print(f"  图片下载完成：{len(url_to_local)} 张成功")
            
            # 更新结果中的 Markdown 内容，替换图片 URL
            for result in results:
                if result.success and result.md_content:
                    result.md_content = replace_image_urls_in_markdown(
                        result.md_content, url_to_local
                    )
        else:
            print("\n未发现需要下载的图片")
    
    # 输出结果
    if args.merge:
        # 合并输出模式
        output_file = args.merge_output or "merged.md"
        # 自动创建同名上级目录（如果用户未指定目录）
        output_file = auto_wrap_output_dir(output_file)
        
        # 确保输出目录存在
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        # 检查是否已存在
        if os.path.exists(output_file) and not args.overwrite:
            print(f"文件已存在：{output_file}（如需覆盖请加 --overwrite）", file=sys.stderr)
            return EXIT_FILE_EXISTS
        
        # 来源 URL 优先级：--source-url > 爬取模式的索引页 > None（提取域名）
        final_source_url = args.source_url or source_url
        
        merged_content, anchor_stats = generate_merged_markdown(
            results=results,
            include_toc=args.toc,
            main_title=args.merge_title or args.title,
            source_url=final_source_url,
            rewrite_links=args.rewrite_links,
            show_source_summary=not args.no_source_summary,
            redact_urls=args.redact_url,
        )
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(merged_content)
        
        print(f"\n已生成合并文档：{output_file}")
        print(f"文档大小：{len(merged_content):,} 字符")
        
        # Phase 3-A: 输出锚点冲突统计
        if anchor_stats.has_collisions:
            if hasattr(args, 'warn_anchor_collisions') and args.warn_anchor_collisions:
                anchor_stats.print_summary()
            else:
                print(f"📌 锚点冲突：{anchor_stats.collision_count} 个已自动修复（使用 --warn-anchor-collisions 查看详情）")
        if url_to_local:
            assets_dir = os.path.splitext(output_file)[0] + ".assets"
            # 统计图片引用情况（非破坏性：只报告不删除）
            if os.path.isdir(assets_dir):
                # 统计实际文件数
                all_files = [f for f in os.listdir(assets_dir) if os.path.isfile(os.path.join(assets_dir, f))]
                actual_count = len(all_files)
                
                # 统计被引用的文件（保守检测：使用文件名匹配）
                unused_files = []
                for filename in all_files:
                    # 检查文件名是否在最终内容中出现
                    if filename not in merged_content:
                        unused_files.append(filename)
                
                unused_count = len(unused_files)
                if unused_count > 0:
                    print(f"图片目录：{assets_dir}（{actual_count} 张图片，{unused_count} 张可能未引用）")
                    print(f"  ⚠️ 未自动清理未引用图片（可能存在误判），如需清理请手动检查")
                else:
                    print(f"图片目录：{assets_dir}（{actual_count} 张图片）")
            else:
                print(f"图片目录：{assets_dir}（{len(url_to_local)} 张图片）")
        
        # Phase 3-B1: 双版本输出（同时生成分文件版本）
        if hasattr(args, 'split_output') and args.split_output:
            split_dir = args.split_output
            os.makedirs(split_dir, exist_ok=True)
            
            # 确定共享的 assets 目录（使用合并版本的 assets）
            shared_assets = os.path.splitext(output_file)[0] + ".assets" if url_to_local else None
            
            # 生成分文件
            saved_files = batch_save_individual(
                results=results,
                output_dir=split_dir,
                include_frontmatter=args.frontmatter,
                redact_urls=args.redact_url,
                shared_assets_dir=shared_assets,
            )
            
            # 生成索引文件
            index_content = generate_index_markdown(
                results=results,
                output_dir=split_dir,
                main_title=args.merge_title or args.title,
                source_url=final_source_url,
                saved_files=saved_files,
                redact_urls=args.redact_url,
            )
            index_path = os.path.join(split_dir, "INDEX.md")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(index_content)
            
            print(f"\n📂 已同时生成分文件版本：")
            print(f"  • 目录：{split_dir}")
            print(f"  • 文件数：{len(saved_files)} 个")
            print(f"  • 索引：{index_path}")
            if shared_assets:
                rel_assets = os.path.relpath(shared_assets, split_dir)
                print(f"  • 共享 assets：{rel_assets}")
        
    else:
        # 独立文件输出模式
        os.makedirs(args.output_dir, exist_ok=True)
        
        saved_files = batch_save_individual(
            results=results,
            output_dir=args.output_dir,
            include_frontmatter=args.frontmatter,
            redact_urls=args.redact_url,
            shared_assets_dir=None,
        )
        
        # 来源 URL 优先级：--source-url > 爬取模式的索引页 > None（提取域名）
        final_source_url = args.source_url or source_url
        
        # 生成索引文件（使用增强版）
        index_content = generate_index_markdown(
            results=results,
            output_dir=args.output_dir,
            main_title=args.merge_title or args.title,
            source_url=final_source_url,
            saved_files=saved_files,
            redact_urls=args.redact_url,
        )
        index_path = os.path.join(args.output_dir, "INDEX.md")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_content)
        
        print(f"\n已生成 {len(saved_files)} 个文件到：{args.output_dir}")
        print(f"索引文件：{index_path}")
    
    # 显示失败列表
    if fail_count > 0:
        print("\n失败的 URL：")
        for result in results:
            if not result.success:
                print(f"  - {result.url}")
                print(f"    错误：{result.error}")
    
    return EXIT_SUCCESS


def _fetch_page_html(
    session: requests.Session,
    url: str,
    args: argparse.Namespace,
) -> Tuple[Optional[str], Optional[int]]:
    """获取页面 HTML 并处理错误和 JS 反爬检测。

    Returns:
        (page_html, exit_code) — exit_code 为 None 表示成功
    """
    print(f"下载页面：{url}")
    try:
        page_html = fetch_html(
            session=session,
            url=url,
            timeout_s=args.timeout,
            retries=args.retries,
            max_html_bytes=args.max_html_bytes,
        )
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        safe_url = redact_url(url) if args.redact_url else url
        print(f"错误：请求失败（HTTP {status}）：{safe_url}", file=sys.stderr)
        if status in (403, 429):
            print("", file=sys.stderr)
            print("可能触发了站点的反爬或访问频控。建议：", file=sys.stderr)
            print("  1. 在浏览器中打开该 URL，等待页面完全加载", file=sys.stderr)
            print("  2. 右键页面另存为 .html 文件", file=sys.stderr)
            print("  3. 使用 --local-html 与 --base-url 进行处理，例如：", file=sys.stderr)
            print(
                f'     python grab_web_to_md.py --local-html saved.html --base-url "{safe_url}" --out output.md',
                file=sys.stderr,
            )
        return None, EXIT_ERROR
    except requests.exceptions.RequestException as exc:
        safe_url = redact_url(url) if args.redact_url else url
        print(f"错误：下载失败：{safe_url}", file=sys.stderr)
        print(f"详情：{exc}", file=sys.stderr)
        print("建议：可改用浏览器保存 HTML 后，通过 --local-html 离线处理。", file=sys.stderr)
        return None, EXIT_ERROR

    # JS 反爬检测
    js_detection = detect_js_challenge(page_html)
    if js_detection.is_challenge:
        print_js_challenge_warning(js_detection, url)
        if not args.force:
            return None, EXIT_JS_CHALLENGE
        print("已添加 --force 参数，强制继续处理...", file=sys.stderr)

    return page_html, None


def _extract_title_for_filename(page_html: str, url: str = "") -> str:
    """从页面 HTML 中提取标题（用于自动命名文件）。

    优先级：微信标题 > H1 > <title> > "Untitled"
    """
    # 注意：url 可能为空（--local-html 未指定 --base-url），
    # 此时仍需通过 HTML 特征检测微信页面，因此两个条件用 or 连接。
    is_wechat = (bool(url) and is_wechat_article_url(url)) or is_wechat_article_html(page_html)
    if is_wechat:
        wechat_title = extract_wechat_title(page_html)
        if wechat_title:
            return wechat_title
    return extract_h1(page_html) or extract_title(page_html) or "Untitled"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="抓取网页正文与图片，保存为 Markdown + assets。支持单页和批量模式。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
批量处理示例：
  # 从文件读取 URL 列表，合并为单个文档
  python grab_web_to_md.py --urls-file urls.txt --merge --merge-output output.md

  # 从索引页爬取链接并批量导出
  python grab_web_to_md.py https://example.com/index --crawl --merge --toc

  # 批量导出为独立文件
  python grab_web_to_md.py --urls-file urls.txt --output-dir ./docs

urls.txt 文件格式：
  # 这是注释
  https://example.com/page1
  https://example.com/page2 | 自定义标题
""",
    )
    ap.add_argument("url", nargs="?", help="要抓取的网页 URL（单页模式必需，批量模式可选）")
    ap.add_argument("--out", help="输出 md 文件名（默认根据 URL 自动生成）")
    ap.add_argument("--auto-title", action="store_true",
                    help="从页面标题自动生成输出文件名（优先级低于 --out；未指定 --out 时生效）")
    ap.add_argument("--assets-dir", help="图片目录名（默认 <out>.assets）")
    ap.add_argument("--title", help="Markdown 顶部标题（默认从 <title> 提取）")
    ap.add_argument("--with-pdf", action="store_true", help="同时生成同名 PDF（需要本机 Edge/Chrome）")
    ap.add_argument("--timeout", type=int, default=60, help="请求超时（秒），默认 60")
    ap.add_argument("--retries", type=int, default=3, help="网络重试次数，默认 3")
    ap.add_argument(
        "--max-html-bytes",
        type=int,
        default=_DEFAULT_MAX_HTML_BYTES,
        help="单页 HTML 最大允许字节数（默认 10MB；设为 0 表示不限制）",
    )
    ap.add_argument("--best-effort-images", action="store_true", help="图片下载失败时仅警告并跳过（默认失败即退出）")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的 md 文件")
    ap.add_argument("--validate", action="store_true", help="生成后执行校验并输出结果")
    # JS 反爬处理
    ap.add_argument("--local-html", metavar="FILE", help="从本地 HTML 文件读取内容（跳过网络请求，用于处理浏览器保存的页面）")
    ap.add_argument("--base-url", help="配合 --local-html 使用，指定图片下载的基准 URL")
    ap.add_argument("--force", action="store_true", help="检测到 JS 反爬时仍强制继续处理（内容可能为空或不完整）")
    ap.add_argument(
        "--max-image-bytes",
        type=int,
        default=_DEFAULT_MAX_IMAGE_BYTES,
        help="单张图片最大允许字节数（默认 25MB；设为 0 表示不限制）",
    )
    ap.add_argument(
        "--redact-url",
        dest="redact_url",
        action="store_true",
        default=True,
        help="输出文件中对 URL 脱敏（默认启用）：仅保留 scheme://host/path，移除 query/fragment",
    )
    ap.add_argument(
        "--no-redact-url",
        dest="redact_url",
        action="store_false",
        help="关闭 URL 脱敏（保留完整 URL，包括 query/fragment）",
    )
    ap.add_argument(
        "--no-map-json",
        action="store_true",
        help="不生成 *.assets.json URL→本地映射文件（避免泄露图片 URL）",
    )
    ap.add_argument(
        "--pdf-allow-file-access",
        action="store_true",
        help="生成 PDF 时允许 file:// 访问其他本地文件（可能有安全风险；默认关闭）",
    )
    # Frontmatter 支持
    ap.add_argument("--frontmatter", action="store_true", default=True,
                    help="生成 YAML Frontmatter 元数据头（默认启用）")
    ap.add_argument("--no-frontmatter", action="store_false", dest="frontmatter",
                    help="禁用 YAML Frontmatter")
    ap.add_argument("--tags", help="Frontmatter 中的标签，逗号分隔，如 'tech,ai,tutorial'")
    # Cookie/Header 支持
    ap.add_argument("--cookie", help="Cookie 字符串，如 'session=abc; token=xyz'")
    ap.add_argument("--cookies-file", help="Netscape 格式的 cookies.txt 文件路径")
    ap.add_argument("--headers", help="自定义请求头，JSON 格式，如 '{\"Authorization\": \"Bearer xxx\"}'")
    ap.add_argument("--header", action="append", default=[], help="追加请求头（可重复），如 'Authorization: Bearer xxx'")
    # UA 可配置
    ap.add_argument("--ua-preset", choices=sorted(UA_PRESETS.keys()), default="chrome-win", help="User-Agent 预设（默认 chrome-win）")
    ap.add_argument("--user-agent", "--ua", dest="user_agent", help="自定义 User-Agent（优先于 --ua-preset）")
    # 复杂表格保留 HTML
    ap.add_argument("--keep-html", action="store_true",
                    help="对复杂表格（含 colspan/rowspan）保留原始 HTML 而非强转 Markdown")
    # 手动指定正文区域
    ap.add_argument("--target-id", help="手动指定正文容器 id（如 content / post-content），优先级高于自动抽取")
    ap.add_argument("--target-class", help="手动指定正文容器 class（如 post-body），优先级高于自动抽取")
    # SPA 页面提示
    ap.add_argument("--spa-warn-len", type=int, default=500, help="正文文本长度低于该值时提示可能为 SPA 动态渲染，默认 500；设为 0 可关闭")
    # Wiki 噪音清理
    ap.add_argument("--clean-wiki-noise", action="store_true",
                    help="清理 Wiki 系统噪音（编辑按钮、导航链接、返回顶部等），适用于 PukiWiki/MediaWiki 等站点")
    # 微信公众号文章支持
    ap.add_argument("--wechat", action="store_true",
                    help="微信公众号文章模式：自动提取 rich_media_content 正文并清理交互按钮噪音。"
                         "如不指定，脚本会自动检测 mp.weixin.qq.com 链接并启用此模式")
    
    # ========== 导航/目录剥离参数（Phase 1）==========
    nav_group = ap.add_argument_group("导航剥离参数（Docs/Wiki 站点优化）")
    nav_group.add_argument("--strip-nav", action="store_true",
                           help="移除导航元素（nav/aside/.sidebar 等），适用于 docs 站点批量导出")
    nav_group.add_argument("--strip-page-toc", action="store_true",
                           help="移除页内目录（.toc/.on-this-page 等）")
    nav_group.add_argument("--exclude-selectors",
                           help="自定义移除的元素选择器（逗号分隔），支持：tag/.class/#id/[attr=val]")
    nav_group.add_argument("--anchor-list-threshold", type=int, default=0,
                           help="连续锚点列表移除阈值（默认 0 关闭），建议与 --strip-nav 配合使用，推荐值 10-20")
    
    # ========== 智能正文定位参数（Phase 2）==========
    smart_group = ap.add_argument_group("智能正文定位参数（Phase 2）")
    smart_group.add_argument("--docs-preset", choices=get_available_presets(),
                             help="使用文档框架预设（自动配置 target 和 exclude）：" + 
                                  ", ".join(get_available_presets()))
    smart_group.add_argument("--auto-detect", action="store_true",
                             help="自动检测文档框架并应用预设（高置信度时）")
    smart_group.add_argument("--list-presets", action="store_true",
                             help="列出所有可用的文档框架预设")
    
    # ========== 批量处理参数 ==========
    batch_group = ap.add_argument_group("批量处理参数")
    batch_group.add_argument("--urls-file", help="从文件读取 URL 列表（每行一个，支持 # 注释和 URL|标题 格式）")
    batch_group.add_argument("--output-dir", default="./batch_output", help="批量输出目录（默认 ./batch_output）")
    batch_group.add_argument("--max-workers", type=int, default=3, help="并发线程数（默认 3，建议不超过 5）")
    batch_group.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数（默认 1.0，避免被封）")
    batch_group.add_argument("--skip-errors", action="store_true", help="跳过失败的 URL 继续处理")
    batch_group.add_argument("--download-images", action="store_true", 
                             help="下载图片到本地 assets 目录（默认不下载，保留原始 URL）")
    
    # 合并输出参数
    merge_group = ap.add_argument_group("合并输出参数")
    merge_group.add_argument("--merge", action="store_true", help="合并所有页面为单个 MD 文件")
    merge_group.add_argument("--merge-output", help="合并输出文件名（默认 merged.md）")
    merge_group.add_argument("--toc", action="store_true", help="在合并文件开头生成目录")
    merge_group.add_argument("--merge-title", help="合并文档的主标题")
    merge_group.add_argument("--source-url", help="来源站点 URL（显示在文档信息中）")
    merge_group.add_argument("--rewrite-links", action="store_true",
                             help="将站内链接改写为文档内锚点（仅合并模式有效）")
    merge_group.add_argument("--no-source-summary", action="store_true",
                             help="不在文档开头显示来源信息汇总")
    merge_group.add_argument("--warn-anchor-collisions", action="store_true",
                             help="显示锚点冲突详情（同名标题自动添加后缀 -2, -3...）")
    merge_group.add_argument("--split-output", metavar="DIR",
                             help="同时输出分文件版本到指定目录（与 --merge 配合使用，生成双版本）")
    
    # 爬取模式参数
    crawl_group = ap.add_argument_group("爬取模式参数")
    crawl_group.add_argument("--crawl", action="store_true", help="从索引页提取链接并批量抓取")
    crawl_group.add_argument("--crawl-pattern", help="链接匹配正则表达式（如 'index\\.php\\?MMR'）")
    crawl_group.add_argument("--same-domain", action="store_true", default=True, help="仅抓取同域名链接（默认启用）")
    crawl_group.add_argument("--no-same-domain", action="store_false", dest="same_domain", help="允许抓取跨域链接")
    
    args = ap.parse_args(argv)
    
    # ========== 列出预设 ==========
    if args.list_presets:
        print("\n📦 可用的文档框架预设：\n")
        for name, preset in DOCS_PRESETS.items():
            print(f"  {name:15} - {preset.description}")
            print(f"                   正文 ID: {', '.join(preset.target_ids) or '(无)'}")
            print(f"                   正文 class: {', '.join(preset.target_classes[:3]) or '(无)'}{'...' if len(preset.target_classes) > 3 else ''}")
            print(f"                   排除选择器: {len(preset.exclude_selectors)} 个")
            print()
        print("使用示例：python grab_web_to_md.py URL --docs-preset mintlify")
        return EXIT_SUCCESS
    
    # ========== 批量处理模式 ==========
    is_batch_mode = bool(args.urls_file or args.crawl)
    
    if is_batch_mode:
        return _batch_main(args)
    
    # ========== 单页处理模式（原有逻辑） ==========
    
    page_html: Optional[str] = None  # 可能在 auto-title 或 local-html 模式下提前获取
    session: Optional[requests.Session] = None  # 可能在 auto-title 模式下提前创建

    # --auto-title 与 --out 同时指定时，--out 优先（auto-title 被忽略）
    use_auto_title = bool(args.auto_title and not args.out)

    # 支持 --local-html 模式（从本地文件读取，跳过网络请求）
    if args.local_html:
        if not os.path.isfile(args.local_html):
            print(f"错误：本地 HTML 文件不存在：{args.local_html}", file=sys.stderr)
            return EXIT_ERROR

        # 本地文件同样做体积保护（与 fetch_html 的 --max-html-bytes 行为保持一致）
        try:
            size = os.path.getsize(args.local_html)
            if args.max_html_bytes and args.max_html_bytes > 0 and size > args.max_html_bytes:
                print(
                    f"错误：本地 HTML 文件过大（{size} > {args.max_html_bytes} bytes）：{args.local_html}",
                    file=sys.stderr,
                )
                return EXIT_ERROR
        except OSError:
            pass
        
        # --local-html 模式下，url 参数可选，用于图片下载；优先使用 --base-url
        url = args.base_url or args.url or ""
        if not url:
            print("警告：未指定 --base-url 或 url，图片将无法下载（仅保留原始引用）", file=sys.stderr)
        
        with open(args.local_html, "r", encoding="utf-8", errors="replace") as f:
            page_html = f.read()
        print(f"从本地文件读取：{args.local_html}")
        
        # 输出文件名
        if args.out:
            base = args.out
        elif use_auto_title:
            _page_title = _extract_title_for_filename(page_html, url)
            _auto_name = _sanitize_filename_part(_page_title)
            if len(_auto_name) > 80:
                _auto_name = _auto_name[:80].rstrip("-")
            base = _auto_name + ".md"
            print(f"自动标题命名：{_page_title} → {base}")
        else:
            base = os.path.splitext(os.path.basename(args.local_html))[0] + ".md"
    else:
        # 网络模式：必须提供 URL
        if not args.url:
            ap.error("单页模式必须提供 URL 参数，或使用 --urls-file / --crawl 进入批量模式，或使用 --local-html 读取本地文件")
        
        url = args.url

        if args.out:
            base = args.out
        elif use_auto_title:
            # --auto-title 模式：先获取页面，提取标题后生成文件名
            session = _create_session(args, referer_url=url)
            page_html, exit_code = _fetch_page_html(session, url, args)
            if exit_code is not None:
                return exit_code
            _page_title = _extract_title_for_filename(page_html, url)
            _auto_name = _sanitize_filename_part(_page_title)
            if len(_auto_name) > 80:
                _auto_name = _auto_name[:80].rstrip("-")
            base = _auto_name + ".md"
            print(f"自动标题命名：{_page_title} → {base}")
        else:
            base = _default_basename(url) + ".md"
    
    out_md = base
    # 自动创建同名上级目录（如果用户未指定目录）
    out_md = auto_wrap_output_dir(out_md)
    # 检查输出文件路径长度
    md_dir = os.path.dirname(out_md) or "."
    out_md_name = os.path.basename(out_md)
    out_md_name = _safe_path_length(md_dir, out_md_name)
    out_md = os.path.join(md_dir, out_md_name) if md_dir != "." else out_md_name
    assets_dir = args.assets_dir or (os.path.splitext(out_md)[0] + ".assets")
    map_json = out_md + ".assets.json"
    
    # 确保输出目录存在
    if md_dir != ".":
        os.makedirs(md_dir, exist_ok=True)

    if os.path.exists(out_md) and not args.overwrite:
        print(f"文件已存在：{out_md}（如需覆盖请加 --overwrite）", file=sys.stderr)
        return EXIT_FILE_EXISTS

    # 创建 Session（如果尚未在 auto-title 流程中创建）
    if session is None:
        session = _create_session(args, referer_url=url)

    # 网络模式下下载页面（如果尚未在 auto-title 流程中获取）
    if not args.local_html and page_html is None:
        page_html, exit_code = _fetch_page_html(session, url, args)
        if exit_code is not None:
            return exit_code

    # 微信公众号文章自动检测
    is_wechat = args.wechat
    if url and not is_wechat and is_wechat_article_url(url):
        is_wechat = True
        print("检测到微信公众号文章，自动启用微信模式")
    elif not is_wechat and is_wechat_article_html(page_html):
        is_wechat = True
        print("检测到微信公众号文章特征，自动启用微信模式")

    # 确定正文提取策略
    target_id = args.target_id
    target_class = args.target_class
    exclude_selectors = args.exclude_selectors
    strip_nav = args.strip_nav
    strip_page_toc = args.strip_page_toc
    anchor_list_threshold = args.anchor_list_threshold
    
    # 单页模式：应用 docs-preset（Phase 2）
    if hasattr(args, 'docs_preset') and args.docs_preset:
        preset = DOCS_PRESETS.get(args.docs_preset)
        if preset:
            print(f"📦 使用文档框架预设：{preset.name} ({preset.description})")
            # 应用预设的 target 配置（仅当用户未指定时）
            if not target_id and preset.target_ids:
                target_id = ",".join(preset.target_ids)
            if not target_class and preset.target_classes:
                target_class = ",".join(preset.target_classes)
            # 合并预设的 exclude_selectors
            preset_excludes = ",".join(preset.exclude_selectors)
            if exclude_selectors:
                exclude_selectors = f"{exclude_selectors},{preset_excludes}"
            else:
                exclude_selectors = preset_excludes
            # 自动启用导航剥离
            strip_nav = True
            strip_page_toc = True
            # 预设模式下自动启用锚点列表剥离
            if anchor_list_threshold == 0:
                anchor_list_threshold = 10
            print(f"  • 正文容器 ID：{target_id or '(未设置)'}")
            print(f"  • 正文容器 class：{target_class or '(未设置)'}")
    
    # 单页模式：自动检测文档框架（Phase 2）
    elif hasattr(args, 'auto_detect') and args.auto_detect:
        framework, confidence, signals = detect_docs_framework(page_html)
        if framework and confidence >= 0.6:
            preset = DOCS_PRESETS.get(framework)
            if preset:
                print(f"🔍 自动检测到文档框架：{preset.name}（置信度：{confidence:.0%}）")
                # 应用预设配置
                if not target_id and preset.target_ids:
                    target_id = ",".join(preset.target_ids)
                if not target_class and preset.target_classes:
                    target_class = ",".join(preset.target_classes)
                preset_excludes = ",".join(preset.exclude_selectors)
                if exclude_selectors:
                    exclude_selectors = f"{exclude_selectors},{preset_excludes}"
                else:
                    exclude_selectors = preset_excludes
                strip_nav = True
                strip_page_toc = True
                if anchor_list_threshold == 0:
                    anchor_list_threshold = 10
        elif framework:
            print(f"🔍 检测到可能的文档框架：{framework}（置信度：{confidence:.0%}，未自动应用）")
    
    # 微信模式下，如果未指定 target，自动使用 rich_media_content
    if is_wechat and not target_id and not target_class:
        target_class = "rich_media_content"
        print("使用微信正文区域：rich_media_content")

    # 使用多值 target 提取（Phase 2 支持逗号分隔）
    if target_id or target_class:
        article_html, matched_selector = extract_target_html_multi(
            page_html, target_ids=target_id, target_classes=target_class
        )
        if not article_html:
            print("警告：未找到指定的目标区域，将回退到自动抽取。", file=sys.stderr)
            article_html = extract_main_html(page_html)
        elif matched_selector:
            print(f"使用正文容器：{matched_selector}")
    else:
        article_html = extract_main_html(page_html)

    # 单页模式：应用导航剥离（Phase 1）
    strip_selectors = get_strip_selectors(
        strip_nav=strip_nav,
        strip_page_toc=strip_page_toc,
        exclude_selectors=exclude_selectors,
    )
    if strip_selectors:
        article_html, strip_stats = strip_html_elements(article_html, strip_selectors)
        if strip_stats.elements_removed > 0:
            print(f"已移除 {strip_stats.elements_removed} 个导航元素")

    if args.spa_warn_len and html_text_len(article_html) < args.spa_warn_len:
        print(
            f"警告：抽取到的正文内容较短（<{args.spa_warn_len} 字符），该页面可能为 SPA 动态渲染；"
            "如内容为空/不完整，可尝试：1) 使用 --target-id/--target-class 指定正文区域；"
            "2) 等待页面完整加载后保存 HTML 再处理；3) 使用浏览器开发者工具获取渲染后的 HTML。",
            file=sys.stderr,
        )

    collector = ImageURLCollector(base_url=url)
    collector.feed(article_html)
    image_urls = uniq_preserve_order(collector.image_urls)

    print(f"发现图片：{len(image_urls)} 张，开始下载到：{assets_dir}")
    url_to_local = download_images(
        session=session,
        image_urls=image_urls,
        assets_dir=assets_dir,
        md_dir=md_dir,
        timeout_s=args.timeout,
        retries=args.retries,
        best_effort=bool(args.best_effort_images),
        page_url=url,
        redact_urls=args.redact_url,
        max_image_bytes=args.max_image_bytes,
    )

    # 提取标题（微信模式下优先使用专用提取函数）
    if args.title:
        title = args.title
    elif is_wechat:
        title = extract_wechat_title(page_html) or extract_h1(article_html) or extract_title(page_html) or "Untitled"
    else:
        title = extract_h1(article_html) or extract_title(page_html) or "Untitled"
    md_body = html_to_markdown(
        article_html=article_html,
        base_url=url,
        url_to_local=url_to_local,
        keep_html=args.keep_html,
    )
    md_body = strip_duplicate_h1(md_body, title)

    # 清理噪音内容
    if is_wechat:
        md_body = clean_wechat_noise(md_body)
        print("已清理微信公众号 UI 噪音")

    # 单页模式：锚点列表剥离（Phase 1）
    # 使用局部变量 anchor_list_threshold（可能被预设修改）
    if anchor_list_threshold > 0:
        md_body, anchor_stats = strip_anchor_lists(md_body, anchor_list_threshold)
        if anchor_stats.anchor_lists_removed > 0:
            print(f"已移除 {anchor_stats.anchor_lists_removed} 个锚点列表块（共 {anchor_stats.anchor_lines_removed} 行）")

    # 解析 tags 参数
    tags: Optional[List[str]] = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    display_url = redact_url(url) if args.redact_url else url
    if args.redact_url:
        md_body = redact_urls_in_markdown(md_body)

    with open(out_md, "w", encoding="utf-8") as f:
        if args.frontmatter:
            f.write(generate_frontmatter(title, display_url, tags))
        # 保持正文可读性：无论是否启用 frontmatter，都写入可见标题与来源行。
        f.write(f"# {title}\n\n")
        f.write(f"- Source: {display_url}\n\n")
        f.write(md_body)

    wrote_map_json = False
    if not args.no_map_json:
        with open(map_json, "w", encoding="utf-8") as f:
            map_payload = _redact_url_to_local_map(url_to_local) if args.redact_url else url_to_local
            json.dump(map_payload, f, ensure_ascii=False, indent=2)
        wrote_map_json = True
    else:
        # Bug fix: --no-map-json 时删除旧的映射文件，避免遗留未脱敏的历史 URL
        if os.path.exists(map_json):
            try:
                os.remove(map_json)
                print(f"已删除旧映射文件：{map_json}")
            except OSError as e:
                print(f"警告：无法删除旧映射文件 {map_json}: {e}", file=sys.stderr)

    print(f"已生成：{out_md}")
    print(f"图片目录：{assets_dir}")
    if wrote_map_json:
        print(f"映射文件：{map_json}")

    if args.with_pdf:
        out_pdf = os.path.splitext(out_md)[0] + ".pdf"
        if os.path.exists(out_pdf) and (not args.overwrite):
            print(f"PDF 已存在，跳过：{out_pdf}（如需覆盖请加 --overwrite）", file=sys.stderr)
        else:
            print(f"生成 PDF：{out_pdf}")
            if args.frontmatter:
                # md 文件保留 frontmatter；但 PDF 渲染时剥离元数据块，并补一个可见标题/来源行。
                pdf_md = f"# {title}\n\n- Source: {display_url}\n\n{md_body}"
                md_dir_abs = os.path.dirname(os.path.abspath(out_md)) or "."
                tmp = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w",
                        encoding="utf-8",
                        suffix=".no_frontmatter.md",
                        dir=md_dir_abs,
                        delete=False,
                    ) as tf:
                        tf.write(strip_yaml_frontmatter(pdf_md))
                        tmp = tf.name
                    generate_pdf_from_markdown(md_path=tmp, pdf_path=out_pdf, allow_file_access=args.pdf_allow_file_access)
                finally:
                    if tmp and os.path.isfile(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
            else:
                generate_pdf_from_markdown(md_path=out_md, pdf_path=out_pdf, allow_file_access=args.pdf_allow_file_access)

    if args.validate:
        result = validate_markdown(out_md, assets_dir)
        print("\n校验结果：")
        print(f"- 图片引用数（总）：{result.image_refs}")
        print(f"- 图片引用数（本地）：{result.local_image_refs}")
        print(f"- assets 文件数：{result.asset_files}")
        if result.missing_files:
            print("- 缺失文件：")
            for m in result.missing_files:
                print(f"  - {m}")
            return EXIT_VALIDATION_FAILED
        else:
            print("- 缺失文件：0")

    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
