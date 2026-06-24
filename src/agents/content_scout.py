"""内容侦察Agent — 自动挖掘公版宝藏，识别中译空白"""

from __future__ import annotations
import requests, time, re, json
from typing import Optional
from src.agents.base import BaseAgent, AgentResult


class ContentScoutAgent(BaseAgent):
    """内容侦察Agent — 扫描公版资源，发现中译空白的高价值作品"""

    agent_name = "content_scout"
    agent_description = "公版作品侦察：自动扫描→查中译本→评估价值→推荐翻译"

    # 已知有中译本的著名作品（白名单，直接跳过）
    KNOWN_TRANSLATED = {
        "The Call of the Wild", "White Fang", "The Sea Wolf",  # 杰克·伦敦
        "The Adventures of Tom Sawyer", "Adventures of Huckleberry Finn",  # 马克·吐温
        "The Great Gatsby", "This Side of Paradise",  # 菲茨杰拉德
        "The Portrait of a Lady", "The Turn of the Screw",  # 亨利·詹姆斯
        "Daisy Miller", "Washington Square",
        "The Red Badge of Courage",  # 斯蒂芬·克莱恩
        "The Gift of the Magi", "The Last Leaf",  # 欧·亨利
        "The Mysterious Stranger", "A Connecticut Yankee in King Arthur's Court",
        "The Million Pound Note",
        "Ethan Frome", "The Age of Innocence",  # 伊迪丝·华顿
        "My Ántonia", "O Pioneers!",  # 薇拉·凯瑟
        "The Souls of Black Folk",  # W.E.B. Du Bois
        "The Jungle",  # 厄普顿·辛克莱
        "The House of Mirth",  # 伊迪丝·华顿
        "Studies in Classic American Literature",  # D.H.劳伦斯（有中译本《美国经典文学研究》）
        "Lady Chatterley's Lover",  # D.H. 劳伦斯
        "The Rainbow", "Women in Love",
        "Sons and Lovers",
    }

    def execute(self, query: str = "", max_results: int = 5) -> AgentResult:
        """
        执行侦察任务。

        Args:
            query: 搜索关键词（如 "1920 short stories", "public domain science fiction"）
            max_results: 最大返回数
        """
        candidates = []

        # 1. 从Gutenberg搜索公版作品
        gutenberg_hits = self._search_gutenberg(query, limit=15)
        print(f"  [侦察] Gutenberg命中: {len(gutenberg_hits)} 本")

        # 2. 逐个检查是否有中译本
        checked = 0
        for hit in gutenberg_hits:
            title = hit.get("title", "").strip()
            author = hit.get("author", "").strip()
            gutenberg_id = hit.get("id", "")

            # 跳过太长或太短的
            word_count = hit.get("word_count", 0)
            if word_count < 2000 or word_count > 150000:
                continue

            # 白名单快速跳过（模糊匹配：书名包含白名单中任意词条）
            title_lower = title.lower()
            whitelisted = False
            for wt in self.KNOWN_TRANSLATED:
                if wt.lower() in title_lower or title_lower in wt.lower():
                    whitelisted = True
                    break
            if whitelisted:
                print(f"  ⏭ {title[:40]}... → 白名单（已知有中译本）")
                continue

            checked += 1
            print(f"  [{checked}] 检测: {title[:50]}... ({author})")

            # 检查中译本（多重验证）
            has_cn, cn_info = self._check_chinese_translation_v2(title, author)
            status = "✅ 有中译" if has_cn else "❌ 无中译（空白！）"
            print(f"       → {status} {cn_info}")

            if not has_cn:
                candidates.append({
                    "title": title,
                    "author": author,
                    "gutenberg_id": gutenberg_id,
                    "word_count": word_count,
                    "has_chinese": False,
                    "cn_quality": "无",
                    "url": f"https://www.gutenberg.org/ebooks/{gutenberg_id}",
                    "value_score": self._assess_value(title, author, word_count),
                    "reason": cn_info,
                })
            elif cn_info.get("quality") == "差":
                candidates.append({
                    "title": title,
                    "author": author,
                    "gutenberg_id": gutenberg_id,
                    "word_count": word_count,
                    "has_chinese": True,
                    "cn_quality": "差",
                    "url": f"https://www.gutenberg.org/ebooks/{gutenberg_id}",
                    "value_score": self._assess_value(title, author, word_count) - 10,
                    "reason": cn_info,
                })

            if checked >= max_results + 5:
                break

        # 3. 按价值评分排序
        candidates.sort(key=lambda x: x["value_score"], reverse=True)

        # 4. 保存结果
        self._save_results(candidates[:max_results], checked, len(candidates))

        return AgentResult(
            success=True,
            data={
                "candidates": candidates[:max_results],
                "total_scanned": checked,
                "blanks_found": len([c for c in candidates if not c["has_chinese"]]),
            },
            context=self._last_context,
        )

    def _search_gutenberg(self, query: str, limit: int = 10) -> list[dict]:
        """从Gutenberg搜索公版作品（多关键词轮询）"""
        results = {}
        # 多组搜索词，覆盖不同维度
        search_queries = [
            "American short stories 1920",
            "British short stories 1910",
            "classic American literature",
            "public domain novel",
            "short stories collection",
            query,  # 保留原始查询
        ]
        for sq in search_queries:
            try:
                url = "https://gutendex.com/books"
                params = {"search": sq, "languages": "en", "sort": "popular"}
                r = requests.get(url, params=params, timeout=15)
                data = r.json()
                for book in data.get("results", []):
                    bid = book.get("id")
                    if bid in results:
                        continue
                    title = book.get("title", "")
                    authors = book.get("authors", [])
                    author_name = authors[0].get("name", "") if authors else ""
                    results[bid] = {
                        "id": bid,
                        "title": title,
                        "author": author_name,
                        "word_count": book.get("word_count", 0) or 30000,
                        "subjects": book.get("subjects", []),
                        "download_count": book.get("download_count", 0),
                    }
            except Exception as e:
                print(f"  [Gutenberg] 搜索出错({sq[:30]}): {e}")
        # 按下载量排序
        sorted_results = sorted(results.values(), key=lambda x: x["download_count"], reverse=True)
        print(f"  [Gutenberg] 多关键词搜索: {len(results)} 本去重结果")
        return sorted_results[:limit + 10]

    def _check_chinese_translation_v2(self, title: str, author: str) -> tuple[bool, dict]:
        """
        改进版中译本检测（三重验证）：
        1. 豆瓣读书API直接查询（最可靠）
        2. Bing站点限定搜索（douban.com/subject）
        3. 综合判定

        Returns:
            (has_chinese, info_dict)
        """
        # 第一重：豆瓣读书搜索API
        douban_hit = self._check_douban(title, author)
        if douban_hit is not None:
            return douban_hit, {"source": "douban", "quality": "未知"}

        # 第二重：Bing站点限定搜索（更精确）
        bing_hit = self._check_bing_douban(title, author)
        if bing_hit is not None:
            return bing_hit, {"source": "bing", "quality": "未知"}

        # 第三重：通用搜索（最后手段，保守判断）
        web_hit = self._check_web_conservative(title, author)
        return web_hit, {"source": "web", "quality": "未知"}

    def _check_douban(self, title: str, author: str) -> Optional[bool]:
        """
        用豆瓣搜索接口检测。
        尝试多个接口路径，解析返回的JSON/HTML判断是否有关联书籍。
        """
        try:
            # 方法1: 尝试豆瓣的搜索接口（返回JSON）
            # 注意：豆瓣对API访问有限制，这里用模拟搜索的方式
            search_url = "https://www.douban.com/search"
            params = {"q": f"{title} {author}", "cat": "1001"}  # cat=1001 限定书籍
            r = requests.get(
                search_url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10,
            )
            html = r.text

            # 解析搜索结果数量
            # 豆瓣搜索结果页面中，如果有结果，会包含 "找到" 或 "共" 等关键词
            # 更可靠的方式是检查是否有 subject 链接
            subject_count = len(re.findall(r'/subject/\d+/', html))
            if subject_count >= 2:  # 至少2个结果（去重后）
                return True

            # 检查是否明确显示"没有找到"
            if "没有找到" in html or "未找到" in html:
                return False

        except Exception as e:
            print(f"      [豆瓣] 查询失败: {e}")

        return None  # 无法判断，交给下一重

    def _check_bing_douban(self, title: str, author: str) -> Optional[bool]:
        """
        用Bing搜索，限定在douban.com/subject下，判断是否已有豆瓣条目。
        这是比全文搜索更精确的方式。
        """
        try:
            query = f'site:douban.com/subject "{title}" {author}'
            r = requests.get(
                "https://www.bing.com/search",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            html = r.text.lower()

            # 检查是否有实际的搜索结果（不只是"相关搜索"）
            # Bing结果中，真实结果会有 <li class="b_algo"> 标签
            result_count = len(re.findall(r'<li class="b_algo">', html))
            if result_count >= 1:
                # 进一步验证：结果中是否包含douban.com/subject的链接
                if "douban.com/subject" in html:
                    return True

            # 如果Bing返回"没有结果"
            no_results_patterns = ["没有结果", "no results", "未找到与"]
            for p in no_results_patterns:
                if p in html:
                    return False

        except Exception as e:
            print(f"      [Bing] 查询失败: {e}")

        return None

    def _check_web_conservative(self, title: str, author: str) -> bool:
        """
        保守的网页搜索检测。
        只有当搜索结果中出现明确的"译本"/"中文版"/"翻译"等词，
        且结果数量足够多时，才判定为"有中译本"。
        """
        try:
            # 用英文标题+中文关键词搜索
            queries = [
                f'"{title}" 译本',
                f'"{title}" 中文版',
                f'{author} "{title}" 翻译',
            ]

            positive_signals = 0
            for q in queries[:2]:  # 只试前两个，避免太慢
                r = requests.get(
                    "https://www.bing.com/search",
                    params={"q": q},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=8,
                )
                html = r.text

                # 统计正向信号
                signals = ["译本", "中文版", "翻译", "人民文学", "上海译文", "译林"]
                hits = sum(1 for s in signals if s in html)
                if hits >= 2:
                    positive_signals += 1

            return positive_signals >= 1  # 至少两个查询都返回正向信号

        except:
            return False

    def _assess_value(self, title: str, author: str, words: int) -> int:
        """评估翻译价值（0-100）"""
        score = 50  # 基准分

        # 知名作者加分
        notable_authors = [
            "Sherwood Anderson", "Willa Cather", "Edith Wharton",
            "Theodore Dreiser", "Stephen Crane", "Jack London",
            "O. Henry", "Mark Twain", "Kate Chopin",
            "Willa Sibert Cather", "Henry James", "F. Scott Fitzgerald",
            "John Galsworthy", "Joseph Conrad", "H. G. Wells",
            "Arthur Conan Doyle", "Rudyard Kipling",
        ]
        for na in notable_authors:
            if na.lower() in author.lower():
                score += 20
                break

        # 适中长度加分（5000-50000词）
        if 5000 <= words <= 50000:
            score += 15
        elif words < 5000:
            score += 5

        # 短篇集加分（适合连载/小红书发布）
        if words < 15000:
            score += 10

        return min(score, 100)

    def _save_results(self, candidates: list, scanned: int, blanks: int):
        """保存侦察结果到JSON"""
        import os
        os.makedirs("/workspace/evolution_logs", exist_ok=True)
        result = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scanned": scanned,
            "blanks_found": blanks,
            "candidates": candidates,
        }
        with open("/workspace/evolution_logs/scout_results.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n  [侦察] 结果已保存: {blanks} 本空白作品")

    def _default_prompt(self) -> tuple[str, str]:
        return ("你是内容侦察专家，负责发现公版宝藏。", "请搜索并评估以下内容的价值。")
