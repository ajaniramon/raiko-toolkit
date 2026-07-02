"""Benchmark runner: executes a task against a model and scores it.

- READ-ONLY tool set (write_file is excluded).
- Non-streaming requests (more robust for measuring): exact usage, per-call
  timeout, handling of malformed JSON / no tool / timeouts without hanging the suite.
- Captures EVERYTHING: reasoning (reasoning_content and/or <think>) + completions +
  tool calls + results, to log it all.
- Thinking on/off mode via chat_template_kwargs.enable_thinking.
"""

import json
import os
import re
import sys
import time

# allow importing tools.py from the parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import TOOLS, DISPATCH, call_tool  # noqa: E402

# read-only tools: all except write_file
READONLY_TOOLS = [t for t in TOOLS if t["function"]["name"] != "write_file"]
READONLY_NAMES = {t["function"]["name"] for t in READONLY_TOOLS}

SYSTEM_PROMPT = (
    "You are a tool-using agent operating inside a sandbox project directory. "
    "Use the available read-only tools to inspect files and directories, then "
    "answer the user's question. Always call a tool to gather evidence before "
    "answering; never guess. When you have the answer, reply with a short, direct "
    "final message stating it. If something does not exist, say so plainly instead "
    "of inventing an answer."
)

THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# DeepSeek (vía algunos providers) a veces emite los tool calls como TEXTO con markup
# DSML en vez de por la API de tools. Sin fallback, ese turno se interpretaba como
# respuesta final y el run moría — fallo de infra contado como fallo del modelo.
_DSML_INVOKE_RE = re.compile(
    r"<｜DSML｜invoke\s+name=\"([^\"]+)\">(.*?)</｜DSML｜invoke>", re.DOTALL)
_DSML_PARAM_RE = re.compile(
    r"<｜DSML｜parameter\s+name=\"([^\"]+)\"[^>]*>(.*?)</｜DSML｜parameter>", re.DOTALL)
_DSML_ANY_RE = re.compile(r"<｜DSML｜[^>]*>")


def _parse_dsml_calls(text):
    """Extrae [(tool_name, args_dict)] del markup DSML embebido en texto. [] si no hay."""
    calls = []
    for name, body in _DSML_INVOKE_RE.findall(text or ""):
        args = {}
        for pname, pval in _DSML_PARAM_RE.findall(body):
            pval = pval.strip()
            try:
                args[pname] = json.loads(pval)   # números / bools / json embebido
            except (json.JSONDecodeError, ValueError):
                args[pname] = pval               # string plano
        calls.append((name, args))
    return calls

MAX_ITERATIONS = 6
PER_CALL_TIMEOUT = 150          # s per request to the model
TOOL_RESULT_CAP = 6000          # chars of tool result we return to the model


def _split_think(content: str):
    """Separates inline <think>..</think> from the rest. Returns (clean, think_text)."""
    if not content:
        return content or "", ""
    thinks = THINK_RE.findall(content)
    clean = THINK_RE.sub("", content).strip()
    return clean, "\n".join(thinks).strip()


def _extract_reasoning(msg) -> str:
    r = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    if not r and getattr(msg, "model_extra", None):
        r = msg.model_extra.get("reasoning_content") or msg.model_extra.get("reasoning")
    return r or ""


