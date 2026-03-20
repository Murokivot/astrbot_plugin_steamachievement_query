from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger  # 使用官方logger

import re
import json
import time
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup

# 插件注册（适配你的AstrBot版本，补充必填的desc参数）
@register(
    name="astrbot_plugin_steamachievement_query",        # 插件唯一标识
    author="Muroki",               # 替换为你的名字/用户名
    version="1.0.0",                 # 版本号
    desc="查询SteamHunters平台的游戏成就数据，支持Steam64ID/个人资料URL"  # 必填描述
)
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 配置项（可根据需要修改）
        self.steam_api_key = ""  # 可选：填写你的Steam API Key（用于自定义URL转换，不填也不影响纯ID/Profiles URL查询）
        self.cache_expire = 3600  # 缓存有效期（秒）：1小时
        self.cache_path = Path("/AstrBot/data/steam_achievement_cache.json")  # 缓存文件路径
        # 初始化缓存目录（确保目录存在）
        if not self.cache_path.parent.exists():
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    # -------------------------- 工具函数：缓存处理 --------------------------
    def _init_cache(self):
        """初始化/读取缓存文件"""
        if not self.cache_path.exists():
            # 缓存文件不存在则创建空文件
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False)
            return {}
        try:
            # 读取缓存文件
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"初始化缓存失败：{str(e)}")
            return {}

    def _save_cache(self, cache_data):
        """保存缓存数据到文件"""
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存缓存失败：{str(e)}")

    # -------------------------- 工具函数：Steam ID解析 --------------------------
    async def _parse_steam64_id(self, input_str: str) -> str | None:
        """
        宽松解析Steam ID/URL：
        支持：17位纯数字ID、Profiles URL、自定义ID URL
        返回：解析后的Steam ID/自定义ID（确保能被SteamHunters识别）
        """
        # 去除首尾空格，统一处理
        s = input_str.strip()

        # 1. 匹配17位纯数字Steam64 ID（最常用）
        pure_id_match = re.fullmatch(r"\d{17}", s)
        if pure_id_match:
            return pure_id_match.group(0)

        # 2. 匹配Profiles URL（如：https://steamcommunity.com/profiles/76561198187914141）
        profiles_match = re.search(r"profiles/(\d{17})", s)
        if profiles_match:
            return profiles_match.group(1)

        # 3. 匹配自定义ID URL（如：https://steamcommunity.com/id/xxx）
        vanity_match = re.search(r"id/([^/]+)", s)
        if vanity_match:
            vanity_id = vanity_match.group(1)
            # 如果填写了API Key，尝试转换为Steam64 ID；否则直接返回自定义ID（SteamHunters也支持）
            if self.steam_api_key:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001",
                            params={"key": self.steam_api_key, "vanityurl": vanity_id},
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=10
                        ) as resp:
                            data = await resp.json()
                            if data.get("response", {}).get("success") == 1:
                                return data["response"]["steamid"]
                except Exception as e:
                    logger.error(f"转换自定义ID失败：{str(e)}")
            # 无论是否转换成功，都返回自定义ID（SteamHunters可直接识别）
            return vanity_id

        # 4. 兜底：如果输入是纯字母/数字组合（自定义ID），直接返回
        if re.match(r"^[a-zA-Z0-9_-]+$", s):
            return s

        # 无匹配结果
        return None

    # -------------------------- 工具函数：抓取SteamHunters数据 --------------------------
    async def _fetch_steam_data(self, steam_id: str) -> dict | None:
        """抓取SteamHunters完整成就数据"""
        # 构造请求URL
        url = f"https://steamhunters.com/profiles/{steam_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=20  # 超时时间20秒
                ) as resp:
                    # 检查响应状态
                    if resp.status != 200:
                        logger.error(f"访问SteamHunters失败，状态码：{resp.status}")
                        return None

                    # 解析HTML
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")

                    # 初始化数据结构（所有字段默认值）
                    data = {
                        "username": "未知用户",
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

                    # 1. 解析用户名
                    username_elem = soup.find("h1") or soup.find("h2", class_="profile_header_name")
                    if username_elem:
                        data["username"] = username_elem.get_text(strip=True)

                    # 2. 解析总成就积分
                    points_elem = soup.find("span", attrs={"data-stat-key": "ValidPoints"})
                    if points_elem:
                        data["points"] = re.sub(r"[^\d]", "", points_elem.get_text(strip=True)) or "0"

                    # 3. 解析已解锁成就数
                    achv_elem = soup.find("span", attrs={"data-stat-key": "ValidAchievementUnlockCount"})
                    if achv_elem:
                        data["achievements"] = re.sub(r"[^\d]", "", achv_elem.get_text(strip=True)) or "0"

                    # 4. 解析玩过的游戏数
                    played_elem = soup.find("span", attrs={"data-stat-key": "ValidStartedGameCount"})
                    if played_elem:
                        data["games_played"] = re.sub(r"[^\d]", "", played_elem.get_text(strip=True)) or "0"

                    # 5. 解析全成就游戏数
                    completed_elem = soup.find("span", attrs={"data-stat-key": "ValidCompletedGameCount"})
                    if completed_elem:
                        data["games_completed"] = re.sub(r"[^\d]", "", completed_elem.get_text(strip=True)) or "0"

                    # 6. 解析平均成就积分
                    avg_elem = soup.find("span", attrs={"data-stat-key": "ValidPointsPerAchievement"})
                    if avg_elem:
                        main_text = avg_elem.contents[0].strip() if avg_elem.contents else ""
                        int_part = re.sub(r"[^\d]", "", main_text) or "0"
                        decimal_elem = avg_elem.find("span", class_="decimal")
                        decimal_part = decimal_elem.get_text(strip=True) if decimal_elem else ""
                        data["avg_points"] = f"{int_part}.{decimal_part}" if decimal_part else int_part

                    # 7. 解析成就完成率
                    completion_elem = soup.find("span", attrs={"data-stat-key": "ValidAgcObtainable"})
                    if completion_elem:
                        main_text = completion_elem.contents[0].strip() if completion_elem.contents else ""
                        int_part = re.sub(r"[^\d]", "", main_text) or "0"
                        decimal_elem = completion_elem.find("span", class_="decimal")
                        decimal_part = decimal_elem.get_text(strip=True) if decimal_elem else ""
                        data["completion_rate"] = f"{int_part}.{decimal_part}%" if decimal_part else f"{int_part}%"

                    # 8. 解析总游戏时长（提取title中的数字）
                    playtime_elem = soup.find("span", attrs={"data-stat-key": "Playtime"})
                    if playtime_elem and playtime_elem.parent and playtime_elem.parent.get("title"):
                        title_text = playtime_elem.parent["title"]
                        numbers = re.findall(r"[\d,]+", title_text)
                        if numbers:
                            # 去除逗号并转换为数字
                            nums = [int(n.replace(",", "")) for n in numbers if n.replace(",", "").isdigit()]
                            if nums:
                                data["playtime"] = str(max(nums))  # 取最大值

                    # 9. 解析世界排名
                    global_rank_elem = soup.find("td", title=re.compile("Global points rank", re.IGNORECASE))
                    if global_rank_elem and global_rank_elem.find("a"):
                        rank_text = global_rank_elem.find("a").get_text(strip=True)
                        rank_match = re.search(r"#([\d,]+)", rank_text)
                        if rank_match:
                            data["global_rank"] = rank_match.group(1).replace(",", "")

                    # 10. 解析全国排名
                    cn_rank_elem = soup.find("td", title=re.compile("Country points rank", re.IGNORECASE))
                    if cn_rank_elem and cn_rank_elem.find("a"):
                        rank_text = cn_rank_elem.find("a").get_text(strip=True)
                        rank_match = re.search(r"#([\d,]+)", rank_text)
                        if rank_match:
                            data["cn_rank"] = rank_match.group(1).replace(",", "")

                    # 返回完整数据
                    return data

        except Exception as e:
            logger.error(f"抓取Steam数据失败：{str(e)}")
            return None

    # -------------------------- 核心指令处理函数 --------------------------
    @filter.command("查steam成就")
    async def steam_achievement_handler(self, event: AstrMessageEvent):
        '''查询SteamHunters平台的游戏成就数据，支持17位Steam64ID/Profiles URL/自定义ID'''
        # 1. 获取用户输入的消息内容
        message_str = event.message_str.strip()
        user_name = event.get_sender_name()
        logger.info(f"用户 {user_name} 触发Steam成就查询，输入：{message_str}")

        # 2. 拆分指令和参数（支持空格分隔）
        # 示例：/查steam成就 76561198187914141 → 拆分后 argv = ["/查steam成就", "76561198187914141"]
        argv = message_str.split(maxsplit=1)
        if len(argv) < 2:
            # 无参数输入，返回用法提示
            yield event.plain_result("""❌ 指令格式错误！
✅ 正确用法：/查steam成就 <Steam64ID/个人资料URL/自定义ID>
📌 示例1（纯ID）：/查steam成就 76561198187914141
📌 示例2（Profiles URL）：/查steam成就 https://steamcommunity.com/profiles/76561198187914141
📌 示例3（自定义ID）：/查steam成就 https://steamcommunity.com/id/your_custom_id""")
            return

        # 提取查询参数（第二个部分）
        param = argv[1]

        # 3. 解析Steam ID
        steam_id = await self._parse_steam64_id(param)
        if not steam_id:
            # 解析失败，返回提示
            yield event.plain_result("❌ 无法识别Steam ID！请输入：\n1. 17位纯数字Steam64 ID\n2. Steam Profiles URL\n3. Steam自定义ID/URL")
            return

        # 4. 缓存逻辑处理
        cache = self._init_cache()
        now = int(time.time())
        cache_key = f"steam_{steam_id}"  # 缓存键（避免冲突）

        # 检查缓存是否有效
        if cache_key in cache and (now - cache[cache_key]["timestamp"]) < self.cache_expire:
            logger.info(f"使用缓存数据查询 {steam_id}")
            data = cache[cache_key]["data"]
        else:
            # 缓存无效/不存在，抓取新数据
            logger.info(f"抓取新数据：{steam_id}")
            data = await self._fetch_steam_data(steam_id)
            if not data:
                # 抓取失败，返回提示
                yield event.plain_result("❌ 查询失败！可能原因：\n1. Steam ID不存在/无效\n2. SteamHunters网站无法访问\n3. 网络超时\n请检查后重试")
                return
            # 保存新数据到缓存
            cache[cache_key] = {
                "timestamp": now,
                "data": data
            }
            self._save_cache(cache)

        # 5. 构造回复消息（美观排版）
        reply = f"""🎮 Steam成就查询结果（{data['username']}）
├─ 🏆 总成就积分：{data['points']}
├─ 🎯 已解锁成就：{data['achievements']} 个
├─ 🎮 玩过的游戏：{data['games_played']} 款
├─ 🎰 全成就游戏：{data['games_completed']} 款
├─ ⭐ 平均成就积分：{data['avg_points']}
├─ 📊 成就完成率：{data['completion_rate']}
├─ ⏱️ 总游戏时长：{data['playtime']} 小时
├─ 🌍 世界排名：{data['global_rank']}
└─ 🌍 全国排名：{data['cn_rank']}"""

        # 6. 返回查询结果
        yield event.plain_result(reply)

    # -------------------------- 插件卸载回调 --------------------------
    async def terminate(self):
        '''插件卸载/停用时执行的清理操作'''
        logger.info("✅ Steam成就查询插件已卸载，缓存文件保留：{}".format(self.cache_path))
