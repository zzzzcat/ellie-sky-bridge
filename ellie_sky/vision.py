from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass

import requests
from PIL import Image

from .commands import is_interaction_command


def is_supported_chat_message(text: str) -> bool:
    """Accept Chinese or English chat while rejecting short UI noise."""
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text):
        return True
    return len(re.findall(r"[A-Za-z]", text)) >= 2


def model_request_options(model: str) -> dict:
    if "doubao" in model.lower():
        return {"thinking": {"type": "disabled"}}
    return {}


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    direction: str = "unknown"
    has_sender_suffix: bool = False
    x_center: float = 0.5
    raw_text: str = ""

    def is_incoming_from(self, user_name: str) -> bool:
        suffix_pattern = re.compile(
            rf"\s*-\s*{re.escape(user_name)}\s*$",
            flags=re.IGNORECASE,
        )
        visual_incoming = self.direction == "incoming" and self.x_center < 0.5
        suffix_evidence = bool(suffix_pattern.search(self.raw_text))
        structured_evidence = self.sender == user_name and self.has_sender_suffix
        return visual_incoming and (suffix_evidence or structured_evidence)


@dataclass(frozen=True)
class PlayerMessage:
    sender: str
    text: str

    def ledger_key(self) -> str:
        return f"{self.sender}\n{self.text}"

    def as_dict(self) -> dict[str, str]:
        return {"sender": self.sender, "text": self.text}


@dataclass(frozen=True)
class VisionObservation:
    new_messages: list[PlayerMessage]
    visible_incoming_messages: list[PlayerMessage] | None
    scene_narration: str
    interaction_state: str
    friend_tree_panel_open: bool = False
    f_prompt_visible: bool = False
    is_friend_tree_star: bool | None = None
    interaction_confidence: float = 0.0
    raw_response: str = ""
    parsed_response: dict | None = None


