export interface ExpressionEvaluationContext {
  t: number;
}

export interface ExpressionEvaluationResult {
  value: number | null;
  error?: string;
}

const ALLOWED_EXPRESSION = /^[0-9+\-*/().t]*$/;

function normalizeExpression(expression: string): { normalized: string; error?: string } {
  const trimmed = expression.trim().toLowerCase();

  if (!trimmed) {
    return { normalized: "", error: "Enter a value" };
  }

  let normalized = trimmed
    .replace(/(\d+(?:\.\d+)?)\s*(t|\()/g, "$1*$2")
    .replace(/(t|\))\s*(\d+(?:\.\d+)?|\()/g, "$1*$2")
    .replace(/\^/g, "**")
    .replace(/\s+/g, "");

  if (!ALLOWED_EXPRESSION.test(normalized)) {
    return {
      normalized,
      error: "Use digits, t, parentheses, and + - * / ^ operators only",
    };
  }

  return { normalized };
}

export function evaluateExpression(
  expression: string,
  context: ExpressionEvaluationContext
): ExpressionEvaluationResult {
  const { normalized, error } = normalizeExpression(expression);

  if (error) {
    return { value: null, error };
  }

  const targetExpression = normalized || "0";

  try {
    // eslint-disable-next-line no-new-func
    const evaluator = new Function("t", `return ${targetExpression};`) as (t: number) => unknown;
    const result = evaluator(context.t);
    const value = Number(result);

    if (!Number.isFinite(value)) {
      return { value: null, error: "Expression must produce a finite number" };
    }

    return { value };
  } catch {
    return { value: null, error: "Invalid expression" };
  }
}
