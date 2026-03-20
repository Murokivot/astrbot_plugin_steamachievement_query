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
    desc="查询SteamHunters平台的游戏成就数据，精准解析排名"
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

    def _parse_update_time(self, title_str):
        try:
            match = re.search(r"Updated:\s*(.+?)(?:&lt;br|$)", title_str, re.I)
            if match:
                return match.group(1).strip().replace("&#39;", "'")
            return "未知"
        except:
            return "未知"

    def _parse_country(self, soup):
        try:
            country_text = soup.find(text=re.compile(r"China|USA|Japan|Korea|UK|Germany|France|Russia|Canada|Australia"))
            if country_text:
                mp = {"China":"中国","USA":"美国","Japan":"日本","Korea":"韩国","UK":"英国","Germany":"德国","France":"法国","Russia":"俄罗斯","Canada":"加拿大","Australia":"澳大利亚"}
                return mp.get(country_text.strip(), country_text.strip())
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

    def _parse_ymd_from_playtime(self, playtime_text):
        years, months, days = 0, 0, 0
        year_match = re.search(r"(\d+)(?:&frac14;|¼|\\u00bc|.25)?\s*year", playtime_text, re.I)
        month_match = re.search(r"(\d+)(?:&frac12;|½|\\u00bd|.5)?\s*month", playtime_text, re.I)
        day_match = re.search(r"(\d+)(?:&frac34;|¾|\\u00be|.75)?\s*day", playtime_text, re.I)

        if year_match:
            years = int(year_match.group(1))
            if any(s in playtime_text for s in ["¼", "&frac14;", ".25"]):
                months += 3
        if month_match:
            months += int(month_match.group(1))
            if any(s in playtime_text for s in ["½", "&frac12;", ".5"]):
                days += 15
        if day_match:
            days += int(day_match.group(1))
            if any(s in playtime_text for s in ["¾", "&frac34;", ".75"]):
                hours = int(days * 24 + 18)

        total_hours = years * 8760 + months * 730 + days * 24
        if months >= 12:
            years += months // 12
            months = months % 12
        return str(years), str(months), str(days), str(total_hours)

    async def _fetch_steam_data(self, steam_id):
        url = f"https://steamhunters.com/profiles/{steam_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://steamhunters.com/",
            "Connection": "keep-alive"
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers, timeout=15) as resp:
                    if resp.status == 404 and not steam_id.isdigit():
                        url = f"https://steamhunters.com/id/{steam_id}"
                        async with s.get(url, headers=headers, timeout=15) as resp2:
                            if resp2.status != 200:
                                return None
                            html = await resp2.text()
                    else:
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
                "playtime_ymd": "0年0月0天",
                "update_time": "未知",
                # 精准排名字段（完全匹配源码title）
                "cn_points_rank": "未上榜",          # Country points rank
                "cn_achievements_rank": "未上榜",    # Country achievements rank
                "cn_completed_rank": "未上榜",       # Country completed games rank
                "global_points_rank": "未上榜",      # Global points rank
                "global_achievements_rank": "未上榜",# Global achievements rank
                "global_completed_rank": "未上榜",   # Global completed games rank
                "is_banned": False,
                "ban_msg": "",
                "has_data": False
            }

            txt = soup.get_text(" ").lower()
            if "not listed on the leaderboards" in txt:
                data["is_banned"] = True
                data["ban_msg"] = "无法查询有效排名，该用户疑似因刷成就被平台封禁"

            # 解析用户名
            h1 = soup.find("h1")
            if h1:
                data["username"] = h1.get_text(strip=True)
            else:
                username_match = re.search(r"(\w+)\s+3 hours ago", soup.get_text())
                if username_match:
                    data["username"] = username_match.group(1)

            # 解析国家
            data["country"] = self._parse_country(soup)

            # 解析核心数据
            points_match = re.search(r"([\d,]+)\s*points", soup.get_text(), re.I)
            if points_match:
                data["points"] = points_match.group(1).replace(",", "")
            ach_match = re.search(r"([\d,]+)\s*achievements", soup.get_text(), re.I)
            if ach_match:
                data["achievements"] = ach_match.group(1).replace(",", "")
            comp_match = re.search(r"(\d+)\s*completed games", soup.get_text(), re.I)
            if comp_match:
                data["games_completed"] = comp_match.group(1)
            start_match = re.search(r"(\d+)\s*started games", soup.get_text(), re.I)
            if start_match:
                data["games_played"] = start_match.group(1)

            avg_point_match = re.search(r"([\d.]+)\s*points per achievement", soup.get_text(), re.I)
            if avg_point_match:
                data["avg_points"] = avg_point_match.group(1)
            rate_match = re.search(r"([\d.]+)\s*%\s*avg\. completion", soup.get_text(), re.I)
            if rate_match:
                data["completion_rate"] = f"{rate_match.group(1)}%"

            # 解析时长
            playtime_match = re.search(r"(\d+[¼½¾\.]?\s*years?|\d+[¼½¾\.]?\s*months?|\d+[¼½¾\.]?\s*days?)", soup.get_text(), re.I)
            if playtime_match:
                playtime_text = playtime_match.group(1)
                years, months, days, total_hours = self._parse_ymd_from_playtime(playtime_text)
                data["playtime_ymd"] = f"{years}年{months}月{days}天"
                data["playtime"] = total_hours
            elif soup.find("span", {"data-stat-key": "Playtime"}):
                playtime_span = soup.find("span", {"data-stat-key": "Playtime"})
                playtime_div = playtime_span.find_parent("div")
                if playtime_div and playtime_div.has_attr("title"):
                    title_content = playtime_div["title"]
                    year_match = re.search(r"<value>(\d+)</value>&nbsp;years", title_content)
                    month_match = re.search(r"<value>(\d+)</value>&nbsp;months", title_content)
                    day_match = re.search(r"<value>(\d+)</value>&nbsp;days", title_content)
                    years = year_match.group(1) if year_match else "0"
                    months = month_match.group(1) if month_match else "0"
                    days = day_match.group(1) if day_match else "0"
                    data["playtime_ymd"] = f"{years}年{months}月{days}天"
                    data["playtime"] = str(int(years)*8760 + int(months)*730 + int(days)*24)

            # 解析更新时间
            update_match = re.search(r"(\d+\s*hours?|\d+\s*days?)\s*ago", soup.get_text(), re.I)
            if update_match:
                data["update_time"] = update_match.group(1) + "前"
            else:
                time_tag = soup.find("time", class_="title")
                if time_tag and time_tag.get("title"):
                    data["update_time"] = self._parse_update_time(time_tag["title"])

            # 解析排名
            def get_rank_by_title(title_text):
                """根据title属性查找排名，返回去除逗号的数字"""
                td = soup.find("td", title=title_text)
                if td:
                    a_tag = td.find("a")
                    if a_tag:
                        # 提取#后面的数字，去掉逗号
                        rank = re.sub(r"[#,]", "", a_tag.get_text(strip=True))
                        return rank if rank.isdigit() else "未上榜"
                return "未上榜"

            # 逐个解析排名
            data["cn_points_rank"] = get_rank_by_title("Country points rank")
            data["cn_achievements_rank"] = get_rank_by_title("Country achievements rank")
            data["cn_completed_rank"] = get_rank_by_title("Country completed games rank")
            data["global_points_rank"] = get_rank_by_title("Global points rank")
            data["global_achievements_rank"] = get_rank_by_title("Global achievements rank")
            data["global_completed_rank"] = get_rank_by_title("Global completed games rank")
            # =====================================================================

            # 有效数据判断
            core_fields = [data["points"], data["achievements"], data["games_played"], data["username"]]
            data["has_data"] = any(x not in ("0", "", "未知") for x in core_fields)

            return data

        except Exception as e:
            logger.error(f"获取失败: {e}")
            return None

    @filter.command("查steam成就")
    async def steam_achievement_handler(self, event):
        msg = event.message_str.strip()
        if msg.startswith("/"):
            argv = msg[1:].split(maxsplit=1)
        else:
            argv = msg.split(maxsplit=1)
        
        if len(argv) < 2 or argv[0] != "查steam成就":
            yield event.plain_result("用法：查steam成就 Steam64ID/个人资料URL（或 /查steam成就 Steam64ID/个人资料URL）")
            return

        sid = await self._parse_steam64_id(argv[1])
        if not sid:
            yield event.plain_result("无法识别SteamID，请检查格式（支持17位数字Steam64ID、个人资料URL/自定义ID）")
            return

        cache = self._init_cache()
        now = int(time.time())
        key = f"sid_{sid}"

        # 强制清理旧缓存（确保读取最新排名）
        if key in cache:
            del cache[key]
            self._save_cache(cache)

        data = await self._fetch_steam_data(sid)
        if not data:
            yield event.plain_result("查询失败，请检查：\n1. SteamID/URL格式是否正确\n2. Steam隐私设置是否公开\n3. 该账户在SteamHunters有数据")
            return
        cache[key] = {"ts": now, "data": data}
        self._save_cache(cache)

        if not data["has_data"] and not data["is_banned"]:
            yield event.plain_result("未查询到有效数据，请检查SteamID是否正确或前往SteamHunters更新档案")
            return

        # 构造最终返回消息（完全匹配你要的排名）
        lines = [
            f"🎮 Steam成就查询结果（{data['username']}）",
            f"├─ 🗺️ 所属地区：{data['country']}",
            f"├─ 🏆 总成就积分：{data['points']}",
            f"├─ 🎯 已解锁成就：{data['achievements']} 个",
            f"├─ 🎮 玩过的游戏：{data['games_played']} 款",
            f"├─ 🎰 全成就游戏：{data['games_completed']} 款",
            f"├─ ⭐ 平均成就积分：{data['avg_points']}",
            f"├─ 📊 成就完成率：{data['completion_rate']}",
            f"├─ ⏱️ 总游戏时长：{data['playtime_ymd']}（约{data['playtime']}小时）",
            f"├─ 🕒 档案最近更新：{data['update_time']}",
            "├────────────────────────────",
            "├─ 📈 积分排名",
            f"│  ├─ 🌍 {data['country']}排名：{data['cn_points_rank']}",
            f"│  └─ 🌍 世界排名：{data['global_points_rank']}",
            "├─ 🏅 成就数排名",
            f"│  ├─ 🌍 {data['country']}排名：{data['cn_achievements_rank']}",
            f"│  └─ 🌍 世界排名：{data['global_achievements_rank']}",
            "├─ 🎮 完成游戏排名",
            f"│  ├─ 🌍 {data['country']}排名：{data['cn_completed_rank']}",
            f"│  └─ 🌍 世界排名：{data['global_completed_rank']}"
        ]

        if data["is_banned"]:
            lines.insert(-1, f"├─ ⚠️ {data['ban_msg']}")

        yield event.plain_result("\n".join(lines))

    """@filter.command("清理steam缓存")
    async def clear_steam_cache(self, event):
        try:
            if self.cache_path.exists():
                self.cache_path.unlink()
                self._init_cache()
                await event.reply("✅ Steam成就查询缓存已成功清理！")
            else:
                await event.reply("ℹ️ 当前无Steam成就查询缓存文件，无需清理！")
        except Exception as e:
            logger.error(f"清理缓存失败：{e}")
            await event.reply(f"❌ 清理缓存失败：{str(e)}")"""

    async def terminate(self):
        logger.info("Steam成就插件（v1.0.0）已卸载")
