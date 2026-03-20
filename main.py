from astrbot.core.star import Star, register
from astrbot.core.message import AstrMessageEvent
import re
import json
import time
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup

# ===================== 配置项=====================
# STEAM_API_KEY = "你的Steam API Key"  # 替换为真实Steam API Key
CACHE_EXPIRE = 3600  # 缓存有效期（秒）
CACHE_PATH = Path("/AstrBot/data/steam_hunter_cache.json")  # 缓存文件路径

# ===================== 工具函数 =====================
async def init_cache():
    """初始化缓存文件（异步）"""
    if not CACHE_PATH.parent.exists():
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CACHE_PATH.exists():
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False)
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

async def save_cache(cache_data):
    """保存缓存（异步）"""
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

async def parse_steam64_id(input_str: str) -> str | None:
    """解析Steam64 ID（支持纯ID/URL）"""
    # 匹配Steam64 URL
    match_64_url = re.search(r"steamcommunity\.com/profiles/(\d{17})", input_str)
    if match_64_url:
        return match_64_url.group(1)
    
    # 匹配自定义URL并转换为64位ID
    match_custom_url = re.search(r"steamcommunity\.com/id/([^/]+)", input_str)
    if match_custom_url and STEAM_API_KEY:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001",
                    params={"key": STEAM_API_KEY, "vanityurl": match_custom_url.group(1)},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10
                ) as resp:
                    data = await resp.json()
                    if data.get("response", {}).get("success") == 1:
                        return data["response"]["steamid"]
        except:
            pass
    
    # 直接匹配纯Steam64 ID
    if re.fullmatch(r"7656119\d{10}", input_str.strip()):
        return input_str.strip()
    
    return None

async def fetch_steamhunters_data(steam64: str) -> dict | None:
    """异步获取SteamHunters数据"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://steamhunters.com/"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://steamhunters.com/profiles/{steam64}",
                headers=headers,
                timeout=20
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # 初始化返回数据
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
                
                # 解析用户名
                username_elem = soup.find("h1") or soup.find("h2")
                if username_elem:
                    data["username"] = username_elem.get_text(strip=True)
                
                # 总成就积分
                points_elem = soup.find("span", attrs={"data-stat-key": "ValidPoints"})
                if points_elem:
                    data["points"] = re.sub(r"[^\d]", "", points_elem.get_text(strip=True))
                
                # 已解锁成就数
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
                
                # 平均每个成就积分
                avg_elem = soup.find("span", attrs={"data-stat-key": "ValidPointsPerAchievement"})
                if avg_elem:
                    main_text = avg_elem.contents[0].strip()
                    int_part = re.sub(r"[^\d]", "", main_text)
                    decimal_elem = avg_elem.find("span", class_="decimal")
                    decimal_part = decimal_elem.get_text(strip=True) if decimal_elem else ""
                    data["avg_points"] = f"{int_part}.{decimal_part}" if decimal_part else int_part
                
                # 成就完成率
                completion_elem = soup.find("span", attrs={"data-stat-key": "ValidAgcObtainable"})
                if completion_elem:
                    main_text = completion_elem.contents[0].strip()
                    int_part = re.sub(r"[^\d]", "", main_text)
                    decimal_elem = completion_elem.find("span", class_="decimal")
                    decimal_part = decimal_elem.get_text(strip=True) if decimal_elem else ""
                    data["completion_rate"] = f"{int_part}.{decimal_part}%" if decimal_part else f"{int_part}%"
                
                # 总游戏时长
                playtime_elem = soup.find("span", attrs={"data-stat-key": "Playtime"})
                if playtime_elem and playtime_elem.parent.get("title"):
                    numbers = re.findall(r"[\d,]+", playtime_elem.parent["title"])
                    if numbers:
                        nums = [int(n.replace(",", "")) for n in numbers if n.replace(",", "").isdigit()]
                        if nums:
                            data["playtime"] = str(max(nums))
                
                # 世界排名
                global_rank_elem = soup.find("td", title=re.compile("Global points rank", re.IGNORECASE))
                if global_rank_elem and global_rank_elem.find("a"):
                    rank_text = global_rank_elem.find("a").get_text(strip=True)
                    rank_match = re.search(r"#([\d,]+)", rank_text)
                    if rank_match:
                        data["global_rank"] = rank_match.group(1).replace(",", "")
                
                # 全国排名
                cn_rank_elem = soup.find("td", title=re.compile("Country points rank", re.IGNORECASE))
                if cn_rank_elem and cn_rank_elem.find("a"):
                    rank_text = cn_rank_elem.find("a").get_text(strip=True)
                    rank_match = re.search(r"#([\d,]+)", rank_text)
                    if rank_match:
                        data["cn_rank"] = rank_match.group(1).replace(",", "")
                
                return data
    except Exception:
        return None
# ===================== 插件核心类=====================
@register(
    name="steam_hunter",
    author="YourName",
    description="查询SteamHunters平台的游戏成就数据",
    version="1.0.0"
)
class MyPlugin(Star):
    """Steam成就查询插件（基于AstrBot官方helloworld模板）"""
    
    def initialize(self):
        """插件初始化（可选）"""
        self.logger.info("Steam成就查询插件已初始化")
    
    @filter.command("查steam成就")
    async def steam_hunter_query(self, event: AstrMessageEvent):
        """响应/查steam成就指令"""
        # 提取用户输入参数
        input_text = event.message_str.strip()
        params = input_text.replace("/查steam成就", "").strip()
        
        # 无参数时返回用法提示
        if not params:
            yield event.plain_result("""❌ 指令格式错误！
✅ 正确用法：/查steam成就 <Steam64ID/Steam社区链接>
📌 示例1：/查steam成就 76561198187914141
📌 示例2：/查steam成就 https://steamcommunity.com/profiles/76561198187914141""")
            return
        
        # 解析Steam64 ID
        steam64 = await parse_steam64_id(params)
        if not steam64:
            yield event.plain_result("❌ 无法识别Steam ID！请输入17位Steam64 ID或有效的Steam个人资料链接")
            return
        
        # 加载缓存
        cache = await init_cache()
        now = int(time.time())
        
        # 优先使用缓存（未过期）
        if steam64 in cache and (now - cache[steam64]["timestamp"]) < CACHE_EXPIRE:
            data = cache[steam64]["data"]
        else:
            # 缓存过期/无缓存，重新获取数据
            data = await fetch_steamhunters_data(steam64)
            if not data:
                yield event.plain_result("查询失败，请前往SteamHunters手动更新档案")
                return
            # 保存新缓存
            cache[steam64] = {"timestamp": now, "data": data}
            await save_cache(cache)
        
        # 构造回复内容
        reply = f"""Steam成就查询结果
├─ 👤 用户名：{data['username']}
├─ 🏆 总成就积分：{data['points']}
├─ 🎯 已解锁成就：{data['achievements']}
├─ 🎮 玩过的游戏：{data['games_played']}
├─ 🎰 全成就游戏：{data['games_completed']}
├─ ⭐ 平均成就积分：{data['avg_points']}
├─ 📊 成就完成率：{data['completion_rate']}
├─ ⏱️ 总游戏时长：{data['playtime']} 小时
├─ 🌍 世界排名：{data['global_rank']}
└─ 🇨🇳 全国排名：{data['cn_rank']}"""
        
        yield event.plain_result(reply)
    
    def terminate(self):
        """插件销毁（可选）"""
        self.logger.info("Steam成就查询插件已销毁")
