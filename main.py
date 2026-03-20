from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

import re
import json
import time
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup

@register(
    name="astrbot_plugin_steamachievement_query",
    author="Muroki",
    version="1.0.0",
    desc="查询SteamHunters平台的游戏成就数据，支持Steam64ID/个人资料URL"
)
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.steam_api_key = ""
        self.cache_expire = 300
        self.cache_path = Path("/AstrBot/data/steam_achievement_cache.json")
        if not self.cache_path.parent.exists():
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_cache(self):
        if not self.cache_path.exists():
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False)
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"缓存初始化失败: {e}")
            return {}

    def _save_cache(self, cache_data):
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"缓存保存失败: {e}")

    async def _parse_steam64_id(self, input_str: str) -> str | None:
        """
        宽松的ID解析逻辑（恢复原有功能）
        支持：17位数字ID、Steam URL、自定义ID
        只有完全无法识别时才返回None
        """
        s = input_str.strip()
        
        # 1. 纯17位数字（Steam64ID）- 优先级最高
        if re.fullmatch(r"\d{17}", s):
            return s
        
        # 2. 从Steam URL中提取ID（宽松匹配）
        url_patterns = [
            r"steamcommunity\.com/(?:profiles|id)/([^/?]+)",  # 标准URL
            r"profiles/(\d{17})",  # 简化URL
            r"id/([a-zA-Z0-9_-]+)"  # 自定义ID URL
        ]
        for pattern in url_patterns:
            match = re.search(pattern, s, re.I)
            if match:
                return match.group(1)
        
        # 3. 自定义ID（字母/数字/下划线/连字符）- 宽松匹配
        if re.match(r"^[a-zA-Z0-9_\-]{3,}$", s):
            return s
        
        # 4. 只有完全不符合以上规则才返回None（格式错误）
        return None

    def _parse_update_time(self, title_str):
        try:
            match = re.search(r"Updated:\s*(.+?)(?:&lt;br|$)", title_str, re.I)
            if match:
                return match.group(1).strip().replace("&#39;", "'")
            return "未知"
        except:
            return "未知"

    def _parse_rank(self, rank_elem):
        if rank_elem and rank_elem.find("a"):
            rt = rank_elem.find("a").get_text(strip=True)
            m = re.search(r"#([\d,]+)", rt)
            if m:
                return m.group(1).replace(",", "")
        return "未上榜"

    def _parse_country(self, soup):
        try:
            flag = soup.find("span", class_="flag")
            if flag:
                t = flag.parent.get_text(strip=True)
                if t:
                    return t
                img = flag.find("img")
                if img and img.get("src"):
                    c = img["src"].split("/")[-1].replace(".svg", "").upper()
                    mp = {"CN":"中国","US":"美国","JP":"日本","KR":"韩国","GB":"英国","DE":"德国","FR":"法国","RU":"俄罗斯","CA":"加拿大","AU":"澳大利亚"}
                    return mp.get(c, c)
            return "未知"
        except:
            return "未知"

    async def _fetch_steam_data(self, steam_id):
        url = f"https://steamhunters.com/profiles/{steam_id}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers, timeout=15) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            data = {
                "username": "未知",
                "country": "未知",
                "points": "0",
                "achievements": "0",
                "games_played": "0",
                "games_completed": "0",
                "avg_points": "0",
                "completion_rate": "0%",
                "playtime": "0",
                "update_time": "未知",
                "cn_points_rank": "未上榜",
                "global_points_rank": "未上榜",
                "cn_achievements_rank": "未上榜",
                "global_achievements_rank": "未上榜",
                "is_banned": False,
                "ban_msg": "",
                "has_data": False
            }

            # 封禁检测（全局文本匹配，100%生效）
            txt = soup.get_text(" ").lower()
            if "not listed on the leaderboards" in txt:
                data["is_banned"] = True
                data["ban_msg"] = "无法查询有效排名，该用户疑似因刷成就被平台封禁"

            # 用户名
            h1 = soup.find("h1")
            if h1:
                data["username"] = h1.get_text(strip=True)

            data["country"] = self._parse_country(soup)

            # 各项核心数据解析
            keys = [
                ("points", "ValidPoints"),
                ("achievements", "ValidAchievementUnlockCount"),
                ("games_played", "ValidStartedGameCount"),
                ("games_completed", "ValidCompletedGameCount"),
            ]
            for k, sk in keys:
                tag = soup.find("span", {"data-stat-key": sk})
                if tag:
                    v = re.sub(r"\D", "", tag.get_text())
                    if v:
                        data[k] = v

            # 平均积分 & 完成率
            for k, sk in [("avg_points", "ValidPointsPerAchievement"), ("completion_rate", "ValidAgcObtainable")]:
                tag = soup.find("span", {"data-stat-key": sk})
                if tag:
                    v = tag.get_text(strip=True).replace("..", ".")
                    data[k] = v

            # 游戏时长 最终修复（精准匹配）
            playtime_tag = soup.find("div", class_="stat-item", string=re.compile("Playtime", re.I))
            if playtime_tag:
                pt_text = playtime_tag.get_text(strip=True)
                m = re.search(r"([\d,\.]+)\s*(?:hrs|hours)", pt_text, re.I)
                if m:
                    data["playtime"] = m.group(1).replace(",", "")
            else:
                all_text = soup.get_text()
                m = re.search(r"Playtime.*?([\d,\.]+)\s*(?:hrs|hours)", all_text, re.I | re.S)
                if m:
                    data["playtime"] = m.group(1).replace(",", "")

            # 更新时间解析
            time_tag = soup.find("time", class_="title")
            if time_tag and time_tag.get("title"):
                data["update_time"] = self._parse_update_time(time_tag["title"])

            # 判断是否有有效数据（核心字段非空/非0）
            core_fields = [data["points"], data["achievements"], data["games_played"]]
            data["has_data"] = any(x not in ("0", "") for x in core_fields)

            # 排名解析（未封禁且有数据时）
            if not data["is_banned"] and data["has_data"]:
                cp = soup.find("td", title=re.compile("Country points rank", re.I))
                gp = soup.find("td", title=re.compile("Global points rank", re.I))
                ca = soup.find("td", title=re.compile("Country achievements rank", re.I))
                ga = soup.find("td", title=re.compile("Global achievements rank", re.I))
                
                data["cn_points_rank"] = self._parse_rank(cp)
                data["global_points_rank"] = self._parse_rank(gp)
                data["cn_achievements_rank"] = self._parse_rank(ca)
                data["global_achievements_rank"] = self._parse_rank(ga)

            return data

        except Exception as e:
            logger.error(f"数据获取失败: {str(e)}")
            return None

    @filter.command("查steam成就")
    async def steam_achievement_handler(self, event):
        msg = event.message_str.strip()
        argv = msg.split(maxsplit=1)
        
        # 1. 无参数 - 提示正确格式
        if len(argv) < 2:
            yield event.plain_result("请输入正确格式：/查steam成就 + steam64id 或steam主页URL")
            return

        # 2. 解析ID
        sid = await self._parse_steam64_id(argv[1])
        # 3. ID格式完全错误 - 提示正确格式
        if not sid:
            yield event.plain_result("请输入正确格式：/查steam成就 + steam64id 或steam主页URL")
            return

        # 4. 缓存逻辑
        cache = self._init_cache()
        now = int(time.time())
        key = f"sid_{sid}"

        if key in cache and now - cache[key]["ts"] < self.cache_expire:
            data = cache[key]["data"]
        else:
            # 5. 调用接口获取数据
            data = await self._fetch_steam_data(sid)
            # 6. 接口返回空（无法访问/无数据）- 提示检查档案
            if not data:
                yield event.plain_result("未查询到档案，请检查steam隐私设置，并前往SteamHunter更新档案")
                return
            # 7. 存入缓存
            cache[key] = {"ts": now, "data": data}
            self._save_cache(cache)

        # 8. 有ID但无有效数据（非封禁）- 提示检查档案
        if not data["has_data"] and not data["is_banned"]:
            yield event.plain_result("未查询到档案，请检查steam隐私设置，并前往SteamHunter更新档案")
            return

        # 9. 构建正常回复
        lines = [
            f"🎮 Steam成就查询结果（{data['username']}）",
            f"├─ 🗺️ 所属地区：{data['country']}",
            f"├─ 🏆 总成就积分：{data['points']}",
            f"├─ 🎯 已解锁成就：{data['achievements']} 个",
            f"├─ 🎮 玩过的游戏：{data['games_played']} 款",
            f"├─ 🎰 全成就游戏：{data['games_completed']} 款",
            f"├─ ⭐ 平均成就积分：{data['avg_points']}",
            f"├─ 📊 成就完成率：{data['completion_rate']}",
            f"├─ ⏱️ 总游戏时长：{data['playtime']} 小时",
            f"├─ 🕒 档案最近更新：{data['update_time']}"
        ]

        # 10. 封禁提示
        if data["is_banned"]:
            lines.append(f"├─ ⚠️ {data['ban_msg']}")
        else:
            # 11. 显示排名（有数据时）
            lines += [
                "├────────────────────────────",
                "├─ 📈 积分排名",
                f"│  ├─ 🌍 {data['country']}排名：{data['cn_points_rank']}",
                f"│  └─ 🌍 世界排名：{data['global_points_rank']}",
                "├─ 🏅 成就数排名",
                f"│  ├─ 🌍 {data['country']}排名：{data['cn_achievements_rank']}",
                f"│  └─ 🌍 世界排名：{data['global_achievements_rank']}"
            ]

        # 12. 输出最终结果
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        logger.info("Steam成就插件已卸载")