def _image_data_url(
    image: Image.Image,
    max_size: tuple[int, int] = (1536, 1536),
    quality: int = 88,
) -> str:
    image = image.copy()
    image.thumbnail(max_size)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _normalize_visual_perspective(text: str, user_name: str) -> str:
    """Remove model-added bilingual/name glosses from visual narration."""
    value = re.sub(
        r"她\s*[（(]\s*(?:Ellie|she|her|艾莉)\s*[）)]",
        "她",
        text,
        flags=re.IGNORECASE,
    )
    escaped_name = re.escape(user_name)
    value = re.sub(
        rf"[‘'\"“”]?我[’'\"“”]?\s*[（(]\s*"
        rf"[‘'\"“”]?{escaped_name}[’'\"“”]?\s*[）)]",
        "我",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def _extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


class VisionClient:
    def __init__(self, base_url: str, model: str, key_env: str, timeout: float):
        api_key = os.environ.get(key_env)
        if not api_key:
            raise RuntimeError(f"Set the {key_env} environment variable before starting.")
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def read_chat(self, image: Image.Image, user_name: str) -> list[ChatMessage]:
        prompt = f"""
Read the visible Sky: Children of the Light chat-history column.
The game UI and chat messages are Chinese.

Return strict JSON only:
{{
  "messages": [
    {{
      "sender": "exact visible sender, or empty for Ellie's own message",
      "raw_text": "all text visibly printed in the bubble, including any sender suffix",
      "text": "message body without the sender suffix",
      "direction": "incoming or outgoing",
      "has_sender_suffix": true,
      "x_center": 0.25
    }}
  ]
}}

Rules:
- Preserve Chinese messages exactly, including punctuation. Do not translate.
- Read messages from top to bottom.
- Ignore the chat input placeholder and UI labels.
- An incoming message is a DARK bubble aligned on the LEFT. Its visible text
  ends with a sender suffix such as " - {user_name}".
- An outgoing message is a LIGHT bubble aligned on the RIGHT. It has NO sender
  suffix. These are Ellie's own messages and must NEVER be labeled as {user_name}.
- Remove " - {user_name}" from returned incoming text. For that bubble set
  sender="{user_name}", direction="incoming", and has_sender_suffix=true.
  Preserve the suffix in raw_text.
- For outgoing right-side bubbles set sender="", direction="outgoing", and
  has_sender_suffix=false. Preserve their visible message in raw_text.
- x_center is the horizontal center of the bubble divided by image width.
  The left edge is 0.0 and the right edge is 1.0.
- Ignore any sender shown as \u964c\u751f\u4eba.
- Ignore messages whose text consists only of dots or ellipses.
- The target user's exact nickname is {user_name}.
- Do not translate message text.
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                ],
            }],
            "temperature": 0,
        }
        payload.update(model_request_options(self.model))
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )

        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        messages: list[ChatMessage] = []
        suffix_pattern = re.compile(
            rf"\s*-\s*{re.escape(user_name)}\s*$",
            flags=re.IGNORECASE,
        )
        for item in data.get("messages", []):
            sender = str(item.get("sender", "")).strip()
            text = str(item.get("text", "")).strip()
            raw_text = str(item.get("raw_text", text)).strip()
            direction = str(item.get("direction", "unknown")).strip().lower()
            reported_suffix = item.get("has_sender_suffix") is True
            try:
                x_center = float(item.get("x_center", 0.5))
            except (TypeError, ValueError):
                x_center = 0.5

            if not text or sender == "\u964c\u751f\u4eba":
                continue
            if not text.strip(".\u2026 "):
                continue

            visible_suffix = bool(suffix_pattern.search(raw_text))
            text = suffix_pattern.sub("", text).strip()
            messages.append(ChatMessage(
                sender=sender,
                text=text,
                direction=direction,
                has_sender_suffix=reported_suffix or visible_suffix,
                x_center=x_center,
                raw_text=raw_text,
            ))
        return messages

    def read_new_incoming(
        self,
        previous_chat_image: Image.Image,
        current_chat_image: Image.Image,
        user_name: str,
        recent_outgoing: list[str] | None = None,
    ) -> list[str]:
        outgoing_json = json.dumps(recent_outgoing or [], ensure_ascii=False)
        prompt = f"""
Compare two chronological screenshots of the Sky: Children of the Light
chat-history panel.

Image 1 is the BEFORE chat-history panel.
Image 2 is the AFTER chat-history panel.

Return only messages that appeared newly in Image 2:
- Return EVERY qualifying new message, in visual top-to-bottom order. Do not
  return only the newest one.
- The bubble is dark and aligned on the LEFT.
- Its visible text ends with the sender suffix "- {user_name}".
- It is not already visible in Image 1.
- The message body may be Chinese, English, or a mixture of both. Reject text
  with neither Chinese characters nor at least two English letters. The
  interaction command words "牵我" and "上来" are valid messages.
- Exclude all light RIGHT-side bubbles. Those are Ellie's own messages.
- The bridge recently sent these Ellie messages:
  {outgoing_json}
  Never return any of them, even if wrapping, punctuation, or OCR differs.
- Exclude moved, reflowed, or scrolled copies of old messages.
- Remove the trailing sender suffix from returned message text.
- Preserve the message body and all punctuation exactly, especially @. Do not
  translate it.
- Ignore messages from \u964c\u751f\u4eba and dot-only messages.

Return strict JSON only:
{{"new_messages":["exact message body"]}}

If uncertain whether a message is genuinely new, return no message.
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(previous_chat_image)},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(current_chat_image)},
                    },
                ],
            }],
            "temperature": 0,
        }
        payload.update(model_request_options(self.model))
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )
        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        messages = []
        for value in data.get("new_messages", []):
            text = str(value).strip()
            if (
                text
                and text.strip(".\u2026 ")
                and (is_supported_chat_message(text) or is_interaction_command(text))
            ):
                messages.append(text)
        return messages

    def describe_scene(
        self,
        current_scene_image: Image.Image,
        user_name: str,
    ) -> str:
        prompt = f"""
请为 Ellie 描述当前的《光·遇》游戏画面。

- 用不超过120个汉字的一段中文，描述她真正需要知道的视觉信息。
- Ellie 是本机玩家。她的角色没有昵称标签，当前外观是粉色斗篷、樱花头饰和双马尾。
- 昵称为“{user_name}”的角色是“我”。描述中用“我”称呼该角色，用“她”称呼 Ellie。
- 优先描述人物、互动、动作和重要地点；环境可使用生动、唯美但准确的语言。
- 如果不能确定地图名称，就描述其可见外观，不要猜测。
- 只能描述画面中可见的事实，不要推测心理、情绪、意图、对话或画面外事件。
- 不要输出星号。

只返回严格 JSON：
{{"scene_narration":"简洁的中文画面描述"}}
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(
                                current_scene_image,
                                max_size=(1024, 1024),
                                quality=78,
                            )
                        },
                    },
                ],
            }],
            "temperature": 0,
        }
        payload.update(model_request_options(self.model))
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )
        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        narration = str(data.get("scene_narration", "")).strip()
        narration = re.sub(r"[*\r\n]+", " ", narration)
        narration = re.sub(r"\s+", " ", narration).strip()
        if not narration:
            narration = "她能看到当前的光遇场景，但细节不清楚。"
        return narration

    def observe_changes(
        self,
        previous_view_image: Image.Image,
        current_view_image: Image.Image,
        user_name: str,
        recent_outgoing: list[str] | None = None,
    ) -> VisionObservation:
        outgoing_json = json.dumps(recent_outgoing or [], ensure_ascii=False)
        prompt = f"""
