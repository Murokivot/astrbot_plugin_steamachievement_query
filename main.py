from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

import re
import json
import time
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime

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
        self.cache_expire = 3600
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
        """从title中提取Updated时间并格式化"""
        try:
            match = re.search(r"Updated: (.+?)<br", title_str)
            if match:
                update_str = match.group(1).strip()
                return update_str.replace("&#39;", "'")
            return "未知"
        except:
            return "未知"

    def _parse_rank(self, rank_elem) -> str:
        """通用排名解析函数"""
        if rank_elem and rank_elem.find("a"):
            rank_text = rank_elem.find("a").get_text(strip=True)
            rank_match = re.search(r"#([\d,]+)", rank_text)
            if rank_match:
                return rank_match.group(1).replace(",", "")
        return "未上榜"

    def _parse_country(self, soup) -> str:
        """解析用户国籍/地区"""
        try:
            # 匹配包含国旗图标的元素
            flag_elem = soup.find("span", class_="flag")
            if flag_elem:
                # 方式1：从父节点提取国家名称
                parent_text = flag_elem.parent.get_text(strip=True)
                if parent_text:
                    return parent_text
                # 方式2：从国旗图片URL提取国家代码并映射
                img_elem = flag_elem.find("img")
                if img_elem and img_elem.get("src"):
                    src = img_elem["src"]
                    country_code = src.split("/")[-1].replace(".svg", "").upper()
                    # 常用国家代码映射（可扩展）
                    country_map = {
                        "CN": "中国",
                        "US": "美国",
                        "JP": "日本",
                        "KR": "韩国",
                        "GB": "英国",
                        "DE": "德国",
                        "FR": "法国",
                        "RU": "俄罗斯",
                        "CA": "加拿大",
                        "AU": "澳大利亚"
                    }
                    return country_map.get(country_code, country_code)
            return "未知"
        except Exception as e:
            logger.error(f"解析国籍失败: {e}")
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

                    # 初始化完整数据结构
                    data = {
                        "username": "未知",
                        "country": "未知",  # 新增：国籍/地区
                        "points": "0",
                        "achievements": "0",
                        "games_played": "0",
                        "games_completed": "0",
                        "avg_points": "0",
                        "completion_rate": "0%",
                        "playtime": "0",
                        "update_time": "未知",
                        # 积分排名
                        "cn_points_rank": "未上榜",
                        "global_points_rank": "未上榜",
                        # 成就数排名
                        "cn_achievements_rank": "未上榜",
                        "global_achievements_rank": "未上榜",
                        # 封禁检测
                        "is_banned": False,
                        "ban_note": ""
                    }

                    # ========== 封禁检测 ==========
                    ban_elem = soup.find("p", string=re.compile("Not listed on the leaderboards", re.IGNORECASE))
                    if ban_elem:
                        data["is_banned"] = True
                        data["ban_note"] = "无法查询有效排名，该用户疑似因刷成就被平台封禁"
                        logger.warning(f"用户 {steam_id} 疑似被SteamHunters封禁")

                    # ========== 新增：解析国籍/地区 ==========
                    data["country"] = self._parse_country(soup)

                    # 解析用户名
                    user = soup.find("h1")
                    if user:
                        data["username"] = user.get_text(strip=True)

                    # 解析核心成就数据
                    points = soup.find("span", {"data-stat-key": "ValidPoints"})
                    if points:
                        data["points"] = re.sub(r"\D", "", points.get_text())

                    ach = soup.find("span", {"data-stat-key": "ValidAchievementUnlockCount"})
                    if ach:
                        data["achievements"] = re.sub(r"\D", "", ach.get_text())

                    played = soup.find("span", {"data-stat-key": "ValidStartedGameCount"})
                    if played:
                        data["games_played"] = re.sub(r"\D", "", played.get_text())

                    completed = soup.find("span", {"data-stat-key": "ValidCompletedGameCount"})
                    if completed:
                        data["games_completed"] = re.sub(r"\D", "", completed.get_text())

                    # 修复小数点问题
                    avg = soup.find("span", {"data-stat-key": "ValidPointsPerAchievement"})
                    if avg:
                        txt = avg.get_text(strip=True)
                        data["avg_points"] = txt.replace("..", ".").strip()

                    comp = soup.find("span", {"data-stat-key": "ValidAgcObtainable"})
                    if comp:
                        txt = comp.get_text(strip=True)
                        data["completion_rate"] = txt.replace("..", ".").strip()

                    # 解析游戏时长
                    playtime = soup.find("span", {"data-stat-key": "Playtime"})
                    if playtime and playtime.parent.get("title"):
                        nums = re.findall(r"\d+", playtime.parent["title"])
                        if nums:
                            data["playtime"] = nums[0]

                    # 解析最近更新时间
                    time_elem = soup.find("time", class_="title")
                    if time_elem and time_elem.get("title"):
                        data["update_time"] = self._parse_update_time(time_elem["title"])

                    # 未封禁时解析排名
                    if not data["is_banned"]:
                        # 积分排名解析
                        cn_points_elem = soup.find("td", title=re.compile("Country points rank", re.IGNORECASE))
                        data["cn_points_rank"] = self._parse_rank(cn_points_elem)

                        global_points_elem = soup.find("td", title=re.compile("Global points rank", re.IGNORECASE))
                        data["global_points_rank"] = self._parse_rank(global_points_elem)

                        # 成就数排名解析
                        cn_ach_elem = soup.find("td", title=re.compile("Country achievements rank", re.IGNORECASE))
                        data["cn_achievements_rank"] = self._parse_rank(cn_ach_elem)

                        global_ach_elem = soup.find("td", title=re.compile("Global achievements rank", re.IGNORECASE))
                        data["global_achievements_rank"] = self._parse_rank(global_ach_elem)

                    return data
        except Exception as e:
            logger.error(f"获取失败: {e}")
            return None

    @filter.command("查steam成就")
    async def steam_achievement_handler(self, event: AstrMessageEvent):
        '''查询SteamHunters成就（含国籍+封禁检测+积分/成就数排名+更新时间）'''
        message_str = event.message_str.strip()
        argv = message_str.split(maxsplit=1)

        if len(argv) < 2:
            yield event.plain_result("""❌ 格式错误
用法：/查steam成就 Steam64ID 或 个人资料URL
例：/查steam成就 76561198187914141""")
            return

        param = argv[1]
        steam_id = await self._parse_steam64_id(param)
        if not steam_id:
            yield event.plain_result("❌ 无法识别Steam ID！请输入17位Steam64 ID或有效URL")
            return

        cache = self._init_cache()
        now = int(time.time())
        key = f"sid_{steam_id}"

        if key in cache and (now - cache[key]["ts"]) < self.cache_expire:
            data = cache[key]["data"]
        else:
            data = await self._fetch_steam_data(steam_id)
            if not data:
                yield event.plain_result("查询失败，请检查网络或稍后再试")
                return
            cache[key] = {"ts": now, "data": data}
            self._save_cache(cache)

        # 构建基础回复
        reply_lines = [
            f"🎮 Steam成就查询结果（{data['username']}）",
            f"├─ 🗺️ 所属地区：{data['country']}",  # 新增：显示国籍/地区
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
            reply_lines.append(f"├─ ⚠️ {data['ban_note']}")
        else:
            # 未封禁时显示排名（全国排名前加国籍）
            reply_lines.extend([
                f"├────────────────────────────",
                f"├─ 📈 积分排名",
                f"│  ├─ 🇨🇳 {data['country']}排名：{data['cn_points_rank']}",  # 显示对应国家排名
                f"│  └─ 🌍 世界排名：{data['global_points_rank']}",
                f"├─ 🏅 成就数排名",
                f"│  ├─ 🇨🇳 {data['country']}排名：{data['cn_achievements_rank']}",
                f"│  └─ 🌍 世界排名：{data['global_achievements_rank']}"
            ])

        # 拼接最终回复
        reply = "\n".join(reply_lines)
        yield event.plain_result(reply)

    async def terminate(self):
        logger.info("Steam成就插件已卸载")
