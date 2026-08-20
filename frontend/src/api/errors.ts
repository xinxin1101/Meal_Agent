export class ApiError extends Error {
  constructor(public readonly status: number, message: string, public readonly detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

export class ConflictError extends ApiError {
  constructor(detail?: unknown) {
    super(409, "数据已被其他操作更新，请刷新后重试。", detail);
    this.name = "ConflictError";
  }
}

export class ValidationError extends ApiError {
  constructor(detail?: unknown) {
    super(422, "提交内容未通过接口校验。", detail);
    this.name = "ValidationError";
  }
}

const validationMessages: Record<string, string> = {
  SILICONFLOW_NOT_CONFIGURED: "硅基流动模型尚未配置，请检查服务端环境变量。",
  LLM_PROVIDER_FAILED: "模型服务调用失败，请稍后重试。",
  LLM_EMPTY_RESPONSE: "模型没有返回结构化内容，请重试。",
  LLM_OUTPUT_SCHEMA_INVALID: "模型返回的 JSON 不符合完整菜谱合同，自动修复后仍未通过 Pydantic 校验。",
  LLM_RESPONSE_INCOMPLETE: "模型没有填写完整的标题、份数、餐次、时间、食材或步骤。",
  LLM_LEGACY_PATCH_NOT_ALLOWED: "模型返回了旧版局部补丁，系统要求完整菜谱 JSON。",
  LLM_INGREDIENT_COVERAGE_MISMATCH: "模型返回的食材数量或顺序与来源数据不一致。",
  LLM_INGREDIENT_SOURCE_BINDING_INVALID: "模型修改了来源食材名称，已被来源绑定校验拒绝。",
  LLM_STEP_COVERAGE_MISMATCH: "模型返回的制作步骤数量或顺序与来源数据不一致。",
  LLM_CANONICAL_ID_NOT_ALLOWED: "模型使用了不在本地可信食材目录中的标准食材 ID。",
};

function detailCode(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "detail" in detail) {
    const value = (detail as { detail?: unknown }).detail;
    return typeof value === "string" ? value : undefined;
  }
  return undefined;
}

export function toUserMessage(error: unknown): string {
  if (error instanceof ValidationError) {
    const code = detailCode(error.detail);
    return code ? validationMessages[code] ?? `${error.message}（${code}）` : error.message;
  }
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && error.name === "AbortError") return "请求超时，请稍后重试。";
  if (error instanceof Error && error.message) return error.message;
  return "无法连接到服务，请确认后端已经启动。";
}