请比较两张按时间先后排列的《光·遇》完整游戏截图。
只读取游戏主界面中玩家头顶的聊天气泡，不要读取左侧聊天记录面板。

图1：较早的游戏画面。
图2：较新的游戏画面。

角色识别规则：
- 这是第三人称视角，镜头由本机角色“她”控制。
- 她没有昵称标签，当前外观是粉色斗篷、樱花头饰和双马尾，通常位于画面下方或中央附近。
- 昵称为“{user_name}”的角色是“我”。其他有可读昵称的角色是其他玩家。
- 不要只凭角色距离判断身份；应综合昵称标签、外观、第三人称位置和两张画面的运动连续性。
- 光照可能使粉色斗篷看起来偏红或橙色；也不要把装扮相似的其他玩家误认成她。
- 如果她被遮挡或无法可靠识别，应明确表示看不清，不要猜测。

new_messages 字段：
- 返回图2中新出现的所有具名玩家消息，按画面中从上到下的顺序排列。
- 有效消息必须位于该玩家可见昵称的正下方；sender 使用画面中准确可见的昵称。
- “{user_name}”以及其他具名玩家的消息同样有效。
- 主界面气泡末尾没有发送者后缀，发送者只能根据气泡正上方的昵称判断。
- 接受中文、英文及中英混合消息。既没有中文、也没有至少两个英文字母的文本无效。
- “牵我”和“上来”也是有效指令消息，必须原样保留。
- 排除她自己发出的气泡。她自己的气泡没有昵称位于正上方，颜色可能较浅或呈米白色。
- 绝对不要返回以下她最近发出的消息：
  {outgoing_json}
- 这份列表只用于辅助识别她没有昵称的自有气泡；如果相同文字明确挂在其他玩家昵称下方，仍应返回。
- 排除图1中已经存在的旧消息。保留消息原文，不要翻译。
- 忽略昵称显示为“陌生人”、昵称无法辨认以及内容只有点号或省略号的消息。
- 忽略 F 提示、四角星好友树按钮、玩家昵称本身、能量条及其他 UI 文本。
- 无法确定一条消息是否确实为新消息时，宁可省略。

visible_incoming_messages 字段：
- 返回图2中当前可见的全部有效玩家气泡，无论新旧，按画面从上到下排列。
- 包含“牵我”和“上来”指令气泡。
- 昵称只放入 sender，不要混入 text，也不要包含 UI 文本。

scene_narration 字段：
- 这是提供给她的视觉场景更新，而不是机械的截图说明。
- 只有图1到图2发生了有意义的场景变化时才填写；没有变化时返回空字符串。
- 场景变化包括换地图、进入或离开房间、来到明显不同的区域，或周围环境发生显著变化。
- 发生场景变化时，用不超过80个汉字生动描述静态景色、光线、氛围及重要地点或物体。
- 可以使用“绿意盎然”“静谧梦幻”等准确的唯美表达，但不要为了填满字段而堆砌无用细节。
- 不要因为轻微走动、镜头旋转、UI变化或人物遮挡而重复描述相同的草地、花朵、烛火或建筑。
- 不要提及聊天面板、聊天窗口、聊天气泡、输入框、HUD 或其他 UI 元素。

interaction_state 字段：
- 用不超过80个汉字的一至两句中文，概括当前最有交流价值的人物与互动信息。
- 始终只用“她”称呼本机角色，只用“我”称呼昵称为“{user_name}”的角色。
- 输出必须使用自然中文，不要使用英文名字或英文人称代词。
- 禁止在“她”或“我”后面用括号补充角色名、昵称、翻译或代词解释。
- 正确示例：“她和我并肩站着，丫丫来到我们身边。”
- 按以下优先级描述：人物与社交变化；我和她的状态；人物正在做什么；重要地点或物体。
- 人物与社交变化包括谁新出现、谁靠近我们、谁暂时离开视野。新出现的玩家应写出准确昵称，并具体描述其显著穿着和外貌。
- 外貌只在人物首次出现、装扮明显变化或确有交流价值时详细描述，不要每次重复。
- 明确描述我和她当前是牵手、背背、公主抱、拥抱、一起坐着、并肩、面对面、分开或一起飞行；看不清就如实说明。
- 描述画面中明确可见的动作，例如坐下、飞行、点火、招手或面向某个物体。
- 某人仅仅不在图2中，不代表已经离开；镜头转动或遮挡时应写“暂时离开她的视野”，不要断言对方离开游戏。
- 只描述可见事实，不要推测感情、心理、意图、正在交谈、等待指示或画面外事件。
- 不要为了填满字段而强行输出无用信息。即使没有人物变化，也应简短写明我和她当前可见的互动状态。
- 不要输出星号。

