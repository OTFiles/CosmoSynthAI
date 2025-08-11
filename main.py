import os
import json
import random
import re
import time
import traceback
from datetime import datetime
from chat_core import load_configs, run_chat_session, save_session, load_session, attach_file
from chat_core import FileTooLargeError, APIConnectionError, APIResponseError, InvalidSessionError, ConfigLoadError

# 自定义异常
class ConfigError(Exception):
    pass

class InvalidMessageFormat(Exception):
    pass

# 颜色输出工具
class Color:
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BLUE = "\033[34m"
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
                if channel not in ["prompt", "监察", "api"]:  # 跳过特殊字段
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
        self.history.add_system_event("error", message)

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
        
        # 彩色输出到终端
        print(f"{Color.YELLOW}[{channel}]{Color.RESET}{Color.RED}{ai_id}:{Color.RESET}{Color.BLUE} {message}{Color.RESET}")

    def get_eligible_speakers(self):
        """获取有发言权限的AI列表"""
        eligible = []
        for ai_id, ai_config in self.tool_config["AI"].items():
            for channel, perms in ai_config.items():
                if channel not in ["prompt", "监察", "api"] and "发送" in perms:
                    eligible.append(ai_id)
                    break
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
            if channel not in ["prompt", "监察", "api"] and "发送" in perms:
                broadcast_channels.append(channel)
        
        if not broadcast_channels:
            raise InvalidMessageFormat(f"{speaker_id} 没有在任何频道拥有发送权限")
        
        return [(ch, message) for ch in broadcast_channels]

    def monitor_message(self, speaker_id, message):
        """监察消息是否合规"""
        monitor_id = self.tool_config["AI"][speaker_id].get("监察")
        if not monitor_id or monitor_id not in self.tool_config["AI"]:
            return True  # 没有监察AI或监察AI不存在，自动通过
        
        try:
            # 准备监察会话
            session = self.ai_memories[monitor_id][:]
            session.append({
                "role": "user",
                "content": f"请审查以下来自 {speaker_id} 的消息：\n\n{message}\n\n请判断是否应驳回。"
                "回复必须以'驳回'或'不驳回'开头，并简要说明理由。"
            })
            
            # 获取监察结果
            api_index = self.tool_config["AI"][monitor_id]["api"]
            _, response = run_chat_session(self.api_configs, session, api_index)
            
            # 确保响应是字符串
            if not isinstance(response, str):
                self.log_error(f"监察响应不是字符串类型: {type(response)}")
                response = str(response)
            
            # 记录监察过程
            self.log_message("监察", monitor_id, f"审查 {speaker_id} 的消息: {response}")
            
            # 检查结果
            if response.startswith("驳回"):
                self.log_message("系统", "监察", f"已驳回 {speaker_id} 的消息")
                # 添加被驳回消息到历史记录
                self.history.add_message("驳回", speaker_id, message)
                return False
            return True
        
        except Exception as e:
            self.log_error(f"监察过程中出错: {str(e)}")
            return True  # 出错时默认通过

    def distribute_message(self, speaker_id, parsed_messages):
        """分发消息到各个频道和AI"""
        for channel, content in parsed_messages:
            # 验证发送权限
            if channel not in self.tool_config["AI"][speaker_id] or "发送" not in self.tool_config["AI"][speaker_id][channel]:
                self.log_error(f"{speaker_id} 在 {channel} 没有发送权限")
                continue
            
            # 记录到频道
            self.log_message(channel, speaker_id, content)
            
            # 添加到接收者的记忆
            for ai_id, ai_config in self.tool_config["AI"].items():
                if channel in ai_config and "接受" in ai_config[channel]:
                    role = "assistant" if ai_id == speaker_id else "user"
                    self.ai_memories[ai_id].append({
                        "role": role,
                        "content": f"[{channel}] {content}"
                    })

    def rotate_prompts(self):
        """轮换提示词"""
        if "提示词生成AI" not in self.tool_config:
            return
        
        gen_ai_id = self.tool_config["提示词生成AI"]["AI"]
        source_channel = self.tool_config["提示词生成AI"]["从哪个频道生成"]
        
        if gen_ai_id not in self.tool_config["AI"]:
            self.log_error(f"提示词生成AI '{gen_ai_id}' 未定义")
            return
        
        try:
            # 准备频道历史
            channel_messages = self.history.get_channel_history(source_channel, 100)
            channel_history = "\n".join([msg["content"] for msg in channel_messages])
            
            # 生成新提示词
            session = [{
                "role": "system",
                "content": "你是一个提示词生成AI。请根据以下对话历史为各个AI生成新的系统提示词。"
            }, {
                "role": "user",
                "content": f"请根据以下频道对话历史为每个AI生成新的系统提示词：\n\n{channel_history}\n\n"
                "请按以下格式回复：\nAI1: 新提示词\nAI2: 新提示词\n..."
            }]
            
            api_index = self.tool_config["AI"][gen_ai_id]["api"]
            _, response = run_chat_session(self.api_configs, session, api_index)
            
            # 解析并应用新提示词
            for line in response.split("\n"):
                if ":" in line:
                    ai_id, new_prompt = line.split(":", 1)
                    ai_id = ai_id.strip()
                    new_prompt = new_prompt.strip()
                    
                    if ai_id in self.tool_config["AI"]:
                        # 更新提示词
                        self.tool_config["AI"][ai_id]["prompt"] = new_prompt
                        
                        # 重置记忆
                        self.ai_memories[ai_id] = [{
                            "role": "system",
                            "content": new_prompt
                        }]
            
            self.log_message("系统", "管理员", f"已轮换所有AI的提示词")
            self.last_prompt_rotation = self.round_count
            
            # 记录提示词轮换事件
            self.history.add_system_event("prompt_rotation", {
                "round": self.round_count,
                "source_channel": source_channel
            })
        
        except Exception as e:
            self.log_error(f"提示词轮换失败: {str(e)}")

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
                session_data = {
                    "timestamp": int(time.time()),
                    "title": f"{ai_id}_session",
                    "model": "multi-ai-system",
                    "messages": memory
                }
                save_session(session_data, f"sessions/{ai_id}_session_{self.round_count}.json")
            
            # 保存频道日志
            for channel, logs in self.channel_logs.items():
                with open(f"logs/{channel}_log.txt", "a", encoding="utf-8") as f:
                    f.write("\n".join(logs[-10:]) + "\n")
            
            # 保存全局日志
            with open("logs/global_log.txt", "a", encoding="utf-8") as f:
                f.write("\n".join(self.global_log[-20:]) + "\n")
            
            # 保存历史记录
            self.history.save_history()
            
            # 保存系统状态
            state = {
                "round_count": self.round_count,
                "last_prompt_rotation": self.last_prompt_rotation,
                "last_observation": self.last_observation,
                "start_time": self.start_time
            }
            with open("system_state.json", "w", encoding="utf-8") as f:
                json.dump(state, f)
            
            self.log_message("系统", "管理员", f"系统状态已保存 (轮次: {self.round_count})")
        
        except Exception as e:
            self.log_error(f"保存状态失败: {str(e)}")
            self.log_error(traceback.format_exc())  # 添加详细的错误追踪

    def run(self):
        """运行主循环"""
        try:
            # 创建必要的目录
            os.makedirs("sessions", exist_ok=True)
            os.makedirs("logs", exist_ok=True)
            
            self.load_configurations()
            self.initialize_system()
            
            self.log_message("系统", "管理员", "多AI交流系统已启动")
            
            while True:
                self.round_count += 1
                
                # 1. 选择发言人
                eligible = self.get_eligible_speakers()
                if not eligible:
                    self.log_error("没有符合条件的AI可以发言")
                    time.sleep(5)
                    continue
                
                speaker_id = random.choice(eligible)
                
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
                    
                    # 3. 监察机制
                    if not self.monitor_message(speaker_id, response):
                        continue  # 消息被驳回
                    
                    # 4. 解析消息
                    parsed_messages = self.parse_message(response, speaker_id)
                    
                    # 5. 分发消息
                    self.distribute_message(speaker_id, parsed_messages)
                    
                    # 6. 提示词轮换
                    rotation_freq = self.tool_config.get("每多少次提示词轮换", 100)
                    if self.round_count - self.last_prompt_rotation >= rotation_freq:
                        self.rotate_prompts()
                    
                    # 7. 观察总结
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