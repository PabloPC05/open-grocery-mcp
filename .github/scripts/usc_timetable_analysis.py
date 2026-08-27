from __future__ import annotations

import asyncio
import json
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from mcp_usc.server import mcp

SUBJECTS: dict[str, dict[str, Any]] = {
    "G4012223": {"name": "Sistemas Operativos I", "degree": "informatics", "course": 2},
    "G4012227": {"name": "Sistemas Operativos II", "degree": "informatics", "course": 2},
    "G4012322": {
        "name": "Administración de Sistemas y Redes",
        "degree": "informatics",
        "course": 3,
    },
    "G4012224": {"name": "Redes", "degree": "informatics", "course": 2},
    "G4012328": {"name": "Inteligencia Artificial", "degree": "informatics", "course": 3},
    "G4012455": {"name": "Aprendizaje Automático", "degree": "informatics", "course": 4},
    "G4012326": {"name": "Computación Distribuida", "degree": "informatics", "course": 3},
    "G4012329": {
        "name": "Seguridad de la Información",
        "degree": "informatics",
        "course": 4,
    },
    "G4012421": {
        "name": "Interacción Persona-Ordenador",
        "degree": "informatics",
        "course": 4,
    },
    "G1011449": {"name": "Ecuaciones Diferenciales", "degree": "math", "course": 4},
    "G1011442": {"name": "Variable Compleja", "degree": "math", "course": 3},
    "G1011132": {"name": "Ecuaciones Algebraicas", "degree": "math", "course": 3},
    "G1012226": {"name": "Geometría Lineal", "degree": "math", "course": 2},
}

PREFERRED_YEARS = ("2026/2027", "2025/2026")


def fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    print(f"CALL {name} {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
    return await mcp._tool_manager.call_tool(name, arguments)  # noqa: SLF001


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk(nested)


def collect_weeks(value: Any) -> list[dict[str, Any]]:
    weeks: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in walk(value):
        if not {"start_date", "end_date", "endpoint_url"}.issubset(item):
            continue
        key = (str(item["start_date"]), str(item["end_date"]), str(item["endpoint_url"]))
        weeks[key] = dict(item)
    return sorted(weeks.values(), key=lambda item: str(item["start_date"]))


def collect_sessions(value: Any) -> list[dict[str, Any]]:
    sessions: dict[tuple[Any, ...], dict[str, Any]] = {}
    required = {"date", "start_time", "end_time", "subject_name", "activity_type"}
    for item in walk(value):
        if not required.issubset(item):
            continue
        session = {
            "date": item.get("date"),
            "weekday": item.get("weekday"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "subject_name": item.get("subject_name"),
            "subject_url": item.get("subject_url"),
            "activity_type": item.get("activity_type"),
            "group_code": item.get("group_code"),
            "room": item.get("room"),
        }
        key = tuple(session[name] for name in session)
        sessions[key] = session
    return sorted(
        sessions.values(),
        key=lambda item: (
            str(item.get("date")),
            str(item.get("start_time")),
            str(item.get("subject_name")),
            str(item.get("group_code")),
        ),
    )


def collect_sources(value: Any) -> list[str]:
    sources: set[str] = set()
    for item in walk(value):
        for key, raw in item.items():
            if not isinstance(raw, str):
                continue
            if (key.endswith("_url") or key in {"url", "source_url"}) and raw.startswith(
                ("https://www.usc.gal/", "https://usc.gal/")
            ):
                sources.add(raw)
    return sorted(sources)


def exact_degree_urls(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    degrees = catalog.get("degrees", [])
    informatics = [
        item
        for item in degrees
        if "grao en enxenaria informatica" in fold(str(item.get("name", "")))
        and "dobre" not in fold(str(item.get("name", "")))
        and "2a edicion" in fold(str(item.get("name", "")))
    ]
    maths = [
        item
        for item in degrees
        if fold(str(item.get("name", ""))).strip() == "grao en matematicas"
    ]
    if len(informatics) != 1 or len(maths) != 1:
        raise RuntimeError(
            f"Could not resolve exact degree pages: informatics={informatics!r}, maths={maths!r}"
        )
    return {"informatics": informatics[0], "math": maths[0]}


def parse_locations(located: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    result: dict[str, list[dict[str, Any]]] = {}
    matched = 0
    for subject in located.get("subjects", []):
        code = str(subject.get("subject_code", ""))
        locations = [dict(item) for item in subject.get("locations", []) if isinstance(item, dict)]
        result[code] = locations
        if locations:
            matched += 1
    return result, matched


def timetable_program_ids(value: Any, course: int) -> list[int | None]:
    values: set[int] = set()
    for item in walk(value):
        if item.get("course_number") != course or "program_id" not in item:
            continue
        try:
            values.add(int(item["program_id"]))
        except (TypeError, ValueError):
            continue
    return sorted(values) or [None]


async def fetch_one_program(
    degree_url: str,
    course: int,
    year: str,
    program_id: int | None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "degree_url": degree_url,
        "course_number": course,
        "academic_year": year,
        "semester": 1,
    }
    if program_id is not None:
        arguments["program_id"] = program_id

    initial = await call_tool("get_degree_class_timetable", arguments)
    weeks = collect_weeks(initial)
    responses = [initial]
    seen_dates: set[str] = set()
    for week in weeks:
        start_date = str(week["start_date"])
        if start_date in seen_dates:
            continue
        seen_dates.add(start_date)
        weekly_arguments = dict(arguments)
        weekly_arguments["date_in_week"] = start_date
        responses.append(await call_tool("get_degree_class_timetable", weekly_arguments))

    sessions: list[dict[str, Any]] = []
    sources: set[str] = set()
    for response in responses:
        sessions.extend(collect_sessions(response))
        sources.update(collect_sources(response))
    deduplicated = collect_sessions({"sessions": sessions})
    return {
        "degree_url": degree_url,
        "course": course,
        "program_id": program_id,
        "available_weeks": weeks,
        "sessions": deduplicated,
        "sources": sorted(sources),
    }


async def fetch_degree_course(
    degree_url: str,
    course: int,
    year: str,
) -> dict[str, Any]:
    discovery = await call_tool(
        "list_degree_timetables",
        {"degree_url": degree_url, "course_number": course},
    )
    program_ids = timetable_program_ids(discovery, course)
    programs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for program_id in program_ids:
        try:
            programs.append(await fetch_one_program(degree_url, course, year, program_id))
        except Exception as error:  # keep partial official results
            errors.append(
                {
                    "program_id": program_id,
                    "error": type(error).__name__,
                    "message": str(error),
                }
            )
    return {
        "degree_url": degree_url,
        "course": course,
        "program_ids": program_ids,
        "programs": programs,
        "errors": errors,
        "discovery_sources": collect_sources(discovery),
    }


def aliases_for(code: str, locations: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    names = {fold(str(SUBJECTS[code]["name"]))}
    urls: set[str] = set()
    for location in locations:
        if name := str(location.get("subject_name", "")).strip():
            names.add(fold(name))
        for key in ("subject_url",):
            if url := str(location.get(key, "")).strip():
                urls.add(url.rstrip("/"))
        for url in location.get("subject_urls", []) or []:
            if isinstance(url, str) and url:
                urls.add(url.rstrip("/"))
    return names, urls


def match_code(
    session: dict[str, Any],
    aliases: dict[str, tuple[set[str], set[str]]],
) -> str | None:
    url = str(session.get("subject_url") or "").rstrip("/")
    name = fold(str(session.get("subject_name") or ""))
    by_url = [code for code, (_, urls) in aliases.items() if url and url in urls]
    if len(by_url) == 1:
        return by_url[0]
    by_name = [code for code, (names, _) in aliases.items() if name and name in names]
    if len(by_name) == 1:
        return by_name[0]
    return None


def is_laboratory(session: dict[str, Any]) -> bool:
    activity = fold(str(session.get("activity_type") or ""))
    room = fold(str(session.get("room") or ""))
    explicit_activity = any(
        marker in activity
        for marker in ("laborator", "ordenador", "computer", "informatica", "practica")
    )
    explicit_room = any(
        marker in room for marker in ("laborator", "aula informatica", "ordenador", "computer")
    )
    return explicit_activity or explicit_room


def is_friday(session: dict[str, Any]) -> bool:
    try:
        return date.fromisoformat(str(session["date"])).weekday() == 4
    except (KeyError, TypeError, ValueError):
        weekday = fold(str(session.get("weekday") or ""))
        return any(marker in weekday for marker in ("viernes", "venres", "friday"))


def overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("date") == right.get("date")
        and str(left.get("start_time")) < str(right.get("end_time"))
        and str(right.get("start_time")) < str(left.get("end_time"))
    )


def option_summary(group: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    slots = sorted(
        {
            (
                str(item.get("weekday")),
                str(item.get("start_time")),
                str(item.get("end_time")),
                str(item.get("activity_type")),
                str(item.get("room")),
            )
            for item in sessions
        }
    )
    friday_dates = sorted({str(item.get("date")) for item in sessions if is_friday(item)})
    return {
        "group": group,
        "has_friday": bool(friday_dates),
        "friday_dates": friday_dates,
        "sessions": sessions,
        "slot_patterns": [
            {
                "weekday": slot[0],
                "start_time": slot[1],
                "end_time": slot[2],
                "activity_type": slot[3],
                "room": slot[4],
            }
            for slot in slots
        ],
    }


def build_lab_options(subject_sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labs = [item for item in subject_sessions if is_laboratory(item)]
    common = [item for item in labs if not str(item.get("group_code") or "").strip()]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in labs:
        group = str(item.get("group_code") or "").strip()
        if group:
            grouped[group].append(item)
    if grouped:
        return [
            option_summary(group, collect_sessions({"sessions": common + sessions}))
            for group, sessions in sorted(grouped.items())
        ]
    if common:
        return [option_summary("común", common)]
    return []


def find_compatible_solutions(
    options_by_code: dict[str, list[dict[str, Any]]],
    limit: int = 30,
) -> list[dict[str, Any]]:
    eligible = {
        code: [option for option in options if not option["has_friday"]]
        for code, options in options_by_code.items()
        if options
    }
    if any(not options for options in eligible.values()):
        return []
    ordered = sorted(eligible, key=lambda code: (len(eligible[code]), code))
    solutions: list[dict[str, Any]] = []

    def search(index: int, chosen: dict[str, dict[str, Any]], occupied: list[tuple[str, dict[str, Any]]]):
        if len(solutions) >= limit:
            return
        if index == len(ordered):
            solutions.append(
                {
                    code: {
                        "group": option["group"],
                        "slot_patterns": option["slot_patterns"],
                    }
                    for code, option in sorted(chosen.items())
                }
            )
            return
        code = ordered[index]
        for option in eligible[code]:
            conflict = False
            for session in option["sessions"]:
                if any(other_code != code and overlaps(session, other) for other_code, other in occupied):
                    conflict = True
                    break
            if conflict:
                continue
            chosen[code] = option
            appended = [(code, session) for session in option["sessions"]]
            occupied.extend(appended)
            search(index + 1, chosen, occupied)
            del occupied[-len(appended) :]
            chosen.pop(code, None)

    search(0, {}, [])
    return solutions


async def analyse_year(
    year: str,
    degrees: dict[str, dict[str, Any]],
    locations_by_code: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    schedules: dict[str, Any] = {}
    for degree_kind, degree in degrees.items():
        courses = sorted(
            {
                int(details["course"])
                for details in SUBJECTS.values()
                if details["degree"] == degree_kind
            }
        )
        for course in courses:
            key = f"{degree_kind}-{course}"
            schedules[key] = await fetch_degree_course(str(degree["url"]), course, year)

    aliases = {
        code: aliases_for(code, locations_by_code.get(code, []))
        for code in SUBJECTS
    }
    all_sessions: list[dict[str, Any]] = []
    sources: set[str] = set()
    schedule_errors: list[dict[str, Any]] = []
    for schedule_key, schedule in schedules.items():
        sources.update(schedule.get("discovery_sources", []))
        for error in schedule.get("errors", []):
            schedule_errors.append({"schedule": schedule_key, **error})
        for program in schedule.get("programs", []):
            all_sessions.extend(program.get("sessions", []))
            sources.update(program.get("sources", []))
    all_sessions = collect_sessions({"sessions": all_sessions})

    sessions_by_code: dict[str, list[dict[str, Any]]] = {code: [] for code in SUBJECTS}
    unmatched: list[dict[str, Any]] = []
    for session in all_sessions:
        code = match_code(session, aliases)
        if code is None:
            unmatched.append(session)
        else:
            sessions_by_code[code].append(session)
    sessions_by_code = {
        code: collect_sessions({"sessions": sessions})
        for code, sessions in sessions_by_code.items()
    }

    options_by_code = {
        code: build_lab_options(sessions)
        for code, sessions in sessions_by_code.items()
    }
    first_semester_codes = [code for code, sessions in sessions_by_code.items() if sessions]
    no_lab_codes = [code for code in first_semester_codes if not options_by_code[code]]
    friday_forced_codes = [
        code
        for code in first_semester_codes
        if options_by_code[code] and all(option["has_friday"] for option in options_by_code[code])
    ]
    solutions = find_compatible_solutions(options_by_code)

    return {
        "academic_year": year,
        "semester": 1,
        "degrees": degrees,
        "schedules": schedules,
        "first_semester_codes": first_semester_codes,
        "absent_from_first_semester": [code for code in SUBJECTS if code not in first_semester_codes],
        "sessions_by_code": sessions_by_code,
        "activity_types_by_code": {
            code: sorted({str(item.get("activity_type")) for item in sessions})
            for code, sessions in sessions_by_code.items()
        },
        "lab_options_by_code": options_by_code,
        "no_lab_codes": no_lab_codes,
        "friday_forced_codes": friday_forced_codes,
        "compatible_friday_free_solutions": solutions,
        "solution_count_capped": len(solutions),
        "schedule_errors": schedule_errors,
        "unmatched_sessions": unmatched,
        "official_sources": sorted(sources),
        "target_session_count": sum(len(items) for items in sessions_by_code.values()),
    }


async def main() -> None:
    catalog = await call_tool("list_usc_degrees", {})
    degrees = exact_degree_urls(catalog)
    degree_urls = [str(item["url"]) for item in degrees.values()]

    locate_attempts: dict[str, Any] = {}
    selected_year: str | None = None
    selected_locations: dict[str, list[dict[str, Any]]] = {}
    best_matched = -1
    for year in PREFERRED_YEARS:
        try:
            located = await call_tool(
                "locate_usc_subject_codes",
                {
                    "subject_codes": list(SUBJECTS),
                    "academic_year": year,
                    "degree_urls": degree_urls,
                    "concurrency": 4,
                },
            )
            locations, matched = parse_locations(located)
            locate_attempts[year] = {"matched_count": matched, "result": located}
            if matched > best_matched:
                best_matched = matched
                selected_year = year
                selected_locations = locations
        except Exception as error:
            locate_attempts[year] = {
                "error": type(error).__name__,
                "message": str(error),
                "matched_count": 0,
            }

    if selected_year is None:
        raise RuntimeError(f"No academic year could be located: {locate_attempts!r}")

    year_order = [selected_year] + [year for year in PREFERRED_YEARS if year != selected_year]
    analyses: dict[str, Any] = {}
    final_year = selected_year
    for year in year_order:
        locations = parse_locations(locate_attempts.get(year, {}).get("result", {}))[0]
        try:
            analyses[year] = await analyse_year(year, degrees, locations)
        except Exception as error:
            analyses[year] = {"error": type(error).__name__, "message": str(error)}
            continue
        if analyses[year].get("target_session_count", 0) > 0:
            final_year = year
            break

    result = {
        "requested_subjects": SUBJECTS,
        "degree_catalog_source": catalog.get("source_url"),
        "degree_pages": degrees,
        "locate_attempts": locate_attempts,
        "selected_academic_year": final_year,
        "analysis": analyses.get(final_year),
        "all_year_analyses": analyses,
    }

    output = Path("usc_timetable_analysis.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    analysis = result.get("analysis") or {}
    summary = {
        "selected_academic_year": final_year,
        "first_semester_codes": analysis.get("first_semester_codes", []),
        "absent_from_first_semester": analysis.get("absent_from_first_semester", []),
        "no_lab_codes": analysis.get("no_lab_codes", []),
        "friday_forced_codes": analysis.get("friday_forced_codes", []),
        "solution_count_capped": analysis.get("solution_count_capped", 0),
        "first_solution": (analysis.get("compatible_friday_free_solutions") or [None])[0],
        "schedule_errors": analysis.get("schedule_errors", []),
        "official_sources": analysis.get("official_sources", []),
    }
    Path("usc_timetable_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("USC_TIMETABLE_SUMMARY=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