f_prompt_visible 与 is_friend_tree_star 字段：
- 如果好友树面板已经打开，即使面板上存在图标，也必须令 f_prompt_visible=false、is_friend_tree_star=null。
- 检查图2中所有玩家附近可见的 F 键互动提示，不限于昵称“{user_name}”的角色。
- F 提示通常附着在对应玩家的昵称下方或角色头顶附近；不要把聊天文字、普通 UI 字母或键位说明误认成互动提示。
- 优先寻找任意玩家身上的非好友树动作提示，例如牵手、背背、拥抱、公主抱或其他身体/动作符号。
- 只要画面中至少有一个清晰的非好友树动作 F 提示，就令 f_prompt_visible=true、is_friend_tree_star=false；即使同时还看到了四角星按钮也一样。
- 如果画面中可见的 F 提示全都是圆形四角闪光星形好友树按钮，令 f_prompt_visible=true、is_friend_tree_star=true。
- 如果完全没有 F 互动提示，令 f_prompt_visible=false、is_friend_tree_star=null。
- 如果确实看到了 F 互动提示，但所有图标都模糊到无法判断类型，令 f_prompt_visible=true、is_friend_tree_star=null，并给出较低置信度。

friend_tree_panel_open 字段：
- 图2出现大型半透明好友树面板、关系连线和动作节点，或底部出现“信息 Q”“选择 SPACE”“退后 ESC”等提示时为 true。
- 普通游戏主界面中为 false。

只返回严格 JSON：
{{
  "new_messages":[{{"sender":"画面中的准确昵称","text":"消息原文"}}],
  "visible_incoming_messages":[{{"sender":"画面中的准确昵称","text":"消息原文"}}],
  "scene_narration":"有变化时填写生动的中文环境描述，否则为空字符串",
  "interaction_state":"简洁的中文人物及互动状态",
  "friend_tree_panel_open":false,
  "f_prompt_visible":false,
  "is_friend_tree_star":null,
  "interaction_confidence":0.0
}}
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(
                                previous_view_image,
                                max_size=(1280, 1280),
                                quality=82,
                            )
                        },
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(
                                current_view_image,
                                max_size=(1280, 1280),
                                quality=82,
                            )
                        },
                    },
                ],
            }],
            "temperature": 0,
        }
        payload.update(model_request_options(self.model))
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )
        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        messages: list[PlayerMessage] = []
        for value in data.get("new_messages", []):
            if not isinstance(value, dict):
                continue
            sender = str(value.get("sender", "")).strip()
            text = str(value.get("text", "")).strip()
            if (
                sender
                and sender != "陌生人"
                and text
                and text.strip(".\u2026 ")
                and (is_supported_chat_message(text) or is_interaction_command(text))
            ):
                messages.append(PlayerMessage(sender, text))
        raw_visible_messages = data.get("visible_incoming_messages")
        visible_messages: list[PlayerMessage] | None = None
        if isinstance(raw_visible_messages, list):
            visible_messages = []
            for value in raw_visible_messages:
                if not isinstance(value, dict):
                    continue
                sender = str(value.get("sender", "")).strip()
                text = str(value.get("text", "")).strip()
                if (
                    sender
                    and sender != "陌生人"
                    and text
                    and text.strip(".\u2026 ")
                    and (is_supported_chat_message(text) or is_interaction_command(text))
                ):
                    visible_messages.append(PlayerMessage(sender, text))
        narration = str(data.get("scene_narration", "")).strip()
        narration = re.sub(r"[*\r\n]+", " ", narration)
        narration = _normalize_visual_perspective(narration, user_name)
        interaction_state = str(data.get("interaction_state", "")).strip()
        interaction_state = re.sub(r"[*\r\n]+", " ", interaction_state)
        interaction_state = _normalize_visual_perspective(
            interaction_state,
            user_name,
        )
        if not interaction_state:
            interaction_state = "我和她目前的互动状态不清楚。"
        friend_tree_panel_open = data.get("friend_tree_panel_open") is True
        f_prompt_visible = data.get("f_prompt_visible") is True
        raw_friend_tree = data.get("is_friend_tree_star")
        is_friend_tree_star = raw_friend_tree if isinstance(raw_friend_tree, bool) else None
        if friend_tree_panel_open:
            f_prompt_visible = False
            is_friend_tree_star = None
        try:
            interaction_confidence = float(data.get("interaction_confidence", 0.0))
        except (TypeError, ValueError):
            interaction_confidence = 0.0
        interaction_confidence = max(0.0, min(1.0, interaction_confidence))
        return VisionObservation(
            messages,
            visible_messages,
            narration,
            interaction_state,
            friend_tree_panel_open,
            f_prompt_visible,
            is_friend_tree_star,
            interaction_confidence,
            content,
            data,
        )
