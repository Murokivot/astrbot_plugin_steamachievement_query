from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger  # 官方logger接口

import re
import json
import time
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup

# 插件注册
@register(
    name="steam_achievement",        # 插件唯一标识（必填）
    author="YourName",               # 作者（必填）
    version="1.0.0",                 # 版本（必填）
    desc="查询SteamHunters平台的游戏成就数据，支持Steam64ID/个人资料URL"  # 你的版本要求的必填desc参数
)
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 配置项
        self.steam_api_key = "你的Steam API Key"  # 替换为真实Key
        self.cache_expire = 3600
        self.cache_path = Path("/AstrBot/data/steam_achievement_cache.json")
        # 初始化缓存目录
        if not self.cache_path.parent.exists():
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    # 初始化缓存
    def _init_cache(self):
        if not self.cache_path.exists():
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False)
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"初始化缓存失败：{e}")
            return {}

    # 保存缓存
    def _save_cache(self, cache_data):
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存缓存失败：{e}")

    # 解析Steam64 ID
    async def _parse_steam64_id(self, input_str: str) -> str | None:
        s = input_str.strip()
        
        # 匹配Steam64 URL
        url_match = re.search(r"steamcommunity\.com/profiles/(\d{17})", input_str)
        if url_match:
            return url_match.group(1)
        
        # 匹配自定义URL转换
        vanity_match = re.search(r"steamcommunity\.com/id/([^/]+)", input_str)
        if vanity_match and self.steam_api_key:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001",
                        params={"key": self.steam_api_key, "vanityurl": vanity_match.group(1)},
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=10
                    ) as resp:
                        data = await resp.json()
                        if data.get("response", {}).get("success") == 1:
                            return data["response"]["steamid"]
            except Exception as e:
                logger.error(f"转换自定义URL失败：{e}")

        # 匹配 17 位纯数字 SteamID
        if re.fullmatch(r"\d{17}", s):
            return s

        # 匹配 profiles/xxx
        match = re.search(r"profiles/(\d{17})", s)
        if match:
            return match.group(1)

        # 匹配 id/xxx 自定义ID（不依赖API Key也能返回原串，让网页自己解析）
        id_match = re.search(r"id/([^/]+)", s)
        if id_match:
            return id_match.group(1)

        return None

    # 抓取SteamHunters完整数据
    async def _fetch_steam_data(self, steam64: str) -> dict | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://steamhunters.com/profiles/{steam64}",
                    headers=headers,
                    timeout=20
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"访问SteamHunters失败，状态码：{resp.status}")
                        return None
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    # 完整数据解析
                    data = {
                        "username": "未知",
                        "points": "0",
                        "achievements": "0",
                        "games_played": "0",
                        "games_completed": "0",
                        "avg_points": "0",
                        "completion_rate": "0%",
                        "playtime": "0",
                        "global_rank": "未上榜",
                        "cn_rank": "未上榜"
                    }
                    
                    # 用户名
                    username_elem = soup.find("h1") or soup.find("h2")
                    if username_elem:
                        data["username"] = username_elem.get_text(strip=True)
                    
                    # 总积分
                    points_elem = soup.find("span", attrs={"data-stat-key": "ValidPoints"})
                    if points_elem:
                        data["points"] = re.sub(r"[^\d]", "", points_elem.get_text(strip=True))
                    
                    # 成就数
                    achv_elem = soup.find("span", attrs={"data-stat-key": "ValidAchievementUnlockCount"})
                    if achv_elem:
                        data["achievements"] = re.sub(r"[^\d]", "", achv_elem.get_text(strip=True))
                    
                    # 玩过的游戏数
                    played_elem = soup.find("span", attrs={"data-stat-key": "ValidStartedGameCount"})
                    if played_elem:
                        data["games_played"] = re.sub(r"[^\d]", "", played_elem.get_text(strip=True))
                    
                    # 全成就游戏数
                    completed_elem = soup.find("span", attrs={"data-stat-key": "ValidCompletedGameCount"})
                    if completed_elem:
                        data["games_completed"] = re.sub(r"[^\d]", "", completed_elem.get_text(strip=True))
                    
                    # 平均积分
                    avg_elem = soup.find("span", attrs={"data-stat-key": "ValidPointsPerAchievement"})
                    if avg_elem:
                        main_text = avg_elem.contents[0].strip()
                        int_part = re.sub(r"[^\d]", "", main_text)
                        decimal_elem = avg_elem.find("span", class_="decimal")
                        decimal_part = decimal_elem.get_text(strip=True) if decimal_elem else ""
                        data["avg_points"] = f"{int_part}.{decimal_part}" if decimal_part else int_part
                    
                    # 完成率
                    completion_elem = soup.find("span", attrs={"data-stat-key": "ValidAgcObtainable"})
                    if completion_elem:
                        main_text = completion_elem.contents[0].strip()
                        int_part = re.sub(r"[^\d]", "", main_text)
                        decimal_part = completion_elem.find("span", class_="decimal").get_text(strip=True) if completion_elem.find("span", class_="decimal") else ""
                        data["completion_rate"] = f"{int_part}.{decimal_part}%" if decimal_part else f"{int_part}%"
                    
                    # 游戏时长
                    playtime_elem = soup.find("span", attrs={"data-stat-key": "Playtime"})
                    if playtime_elem and playtime_elem.parent.get("title"):
                        numbers = re.findall(r"[\d,]+", playtime_elem.parent["title"])
                        if numbers:
                            nums = [int(n.replace(",", "")) for n in numbers if n.replace(",", "").isdigit()]
                            data["playtime"] = str(max(nums)) if nums else "0"
                    
                    # 世界排名
                    global_rank_elem = soup.find("td", title=re.compile("Global points rank", re.IGNORECASE))
                    if global_rank_elem and global_rank_elem.find("a"):
                        rank_match = re.search(r"#([\d,]+)", global_rank_elem.find("a").get_text(strip=True))
                        if rank_match:
                            data["global_rank"] = rank_match.group(1).replace(",", "")
                    
                    # 全国排名
                    cn_rank_elem = soup.find("td", title=re.compile("Country points rank", re.IGNORECASE))
                    if cn_rank_elem and cn_rank_elem.find("a"):
                        rank_match = re.search(r"#([\d,]+)", cn_rank_elem.find("a").get_text(strip=True))
                        if rank_match:
                            data["cn_rank"] = rank_match.group(1).replace(",", "")
                    
                    return data
        except Exception as e:
            logger.error(f"抓取Steam数据失败：{e}")
            return None

    # 核心指令处理（完全对标官方最小实例）
    @filter.command("查steam成就")
    async def steam_achievement_handler(self, event: AstrMessageEvent):
        '''查询SteamHunters平台的游戏成就数据，支持Steam64ID/个人资料URL'''
        # 获取用户输入
        user_name = event.get_sender_name()
        message_str = event.message_str
        logger.info(f"用户 {user_name} 触发Steam成就查询：{message_str}")
        
        # 提取参数
        params = message_str.replace("/查steam成就", "").strip()
        if not params:
            yield event.plain_result("""❌ 指令格式错误！
✅ 正确用法：/查steam成就 <Steam64ID/个人资料URL>
📌 示例：/查steam成就 76561198187914141""")
            return
        
        # 解析ID
        steam64 = await self._parse_steam64_id(params)
        if not steam64:
            yield event.plain_result("❌ 无法识别Steam ID！请输入17位Steam64 ID或有效URL")
            return
        
        # 缓存逻辑
        cache = self._init_cache()
        now = int(time.time())
        if steam64 in cache and (now - cache[steam64]["timestamp"]) < self.cache_expire:
            data = cache[steam64]["data"]
        else:
            data = await self._fetch_steam_data(steam64)
            if not data:
                yield event.plain_result("查询失败，请检查SteamHunters是否可访问")
                return
            cache[steam64] = {"timestamp": now, "data": data}
            self._save_cache(cache)
        
        # 构造完整回复
        reply = f"""🎮 Steam成就查询结果
├─ 👤 用户名：{data['username']}
├─ 🏆 总成就积分：{data['points']}
├─ 🎯 已解锁成就：{data['achievements']}
├─ 🎮 玩过的游戏：{data['games_played']}
├─ 🎰 全成就游戏：{data['games_completed']}
├─ ⭐ 平均成就积分：{data['avg_points']}
├─ 📊 成就完成率：{data['completion_rate']}
├─ ⏱️ 总游戏时长：{data['playtime']} 小时
├─ 🌍 世界排名：{data['global_rank']}
└─ 🌍 全国排名：{data['cn_rank']}"""
        
        yield event.plain_result(reply)

    # 插件卸载回调
    async def terminate(self):
        '''插件卸载时执行'''
        logger.info("Steam成就查询插件已卸载")
