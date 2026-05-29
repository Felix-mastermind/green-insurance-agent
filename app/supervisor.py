"""
AI Supervisor module for GoHighLevel CRM oversight.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.ghl_client import (
    GHLIntegrationError,
    get_conversations,
    get_opportunities,
    get_pipelines,
    get_users,
)

router = APIRouter(prefix="/supervisor", tags=["supervisor"])
logger = logging.getLogger(__name__)

WAITING_THRESHOLD = timedelta(minutes=15)
RENEWAL_WINDOW = timedelta(days=30)
EXPIRATION_FIELD_IDS = {
    "QvkiNnmPfbbksTNAgY6u",
    "expiration_date",
    "policy_expiration",
    "renewal_date",
}
QUOTE_KEYWORDS = ("quote", "quoted", "cotizacion", "cotización", "precio", "price", "cost", "estimate")
LICENSE_KEYWORDS = ("license", "licencia", "driver license", "drivers license", "dl")
VIN_KEYWORDS = ("vin", "vehicle identification", "numero de vin", "número de vin")
CALLBACK_KEYWORDS = ("call me", "callback", "call back", "llamame", "llámame", "llamar", "call")
APPOINTMENT_KEYWORDS = ("appointment", "appt", "cita", "agendada", "booked", "scheduled")
QUOTED_STAGE_KEYWORDS = ("quote", "quoted", "cotizacion", "cotización", "proposal", "estimate")
CLOSED_STATUSES = {"lost", "won", "closed"}
INFO_KEYWORDS = ("info", "information", "informacion", "details", "detalle")
ILLUSTRATION_KEYWORDS = ("illustration", "ilustracion", "proposal", "quote")
ENROLLMENT_KEYWORDS = ("enroll", "enrollment", "inscribir", "inscripcion", "application", "apply")
BENEFITS_KEYWORDS = ("benefit", "benefits", "coverage", "cobertura", "deductible", "copay")
PRICING_KEYWORDS = ("price", "pricing", "cost", "premium", "precio", "costo", "cuanto")
INTERVIEW_KEYWORDS = ("interview", "entrevista")
JOB_DETAILS_KEYWORDS = ("job", "position", "details", "trabajo", "puesto", "vacante")
AVAILABILITY_KEYWORDS = ("available", "availability", "disponible", "disponibilidad", "schedule")
OVERLOADED_ASSIGNED_THRESHOLD = 60
OVERLOADED_UNANSWERED_THRESHOLD = 20
OVERLOADED_RESPONSE_THRESHOLD = 30


async def load_supervisor_data() -> dict:
    users, opportunities, conversations, pipelines = await asyncio.gather(
        get_users(),
        get_opportunities(),
        get_conversations(),
        get_pipelines(),
    )
    return {
        "users": users,
        "opportunities": opportunities,
        "conversations": conversations,
        "pipelines": pipelines,
    }


def field(record: dict, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return default


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if not isinstance(value, str):
        return None

    clean_value = value.strip()
    if clean_value.isdigit():
        return parse_datetime(int(clean_value))

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            parsed = datetime.strptime(clean_value, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(clean_value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def user_id(record: dict) -> str:
    return str(field(record, "id", "_id", "userId", default=""))


def user_name(user: dict) -> str:
    first_name = str(field(user, "firstName", default="") or "")
    last_name = str(field(user, "lastName", default="") or "")
    full_name = f"{first_name} {last_name}".strip()
    return str(field(user, "name", "fullName", default=full_name or field(user, "email", default=user_id(user) or "Unassigned")))


def assigned_user_id(record: dict) -> str:
    value = field(record, "assignedTo", "assignedToUserId", "assignedUserId", "ownerId", "userId", default="")
    if isinstance(value, dict):
        return str(field(value, "id", "_id", "userId", default=""))
    return str(value or "")


def opportunity_stage_id(opportunity: dict) -> str:
    stage = field(opportunity, "stage", default={})
    if isinstance(stage, dict):
        return str(field(stage, "id", "_id", "stageId", default=""))
    return str(field(opportunity, "pipelineStageId", "stageId", "pipeline_stage_id", default=""))


def opportunity_pipeline_id(opportunity: dict) -> str:
    pipeline = field(opportunity, "pipeline", default={})
    if isinstance(pipeline, dict):
        return str(field(pipeline, "id", "_id", "pipelineId", default=""))
    return str(field(opportunity, "pipelineId", "pipeline_id", default=""))


def build_pipeline_indexes(pipelines: list) -> tuple[dict, dict]:
    pipelines_by_id = {}
    stages_by_id = {}

    for pipeline in pipelines:
        pipeline_id = str(field(pipeline, "id", "_id", "pipelineId", default=""))
        pipeline_name = str(field(pipeline, "name", "title", default=pipeline_id or "Unmapped Pipeline"))
        if pipeline_id:
            pipelines_by_id[pipeline_id] = pipeline_name

        for stage in pipeline.get("stages", []) or []:
            stage_id = str(field(stage, "id", "_id", "stageId", default=""))
            if stage_id:
                stages_by_id[stage_id] = {
                    "stage_name": str(field(stage, "name", "title", default=stage_id)),
                    "pipeline_id": pipeline_id,
                    "pipeline_name": pipeline_name,
                }

    return pipelines_by_id, stages_by_id


def opportunity_pipeline_name(opportunity: dict, pipelines: list) -> str:
    pipelines_by_id, stages_by_id = build_pipeline_indexes(pipelines)
    pipeline_id = opportunity_pipeline_id(opportunity)
    if pipeline_id and pipeline_id in pipelines_by_id:
        return pipelines_by_id[pipeline_id]

    stage_id = opportunity_stage_id(opportunity)
    if stage_id and stage_id in stages_by_id:
        return stages_by_id[stage_id]["pipeline_name"]

    pipeline = field(opportunity, "pipeline", default={})
    if isinstance(pipeline, dict):
        name = field(pipeline, "name", "title", default="")
        if name:
            return str(name)

    return "Unmapped Pipeline"


def opportunity_stage_name(opportunity: dict, pipelines: list) -> str:
    explicit = field(opportunity, "stageName", "pipelineStageName", default="")
    if explicit:
        return str(explicit)

    stage = field(opportunity, "stage", default={})
    if isinstance(stage, dict):
        name = field(stage, "name", "title", default="")
        if name:
            return str(name)

    stage_id = opportunity_stage_id(opportunity)
    _, stages_by_id = build_pipeline_indexes(pipelines)
    if stage_id and stage_id in stages_by_id:
        return stages_by_id[stage_id]["stage_name"]

    return stage_id or "Unmapped Stage"


def contact_id(record: dict) -> str:
    contact = field(record, "contact", default={})
    if isinstance(contact, dict):
        nested_id = field(contact, "id", "_id", "contactId", default="")
        if nested_id:
            return str(nested_id)
    return str(field(record, "contactId", "contact_id", "contactID", default=""))


def contact_name(record: dict) -> str:
    contact = field(record, "contact", default={})
    if isinstance(contact, dict):
        name = field(contact, "name", "fullName", default="")
        if name:
            return str(name)
    return str(field(record, "contactName", "name", "fullName", "title", default="Unnamed Lead"))


def last_message_at(conversation: dict) -> datetime | None:
    return parse_datetime(
        field(
            conversation,
            "lastMessageDate",
            "lastMessageAt",
            "dateUpdated",
            "updatedAt",
            "dateAdded",
            "createdAt",
        )
    )


def message_date(conversation: dict, direction: str) -> datetime | None:
    direction = direction.lower()
    candidates = []

    if direction == "inbound":
        candidates.extend(("lastInboundMessageDate", "lastInboundAt", "lastIncomingMessageDate"))
    else:
        candidates.extend(("lastOutboundMessageDate", "lastOutboundAt", "lastOutgoingMessageDate"))

    for key in candidates:
        parsed = parse_datetime(field(conversation, key, default=None))
        if parsed:
            return parsed

    raw_direction = str(field(conversation, "lastMessageDirection", "direction", default="")).lower()
    if raw_direction in {direction, "incoming" if direction == "inbound" else "outgoing"}:
        return last_message_at(conversation)

    return None


def is_inbound_waiting(conversation: dict) -> bool:
    unread = field(conversation, "unreadCount", "unreadMessages", default=0)
    try:
        if int(unread) > 0:
            return True
    except (TypeError, ValueError):
        pass

    direction = str(field(conversation, "lastMessageDirection", "direction", default="")).lower()
    message_type = str(field(conversation, "lastMessageType", "lastMessageSource", default="")).lower()
    return direction in {"inbound", "incoming"} or "inbound" in message_type


def build_user_index(users: list) -> dict:
    return {user_id(user): {"id": user_id(user), "name": user_name(user), "email": field(user, "email", default="")} for user in users}


def latest_conversation(conversations: list) -> dict:
    if not conversations:
        return {}
    return max(conversations, key=lambda item: last_message_at(item) or datetime.min.replace(tzinfo=timezone.utc))


def latest_message_date(conversations: list, direction: str) -> datetime | None:
    dates = [message_date(conversation, direction) for conversation in conversations]
    dates = [date for date in dates if date]
    return max(dates) if dates else None


def conversation_text(conversations: list) -> str:
    text_parts = []
    for conversation in conversations:
        for key in ("lastMessageBody", "lastMessage", "body", "message", "subject"):
            value = field(conversation, key, default="")
            if value:
                text_parts.append(str(value))
    return " ".join(text_parts).lower()


def has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def pipeline_category(pipeline_name: str) -> str:
    name = pipeline_name.lower()
    if "auto" in name:
        return "AUTO"
    if "life" in name or "vida" in name:
        return "LIFE"
    if "health" in name or "dental" in name or "salud" in name:
        return "HEALTH_DENTAL"
    if "recruit" in name or "hiring" in name or "reclut" in name:
        return "RECRUITING"
    return "GENERAL"


def normalize_pipeline_filter(pipeline: str | None) -> str | None:
    return pipeline.strip().lower() if pipeline else None


def pipeline_matches(lead: dict, pipeline: str | None) -> bool:
    normalized = normalize_pipeline_filter(pipeline)
    if not normalized:
        return True
    return normalized in str(lead.get("pipeline_name", "")).lower()


def waiting_minutes_for_dates(last_inbound: datetime | None, last_outbound: datetime | None) -> int:
    if not last_inbound:
        return 0
    if last_outbound and last_outbound >= last_inbound:
        return 0
    return max(0, int((datetime.now(timezone.utc) - last_inbound.astimezone(timezone.utc)).total_seconds() // 60))


def hot_lead_score(opportunity: dict, conversations: list, pipelines: list) -> dict:
    score = 0
    factors = {}
    text = conversation_text(conversations)
    pipeline_name = opportunity_pipeline_name(opportunity, pipelines)
    category = pipeline_category(pipeline_name)
    stage_name = opportunity_stage_name(opportunity, pipelines).lower()
    status = str(field(opportunity, "status", default="")).lower()
    last_inbound = latest_message_date(conversations, "inbound")
    last_outbound = latest_message_date(conversations, "outbound")
    waiting_minutes = waiting_minutes_for_dates(last_inbound, last_outbound)

    recent_response = bool(last_inbound and (datetime.now(timezone.utc) - last_inbound.astimezone(timezone.utc)) <= timedelta(days=2))
    factor_scores = {}

    if category == "AUTO":
        factor_scores = {
            "requested_quote": (has_keyword(text, QUOTE_KEYWORDS), 25),
            "sent_driver_license": (has_keyword(text, LICENSE_KEYWORDS), 25),
            "sent_vin": (has_keyword(text, VIN_KEYWORDS), 25),
            "recent_response": (recent_response, 25),
        }
    elif category == "LIFE":
        factor_scores = {
            "requested_information": (has_keyword(text, INFO_KEYWORDS), 25),
            "booked_appointment": (has_keyword(text, APPOINTMENT_KEYWORDS) or "appointment" in stage_name or "cita" in stage_name, 25),
            "requested_illustration": (has_keyword(text, ILLUSTRATION_KEYWORDS), 25),
            "recent_response": (recent_response, 25),
        }
    elif category == "HEALTH_DENTAL":
        factor_scores = {
            "requested_enrollment": (has_keyword(text, ENROLLMENT_KEYWORDS), 25),
            "requested_benefits": (has_keyword(text, BENEFITS_KEYWORDS), 25),
            "requested_pricing": (has_keyword(text, PRICING_KEYWORDS), 25),
            "recent_response": (recent_response, 25),
        }
    elif category == "RECRUITING":
        factor_scores = {
            "responded_to_interview": (has_keyword(text, INTERVIEW_KEYWORDS), 35),
            "requested_job_details": (has_keyword(text, JOB_DETAILS_KEYWORDS), 35),
            "confirmed_availability": (has_keyword(text, AVAILABILITY_KEYWORDS), 30),
        }
    else:
        quoted_not_closed = any(keyword in stage_name for keyword in QUOTED_STAGE_KEYWORDS) and status not in CLOSED_STATUSES
        factor_scores = {
            "recent_response": (recent_response, 25),
            "requested_quote": (has_keyword(text, QUOTE_KEYWORDS), 25),
            "requested_callback": (has_keyword(text, CALLBACK_KEYWORDS), 20),
            "appointment_booked": (has_keyword(text, APPOINTMENT_KEYWORDS) or "appointment" in stage_name or "cita" in stage_name, 20),
            "quoted_but_not_closed": (quoted_not_closed, 25),
        }

    for name, (matched, points) in factor_scores.items():
        factors[name] = matched
        if matched:
            score += points

    if waiting_minutes >= 15:
        score += 10
        factors["waiting_more_than_15_minutes"] = True
    else:
        factors["waiting_more_than_15_minutes"] = False

    return {
        "score": min(score, 100),
        "pipeline_category": category,
        "factors": factors,
        "waiting_minutes": waiting_minutes,
        "last_inbound_message_date": last_inbound.isoformat() if last_inbound else None,
        "last_outbound_message_date": last_outbound.isoformat() if last_outbound else None,
    }


def compact_lead(
    record: dict,
    pipelines: list | None = None,
    users: list | None = None,
    conversations: list | None = None,
    score_info: dict | None = None,
) -> dict:
    assignee = assigned_user_id(record)
    users_by_id = build_user_index(users or [])
    pipeline_name = opportunity_pipeline_name(record, pipelines or []) if pipelines is not None else ""
    stage_name = opportunity_stage_name(record, pipelines or []) if pipelines is not None else ""
    last_inbound = latest_message_date(conversations or [], "inbound")
    last_outbound = latest_message_date(conversations or [], "outbound")
    waiting_minutes = waiting_minutes_for_dates(last_inbound, last_outbound)
    if score_info is None and pipelines is not None:
        score_info = hot_lead_score(record, conversations or [], pipelines)
    score_info = score_info or {"score": 0, "factors": {}}

    item = {
        "id": field(record, "id", "_id", "opportunityId", "conversationId", default=""),
        "contact_id": contact_id(record),
        "name": contact_name(record),
        "contact_name": contact_name(record),
        "pipeline_name": pipeline_name,
        "stage_name": stage_name,
        "assigned_agent": users_by_id.get(assignee, {"id": assignee, "name": "Unassigned", "email": ""}) if assignee else {"id": "", "name": "Unassigned", "email": ""},
        "assigned_user": users_by_id.get(assignee, {"id": assignee, "name": "Unassigned", "email": ""}) if assignee else {"id": "", "name": "Unassigned", "email": ""},
        "last_inbound_message_date": last_inbound.isoformat() if last_inbound else None,
        "last_outbound_message_date": last_outbound.isoformat() if last_outbound else None,
        "waiting_time_minutes": waiting_minutes,
        "hot_lead_score": score_info.get("score", 0),
        "score_factors": score_info.get("factors", {}),
        "pipeline_category": score_info.get("pipeline_category", pipeline_category(pipeline_name)),
        "status": field(record, "status", default=""),
        "monetary_value": field(record, "monetaryValue", "value", default=0),
        "opportunity_value": field(record, "monetaryValue", "value", default=0),
        "created_at": field(record, "createdAt", "dateAdded", default=""),
        "updated_at": field(record, "updatedAt", "dateUpdated", default=""),
        "created_date": field(record, "createdAt", "dateAdded", default=""),
        "updated_date": field(record, "updatedAt", "dateUpdated", default=""),
    }
    if pipelines is not None:
        item["stage"] = stage_name
    return item


def build_conversation_index(conversations: list) -> dict:
    index = {}
    for conversation in conversations:
        cid = contact_id(conversation)
        if cid:
            index.setdefault(cid, []).append(conversation)
    return index


def all_leads(data: dict, pipeline: str | None = None) -> list:
    conversations_by_contact = build_conversation_index(data["conversations"])
    leads = []

    for opportunity in data["opportunities"]:
        related_conversations = conversations_by_contact.get(contact_id(opportunity), [])
        score = hot_lead_score(opportunity, related_conversations, data["pipelines"])
        lead = compact_lead(opportunity, data["pipelines"], data["users"], related_conversations, score)
        if pipeline_matches(lead, pipeline):
            leads.append(lead)

    return leads


def filtered_data(data: dict, pipeline: str | None = None) -> dict:
    if not normalize_pipeline_filter(pipeline):
        return data

    leads = all_leads(data, pipeline)
    opportunity_ids = {str(lead["id"]) for lead in leads}
    contact_ids = {str(lead["contact_id"]) for lead in leads if lead.get("contact_id")}

    return {
        **data,
        "opportunities": [
            opportunity for opportunity in data["opportunities"]
            if str(field(opportunity, "id", "_id", "opportunityId", default="")) in opportunity_ids
        ],
        "conversations": [
            conversation for conversation in data["conversations"]
            if contact_id(conversation) in contact_ids
        ],
    }


def hot_leads(data: dict, pipeline: str | None = None) -> list:
    conversations_by_contact = build_conversation_index(data["conversations"])
    hot = []

    for opportunity in data["opportunities"]:
        status = str(field(opportunity, "status", default="")).lower()
        if status in CLOSED_STATUSES:
            continue

        related_conversations = conversations_by_contact.get(contact_id(opportunity), [])
        score = hot_lead_score(opportunity, related_conversations, data["pipelines"])
        if score["score"] >= 70:
            item = compact_lead(opportunity, data["pipelines"], data["users"], related_conversations, score)
            if pipeline_matches(item, pipeline):
                hot.append(item)

    return sorted(hot, key=lambda item: item["hot_lead_score"], reverse=True)


def unattended_leads(data: dict, pipeline: str | None = None) -> list:
    leads = []
    conversations_by_contact = build_conversation_index(data["conversations"])
    for opportunity in data["opportunities"]:
        status = str(field(opportunity, "status", default="")).lower()
        if status not in CLOSED_STATUSES and not assigned_user_id(opportunity):
            related_conversations = conversations_by_contact.get(contact_id(opportunity), [])
            lead = compact_lead(opportunity, data["pipelines"], data["users"], related_conversations)
            if pipeline_matches(lead, pipeline):
                leads.append(lead)
    return leads


def waiting_leads(data: dict, pipeline: str | None = None) -> list:
    return [
        lead for lead in all_leads(data, pipeline)
        if lead["waiting_time_minutes"] >= int(WAITING_THRESHOLD.total_seconds() // 60)
    ]


def agent_workload(data: dict) -> list:
    users_by_id = {user_id(user): user_name(user) for user in data["users"]}
    workload = {}

    for opportunity in data["opportunities"]:
        assignee = assigned_user_id(opportunity) or "unassigned"
        workload.setdefault(assignee, {"agent_id": assignee, "agent_name": users_by_id.get(assignee, "Unassigned"), "open_opportunities": 0, "waiting_conversations": 0})
        status = str(field(opportunity, "status", default="")).lower()
        if status not in CLOSED_STATUSES:
            workload[assignee]["open_opportunities"] += 1

    for conversation in data["conversations"]:
        assignee = assigned_user_id(conversation) or "unassigned"
        workload.setdefault(assignee, {"agent_id": assignee, "agent_name": users_by_id.get(assignee, "Unassigned"), "open_opportunities": 0, "waiting_conversations": 0})
        if is_inbound_waiting(conversation):
            workload[assignee]["waiting_conversations"] += 1

    return sorted(workload.values(), key=lambda item: item["open_opportunities"] + item["waiting_conversations"], reverse=True)


def average_response_time_by_agent(data: dict) -> list:
    users_by_id = {user_id(user): user_name(user) for user in data["users"]}
    buckets = {}

    for conversation in data["conversations"]:
        assignee = assigned_user_id(conversation) or "unassigned"
        last_at = last_message_at(conversation)
        first_response_at = parse_datetime(field(conversation, "firstResponseTime", "firstResponseAt", "lastOutboundMessageDate", default=None))
        created_at = parse_datetime(field(conversation, "dateAdded", "createdAt", default=None))

        if first_response_at and created_at:
            minutes = max(0, int((first_response_at - created_at).total_seconds() // 60))
        elif last_at and is_inbound_waiting(conversation):
            minutes = int((datetime.now(timezone.utc) - last_at.astimezone(timezone.utc)).total_seconds() // 60)
        else:
            continue

        buckets.setdefault(assignee, []).append(minutes)

    results = []
    for assignee, values in buckets.items():
        results.append({
            "agent_id": assignee,
            "agent_name": users_by_id.get(assignee, "Unassigned"),
            "average_response_minutes": round(sum(values) / len(values), 2),
            "sample_size": len(values),
        })
    return sorted(results, key=lambda item: item["average_response_minutes"])


def average_response_map(data: dict) -> dict:
    return {
        item["agent_id"]: item["average_response_minutes"]
        for item in average_response_time_by_agent(data)
    }


def supervisor_workload(data: dict, pipeline: str | None = None) -> dict:
    response_times = average_response_map(filtered_data(data, pipeline))
    workload = {}

    for lead in all_leads(data, pipeline):
        agent = lead["assigned_agent"]
        agent_id = agent.get("id") or "unassigned"
        agent_name = agent.get("name") or "Unassigned"
        workload.setdefault(agent_name, {
            "assigned": 0,
            "unanswered": 0,
            "avg_response_minutes": response_times.get(agent_id, 0),
            "overloaded": False,
        })
        workload[agent_name]["assigned"] += 1
        if lead["waiting_time_minutes"] >= int(WAITING_THRESHOLD.total_seconds() // 60):
            workload[agent_name]["unanswered"] += 1

    for metrics in workload.values():
        metrics["overloaded"] = (
            metrics["assigned"] >= OVERLOADED_ASSIGNED_THRESHOLD
            or metrics["unanswered"] >= OVERLOADED_UNANSWERED_THRESHOLD
            or metrics["avg_response_minutes"] >= OVERLOADED_RESPONSE_THRESHOLD
        )

    return dict(sorted(workload.items(), key=lambda item: item[1]["assigned"], reverse=True))


def leads_by_stage(data: dict, pipeline: str | None = None) -> list:
    stages = {}
    for lead in all_leads(data, pipeline):
        stage = lead["stage_name"]
        stages[stage] = stages.get(stage, 0) + 1
    return [{"stage": stage, "count": count} for stage, count in sorted(stages.items())]


def custom_field_value(record: dict, field_ids: set[str]) -> Any:
    for custom_field in record.get("customFields", []) or []:
        key = str(field(custom_field, "id", "fieldId", "key", "name", default=""))
        if key in field_ids:
            return field(custom_field, "value", "field_value", default=None)
    return None


def renewals_due_soon(data: dict, pipeline: str | None = None) -> list:
    now = datetime.now(timezone.utc)
    due = []
    conversations_by_contact = build_conversation_index(data["conversations"])
    for opportunity in data["opportunities"]:
        raw_date = field(opportunity, "expirationDate", "renewalDate", "policyExpirationDate", default=None)
        if raw_date is None:
            raw_date = custom_field_value(opportunity, EXPIRATION_FIELD_IDS)
        renewal_date = parse_datetime(raw_date)
        if not renewal_date:
            continue
        days_until = (renewal_date.astimezone(timezone.utc).date() - now.date()).days
        if 0 <= days_until <= RENEWAL_WINDOW.days:
            related_conversations = conversations_by_contact.get(contact_id(opportunity), [])
            item = compact_lead(opportunity, data["pipelines"], data["users"], related_conversations)
            item["renewal_date"] = renewal_date.date().isoformat()
            item["days_until_renewal"] = days_until
            if pipeline_matches(item, pipeline):
                due.append(item)
    return sorted(due, key=lambda item: item["days_until_renewal"])


def pipeline_summary(data: dict) -> dict:
    summaries = {}
    hot = hot_leads(data)
    waiting = waiting_leads(data)

    for lead in all_leads(data):
        name = lead["pipeline_name"]
        summaries.setdefault(name, {"name": name, "opportunities": 0, "hot_leads": 0, "unanswered": 0})
        summaries[name]["opportunities"] += 1

    for lead in hot:
        summaries.setdefault(lead["pipeline_name"], {"name": lead["pipeline_name"], "opportunities": 0, "hot_leads": 0, "unanswered": 0})
        summaries[lead["pipeline_name"]]["hot_leads"] += 1

    for lead in waiting:
        summaries.setdefault(lead["pipeline_name"], {"name": lead["pipeline_name"], "opportunities": 0, "hot_leads": 0, "unanswered": 0})
        summaries[lead["pipeline_name"]]["unanswered"] += 1

    return {"pipelines": sorted(summaries.values(), key=lambda item: item["opportunities"], reverse=True)}


def supervisor_actions(data: dict, pipeline: str | None = None) -> dict:
    actions = []

    for lead in waiting_leads(data, pipeline):
        actions.append({
            "priority": 100 + lead["waiting_time_minutes"],
            "action": f"Call {lead['contact_name']} (waiting {lead['waiting_time_minutes']} min)",
            "lead": lead,
        })

    for lead in hot_leads(data, pipeline):
        if lead["hot_lead_score"] >= 70:
            actions.append({
                "priority": 80 + lead["hot_lead_score"],
                "action": f"Follow up {lead['contact_name']} (hot lead score {lead['hot_lead_score']})",
                "lead": lead,
            })

    for lead in renewals_due_soon(data, pipeline):
        actions.append({
            "priority": 70 + max(0, 30 - lead["days_until_renewal"]),
            "action": f"Renewal reminder for {lead['contact_name']} (expires in {lead['days_until_renewal']} days)",
            "lead": lead,
        })

    actions = sorted(actions, key=lambda item: item["priority"], reverse=True)
    return {"actions": [item["action"] for item in actions], "details": actions}


def manager_context(data: dict, pipeline: str | None = None) -> dict:
    selected_leads = all_leads(data, pipeline)
    selected_hot = hot_leads(data, pipeline)
    selected_waiting = waiting_leads(data, pipeline)
    selected_renewals = renewals_due_soon(data, pipeline)
    return {
        "pipeline_filter": pipeline,
        "questions_supported": [
            "How is AUTO performing?",
            "Which agents are overloaded?",
            "Which leads need immediate attention?",
            "How many renewals are due this week?",
        ],
        "metrics": {
            "opportunities": len(selected_leads),
            "hot_leads": len(selected_hot),
            "unanswered": len(selected_waiting),
            "renewals_due_soon": len(selected_renewals),
            "renewals_due_this_week": len([lead for lead in selected_renewals if lead["days_until_renewal"] <= 7]),
        },
    }


def build_supervisor_report(data: dict, pipeline: str | None = None) -> dict:
    scoped_data = filtered_data(data, pipeline)
    workload = agent_workload(scoped_data)
    response_times = average_response_time_by_agent(scoped_data)
    return {
        "counts": {
            "users": len(scoped_data["users"]),
            "opportunities": len(all_leads(data, pipeline)),
            "conversations": len(scoped_data["conversations"]),
            "pipelines": len(scoped_data["pipelines"]),
        },
        "pipeline_filter": pipeline,
        "hot_leads": hot_leads(data, pipeline),
        "unattended_leads": unattended_leads(data, pipeline),
        "leads_waiting_more_than_15_minutes": waiting_leads(data, pipeline),
        "agent_workload": workload,
        "workload": supervisor_workload(data, pipeline),
        "average_response_time_per_agent": response_times,
        "leads_by_stage": leads_by_stage(data, pipeline),
        "renewals_due_soon": renewals_due_soon(data, pipeline),
        "pipelines": pipeline_summary(data)["pipelines"],
        "manager_context": manager_context(data, pipeline),
    }


async def supervisor_response(builder: Callable[[dict], Any]):
    try:
        data = await load_supervisor_data()
        return builder(data)
    except GHLIntegrationError as e:
        logger.error(
            "[Supervisor] GHL error | endpoint=%s | status_code=%s | response_body=%s",
            e.endpoint,
            e.ghl_status or e.status_code,
            e.ghl_response,
        )
        return JSONResponse(
            status_code=e.status_code,
            content={
                "status": "error",
                "ghl_status": e.ghl_status or e.status_code,
                "ghl_response": e.ghl_response,
                "endpoint": e.endpoint,
            },
        )
    except Exception as e:
        logger.exception("[Supervisor] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def summary(pipeline: str | None = None):
    return await supervisor_response(lambda data: build_supervisor_report(data, pipeline))


@router.get("/hot-leads")
async def supervisor_hot_leads(pipeline: str | None = None):
    return await supervisor_response(lambda data: {"hot_leads": hot_leads(data, pipeline)})


@router.get("/agent-performance")
async def supervisor_agent_performance(pipeline: str | None = None):
    return await supervisor_response(
        lambda data: {
            "agent_workload": agent_workload(filtered_data(data, pipeline)),
            "average_response_time_per_agent": average_response_time_by_agent(filtered_data(data, pipeline)),
        }
    )


@router.get("/unanswered")
async def supervisor_unanswered(pipeline: str | None = None):
    return await supervisor_response(
        lambda data: {
            "unattended_leads": unattended_leads(data, pipeline),
            "leads_waiting_more_than_15_minutes": waiting_leads(data, pipeline),
        }
    )


@router.get("/renewals")
async def supervisor_renewals(pipeline: str | None = None):
    return await supervisor_response(lambda data: {"renewals_due_soon": renewals_due_soon(data, pipeline)})


@router.get("/pipelines")
async def supervisor_pipelines():
    return await supervisor_response(pipeline_summary)


@router.get("/workload")
async def supervisor_workload_endpoint(pipeline: str | None = None):
    return await supervisor_response(lambda data: supervisor_workload(data, pipeline))


@router.get("/actions")
async def supervisor_actions_endpoint(pipeline: str | None = None):
    return await supervisor_response(lambda data: supervisor_actions(data, pipeline))
