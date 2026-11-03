"""MCP server: your training (Hevy) and eating (Fitatu) for Claude and other agents, read-only.

Serves Hevy training tools, the Fitatu diary's read tools (from fitatu-api) and tools that put
the two side by side by day. Run it as ``hevy-fitatu-mcp`` (stdio). Needs the ``mcp`` extra.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

try:
    import anyio
    from mcp import types
    from mcp.server import Server, ServerRequestContext
    from mcp.server.stdio import stdio_server
    from mcp.shared.exceptions import MCPError
except ImportError as exc:  # pragma: no cover
    raise ImportError('the MCP server needs the mcp extra: pip install "hevy-fitatu-sync[mcp]"') from exc

from fitatu_api.agent import READ_ONLY_TOOLS
from fitatu_api.agent import TOOLS as FITATU_TOOL_SPECS
from fitatu_api.exceptions import describe_error

from . import __version__
from .combined import (
    MAX_RANGE_DAYS,
    FitatuReadError,
    RangeError,
    ToolCaller,
    analyze,
    check_range,
    daily_log,
    public_log,
)
from .hevy import HevyClient, HevyError
from .training import exercise_progress, summarize_workout, training_summary

logger = logging.getLogger(__name__)

# The Fitatu tools served as they are: reading the diary, goals, weight and the food catalog.
FITATU_TOOLS = (
    "get_today", "get_day", "get_targets", "get_profile", "get_progress", "get_report", "get_range_summary",
    "get_top_foods", "get_weight_history", "get_body_measurements", "get_measurement_history", "get_water",
    "search_food", "get_food_measures", "search_recipes", "get_recipe",
)
assert set(FITATU_TOOLS) <= set(READ_ONLY_TOOLS), set(FITATU_TOOLS) - set(READ_ONLY_TOOLS)

INSTRUCTIONS = """\
The user's strength training (Hevy) and food diary (Fitatu), to analyze together. Read-only: \
nothing here changes either app.
- Start with analyze_training_and_nutrition for "how is it going" questions: training load, intake \
on training vs rest days, protein per kg, and energy balance against the weight trend with an \
estimated maintenance intake. get_daily_log shows the same days one by one.
- Training: analyze_training (weekly load, sets per muscle group, gaps), get_workouts (every set), \
analyze_exercise_progress (e1RM trend and PRs for one lift, by name).
- Food: the Fitatu tools (get_day, get_progress, get_report, get_top_foods, get_targets, get_profile, \
get_weight_history, ...). Fitatu's tool descriptions and names are in Polish/English as the app has them.
- Dates are YYYY-MM-DD in the user's time zone; get_today gives today. Ranges default to recent weeks.
- Be honest about gaps: a day with no food logged is unknown, not zero, and averages cover logged \
days only; say how many days the data covers. Estimates (e1RM, maintenance kcal) are estimates.
- Weights are kg, volume is kg x reps of working sets (warm-ups excluded); a set counts 1 for its \
primary muscle and 0.5 for each secondary one.
"""

MAX_PROGRESS_DAYS = 5 * 366  # one lift over years is still a small answer

_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}


def _range_props(default_days: int) -> dict[str, Any]:
    return {
        "from_date": {**_DATE, "description": f"First day (YYYY-MM-DD); default {default_days} days before to_date."},
        "to_date": {**_DATE, "description": "Last day, included (YYYY-MM-DD); default today."},
    }


def _tool(name: str, title: str, description: str, properties: dict[str, Any], required: Sequence[str] = ()) -> dict:
    return {
        "name": name,
        "title": title,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": list(required),
                         "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                        "openWorldHint": True},
    }


OWN_TOOLS: list[dict[str, Any]] = [
    _tool(
        "analyze_training_and_nutrition", "Analyze training and nutrition",
        "Training and eating together over a range (default the last 28 days): training load per week and "
        "sets per muscle group; average kcal, protein, carbs and fat on training days vs rest days; protein "
        "g/kg of body weight; the weight trend (kg/week) and, with 14+ logged days and weigh-ins, an "
        "estimated maintenance intake and daily surplus/deficit. notes say what the data does not cover.",
        _range_props(27),
    ),
    _tool(
        "get_daily_log", "Daily log: workouts, food, weight",
        "Day by day (default the last 14 days, at most 366): the workouts (title, minutes, working sets, "
        "volume, sets per muscle), what was logged in Fitatu (kcal, protein, fat, carbs, the kcal and "
        "protein goals; null when nothing is logged) and the weight (Fitatu's weigh-in, else Hevy's).",
        _range_props(13),
    ),
    _tool(
        "get_workouts", "Workouts with sets",
        "Hevy workouts in a range (default the last 14 days), oldest first: day, title, minutes, working "
        "sets, reps, volume, sets per muscle group and per exercise its working sets as text "
        "('80kg×5@8') and best e1RM.",
        {**_range_props(13), "with_sets": {"type": "boolean", "description": "Include the sets (default true)."}},
    ),
    _tool(
        "analyze_training", "Analyze training",
        "Training load over a range (default the last 12 weeks): workouts, minutes, working sets and volume "
        "per week; sets per muscle group per week; the longest gap; each week's totals; the most trained "
        "exercises; and the list of workouts.",
        _range_props(83),
    ),
    _tool(
        "analyze_exercise_progress", "Exercise progress",
        "Progress of one exercise found by name ('bench', 'squat (barbell)'), default over the last year: "
        "per session the top weight, best set, e1RM (Epley) and volume, PR flags, and the e1RM change "
        "(kg, %, per 4 weeks). Ranges up to 5 years.",
        {"exercise": {"type": "string", "description": "Exercise name or part of it, as in Hevy."},
         **_range_props(364)},
        required=("exercise",),
    ),
]
_FITATU_SPECS = {t["name"]: t for t in FITATU_TOOL_SPECS}
TOOLS: list[dict[str, Any]] = [*OWN_TOOLS, *(_FITATU_SPECS[name] for name in FITATU_TOOLS)]
TOOL_NAMES = [t["name"] for t in TOOLS]

PROMPTS: dict[str, dict[str, Any]] = {
    "training_nutrition_review": {
        "title": "Training & nutrition review",
        "description": "Review the last weeks of training and eating, with concrete recommendations.",
        "arguments": [{"name": "weeks", "description": "How many weeks back (default 4)."}],
        "text": "Review my last {weeks} weeks of training and nutrition. Call get_today, then "
        "analyze_training_and_nutrition for that range and analyze_training for it. Cover: training "
        "consistency and weekly sets per muscle group (flag muscles under ~10 sets/week), whether I eat "
        "enough protein (aim ~1.6-2.2 g/kg) and enough on training days, whether my intake matches my "
        "weight trend and goal, and the data gaps. End with 3 concrete recommendations.",
        "default": {"weeks": "4"},
    },
    "lift_progress": {
        "title": "Lift progress vs eating",
        "description": "How one lift progressed and whether eating explains stalls.",
        "arguments": [{"name": "exercise", "description": "The lift, e.g. 'bench press'.", "required": True}],
        "text": "How has my {exercise} progressed? Call analyze_exercise_progress for it, then "
        "get_daily_log or analyze_training_and_nutrition around any stall or drop to see whether intake, "
        "protein or body weight changed then. Keep it short and concrete.",
        "default": {"exercise": "my main lift"},
    },
}


# -- the tool calls -----------------------------------------------------------------------


class ToolError(Exception):
    def __init__(self, error_type: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.error = {"error_type": error_type, "message": message, "retryable": False, "hint": hint}


def _timezone() -> ZoneInfo:
    name = os.environ.get("HEVY_FITATU_TIMEZONE") or os.environ.get("HEVY_TIMEZONE")
    if not name:
        name = getattr(datetime.now().astimezone().tzinfo, "key", None) or "UTC"
    return ZoneInfo(name)


def _date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolError("validation", f"{name} must be a date as YYYY-MM-DD, got {value!r}") from None


class Backend:
    """Opens Hevy and Fitatu on first use and answers the tools."""

    def __init__(
        self,
        open_hevy: Callable[[], HevyClient | None],
        open_fitatu: Callable[[], ToolCaller],
        tz: ZoneInfo | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._open_hevy, self._open_fitatu = open_hevy, open_fitatu
        self._hevy: HevyClient | None = None
        self._fitatu: ToolCaller | None = None
        self.tz = tz or _timezone()
        self._today = today or (lambda: datetime.now(self.tz).date())

    def hevy(self) -> HevyClient:
        if self._hevy is None:
            client = self._open_hevy()
            if client is None:
                raise ToolError("auth", "Hevy is not connected: HEVY_API_KEY is not set",
                                "A Hevy Pro API key is in the Hevy app under Settings → Developer.")
            self._hevy = client
        return self._hevy

    def fitatu(self) -> ToolCaller:
        if self._fitatu is None:
            self._fitatu = self._open_fitatu()
        return self._fitatu

    def _optional(self, side: Callable[[], Any], why: list[str]) -> Any:
        """The app's client, or None with the reason added to ``why``: the combined tools go on
        with the other app."""
        try:
            return side()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not open %s: %s", side.__name__, exc)
            why.append(f"{side.__name__}: {exc}")
            return None

    def _log(self, start: date, end: date) -> dict[str, Any]:
        why: list[str] = []
        log = daily_log(self._optional(self.hevy, why), self._optional(self.fitatu, why), start, end, self.tz)
        log["warnings"] = [*log["warnings"], *why]
        return log

    def _range(self, args: dict[str, Any], default_days: int, max_days: int = MAX_RANGE_DAYS) -> tuple[date, date]:
        end = _date(args["to_date"], "to_date") if args.get("to_date") else self._today()
        start = _date(args["from_date"], "from_date") if args.get("from_date") else end - timedelta(days=default_days)
        try:
            check_range(start, end, max_days)
        except RangeError as exc:
            raise ToolError("validation", str(exc)) from None
        return start, end

    def _raws(self, start: date, end: date) -> list[dict[str, Any]]:
        since = datetime.combine(start, datetime.min.time(), self.tz)
        until = datetime.combine(end + timedelta(days=1), datetime.min.time(), self.tz)
        return self.hevy().raw_workouts_between(since, until)

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in FITATU_TOOLS:
            return self.fitatu().call(name, args)
        if name == "get_daily_log":
            start, end = self._range(args, 13)
            return {"ok": True, "data": public_log(self._log(start, end))}
        if name == "analyze_training_and_nutrition":
            start, end = self._range(args, 27)
            return {"ok": True, "data": analyze(self._log(start, end), self.tz, start, end)}
        if name == "get_workouts":
            start, end = self._range(args, 13)
            raws = self._raws(start, end)
            templates = self.hevy().exercise_templates() if raws else {}
            with_sets = args.get("with_sets", True) is not False
            workouts = [summarize_workout(r, templates, self.tz, with_sets=with_sets)
                        for r in sorted(raws, key=lambda r: r["start_time"])]
            return {"ok": True, "data": {"from_date": start.isoformat(), "to_date": end.isoformat(),
                                         "count": len(workouts), "workouts": workouts}}
        if name == "analyze_training":
            start, end = self._range(args, 83)
            raws = self._raws(start, end)
            templates = self.hevy().exercise_templates() if raws else {}
            return {"ok": True, "data": training_summary(raws, templates, self.tz, start, end)}
        if name == "analyze_exercise_progress":
            query = str(args.get("exercise") or "").strip()
            if not query:
                raise ToolError("validation", "exercise is required, e.g. 'bench press'")
            start, end = self._range(args, 364, max_days=MAX_PROGRESS_DAYS)
            return {"ok": True, "data": {"from_date": start.isoformat(), "to_date": end.isoformat(),
                                         **exercise_progress(self._raws(start, end), query, self.tz)}}
        raise ToolError("validation", f"{name!r} is not a tool of this server; tools: {', '.join(TOOL_NAMES)}")

    def safe_call(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        """The call's ``{"ok", "data"}``, or ``{"ok": false, "error"}`` for any failure."""
        try:
            return self.call(name, dict(args or {}))
        except ToolError as exc:
            return {"ok": False, "error": exc.error}
        except HevyError as exc:
            kind = "auth" if exc.status in (401, 403) else "rate_limit" if exc.status == 429 else "server"
            return {"ok": False, "error": {"error_type": kind, "message": str(exc), "retryable": kind != "auth"}}
        except FitatuReadError as exc:
            return {"ok": False, "error": exc.error or {"error_type": "unexpected", "message": str(exc)}}
        except Exception as exc:  # e.g. no Fitatu session: the model tells the user
            logger.exception("tool %s failed", name)
            return {"ok": False, "error": describe_error(exc)}