def run_task(client, model_name, task, root, enable_thinking, tools=None,
             grader_root=False, system_prompt=None, max_iterations=None,
             dispatch=None, grader_ctx=None):
    """Executes a task. Returns a dict with result, metrics and the full transcript.

    tools: list of tools to expose (defaults to READONLY_TOOLS).
    grader_root: if True, the grader is called as check(answer, root) (for tasks
    that write/execute and the filesystem needs to be verified).
    system_prompt: override of the system prompt."""
    tools = tools if tools is not None else READONLY_TOOLS
    messages = [
        {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        {"role": "user", "content": task["prompt"]},
    ]
    transcript = []           # all turns (reasoning + content + tools)
    tool_calls_made = []      # names of tools called (in order)
    malformed_json = 0
    answer = ""
    status = "ok"             # ok | no_answer | max_iter | error
    error_msg = ""
    t0 = time.time()
    prompt_tokens = completion_tokens = 0

    extra_body = {"chat_template_kwargs": {"enable_thinking": bool(enable_thinking)}}
    max_iter = max_iterations or MAX_ITERATIONS

    dsml_recovered = 0
    for iteration in range(max_iter):
        resp = None
        for attempt in (1, 2):   # 1 retry: un glitch puntual del provider no es fallo del modelo
            try:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0,
                    seed=42,
                    timeout=PER_CALL_TIMEOUT,
                    extra_body=extra_body,
                )
                break
            except Exception as e:  # timeout, APIError, 400 from kwargs, etc.
                error_msg = f"{type(e).__name__}: {e}"
                if attempt == 2:
                    status = "error"
                    transcript.append({"turn": iteration, "error": error_msg})
                else:
                    time.sleep(2)
        if resp is None:
            break

        if getattr(resp, "usage", None):
            prompt_tokens = resp.usage.prompt_tokens or prompt_tokens
            completion_tokens += resp.usage.completion_tokens or 0

        msg = resp.choices[0].message
        reasoning = _extract_reasoning(msg)
        raw_content = msg.content or ""
        clean_content, inline_think = _split_think(raw_content)
        if inline_think:
            reasoning = (reasoning + "\n" + inline_think).strip()

        tc = msg.tool_calls or []
        turn_log = {
            "turn": iteration,
            "reasoning": reasoning,
            "content": clean_content,
            "tool_calls": [],
        }

        # reconstruct the assistant message for the history
        assistant_msg = {"role": "assistant", "content": clean_content or None}
        if tc:
            assistant_msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in tc
            ]
        messages.append(assistant_msg)

        if not tc:
            dsml_calls = _parse_dsml_calls(raw_content)
            if dsml_calls:
                # tool calls emitidos como texto (glitch del provider): ejecutarlos y
                # devolver los resultados en un mensaje de usuario para seguir el bucle
                dsml_recovered += len(dsml_calls)
                parts = []
                for name, args in dsml_calls:
                    tool_calls_made.append(name)
                    result = call_tool(name, json.dumps(args), dispatch=dispatch)
                    if len(result) > TOOL_RESULT_CAP:
                        result = result[:TOOL_RESULT_CAP] + "\n... (truncated)"
                    turn_log["tool_calls"].append({
                        "name": name, "arguments": json.dumps(args),
                        "malformed_json": False, "result": result, "dsml_fallback": True,
                    })
                    parts.append(f"[{name}] {result}")
                messages.append({"role": "user",
                                 "content": "Tool results:\n" + "\n".join(parts)})
                transcript.append(turn_log)
                continue
            answer = _DSML_ANY_RE.sub("", clean_content).strip()
            status = "ok" if answer.strip() else "no_answer"
            transcript.append(turn_log)
            break

        # execute each tool call
        for c in tc:
            name = c.function.name
            raw_args = c.function.arguments or "{}"
            tool_calls_made.append(name)
            try:
                json.loads(raw_args)
                bad_json = False
            except (json.JSONDecodeError, TypeError):
                bad_json = True
                malformed_json += 1

            result = call_tool(name, raw_args, dispatch=dispatch)
            if len(result) > TOOL_RESULT_CAP:
                result = result[:TOOL_RESULT_CAP] + f"\n... (truncated, {len(result) - TOOL_RESULT_CAP} chars more)"

            turn_log["tool_calls"].append({
                "name": name, "arguments": raw_args,
                "malformed_json": bad_json, "result": result,
            })
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})

        transcript.append(turn_log)
    else:
        status = "max_iter"
        # last attempt at an answer: ask for a summary without tools
        answer = ""

    latency = time.time() - t0
    iterations_used = len([t for t in transcript if "turn" in t])

    # ---- grading ----
    if grader_ctx is not None:
        try:
            # los graders (p. ej. negativas anti-sycophancy) pueden exigir que hubo
            # lecturas de verificación reales, no solo mirar el texto de la respuesta
            grader_ctx.tool_calls = list(tool_calls_made)
        except AttributeError:
            pass  # ctx sin __dict__ (dict, namedtuple...): el grader no lo usa
    try:
        if grader_ctx is not None:
            correct = bool(task["check"](answer or "", grader_ctx))
        elif grader_root:
            correct = bool(task["check"](answer or "", root))
        else:
            correct = bool(task["check"](answer or ""))
    except Exception:
        correct = False
    # Sin respuesta final no hay task completada: un run que muere en max_iter con el
    # side-effect ya aterrizado NO es un éxito (antes puntuaba 1.00 con "(no answer)").
    if not (answer or "").strip():
        correct = False
    used_tool = len(tool_calls_made) > 0
    tool_ok = any(n in task["expect_tools"] for n in tool_calls_made)
    hallucinated = bool(task.get("negative")) and used_tool is not None and not correct and bool((answer or "").strip())

    # efficiency: penalizes iterations BEYOND what the task legitimately needs. A task can
    # declare `iter_budget` (e.g. a 5-hop chain sets 5-6); default 2 keeps the old behaviour
    # for the floor tiers. This stops multi-hop tasks being penalised for their inherent depth
    # while still punishing flailing (looping past the budget).
    free = max(1, int(task.get("iter_budget", 2)))
    eff = 1.0 if iterations_used <= free else max(0.0, 1.0 - (iterations_used - free) * 0.2)
    if status == "max_iter":
        # agotar el presupuesto sin concluir es flailing por definición; antes las tasks
        # con iter_budget == MAX_ITERATIONS regalaban eff 1.0 justo a los runs colgados
        eff = 0.0
    per_task_score = (0.70 * (1.0 if correct else 0.0)
                      + 0.15 * (1.0 if tool_ok else 0.0)
                      + 0.15 * eff)

    return {
        "id": task["id"],
        "category": task["category"],
        "difficulty": task.get("difficulty"),
        "negative": bool(task.get("negative")),
        "prompt": task["prompt"],
        "answer": answer,
        "correct": correct,
        "used_tool": used_tool,
        "tool_ok": tool_ok,
        "tools_called": tool_calls_made,
        "expect_tools": task["expect_tools"],
        "iterations": iterations_used,
        "latency_s": round(latency, 2),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "malformed_json": malformed_json,
        "dsml_recovered": dsml_recovered,
        "hallucinated": hallucinated,
        "status": status,
        "error": error_msg,
        "efficiency": round(eff, 3),
        "score": round(per_task_score, 4),
        "transcript": transcript,
    }


