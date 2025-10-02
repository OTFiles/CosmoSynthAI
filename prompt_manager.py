# prompt_manager.py
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from logging_system import UnifiedLogger
from configuration_manager import ConfigurationManager

@dataclass
class PromptRegenerationResult:
    """提示词再生结果"""
    success: bool
    new_prompt: Optional[str] = None
    error_message: Optional[str] = None

class PromptManager:
    """提示词管理器，负责提示词轮换和再生机制"""
    
    def __init__(self, config_manager: ConfigurationManager, logger: UnifiedLogger):
        self.config_manager = config_manager
        self.logger = logger
        self.last_prompt_rotation = 0
    
    def should_rotate_prompts(self, current_round: int) -> bool:
        """检查是否应该轮换提示词"""
        rotation_freq = self.config_manager.system_config.prompt_rotation_frequency
        return current_round - self.last_prompt_rotation >= rotation_freq
    
    def rotate_prompts(self, current_round: int, chat_core: Any, 
                      ai_memories: Dict[str, List[Dict[str, str]]]) -> None:
        """轮换提示词（支持提示词再生机制）"""
        if not self.config_manager.system_config.prompt_generators:
            self.logger.info("提示词轮换已跳过: 未配置提示词生成AI")
            return
        
        success_count = 0
        total_count = 0
        
        for ai_id, ai_config in self.config_manager.ai_configs.items():
            if (ai_config.prompt_regeneration and 
                ai_config.prompt_regeneration.get("enabled") == "True"):
                
                total_count += 1
                result = self.regenerate_prompt(ai_id, ai_config, chat_core, ai_memories.get(ai_id, []))
                
                if result.success:
                    success_count += 1
                    # 更新系统提示词
                    ai_memories[ai_id] = [{"role": "system", "content": result.new_prompt}]
        
        self.last_prompt_rotation = current_round
        self.logger.info(
            f"已轮换 {success_count}/{total_count} 个AI的提示词",
            metadata={"success_count": success_count, "total_count": total_count}
        )
    
    def regenerate_prompt(self, ai_id: str, ai_config: Any, 
                         chat_core: Any, ai_memory: List[Dict[str, str]]) -> PromptRegenerationResult:
        """为特定AI重新生成提示词"""
        try:
            regen_config = ai_config.prompt_regeneration
            gen_id = regen_config.get("id")
            
            # 查找匹配的提示词生成AI配置
            generator = self._find_prompt_generator(gen_id)
            if not generator:
                return PromptRegenerationResult(
                    False, 
                    error_message=f"没有可用的提示词生成AI (id={gen_id})"
                )
            
            gen_ai_id = generator["AI"]
            gen_ai_config = self.config_manager.get_ai_config(gen_ai_id)
            
            # 准备生成会话
            session = ai_memory.copy()
            session.append({
                "role": "user",
                "content": regen_config["user_prompt"]
            })
            
            # 运行会话生成新提示词
            _, new_prompt = chat_core.run_chat_session(session, gen_ai_config.api_index)
            
            self.logger.info(
                f"为 {ai_id} 重新生成提示词成功", 
                ai_id=gen_ai_id,
                metadata={"target_ai": ai_id, "generator_ai": gen_ai_id}
            )
            
            return PromptRegenerationResult(True, new_prompt)
            
        except Exception as e:
            error_msg = f"为 {ai_id} 重新生成提示词失败: {str(e)}"
            self.logger.error(error_msg, ai_id=ai_id)
            return PromptRegenerationResult(False, error_message=error_msg)
    
    def _find_prompt_generator(self, gen_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """查找提示词生成器配置"""
        if gen_id is not None:
            for gen in self.config_manager.system_config.prompt_generators:
                if gen["id"] == gen_id:
                    return gen
        
        # 如果未指定id或未找到匹配项，使用第一个生成器
        if self.config_manager.system_config.prompt_generators:
            return self.config_manager.system_config.prompt_generators[0]
        
        return None