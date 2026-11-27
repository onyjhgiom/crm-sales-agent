"""LangChain 生态桥接层（可选、附加，不改变既有答案路径）。

为什么有这一层：
- 项目主链是 LangGraph + 自研 LLM 封装（熔断/重试/指标）+ 统一 ToolRegistry，
  这些都是刻意保持零依赖的实现。但真实业务里经常需要把能力接到 LangChain 生态
  （LCEL 链、工具调用、回调可观测性、外部 Agent 复用）。
- 本模块把现有能力**适配**成 LangChain 标准对象：
  BaseChatModel / ChatPromptTemplate / StructuredTool / BaseCallbackHandler，
  让外部编排可以直接 `prompt | model | parser` 或把 CRM 工具交给 Agent 调用。

硬约束（与 AGENTS.md 一致）：
1. **默认关闭**：`LANGCHAIN_ENABLED=false` 时，主图/节点/LLM 调用路径逐字节不变。
2. **离线零网络**：桥接的 ChatModel 底层仍然调用 `LLM.chat()`——后者在离线/熔断时
   返回 None；这里转成空 AIMessage，由调用方决定降级，绝不自行发起外部请求。
3. **失败可降级**：langchain-core 缺失或版本不兼容时 `available=False`，
   build_* 返回 None，调用方安全退化为原来的手写路径。
4. **错误脱敏**：工具适配层把 `ToolResult` 的用户安全文案转成异常，不暴露 detail/堆栈。
5. **数字不被改写**：这里只做协议适配，不碰答案正文与数字核对逻辑。

依赖：langchain-core（由 langgraph 传递依赖，无需额外主包）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

log = logging.getLogger("crm_agent.langchain_bridge")

# ---- 可选依赖探测 ---------------------------------------------------------
_IMPORT_ERROR = ""
try:  # noqa: SIM105 - 显式捕获，记录一次，避免在热路径反复 import 失败
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import StructuredTool
    from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: F401 - 探活
    from pydantic import Field, create_model
except Exception as exc:  # noqa: BLE001 - 依赖缺失/版本不兼容都归为不可用
    _IMPORT_ERROR = type(exc).__name__
    BaseCallbackHandler = object  # type: ignore[assignment,misc]
    BaseChatModel = object  # type: ignore[assignment,misc]
    AIMessage = BaseMessage = HumanMessage = SystemMessage = object  # type: ignore[assignment,misc]
    StrOutputParser = ChatPromptTemplate = RunnableLambda = StructuredTool = None  # type: ignore[assignment]
    Field = create_model = None  # type: ignore[assignment]


def available() -> bool:
    """langchain-core 是否可用（进程级探测，失败只记一次日志）。"""
    ok = not _IMPORT_ERROR and ChatPromptTemplate is not None
    if not ok and _IMPORT_ERROR:
        log.debug("LangChain 桥接不可用：%s", _IMPORT_ERROR)
    return bool(ok)


def version() -> str:
    """返回 langchain-core 版本，供 /api/health 展示；不可用返回空串。"""
    if not available():
        return ""
    try:
        from importlib.metadata import version as _v
        return _v("langchain-core")
    except Exception:  # noqa: BLE001 - 版本探测失败不影响功能
        return ""


# ---- ChatModel：把自研 LLM 包成 LangChain 标准模型 -------------------------
if available():

    class CRMChatModel(BaseChatModel):  # type: ignore[misc,valid-type]
        """把 `crm_agent.llm.LLM` 适配成 BaseChatModel。

        不重新实现网络请求：底层仍调用 `llm.chat()`，因此熔断、重试、指标与
        ``chat() -> None`` 的降级语义全部保留。这样 LCEL 链和手写路径用的是
        同一个模型出口，评估时不会出现"两条路径答案不一致"。
        """

        llm: Any = None
        temperature: float = 0.2
        max_tokens: int | None = None

        @property
        def _llm_type(self) -> str:
            return "crm_agent_adapter"

        def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                      run_manager: Any = None, **kwargs: Any):
            from langchain_core.outputs import ChatGeneration, ChatResult

            payload: list[dict[str, str]] = []
            for msg in messages:
                role = getattr(msg, "type", "")
                if role == "system":
                    payload.append({"role": "system", "content": str(msg.content)})
                elif role in {"ai", "assistant"}:
                    payload.append({"role": "assistant", "content": str(msg.content)})
                else:
                    payload.append({"role": "user", "content": str(msg.content)})
            text = self.llm.chat(payload, temperature=self.temperature,
                                 max_tokens=self.max_tokens) if self.llm else None
            # 模型不可用时返回空消息，让上层 LCEL 链自行决定降级（不伪造答案）
            generation = ChatGeneration(message=AIMessage(content=text or ""))
            return ChatResult(generations=[generation])

else:  # 依赖缺失时的占位类，import 桥接模块不会抛异常

    class CRMChatModel:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("langchain-core 不可用，无法构造 CRMChatModel")


def build_chat_model(llm: Any, *, temperature: float = 0.2,
                     max_tokens: int | None = None) -> Any | None:
    """构造 LCEL 可用的 ChatModel；不可用时返回 None。"""
    if not available():
        return None
    try:
        return CRMChatModel(llm=llm, temperature=temperature, max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001
        log.warning("CRMChatModel 构造失败：%s", type(exc).__name__)
        return None


# ---- Prompt：把消息列表转成 ChatPromptTemplate ----------------------------
def build_prompt(messages: list[dict[str, str]]) -> Any | None:
    """把 `[{role, content}]` 转成等价的 ChatPromptTemplate。

    花括号会被转义（``{`` → ``{{``），避免模板引擎把 SQL/JSON 里的 ``{}``
    当成变量导致格式化失败——这是把既有提示词接到 LCEL 时最常见的坑。
    不可用时返回 None。
    """
    if not available() or not messages:
        return None
    try:
        pairs: list[tuple[str, str]] = []
        for item in messages:
            role = str(item.get("role", "user"))
            name = "system" if role == "system" else ("assistant" if role == "assistant" else "human")
            content = str(item.get("content", "")).replace("{", "{{").replace("}", "}}")
            pairs.append((name, content))
        return ChatPromptTemplate.from_messages(pairs)
    except Exception as exc:  # noqa: BLE001
        log.warning("ChatPromptTemplate 构造失败：%s", type(exc).__name__)
        return None


# ---- Tool：把 ToolRegistry 适配成 LangChain StructuredTool ----------------
def _tool_schema(spec: Any) -> Any | None:
    """把轻量参数 schema 转成 pydantic 模型（供 StructuredTool 做入参校验）。"""
    if not available():
        return None
    fields: dict[str, Any] = {}
    py_types = {
        "str": str, "int": int, "float": float, "bool": bool,
        "list": list, "dict": dict,
    }
    for name, rule in (spec.parameters or {}).items():
        pytype = py_types.get(str(rule.get("type", "any")), Any)
        required = bool(rule.get("required"))
        default = rule.get("default", ...)
        meta: dict[str, Any] = {}
        desc = rule.get("description")
        if desc:
            meta["description"] = str(desc)
        if required:
            fields[name] = (pytype, Field(**meta))
        else:
            meta["default"] = None if default is ... else default
            fields[name] = (pytype, Field(**meta))
    try:
        return create_model(f"{spec.name}_Args", **fields)
    except Exception as exc:  # noqa: BLE001
        log.warning("工具 %s 的 schema 生成失败：%s", spec.name, type(exc).__name__)
        return None


def _make_tool_handler(registry: Any, name: str, ctx: Any) -> Callable[..., Any]:
    """把 registry.call 的结果语义转成 LangChain 工具调用语义。

    成功返回 data；失败抛出带**用户安全文案**的异常（不泄漏 detail/SQL/堆栈）。
    """

    def handler(**kwargs: Any) -> Any:
        result = registry.call(name, kwargs, ctx=ctx)
        if result.ok:
            return result.data
        raise RuntimeError(result.error or f"工具 {name} 调用失败")

    return handler


def build_tools(registry: Any, ctx: Any = None,
                names: Iterable[str] | None = None) -> list[Any]:
    """把统一工具层的工具导出为 LangChain StructuredTool 列表。

    - 未指定 names 时导出全部已启用工具；
    - 每个工具的 schema/校验/重试/回退/指标仍由 ToolRegistry 负责，这里只做协议适配；
    - 依赖缺失或某个工具构造失败时跳过该工具，不影响其余工具与主链。
    """
    if not available() or registry is None:
        return []
    wanted = set(names) if names is not None else None
    tools: list[Any] = []
    for name in registry.names():
        if wanted is not None and name not in wanted:
            continue
        spec = registry.get(name)
        if spec is None or not spec.enabled:
            continue
        schema = _tool_schema(spec)
        if schema is None:
            continue
        try:
            tools.append(StructuredTool.from_function(
                func=_make_tool_handler(registry, name, ctx),
                name=name,
                description=spec.description or name,
                args_schema=schema,
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("工具 %s 导出为 LangChain tool 失败：%s", name, type(exc).__name__)
            continue
    return tools


# ---- 回调：把 LangChain 事件并入既有指标 ----------------------------------
if available():

    class MetricsCallback(BaseCallbackHandler):  # type: ignore[misc]
        """极简回调：统计 chain/llm/tool 的次数与错误，供 /api/health 展示。

        只记次数与异常类型，不记录 prompt、输入参数或输出正文（隐私与脱敏）。
        """

        def __init__(self) -> None:
            self.chains = 0
            self.llms = 0
            self.tools = 0
            self.errors = 0

        def on_chain_start(self, *args: Any, **kwargs: Any) -> None:
            self.chains += 1

        def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
            self.llms += 1

        def on_tool_start(self, *args: Any, **kwargs: Any) -> None:
            self.tools += 1

        def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
            self.errors += 1

        def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
            self.errors += 1

        def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
            self.errors += 1

        def metrics(self) -> dict[str, Any]:
            return {
                "chains": self.chains, "llms": self.llms,
                "tools": self.tools, "errors": self.errors,
            }

else:

    class MetricsCallback:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("langchain-core 不可用，无法构造 MetricsCallback")

        def metrics(self) -> dict[str, Any]:
            return {}


def build_chain(model: Any, prompt: Any, *, parser: Any = None) -> Any | None:
    """组装 ``prompt | model | parser`` LCEL 链；不可用时返回 None。

    ``parser`` 默认是 StrOutputParser；可观测回调在 ``invoke()`` 时通过 config 注入，
    避免在构造期把回调固化进链。
    """
    if not available() or model is None or prompt is None:
        return None
    try:
        return prompt | model | (parser or StrOutputParser())
    except Exception as exc:  # noqa: BLE001
        log.warning("LCEL 链构造失败：%s", type(exc).__name__)
        return None


def message_dict_to_lc(messages: list[dict[str, str]]) -> list[Any]:
    """把 `[{role, content}]` 转成 LangChain BaseMessage 列表，供直接 invoke 模型。"""
    if not available():
        return []
    result: list[Any] = []
    for m in messages or []:
        role = m.get("role", "user")
        content = str(m.get("content", ""))
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        else:
            result.append(HumanMessage(content=content))
    return result


def lc_message_to_text(message: Any) -> str:
    """把 LangChain 消息/字符串统一取文本，方便上层塞回 state.answer。"""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    # 多模态 content 可能是 [{"type": "text", "text": "..."}]
    if isinstance(content, (list, tuple)):
        return "".join(str(part.get("text", "")) for part in content
                       if isinstance(part, dict))
    return str(content)


# ---- Facade：给 Context / API 一个统一入口 --------------------------------
class LangChainBridge:
    """把上面各组件装配成一个可选门面。

    生命周期跟随 Context：构建一次，模型/工具/回调复用，不随每轮请求重建。
    默认应在 ``settings.langchain_enabled=False`` 时**不构建**，从而对主链零影响。

    典型用法（外部 Agent / LCEL 编排）：:

        bridge = LangChainBridge(llm, registry, ctx)
        chain = bridge.chain(messages)          # prompt | model | parser
        answer = bridge.invoke(messages)         # 一行拿到纯文本
        tools = bridge.tools                     # 交给 LangChain Agent 调用
    """

    def __init__(self, llm: Any, registry: Any = None, ctx: Any = None, *,
                 temperature: float = 0.2, max_tokens: int | None = None,
                 bind_tools: bool = True) -> None:
        self._available = available()
        self._callback = MetricsCallback() if self._available else None
        self._model = build_chat_model(llm, temperature=temperature,
                                       max_tokens=max_tokens) if self._available else None
        self._tools = (build_tools(registry, ctx) if bind_tools and self._available else [])
        if self._available:
            log.info("LangChain 桥接已启用：core=%s tools=%d", version(), len(self._tools))

    @property
    def available(self) -> bool:
        return bool(self._available and self._model is not None)

    @property
    def tools(self) -> list[Any]:
        return list(self._tools)

    def tool_names(self) -> list[str]:
        return [getattr(t, "name", "") for t in self._tools]

    def prompt(self, messages: list[dict[str, str]]) -> Any | None:
        return build_prompt(messages)

    def chain(self, messages: list[dict[str, str]]) -> Any | None:
        prompt = self.prompt(messages)
        return build_chain(self._model, prompt)

    def invoke(self, messages: list[dict[str, str]]) -> str:
        """走 LCEL 链跑一次并返回纯文本；不可用或异常时返回空串（上层自行降级）。"""
        chain = self.chain(messages)
        if chain is None:
            return ""
        try:
            out = chain.invoke({}, config={"callbacks": [self._callback]}
                               if self._callback else None)
            return lc_message_to_text(out)
        except Exception as exc:  # noqa: BLE001
            log.warning("LangChain 链调用失败，已降级为空结果：%s", type(exc).__name__)
            return ""

    def metrics(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "core_version": version(),
            "tools": len(self._tools),
            "tool_names": self.tool_names(),
            "callbacks": self._callback.metrics() if self._callback else {},
        }