def aggregate(results: list) -> dict:
    """Metrics and final 0-100 score for a set of results from a model/mode."""
    n = len(results)
    if n == 0:
        return {}
    correct = sum(r["correct"] for r in results)
    tool_ok = sum(r["tool_ok"] for r in results)
    no_tool = sum(0 if r["used_tool"] else 1 for r in results)
    malformed = sum(r["malformed_json"] for r in results)
    timeouts = sum(1 for r in results if r["status"] == "error")
    max_iter = sum(1 for r in results if r["status"] == "max_iter")
    neg = [r for r in results if r["negative"]]
    neg_ok = sum(r["correct"] for r in neg)
    hallucinations = sum(r["hallucinated"] for r in results)

    correctness_rate = correct / n
    tool_accuracy = tool_ok / n
    efficiency = sum(r["efficiency"] for r in results) / n

    # final score: correctness 70%, tool 15%, efficiency 15%, minus penalties.
    # max_iter ya NO se penaliza aparte: el per-task lo cuenta de forma honesta
    # (correct=False sin respuesta final + eff=0) y sumarle otra multa lo triplicaba.
    base = 100 * (0.70 * correctness_rate + 0.15 * tool_accuracy + 0.15 * efficiency)
    penalty = min(10, malformed) + min(10, timeouts * 2)
    final_score = max(0.0, round(base - penalty, 1))

    # breakdown by category (% accuracy)
    cats = {}
    for r in results:
        cats.setdefault(r["category"], []).append(r["correct"])
    by_category = {c: round(100 * sum(v) / len(v)) for c, v in sorted(cats.items())}

    diffs = {}
    for r in results:
        d = r.get("difficulty")
        if d:
            diffs.setdefault(d, []).append(r["correct"])
    by_difficulty = {d: round(100 * sum(v) / len(v)) for d, v in sorted(diffs.items())}

    return {
        "n_tasks": n,
        "final_score": final_score,
        "correctness_pct": round(100 * correctness_rate, 1),
        "tool_accuracy_pct": round(100 * tool_accuracy, 1),
        "efficiency_pct": round(100 * efficiency, 1),
        "negatives_ok": f"{neg_ok}/{len(neg)}",
        "hallucinations": hallucinations,
        "no_tool_calls": no_tool,
        "malformed_json": malformed,
        "errors_timeouts": timeouts,
        "max_iter": max_iter,
        "avg_iterations": round(sum(r["iterations"] for r in results) / n, 2),
        "avg_latency_s": round(sum(r["latency_s"] for r in results) / n, 2),
        "total_completion_tokens": sum(r["completion_tokens"] for r in results),
        "by_category": by_category,
        "by_difficulty": by_difficulty,
    }
