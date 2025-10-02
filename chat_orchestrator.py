# chat_orchestrator.py
import time
import random
from collections import deque
from typing import Dict, List, Optional, Deque, Tuple, Any
from dataclasses import dataclass
from logging_system import UnifiedLogger, LogType
from configuration_manager import ConfigurationManager
from message_processor import MessageProcessor, ParsedMessage
from command_handler import CommandHandler, CommandResult
from prompt_manager import PromptManager

@dataclass
class PriorityTask:
    """优先级任务"""
    priority: str  # 'A' (最高) 或 'B'
    ai_id: str
    reason: str

class ChatOrchestrator:
    """聊天协调器，负责主循环和发言调度"""
    
    def __init__(self, config_manager: ConfigurationManager, 
                 message_processor: MessageProcessor,
                 command_handler: CommandHandler,
                 prompt_manager: PromptManager,
                 logger: UnifiedLogger,
                 chat_core: Any):
        self.config_manager = config_manager
        self.message_processor = message_processor
        self.command_handler = command_handler
        self.prompt_manager = prompt_manager
        self.logger = logger
        self.chat_core = chat_core
        
        # 状态管理
        self.ai_memories: Dict[str, List[Dict[str, str]]] = {}
        self.priority_queue: Deque[PriorityTask] = deque()
        self.round_count = 0
        self.last_speaker: Optional[str] = None
        self.first_ai_id: Optional[str] = None
        self.first_ai_spoken = False
        
        # 在构造函数中初始化AI记忆
        self._initialize_ai_memories()
        
        # 记录初始化完成
        self.logger.info(f"聊天协调器初始化完成，共初始化 {len(self.ai_memories)} 个AI的记忆")
        
    def _initialize_ai_memories(self) -> None:
        """初始化AI记忆"""
        self.ai_memories.clear()
        if hasattr(self.config_manager, 'ai_configs') and self.config_manager.ai_configs:
            for ai_id, ai_config in self.config_manager.ai_configs.items():
                self.ai_memories[ai_id] = [{
                    "role": "system", 
                    "content": ai_config.prompt
                }]
        else:
            self.logger.warning("配置管理器中的AI配置为空，无法初始化记忆")
    
    def get_next_speaker(self) -> Optional[str]:
        """获取下一个发言的AI（考虑优先级队列）"""
        # 首先检查优先级队列
        if self.priority_queue:
            task = self.priority_queue.popleft()
            self.logger.info(
                f"优先级调用: {task.ai_id} (原因: {task.reason})",
                metadata={"priority": task.priority, "reason": task.reason}
            )
            return task.ai_id
            
        # 没有优先级任务时，从符合条件的AI中随机选择
        eligible = self._get_eligible_speakers()
        if not eligible:
            self.logger.warning("没有符合条件的AI可以发言")
            return None
            
        speaker_id = random.choice(eligible)
        self.last_speaker = speaker_id
        
        # 如果是第一个发言的AI，记录下来
        if not self.first_ai_id:
            self.first_ai_id = speaker_id
            
        return speaker_id
    
    def _get_eligible_speakers(self) -> List[str]:
        """获取有发言权限的AI列表"""
        eligible_set = set()
        excluded_ais = self.config_manager.system_config.excluded_ais
        
        for ai_id, ai_config in self.config_manager.ai_configs.items():
            # 排除配置中指定的AI
            if ai_id in excluded_ais:
                continue
                
            # 检查该AI是否有发送权限
            for channel_perms in ai_config.channels.values():
                if "send" in channel_perms:
                    eligible_set.add(ai_id)
                    break
        
        # 排除上一个发言的AI，增加多样性
        eligible = [ai for ai in eligible_set if ai != self.last_speaker]
        
        # 如果排除上一个发言者后没有可选的AI，则重置选择池
        if not eligible:
            eligible = list(eligible_set)
        
        return eligible
    
    def add_priority_task(self, ai_id: str, reason: str, priority: str = "B") -> None:
        """添加优先级任务"""
        task = PriorityTask(priority=priority, ai_id=ai_id, reason=reason)
        self.priority_queue.append(task)
    
    def process_ai_turn(self, speaker_id: str) -> bool:
        """处理AI的发言回合"""
        try:
            # 处理开场白
            if not self.first_ai_spoken and self.config_manager.system_config.opening_speech:
                if speaker_id == self.first_ai_id:
                    self._add_opening_speech(speaker_id)
                self.first_ai_spoken = True
            
            # 生成消息
            ai_config = self.config_manager.get_ai_config(speaker_id)
            
            # 确保会话格式正确
            session = self.ai_memories[speaker_id]
            if not session:
                session = [{"role": "system", "content": ai_config.prompt}]
            
            updated_session, response = self.chat_core.run_chat_session(
                session, 
                ai_config.api_index
            )
            
            # 更新AI的记忆
            self.ai_memories[speaker_id] = updated_session
            
            # 处理特殊命令
            command_result = self.command_handler.process_command(speaker_id, response)
            if command_result and command_result.success:
                self._handle_command_result(speaker_id, command_result)
                return True
            
            # 监察机制
            if not self.message_processor.monitor_message(speaker_id, response, self.chat_core):
                return False  # 消息被驳回
            
            # 解析和分发消息
            parsed_message = self.message_processor.parse_message(response, speaker_id)
            self._distribute_message(speaker_id, parsed_message)
            
            return True
            
        except Exception as e:
            self.logger.error(f"处理AI回合时出错: {str(e)}", ai_id=speaker_id)
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}", ai_id=speaker_id)
            return False
    
    def _add_opening_speech(self, speaker_id: str) -> None:
        """添加开场白"""
        opening_speech = self.config_manager.system_config.opening_speech
        self.logger.info(f"向第一个AI {speaker_id} 添加开场白")
        self.ai_memories[speaker_id].append({
            "role": "user",
            "content": opening_speech
        })
    
    def _handle_command_result(self, speaker_id: str, result: CommandResult) -> None:
        """处理命令执行结果"""
        # 记录命令执行结果
        self.logger.info(result.message, ai_id=speaker_id, log_type=LogType.COMMAND)
        
        # 处理需要后续操作的情况
        if result.requires_followup and result.followup_ai:
            priority = "A" if speaker_id in [self.config_manager.system_config.channel_manager_ai,
                                           self.config_manager.system_config.memory_manager_ai] else "B"
            self.add_priority_task(result.followup_ai, f"命令后续操作", priority)
    
    def _distribute_message(self, speaker_id: str, parsed_message: ParsedMessage) -> None:
        """分发消息到各个频道和AI"""
        # 处理系统消息
        for sys_msg in parsed_message.system_messages:
            self.logger.info(f"[系统消息] {sys_msg}", ai_id=speaker_id)
            for ai_id in self.config_manager.ai_configs:
                self._add_system_message(ai_id, f"来自 {speaker_id} 的系统消息: {sys_msg}")
        
        # 分发主要消息
        if parsed_message.content:
            # 记录AI消息
            self.logger.log_ai_message(
                speaker_id, 
                parsed_message.content, 
                parsed_message.channels
            )
            
            # 添加到接收者的记忆
            for ai_id, ai_config in self.config_manager.ai_configs.items():
                for channel in parsed_message.channels:
                    if channel in ai_config.channels and "receive" in ai_config.channels[channel]:
                        role = "assistant" if ai_id == speaker_id else "user"
                        self.ai_memories[ai_id].append({
                            "role": role,
                            "content": f"[{channel}] {parsed_message.content}"
                        })
    
    def _add_system_message(self, ai_id: str, message: str) -> None:
        """添加系统消息到AI的记忆"""
        if ai_id in self.ai_memories:
            self.ai_memories[ai_id].append({
                "role": "system",
                "content": message
            })
    
    def run_main_loop(self) -> None:
        """运行主循环"""
        self.logger.info("多AI交流系统已启动")
        
        try:
            while True:
                self.round_count += 1
                
                # 选择发言人
                speaker_id = self.get_next_speaker()
                if not speaker_id:
                    time.sleep(5)
                    continue
                
                # 处理AI回合
                self.process_ai_turn(speaker_id)
                
                # 提示词轮换
                if self.prompt_manager.should_rotate_prompts(self.round_count):
                    self.prompt_manager.rotate_prompts(
                        self.round_count, self.chat_core, self.ai_memories
                    )
                
                # 控制节奏
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.logger.info("系统被用户中断")
        except Exception as e:
            self.logger.error(f"系统致命错误: {str(e)}")