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
    name="steam_achievement",
    author="YourName",
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
        s = input_str.strip()
        if re.fullmatch(r"\d{17}", s):
            return s
        match = re.search(r"profiles/(\d{17})", s)
        if match:
            return match.group(1)
        id_match = re.search(r"id/([^/]+)", s)
        if id_match:
            return id_match.group(1)
        if re.match(r"^[a-zA-Z0-9_-]+$", s):
            return s
        return None

    def _parse_update_time(self, title_str: str) -> str:
        try:
            match = re.search(r"Updated:\s*(.+?)(?:&lt;br|$)", title_str, re.I)
            if match:
                return match.group(1).strip().replace("&#39;", "'")
            return "未知"
        except:
            return "未知"

    def _parse_rank(self, rank_elem) -> str:
        if rank_elem and rank_elem.find("a"):
            rank_text = rank_elem.find("a").get_text(strip=True)
            rank_match = re.search(r"#([\d,]+)", rank_text)
            if rank_match:
                return rank_match.group(1).replace(",", "")
        return "未上榜"

    def _parse_country(self, soup) -> str:
        try:
            flag_elem = soup.find("span", class_="flag")
            if flag_elem:
                pt = flag_elem.parent.get_text(strip=True)
                if pt:
                    return pt
                img = flag_elem.find("img")
                if img and img.get("src"):
                    c = img["src"].split("/")[-1].replace(".svg", "").upper()
                    m = {
                        "CN":"中国","US":"美国","JP":"日本","KR":"韩国","GB":"英国",
                        "DE":"德国","FR":"法国","RU":"俄罗斯","CA":"加拿大","AU":"澳大利亚"
                    }
                    return m.get(c, c)
            return "未知"
        except:
            return "未知"

    async def _fetch_steam_data(self, steam_id: str) -> dict | None:
        url = f"https://steamhunters.com/profiles/{steam_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=15) as resp:
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
                "has_data": False  # 新增：标记是否有有效数据
            }

            # 1. 封禁检测（全局文本匹配，100%生效）
            body_text = soup.get_text(" ").lower()
            if "not listed on the leaderboards" in body_text:
                data["is_banned"] = True
                data["ban_msg"] = "无法查询有效排名，该用户疑似因刷成就被平台封禁"

            # 2. 解析核心数据
            # 用户名
            user = soup.find("h1")
            if user:
                data["username"] = user.get_text(strip=True)

            # 国籍
            data["country"] = self._parse_country(soup)

            # 积分
            points = soup.find("span", {"data-stat-key": "ValidPoints"})
            if points:
                data["points"] = re.sub(r"\D", "", points.get_text())

            # 成就数
            ach = soup.find("span", {"data-stat-key": "ValidAchievementUnlockCount"})
            if ach:
                data["achievements"] = re.sub(r"\D", "", ach.get_text())

            # 玩过游戏
            played = soup.find("span", {"data-stat-key": "ValidStartedGameCount"})
            if played:
                data["games_played"] = re.sub(r"\D", "", played.get_text())

            # 全成就游戏
            completed = soup.find("span", {"data-stat-key": "ValidCompletedGameCount"})
            if completed:
                data["games_completed"] = re.sub(r"\D", "", completed.get_text())

            # 平均积分
            avg = soup.find("span", {"data-stat-key": "ValidPointsPerAchievement"})
            if avg:
                t = avg.get_text(strip=True).replace("..", ".")
                data["avg_points"] = t

            # 完成率
            comp = soup.find("span", {"data-stat-key": "ValidAgcObtainable"})
            if comp:
                t = comp.get_text(strip=True).replace("..", ".")
                data["completion_rate"] = t

            # 游戏时长
            playtime_tag = soup.find("span", {"data-stat-key": "Playtime"})
            if playtime_tag:
                txt = playtime_tag.get_text(strip=True)
                m = re.search(r"([\d,]+)", txt)
                if m:
                    data["playtime"] = m.group(1).replace(",", "")

            # 更新时间
            time_tag = soup.find("time", class_="title")
            if time_tag and time_tag.get("title"):
                data["update_time"] = self._parse_update_time(time_tag["title"])

            # 3. 判断是否有有效数据（核心字段非0/未知）
            core_fields = [
                data["points"], data["achievements"], 
                data["games_played"], data["playtime"]
            ]
            # 如果有任意一个核心字段非0，说明有数据
            data["has_data"] = any([field != "0" and field != "" for field in core_fields])

            # 4. 排名（没被封禁且有数据才读）
            if not data["is_banned"] and data["has_data"]:
                cn_p = soup.find("td", title=re.compile("Country points rank", re.I))
                data["cn_points_rank"] = self._parse_rank(cn_p)

                gl_p = soup.find("td", title=re.compile("Global points rank", re.I))
                data["global_points_rank"] = self._parse_rank(gl_p)

                cn_a = soup.find("td", title=re.compile("Country achievements rank", re.I))
                data["cn_achievements_rank"] = self._parse_rank(cn_a)

                gl_a = soup.find("td", title=re.compile("Global achievements rank", re.I))
                data["global_achievements_rank"] = self._parse_rank(gl_a)

            return data

        except Exception as e:
            logger.error(f"获取失败: {e}")
            return None

    @filter.command("查steam成就")
    async def steam_achievement_handler(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        argv = msg.split(maxsplit=1)
        if len(argv) < 2:
            yield event.plain_result("用法：/查steam成就 Steam64ID/URL")
            return

        sid = await self._parse_steam64_id(argv[1])
        if not sid:
            yield event.plain_result("无法识别SteamID")
            return

        cache = self._init_cache()
        now = int(time.time())
        key = f"sid_{sid}"

        if key in cache and now - cache[key]["ts"] < self.cache_expire:
            data = cache[key]["data"]
        else:
            data = await self._fetch_steam_data(sid)
            if not data:
                # 兜底提示：无档案
                yield event.plain_result("未查询到档案，请检查steam隐私设置，并前往SteamHunter更新档案")
                return
            cache[key] = {"ts": now, "data": data}
            self._save_cache(cache)

        # ========== 新增：无数据兜底提示 ==========
        if not data["has_data"] and not data["is_banned"]:
            yield event.plain_result("未查询到档案，请检查steam隐私设置，并前往SteamHunter更新档案")
            return

        # 构建正常回复
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

        # 封禁提示
        if data["is_banned"]:
            lines.append(f"├─ ⚠️ {data['ban_msg']}")
        else:
            # 有数据才显示排名
            if data["has_data"]:
                lines += [
                    "├────────────────────────────",
                    "├─ 📈 积分排名",
                    f"│  ├─ 🌍{data['country']}排名：{data['cn_points_rank']}",
                    f"│  └─ 🌍 世界排名：{data['global_points_rank']}",
                    "├─ 🏅 成就数排名",
                    f"│  ├─ 🌍{data['country']}排名：{data['cn_achievements_rank']}",
                    f"│  └─ 🌍 世界排名：{data['global_achievements_rank']}"
                ]

        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        logger.info("Steam成就插件已卸载")
