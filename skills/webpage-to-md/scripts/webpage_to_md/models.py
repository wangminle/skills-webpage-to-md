from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class JSChallengeResult:
    """JS 反爬检测结果"""

    is_challenge: bool  # 是否为 JS 挑战页面
    confidence: str  # "high", "medium", "low"
    signals: List[str]  # 检测到的信号

    def get_suggestions(self, url: str) -> List[str]:
        """根据检测结果生成建议"""
        return [
            "1. 在浏览器中打开该 URL，等待页面完全加载",
            "2. 右键点击页面 → 「另存为」或「存储为」→ 保存为 .html 文件",
            "3. 使用 --local-html 参数处理本地文件：",
            f"   python grab_web_to_md.py --local-html saved.html --base-url \"{url}\" --out output.md",
        ]


@dataclass
class ValidationResult:
    image_refs: int
    local_image_refs: int
    asset_files: int
    missing_files: List[str]


@dataclass
class BatchPageResult:
    """单个页面的处理结果"""

    url: str
    title: str
    md_content: str
    success: bool
    error: Optional[str] = None
    order: int = 0  # 用于保持原始顺序
    image_urls: List[str] = field(default_factory=list)  # 收集到的图片 URL


@dataclass
class BatchConfig:
    """批量处理配置"""

    max_workers: int = 3
    delay: float = 1.0
    skip_errors: bool = False
    timeout: int = 60
    retries: int = 3
    max_html_bytes: int = 10 * 1024 * 1024
    best_effort_images: bool = True
    keep_html: bool = False
    target_id: Optional[str] = None
    target_class: Optional[str] = None
    clean_wiki_noise: bool = False  # 清理 Wiki 系统噪音（编辑按钮、导航链接等）
    download_images: bool = False  # 是否下载图片到本地
    wechat: bool = False  # 微信公众号文章模式
    # Phase 1: 导航剥离参数
    strip_nav: bool = False  # 移除导航元素
    strip_page_toc: bool = False  # 移除页内目录
    exclude_selectors: Optional[str] = None  # 自定义移除选择器
    anchor_list_threshold: int = 0  # 连续锚点列表移除阈值，默认 0（关闭）
    # Phase 2: 智能正文定位参数
    docs_preset: Optional[str] = None  # 文档框架预设
    auto_detect: bool = False  # 自动检测框架
    force: bool = False  # 检测到 JS 反爬时强制继续
    no_ssr: bool = False  # 禁用 SSR 数据自动提取
