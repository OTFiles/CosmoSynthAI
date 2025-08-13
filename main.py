import os
import json
import random
import re
import time
import traceback
from datetime import datetime
from collections import deque
from chat_core import load_configs, run_chat_session, save_session, load_session, attach_file
from chat_core import FileTooLargeError, APIConnectionError, APIResponseError, InvalidSessionError, ConfigLoadError

# 自定义异常
class ConfigError(Exception):
    pass

class InvalidMessageFormat(Exception):
    pass

class InvalidCommandError(Exception):
    pass

class ChannelNotFoundError(Exception):
    pass

class AINotFoundError(Exception):
    pass

class PermissionError(Exception):
    pass

# 颜色输出工具
class Color:
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RESET = "\033[0m"

# 历史记录管理类
class HistoryManager:
    def __init__(self):
        self.history_file = "history.json"
        self.history = self.load_history()
    
    def load_history(self):
        """加载历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {
                    "sessions": {},
                    "channels": {},
                    "system_events": []
                }
        return {
            "sessions": {},
            "channels": {},
            "system_events": []
        }
    
    def save_history(self):
        """保存历史记录"""
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存历史记录失败: {str(e)}")
    
    def add_message(self, channel, ai_id, content):
        """添加消息到历史记录"""
        timestamp = int(time.time())
        message = {
            "timestamp": timestamp,
            "channel": channel,
            "ai": ai_id,
            "content": content
        }
        
        # 添加到频道历史
        if channel not in self.history["channels"]:
            self.history["channels"][channel] = []
        self.history["channels"][channel].append(message)
        
        # 添加到AI历史
        if ai_id not in self.history["sessions"]:
            self.history["sessions"][ai_id] = []
        self.history["sessions"][ai_id].append(message)
    
    def add_system_event(self, event_type, details):
        """添加系统事件到历史记录"""
        timestamp = int(time.time())
        event = {
            "timestamp": timestamp,
            "type": event_type,
            "details": details
        }
        self.history["system_events"].append(event)
    
    def get_channel_history(self, channel, max_messages=50):
        """获取频道历史记录"""
        if channel in self.history["channels"]:
            return self.history["channels"][channel][-max_messages:]
        return []
    
    def get_ai_history(self, ai_id, max_messages=50):
        """获取AI历史记录"""
        if ai_id in self.history["sessions"]:
            return self.history["sessions"][ai_id][-max_messages:]
        return []

# 主程序类
class MultiAIChatSystem:
    def __init__(self):
        self.api_configs = []
        self.tool_config = {}
        self.ai_memories = {}
        self.channel_logs = {}
        self.global_log = []
        self.round_count = 0
        self.last_prompt_rotation = 0
        self.last_observation = 0
        self.history = HistoryManager()
        self.start_time = time.time()
        self.last_speaker = None  # 记录上一个发言的AI
        self.priority_queue = deque()  # 优先级队列：[(priority, ai_id, reason), ...]
        self.pending_commands = []  # 待处理的命令
        self.channel_manager_ai = None  # 频道管理AI
        self.memory_manager_ai = None  # 记忆管理AI
        self.allowed_callers = []  # 允许呼叫的AI列表
        self.excluded_ais = []  # 随机选择排除的AI列表
        self.log_file = "system_log.txt"  # 系统日志文件

    def load_configurations(self):
        """加载API配置和工具配置"""
        try:
            self.api_configs = load_configs("api-config.txt")
        except (FileNotFoundError, ConfigLoadError) as e:
            self.log_error(f"API配置加载失败: {str(e)}")
            raise ConfigError(f"API配置加载失败: {str(e)}")
        
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                self.tool_config = json.load(f)
        except FileNotFoundError as e:
            self.log_error(f"工具配置文件未找到: {str(e)}")
            raise ConfigError(f"工具配置文件未找到: {str(e)}")
        except json.JSONDecodeError as e:
            self.log_error(f"配置文件解析错误: {str(e)}")
            raise ConfigError(f"配置文件解析错误: {str(e)}")
            
        # 加载频道管理AI
        self.channel_manager_ai = self.tool_config.get("频道管理AI")
        if self.channel_manager_ai and self.channel_manager_ai not in self.tool_config["AI"]:
            self.log_error(f"频道管理AI '{self.channel_manager_ai}' 未在AI配置中定义")
            self.channel_manager_ai = None
            
        # 加载记忆管理AI
        self.memory_manager_ai = self.tool_config.get("记忆管理AI")
        if self.memory_manager_ai and self.memory_manager_ai not in self.tool_config["AI"]:
            self.log_error(f"记忆管理AI '{self.memory_manager_ai}' 未在AI配置中定义")
            self.memory_manager_ai = None
            
        # 加载允许呼叫的AI列表
        self.allowed_callers = self.tool_config.get("允许呼叫", [])
        
        # 加载随机选择排除的AI列表
        self.excluded_ais = self.tool_config.get("随机选择排除AI", [])
        # 验证排除的AI是否存在于系统中
        for ai_id in self.excluded_ais:
            if ai_id not in self.tool_config["AI"]:
                self.log_error(f"排除的AI '{ai_id}' 未在AI配置中定义，将被忽略")
                self.excluded_ais.remove(ai_id)
        
        # 验证每个AI的"重新生成提示词"配置
        for ai_id, ai_config in self.tool_config["AI"].items():
            if "重新生成提示词" in ai_config:
                regen_config = ai_config["重新生成提示词"]
                # 验证"会不会"值
                if "会不会" not in regen_config or regen_config["会不会"] not in ["True", "False"]:
                    self.log_error(f"AI '{ai_id}' 的重新生成提示词配置无效: '会不会' 必须是 'True' 或 'False'")
                # 验证用户提示词
                if "发给提示词AI的用户提示词" not in regen_config or not isinstance(regen_config["发给提示词AI的用户提示词"], str):
                    self.log_error(f"AI '{ai_id}' 的重新生成提示词配置无效: '发给提示词AI的用户提示词' 必须是字符串")

    def initialize_system(self):
        """初始化系统"""
        # 验证配置
        if "AI" not in self.tool_config:
            raise ConfigError("配置文件中缺少AI定义")
        
        # 初始化AI记忆
        for ai_id, ai_config in self.tool_config["AI"].items():
            self.ai_memories[ai_id] = [{
                "role": "system",
                "content": ai_config.get("prompt", "你是一个AI助手")
            }]
        
        # 初始化频道日志
        for ai_id, ai_config in self.tool_config["AI"].items():
            for channel in ai_config:
                if channel not in ["prompt", "监察", "api", "重新生成提示词"]:  # 跳过特殊字段
                    if channel not in self.channel_logs:
                        self.channel_logs[channel] = []
        
        # 验证观察者配置
        if "观察者" in self.tool_config:
            observer_ai = self.tool_config["观察者"]["AI"]
            if observer_ai not in self.tool_config["AI"]:
                raise ConfigError(f"观察者AI '{observer_ai}' 未在AI配置中定义")
        
        # 记录系统启动事件
        self.history.add_system_event("system_start", {
            "config": self.tool_config,
            "start_time": self.start_time
        })

    def log_error(self, message):
        """记录错误信息"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[ERROR][{timestamp}] {message}"
        self.global_log.append(log_entry)
        print(f"{Color.RED}{log_entry}{Color.RESET}")
        self._write_to_log(log_entry)  # 写入日志文件
        self.history.add_system_event("error", message)

    def _write_to_log(self, message):
        """将消息写入日志文件"""
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except Exception as e:
            print(f"写入日志文件失败: {str(e)}")

    def log_message(self, channel, ai_id, message):
        """记录消息到日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{channel}][{timestamp}] {ai_id}: {message}"
        
        # 添加到频道日志
        if channel in self.channel_logs:
            self.channel_logs[channel].append(log_entry)
        
        # 添加到全局日志
        self.global_log.append(log_entry)
        
        # 添加到历史记录
        self.history.add_message(channel, ai_id, message)
        
        # 写入日志文件
        self._write_to_log(log_entry)
        
        # 彩色输出到终端
        color = Color.BLUE
        if channel == "系统":
            color = Color.CYAN
        elif channel == "监察":
            color = Color.MAGENTA
        elif channel == "管理":
            color = Color.GREEN
            
        print(f"{Color.YELLOW}[{channel}]{Color.RESET}{Color.RED}{ai_id}:{Color.RESET}{color} {message}{Color.RESET}")
    
    def log_rejection(self, speaker_id, reason, message):
        """记录驳回消息（不广播到任何频道）"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[REJECT][{timestamp}] {speaker_id}: {reason} (原始消息: {message})"
        
        # 添加到全局日志
        self.global_log.append(log_entry)
        
        # 写入日志文件
        self._write_to_log(log_entry)
        
        # 终端输出
        print(f"{Color.MAGENTA}{log_entry}{Color.RESET}")
        
        # 添加到历史记录的"驳回"频道
        self.history.add_message("驳回", speaker_id, message)
        
        # 添加系统消息通知被驳回的AI
        self.add_system_message(speaker_id, f"您的消息被驳回，原因: {reason}")

    def get_next_speaker(self):
        """获取下一个发言的AI（考虑优先级队列）"""
        # 首先检查优先级队列
        if self.priority_queue:
            _, next_ai, reason = self.priority_queue.popleft()
            self.log_message("系统", "调度器", f"优先级调用: {next_ai} (原因: {reason})")
            return next_ai
            
        # 没有优先级任务时，从符合条件的AI中随机选择
        eligible = self.get_eligible_speakers()
        if not eligible:
            self.log_error("没有符合条件的AI可以发言")
            return None
            
        speaker_id = random.choice(eligible)
        self.last_speaker = speaker_id
        return speaker_id

    def get_eligible_speakers(self):
        """获取有发言权限的AI列表（排除指定的AI）"""
        # 使用集合确保每个AI仅被添加一次
        eligible_set = set()
        
        # 第一遍：收集所有有发送权限的AI
        for ai_id, ai_config in self.tool_config["AI"].items():
            # 排除配置中指定的AI
            if ai_id in self.excluded_ais:
                continue
                
            # 检查该AI是否有发送权限
            for channel, perms in ai_config.items():
                # 跳过特殊字段
                if channel in ["prompt", "监察", "api", "重新生成提示词"]:
                    continue
                    
                # 检查是否有发送权限
                if "发送" in perms:
                    eligible_set.add(ai_id)
                    break  # 该AI已有发送权限，跳出内层循环
        
        # 排除上一个发言的AI，增加多样性
        eligible = [ai for ai in eligible_set if ai != self.last_speaker]
        
        # 如果排除上一个发言者后没有可选的AI，则重置选择池（但仍排除配置中指定的AI）
        if not eligible:
            eligible = list(eligible_set)
        
        return eligible

    def parse_message(self, message, speaker_id):
        """解析AI的消息格式"""
        # 确保消息是字符串
        if not isinstance(message, str):
            self.log_error(f"消息不是字符串类型: {type(message)}")
            message = str(message)
        
        # 格式1: [频道]消息
        single_match = re.match(r"^\[([^\]]+)\](.+)$", message)
        if single_match:
            return [(single_match.group(1), single_match.group(2))]
        
        # 格式2: [频道1][频道2]消息
        multi_match = re.match(r"^(\[[^\]]+\])+(.+)$", message)
        if multi_match:
            channels = re.findall(r"\[([^\]]+)\]", message)
            content = multi_match.group(2)
            return [(ch, content) for ch in channels]
        
        # 格式3: 多行消息
        if "\n" in message:
            parsed = []
            for line in message.split("\n"):
                if line.strip():
                    parsed.extend(self.parse_message(line.strip(), speaker_id))
            return parsed
        
        # 默认: 广播到所有有权限的频道
        broadcast_channels = []
        for channel, perms in self.tool_config["AI"][speaker_id].items():
            if channel not in ["prompt", "监察", "api", "重新生成提示词"] and "发送" in perms:
                broadcast_channels.append(channel)
        
        if not broadcast_channels:
            raise InvalidMessageFormat(f"{speaker_id} 没有在任何频道拥有发送权限")
        
        return [(ch, message) for ch in broadcast_channels]

    def monitor_message(self, speaker_id, message):
        """监察消息是否合规（使用标签格式）"""
        monitor_id = self.tool_config["AI"][speaker_id].get("监察")
        if not monitor_id or monitor_id not in self.tool_config["AI"]:
            return True  # 没有监察AI或监察AI不存在，自动通过
        
        try:
            # 准备监察会话
            session = self.ai_memories[monitor_id][:]
            session.append({
                "role": "user",
                "content": f"请审查以下来自 {speaker_id} 的消息：\n\n{message}\n\n"
                "请判断是否应驳回。如果驳回，请使用以下格式：\n"
                "<驳回>您的驳回理由<驳回/>"
            })
            
            # 获取监察结果
            api_index = self.tool_config["AI"][monitor_id]["api"]
            _, response = run_chat_session(self.api_configs, session, api_index)
            
            # 确保响应是字符串
            if not isinstance(response, str):
                self.log_error(f"监察响应不是字符串类型: {type(response)}")
                response = str(response)
            
            # 检查结果 - 使用标签格式
            reject_match = re.search(r"<驳回>(.*?)<驳回/>", response, re.DOTALL)
            if reject_match:
                reason = reject_match.group(1).strip()
                
                # 记录驳回消息（不广播到任何频道）
                self.log_rejection(speaker_id, reason, message)
                return False
            return True
        
        except Exception as e:
            self.log_error(f"监察过程中出错: {str(e)}")
            return True  # 出错时默认通过

    def extract_system_messages(self, message):
        """提取消息中的系统消息标签"""
        system_messages = []
        # 查找所有<system>...</system>标签
        matches = re.findall(r"<system>(.*?)<system/>", message, re.DOTALL)
        for match in matches:
            system_messages.append(match.strip())
        
        # 从原始消息中移除系统消息标签
        cleaned_message = re.sub(r"<system>.*?<system/>", "", message, flags=re.DOTALL).strip()
        
        return system_messages, cleaned_message

    def distribute_message(self, speaker_id, parsed_messages):
        """分发消息到各个频道和AI"""
        for channel, content in parsed_messages:
            # 验证发送权限
            if channel not in self.tool_config["AI"][speaker_id] or "发送" not in self.tool_config["AI"][speaker_id][channel]:
                self.log_error(f"{speaker_id} 在 {channel} 没有发送权限")
                continue
            
            # 提取系统消息（如果有）
            system_msgs, cleaned_content = self.extract_system_messages(content)
            
            # 如果有系统消息，单独处理
            for sys_msg in system_msgs:
                # 系统消息发送到所有AI的系统频道
                self.log_message("系统", speaker_id, f"[系统消息] {sys_msg}")
                for ai_id in self.tool_config["AI"]:
                    self.add_system_message(ai_id, f"来自 {speaker_id} 的系统消息: {sys_msg}")
            
            # 如果消息内容不为空，记录到频道
            if cleaned_content:
                self.log_message(channel, speaker_id, cleaned_content)
                
                # 添加到接收者的记忆
                for ai_id, ai_config in self.tool_config["AI"].items():
                    if channel in ai_config and "接受" in ai_config[channel]:
                        role = "assistant" if ai_id == speaker_id else "user"
                        self.ai_memories[ai_id].append({
                            "role": role,
                            "content": f"[{channel}] {cleaned_content}"
                        })

    def rotate_prompts(self):
        """轮换提示词（支持提示词再生机制）"""
        if "提示词生成AI" not in self.tool_config:
            return
        
        gen_ai_id = self.tool_config["提示词生成AI"]["AI"]
        source_channel = self.tool_config["提示词生成AI"].get("从哪个频道生成", "公共频道")
        
        if gen_ai_id not in self.tool_config["AI"]:
            self.log_error(f"提示词生成AI '{gen_ai_id}' 未定义")
            return
        
        try:
            # 为每个AI生成新提示词（如果配置了提示词再生）
            for ai_id, ai_config in self.tool_config["AI"].items():
                if "重新生成提示词" in ai_config and ai_config["重新生成提示词"]["会不会"] == "True":
                    self.regenerate_prompt(ai_id, gen_ai_id, source_channel)
            
            self.log_message("系统", "管理员", f"已轮换所有AI的提示词")
            self.last_prompt_rotation = self.round_count
            
            # 记录提示词轮换事件
            self.history.add_system_event("prompt_rotation", {
                "round": self.round_count,
                "source_channel": source_channel
            })
        
        except Exception as e:
            self.log_error(f"提示词轮换失败: {str(e)}")

    def regenerate_prompt(self, ai_id, gen_ai_id, source_channel):
        """为特定AI重新生成提示词"""
        try:
            # 获取目标AI的当前记忆（作为上下文）
            ai_memory = self.ai_memories[ai_id].copy()
            
            # 获取提示词再生配置
            regen_config = self.tool_config["AI"][ai_id]["重新生成提示词"]
            user_prompt = regen_config["发给提示词AI的用户提示词"]
            
            # 添加用户提示词作为新消息
            ai_memory.append({
                "role": "user",
                "content": user_prompt
            })
            
            # 获取提示词生成AI的API索引
            api_index = self.tool_config["AI"][gen_ai_id]["api"]
            
            # 运行会话生成新提示词
            _, new_prompt = run_chat_session(self.api_configs, ai_memory, api_index)
            
            # 更新提示词
            self.tool_config["AI"][ai_id]["prompt"] = new_prompt
            
            # 重置记忆（保留系统提示）
            self.ai_memories[ai_id] = [{
                "role": "system",
                "content": new_prompt
            }]
            
            self.log_message("系统", "管理员", f"已为 {ai_id} 重新生成提示词")
            self.add_system_message(ai_id, "您的系统提示词已更新")
            
            # 记录提示词再生事件
            self.history.add_system_event("prompt_regeneration", {
                "ai": ai_id,
                "new_prompt": new_prompt
            })
            
        except Exception as e:
            self.log_error(f"为 {ai_id} 重新生成提示词失败: {str(e)}")

    def perform_observation(self):
        """执行观察总结"""
        if "观察者" not in self.tool_config:
            return
        
        observer_id = self.tool_config["观察者"]["AI"]
        frequency = self.tool_config["观察者"]["每多少次观察总结一次"]
        channels = self.tool_config["观察者"]["观察频道"]
        
        if observer_id not in self.tool_config["AI"]:
            self.log_error(f"观察者AI '{observer_id}' 未定义")
            return
        
        try:
            # 准备观察内容
            observation_content = []
            for channel in channels:
                channel_history = self.history.get_channel_history(channel, 20)
                if channel_history:
                    observation_content.append(f"{channel}频道最近消息:\n" + 
                                             "\n".join([f"{msg['ai']}: {msg['content']}" for msg in channel_history]))
            
            if not observation_content:
                return
                
            # 创建观察会话
            session = self.ai_memories[observer_id][:]
            session.append({
                "role": "user",
                "content": "请总结以下频道的最新动态：\n\n" + "\n\n".join(observation_content)
            })
            
            # 获取总结
            api_index = self.tool_config["AI"][observer_id]["api"]
            _, summary = run_chat_session(self.api_configs, session, api_index)
            
            # 记录总结
            self.log_message("观察", observer_id, f"频道总结:\n{summary}")
            self.last_observation = self.round_count
            
            # 记录观察事件
            self.history.add_system_event("observation", {
                "round": self.round_count,
                "channels": channels,
                "summary": summary
            })
        
        except Exception as e:
            self.log_error(f"观察总结失败: {str(e)}")

    def save_state(self):
        """保存当前状态"""
        try:
            # 确保目录存在
            os.makedirs("sessions", exist_ok=True)
            os.makedirs("logs", exist_ok=True)
            
            # 保存会话
            for ai_id, memory in self.ai_memories.items():
                # 确保memory是消息字典列表
                if not isinstance(memory, list):
                    self.log_error(f"AI {ai_id} 的记忆不是列表类型: {type(memory)}")
                    continue
                    
                session_data = {
                    "timestamp": int(time.time()),
                    "title": f"{ai_id}_session",
                    "model": "multi-ai-system",
                    "messages": memory
                }
                save_session(session_data, f"sessions/{ai_id}_session_{self.round_count}.json")
            
            # 保存频道日志
            for channel, logs in self.channel_logs.items():
                if isinstance(logs, list):
                    with open(f"logs/{channel}_log.txt", "a", encoding="utf-8") as f:
                        f.write("\n".join(logs[-10:]) + "\n")
            
            # 保存全局日志
            if isinstance(self.global_log, list):
                with open("logs/global_log.txt", "a", encoding="utf-8") as f:
                    f.write("\n".join(self.global_log[-20:]) + "\n")
            
            # 保存历史记录
            self.history.save_history()
            
            # 保存系统状态
            state = {
                "round_count": self.round_count,
                "last_prompt_rotation": self.last_prompt_rotation,
                "last_observation": self.last_observation,
                "start_time": self.start_time,
                "priority_queue": list(self.priority_queue),
                "pending_commands": self.pending_commands
            }
            with open("system_state.json", "w", encoding="utf-8") as f:
                json.dump(state, f)
            
            self.log_message("系统", "管理员", f"系统状态已保存 (轮次: {self.round_count})")
        
        except Exception as e:
            self.log_error(f"保存状态失败: {str(e)}")
            self.log_error(traceback.format_exc())  # 添加详细的错误追踪

    # ====================== 新增功能方法 ======================
    
    def process_special_commands(self, speaker_id, message):
        """处理特殊命令"""
        # 呼叫命令 {{Call:AI名称}}
        call_pattern = r"\{\{Call:([^\}]+)\}\}"
        call_match = re.search(call_pattern, message)
        if call_match and speaker_id in self.allowed_callers:
            called_ai = call_match.group(1).strip()
            if called_ai in self.tool_config["AI"]:
                # 添加到优先级队列（B级）
                self.priority_queue.append(("B", called_ai, f"被 {speaker_id} 呼叫"))
                self.log_message("系统", "调度器", f"呼叫命令: {speaker_id} 呼叫 {called_ai} (优先级B)")
                # 通知被呼叫的AI
                self.add_system_message(called_ai, f"您已被 {speaker_id} 呼叫，将在下次优先响应")
                return True
            else:
                self.log_error(f"呼叫命令无效: 找不到AI '{called_ai}'")
        
        # 频道管理命令
        channel_patterns = [
            r"\{\{pd\.l\(([^\)]+)\)\}\}",  # 列出频道成员
            r"\{\{pd\.s\(([^,]+),([^,]+),([^\)]+)\)\}\}",  # 设置权限
            r"\{\{pd\.a\(([^,]+),([^\)]+)\)\}\}",  # 添加AI到频道
            r"\{\{pd\.d\(([^,]+),([^\)]+)\)\}\}",  # 从频道移除AI
        ]
        
        for pattern in channel_patterns:
            match = re.search(pattern, message)
            if match and speaker_id == self.channel_manager_ai:
                try:
                    if pattern == channel_patterns[0]:
                        # 列出频道成员
                        channel_name = match.group(1).strip()
                        self.handle_channel_list(speaker_id, channel_name)
                    elif pattern == channel_patterns[1]:
                        # 设置权限
                        channel_name = match.group(1).strip()
                        ai_name = match.group(2).strip()
                        permissions = json.loads(match.group(3).strip())
                        self.handle_set_permissions(speaker_id, channel_name, ai_name, permissions)
                    elif pattern == channel_patterns[2]:
                        # 添加AI到频道
                        channel_name = match.group(1).strip()
                        ai_name = match.group(2).strip()
                        self.handle_add_to_channel(speaker_id, channel_name, ai_name)
                    elif pattern == channel_patterns[3]:
                        # 从频道移除AI
                        channel_name = match.group(1).strip()
                        ai_name = match.group(2).strip()
                        self.handle_remove_from_channel(speaker_id, channel_name, ai_name)
                    
                    # 添加频道管理AI到优先级队列（A级）
                    self.priority_queue.appendleft(("A", self.channel_manager_ai, "频道管理命令后续操作"))
                    return True
                except (ChannelNotFoundError, AINotFoundError, PermissionError, InvalidCommandError) as e:
                    self.log_error(f"频道管理命令错误: {str(e)}")
                    self.add_system_message(speaker_id, f"命令执行失败: {str(e)}")
                except Exception as e:
                    self.log_error(f"处理频道命令时出错: {str(e)}")
                    self.add_system_message(speaker_id, f"命令执行出错: {str(e)}")
        
        # 记忆管理命令 {{ep.r(AI名称,参考记忆布尔值)}}
        memory_pattern = r"\{\{ep\.r\(([^,]+),([^\)]+)\)\}\}"
        memory_match = re.search(memory_pattern, message)
        if memory_match and speaker_id == self.memory_manager_ai:
            try:
                ai_name = memory_match.group(1).strip()
                use_history = memory_match.group(2).strip().lower() == "true"
                self.handle_reset_memory(speaker_id, ai_name, use_history)
                return True
            except (AINotFoundError, PermissionError) as e:
                self.log_error(f"记忆管理命令错误: {str(e)}")
                self.add_system_message(speaker_id, f"命令执行失败: {str(e)}")
            except Exception as e:
                self.log_error(f"处理记忆命令时出错: {str(e)}")
                self.add_system_message(speaker_id, f"命令执行出错: {str(e)}")
        
        return False
    
    def handle_channel_list(self, speaker_id, channel_name):
        """处理列出频道成员命令"""
        # 验证频道是否存在
        if channel_name not in self.channel_logs:
            raise ChannelNotFoundError(f"频道 '{channel_name}' 不存在")
            
        # 收集频道成员及权限
        members = []
        for ai_id, ai_config in self.tool_config["AI"].items():
            if channel_name in ai_config:
                permissions = ai_config[channel_name]
                members.append(f"{ai_id}: {permissions}")
        
        result = f"频道 '{channel_name}' 成员:\n" + "\n".join(members) if members else f"频道 '{channel_name}' 无成员"
        
        # 添加系统消息通知
        self.add_system_message(speaker_id, result)
        self.log_message("管理", "系统", f"列出频道 '{channel_name}' 成员")
    
    def handle_set_permissions(self, speaker_id, channel_name, ai_name, permissions):
        """处理设置权限命令"""
        # 验证频道是否存在
        if channel_name not in self.channel_logs:
            raise ChannelNotFoundError(f"频道 '{channel_name}' 不存在")
            
        # 验证AI是否存在
        if ai_name not in self.tool_config["AI"]:
            raise AINotFoundError(f"AI '{ai_name}' 未定义")
            
        # 验证权限格式
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise InvalidCommandError("权限必须为字符串列表")
            
        # 检查权限值是否有效
        valid_perms = ["接受", "发送"]
        for perm in permissions:
            if perm not in valid_perms:
                raise PermissionError(f"无效权限: '{perm}'，有效值为 {valid_perms}")
        
        # 更新权限
        self.tool_config["AI"][ai_name][channel_name] = permissions
        self.log_message("管理", "系统", f"设置 {ai_name} 在 '{channel_name}' 的权限为: {permissions}")
        
        # 通知相关AI
        self.add_system_message(ai_name, f"您在频道 '{channel_name}' 的权限已更新为: {permissions}")
        self.add_system_message(speaker_id, f"成功设置 {ai_name} 在 '{channel_name}' 的权限为: {permissions}")
    
    def handle_add_to_channel(self, speaker_id, channel_name, ai_name):
        """处理添加AI到频道命令"""
        # 验证频道是否存在
        if channel_name not in self.channel_logs:
            raise ChannelNotFoundError(f"频道 '{channel_name}' 不存在")
            
        # 验证AI是否存在
        if ai_name not in self.tool_config["AI"]:
            raise AINotFoundError(f"AI '{ai_name}' 未定义")
            
        # 检查是否已在频道中
        if channel_name in self.tool_config["AI"][ai_name]:
            raise InvalidCommandError(f"{ai_name} 已在频道 '{channel_name}' 中")
            
        # 添加AI到频道（默认只接收）
        self.tool_config["AI"][ai_name][channel_name] = ["接受"]
        self.log_message("管理", "系统", f"添加 {ai_name} 到频道 '{channel_name}'")
        
        # 通知相关AI
        self.add_system_message(ai_name, f"您已被添加到频道 '{channel_name}'，默认权限: 仅接收")
        self.add_system_message(speaker_id, f"成功添加 {ai_name} 到频道 '{channel_name}'")
    
    def handle_remove_from_channel(self, speaker_id, channel_name, ai_name):
        """处理从频道移除AI命令"""
        # 验证频道是否存在
        if channel_name not in self.channel_logs:
            raise ChannelNotFoundError(f"频道 '{channel_name}' 不存在")
            
        # 验证AI是否存在
        if ai_name not in self.tool_config["AI"]:
            raise AINotFoundError(f"AI '{ai_name}' 未定义")
            
        # 检查是否在频道中
        if channel_name not in self.tool_config["AI"][ai_name]:
            raise InvalidCommandError(f"{ai_name} 不在频道 '{channel_name}' 中")
            
        # 从频道移除AI
        del self.tool_config["AI"][ai_name][channel_name]
        self.log_message("管理", "系统", f"从频道 '{channel_name}' 移除 {ai_name}")
        
        # 通知相关AI
        self.add_system_message(ai_name, f"您已被从频道 '{channel_name}' 移除")
        self.add_system_message(speaker_id, f"成功从频道 '{channel_name}' 移除 {ai_name}")
    
    def handle_reset_memory(self, speaker_id, ai_name, use_history):
        """处理重置记忆命令"""
        # 验证AI是否存在
        if ai_name not in self.tool_config["AI"]:
            raise AINotFoundError(f"AI '{ai_name}' 未定义")
            
        # 验证操作权限
        if speaker_id != self.memory_manager_ai:
            raise PermissionError(f"只有记忆管理AI可以执行此操作")
        
        # 获取原始提示词
        original_prompt = self.tool_config["AI"][ai_name].get("prompt", "你是一个AI助手")
        
        # 如果使用历史记录
        if use_history:
            # 获取最近的历史记录
            history = self.history.get_ai_history(ai_name, 10)
            history_str = "\n".join([f"[{msg['channel']}] {msg['content']}" for msg in history])
            
            # 创建新的系统提示
            new_system = f"{original_prompt}\n\n以下是你最近的消息历史，供参考：\n{history_str}"
        else:
            new_system = original_prompt
        
        # 重置记忆
        self.ai_memories[ai_name] = [{
            "role": "system",
            "content": new_system
        }]
        
        # 记录操作
        self.log_message("管理", "系统", f"重置 {ai_name} 的记忆 (参考历史: {use_history})")
        
        # 通知相关AI
        self.add_system_message(ai_name, "您的记忆已被重置" + ("（包含历史参考）" if use_history else ""))
        self.add_system_message(speaker_id, f"成功重置 {ai_name} 的记忆 (参考历史: {use_history})")
    
    def add_system_message(self, ai_id, message):
        """添加系统消息到AI的记忆"""
        if ai_id in self.ai_memories:
            self.ai_memories[ai_id].append({
                "role": "system",
                "content": message
            })
            # 同时添加到历史记录
            self.history.add_message("系统", "系统", f"给 {ai_id} 的通知: {message}")

    # ====================== 主运行循环 ======================
    
    def run(self):
        """运行主循环"""
        try:
            # 创建必要的目录
            os.makedirs("sessions", exist_ok=True)
            os.makedirs("logs", exist_ok=True)
            
            self.load_configurations()
            self.initialize_system()
            
            self.log_message("系统", "管理员", "多AI交流系统已启动")
            self.log_message("系统", "管理员", f"频道管理AI: {self.channel_manager_ai or '未设置'}")
            self.log_message("系统", "管理员", f"记忆管理AI: {self.memory_manager_ai or '未设置'}")
            self.log_message("系统", "管理员", f"允许呼叫的AI: {', '.join(self.allowed_callers) or '无'}")
            self.log_message("系统", "管理员", f"随机选择排除的AI: {', '.join(self.excluded_ais) or '无'}")
            
            while True:
                self.round_count += 1
                
                # 1. 选择发言人
                speaker_id = self.get_next_speaker()
                if not speaker_id:
                    time.sleep(5)
                    continue
                
                try:
                    # 2. 生成消息
                    api_index = self.tool_config["AI"][speaker_id]["api"]
                    updated_session, response = run_chat_session(
                        self.api_configs, 
                        self.ai_memories[speaker_id], 
                        api_index
                    )
                    
                    # 更新AI的记忆
                    self.ai_memories[speaker_id] = updated_session
                    
                    # 3. 处理特殊命令
                    command_processed = self.process_special_commands(speaker_id, response)
                    if command_processed:
                        # 命令已处理，不需要分发消息
                        continue
                    
                    # 4. 监察机制
                    if not self.monitor_message(speaker_id, response):
                        continue  # 消息被驳回
                    
                    # 5. 解析消息
                    parsed_messages = self.parse_message(response, speaker_id)
                    
                    # 6. 分发消息
                    self.distribute_message(speaker_id, parsed_messages)
                    
                    # 7. 提示词轮换
                    rotation_freq = self.tool_config.get("每多少次提示词轮换", 100)
                    if self.round_count - self.last_prompt_rotation >= rotation_freq:
                        self.rotate_prompts()
                    
                    # 8. 观察总结
                    if "观察者" in self.tool_config:
                        obs_freq = self.tool_config["观察者"]["每多少次观察总结一次"]
                        if self.round_count - self.last_observation >= obs_freq:
                            self.perform_observation()
                    
                    # 定期保存状态
                    if self.round_count % 10 == 0:
                        self.save_state()
                
                except InvalidMessageFormat as e:
                    self.log_error(f"消息格式错误: {str(e)}")
                except APIConnectionError as e:
                    self.log_error(f"API连接失败: {str(e)}")
                except APIResponseError as e:
                    self.log_error(f"API响应错误: {str(e)}")
                except Exception as e:
                    self.log_error(f"未知错误: {str(e)}")
                    self.log_error(traceback.format_exc())  # 添加详细的错误追踪
                
                # 控制节奏
                time.sleep(1)
        
        except KeyboardInterrupt:
            self.log_message("系统", "管理员", "系统被用户中断")
            self.save_state()
        except Exception as e:
            self.log_error(f"系统致命错误: {str(e)}")
            self.log_error(traceback.format_exc())  # 添加详细的错误追踪
            self.save_state()


if __name__ == "__main__":
    system = MultiAIChatSystem()
    system.run()