def default_backend() -> Backend:
    from .cli import fitatu_client, hevy_client

    def open_hevy() -> HevyClient | None:
        return hevy_client() if os.environ.get("HEVY_API_KEY") else None

    def open_fitatu() -> ToolCaller:
        from fitatu_api.agent import FitatuTools

        return FitatuTools(fitatu_client(), tools=FITATU_TOOLS)

    return Backend(open_hevy, open_fitatu)


def build_server(backend: Backend) -> Server[Any]:
    listed = [
        types.Tool.model_validate({**tool, "annotations": {"title": tool["title"], **tool["annotations"]}})
        for tool in TOOLS
    ]
    one_at_a_time = anyio.CapacityLimiter(1)  # the clients are not meant for parallel calls

    async def list_tools(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None):
        return types.ListToolsResult(tools=listed)

    async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams):
        result = await anyio.to_thread.run_sync(backend.safe_call, params.name, params.arguments,
                                                limiter=one_at_a_time)
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(result, ensure_ascii=False, default=str))],
            structured_content=result,
            is_error=not result.get("ok"),
        )

    async def list_prompts(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None):
        return types.ListPromptsResult(prompts=[
            types.Prompt(name=name, title=p["title"], description=p["description"], arguments=p["arguments"])
            for name, p in PROMPTS.items()
        ])

    async def get_prompt(ctx: ServerRequestContext[Any], params: types.GetPromptRequestParams):
        prompt = PROMPTS.get(params.name)
        if prompt is None:
            raise MCPError(code=types.INVALID_PARAMS, message=f"unknown prompt {params.name!r}")
        given = {k: v.strip() for k, v in (params.arguments or {}).items() if v and v.strip()}
        return types.GetPromptResult(
            description=prompt["description"],
            messages=[types.PromptMessage(role="user", content=types.TextContent(
                text=prompt["text"].format(**{**prompt["default"], **given})))],
        )

    return Server(
        "hevy-fitatu",
        version=__version__,
        title="Hevy + Fitatu",
        description="Your training (Hevy) and food diary (Fitatu) side by side, for analysis. Read-only.",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hevy-fitatu-mcp",
        description="Serve Hevy training and Fitatu diary tools to an MCP host over stdio (read-only). "
        "Needs HEVY_API_KEY, and a Fitatu session (`fitatu-api login`, or FITATU_EMAIL and FITATU_PASSWORD).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--log-level", default=os.environ.get("HEVY_FITATU_LOG_LEVEL", "WARNING"),
                        type=str.upper, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, stream=sys.stderr)  # stdout carries only the protocol
    server = build_server(default_backend())

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
