from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from notellm.ir import Context

# 允许的播客生成模型白名单
# 可以在此处添加更多经过验证的模型
ALLOWED_PODCAST_MODELS = {
    "gemini-3.1-flash-lite-preview",
    "deepseek-v4-flash",
    "qwen-turbo",
}

class ModelConfigResolver:
    @staticmethod
    def resolve_model(requested_model: str, ctx: Context) -> str:
        """
        校验并解析传入的 LLM 模型名。
        如果传入名称不在白名单中，抛出 ValueError。
        """
        if requested_model not in ALLOWED_PODCAST_MODELS:
            # 此处可扩展：检查是否为有效的 model_id (ONB内部格式)
            if not requested_model.startswith("model:"):
                raise ValueError(f"模型 '{requested_model}' 不在允许的白名单中。")

        return requested_model
