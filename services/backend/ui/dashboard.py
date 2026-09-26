"""Tenant-facing pages.

Split into real routes (ADR-007). The sidebar previously linked three
`#anchors` on one page and hardcoded which one was "active", so two of the
three nav items were mislabelled at all times and the agent filter's state
could not be bookmarked or shared.

Routes also read less. The single page fanned out to three independent
DynamoDB read paths on every load, so a customer opening the portal purely
to check their allowed list paid for a mitigation query and a model-metadata
GetItem too. Each route now reads only what it renders.
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from services.backend.api.cognito_auth import (
    actor_of, dashboard_auth, dashboard_claims,
)
from services.backend.api.routes.agent import register_agent
from services.backend.api.routes.dashboard import (
    add_whitelist, list_history, list_mitigations, list_own_agents, list_series,
    list_whitelist, mark_history_read, model_status, remove_whitelist,
)
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.api.routes.agent import _try_history
from services.backend.core.tables import (
    AgentsTable, ModelsTable, TenantHistoryTable, TenantsTable,
)
from services.backend.core.usage import (
    protection_status as usage_protection_status,
)
from services.backend.ml.feature_engineering import FEATURE_NAMES
from services.backend.ml.model import TIER1_Z, TIER2_Z, ModelManager
from services.backend.ui.charts import (
    build_axis, gate_curve,
)
from services.backend.schemas.agent_register import AgentRegisterRequest
from services.backend.schemas.whitelist import WhitelistRequest
from services.backend.ui.csrf import CSRF_COOKIE_NAME, verify_csrf
from services.backend.ui.hx import hx_return
from services.backend.ui.presenters import (
    absolute_expiry, agent_health, agent_state, axis_state, baseline_rows,
    capabilities, ledger_rows,
    decompose, system_picture,
    reach, reason_label, relative_expiry, severity_detail,
    tier_css, tier_label,
    timestamp_pair,
)
from services.backend.ui.templates_env import templates

router = APIRouter()


def _rows(mitigations, now, agents=None, paused=False):
    """One row per active mitigation.

    `agents` carries principle 1.5 onto every row: the backend decided, and
    the customer's servers have it only once the agent has collected. Passed
    in rather than queried, because the caller already read them for the
    health line.
    """
    agents = agents or []
    return [{
        "ip": m.ip,
        "decided_at": m.decided_at,
        "reach": reach(m.decided_at, agents, now, paused=paused),
        "tier": m.tier,
        "tier_label": tier_label(m.tier),
        "tier_css": tier_css(m.tier),
        "severity": severity_detail(m.z),
        # The column is scanned, not read: a phrase belongs in the detail
        # pane, a figure belongs in the table.
        "sigma_short": f"{abs(m.z):.1f}σ" if m.z is not None else "n/a",
        "expires_label": relative_expiry(m.expires_at, now),
        "expires_exact": absolute_expiry(m.expires_at),
        "score": m.score,
        "z": m.z,
        # Words, not the stored value. The pane exists to explain a
        # decision to a person, and it was printing behavioral_anomaly.
        "reason": reason_label(m.reason),
        "features": m.features,
    } for m in mitigations]


def _whitelist_rows(whitelist):
    """A register, not a list. Spec 4.5 asks for four facts per entry: the
    address, who allowed it, when, and why.

    The form has always asked for a reason and `added_by` has been written
    since Phase 0. Neither was ever read back, so the field hint promised a
    column that did not exist and "who let this address in" had no answer on
    screen.

    Newest first, because the pattern that matters is a CLUSTER added inside
    one incident. Sorted by address, that cluster is invisible.
    """
    rows = [{
        "ip": e.get("ip", ""),
        "reason": e.get("reason", ""),
        "added_by": e.get("added_by", ""),
        "added_at": e.get("added_at", ""),
        "added": timestamp_pair(e.get("added_at"))["label"],
        "added_exact": timestamp_pair(e.get("added_at"))["exact"],
    } for e in whitelist.entries]
    rows.sort(key=lambda r: r["added_at"], reverse=True)
    return rows


def _baseline_cost(resource, tenant_id: str) -> dict | None:
    """How much of this tenant's measured traffic the allowed list keeps out
    of their own baseline.

    Counted by the nightly walk, carried on the model item, read here through
    the projection that already exists - about 0.5 RCU, and never the 238KB
    blob. None when no model has been trained; a dict with `measured: False`
    when the model predates the count. "Not measured yet" and "zero" are
    different claims and this screen may not merge them.
    """
    item = ModelsTable(resource).get_metadata(tenant_id=tenant_id,
                                              stage_version="production")
    if item is None:
        # The mechanism is true whether or not a baseline has been computed
        # yet, so the sentence stands and only the figure is missing.
        return {"state": "no_model"}
    if "excluded_whitelist_buckets" not in item:
        return {"state": "not_counted"}
    excluded = int(item["excluded_whitelist_buckets"])
    used = int(item.get("training_samples", 0))
    total = used + excluded
    return {
        "state": "measured",
        "excluded": excluded,
        "used": used,
        "total": total,
        # Of everything measured, not of what survived: the denominator a
        # customer means by "how much of my traffic" includes the part that
        # was taken out.
        "share": round(100 * excluded / total) if total else 0,
        # Geometry and the two labels the bar is drawn with. Here, not in the
        # template: a template that formats thousands and multiplies a share
        # by a pixel width is a template nothing can test.
        "width": round(min(excluded / total, 1.0) * 520, 1) if total else 0,
        "excluded_label": f"{excluded:,}".replace(",", "."),
        "total_label": f"{total:,}".replace(",", "."),
    }


def _shell(request, tenant_id, active, resource=None, paused=None, **extra):
    """Context every tenant page needs: who this is, the nav counts that make
    the sidebar carry state instead of merely pointing at it, and the CSRF
    token rendered into an hx-headers attribute.

    Server-rendering the token is what lets `csrf_token` become httpOnly: it
    was readable by script only so the old helper could parse
    document.cookie.

    `paused` is resolved HERE rather than left to each page, because a
    console that shows "5 agents live" on one screen while the customer's
    servers are enforcing nothing is lying by omission, and the screen most
    likely to be open when that matters is whichever one they happened to be
    on. One GetItem on a page nobody polls; callers that have already read
    the tenant pass the answer in and pay nothing.
    """
    if paused is None:
        paused = bool(resource is not None and (
            TenantsTable(resource).get(tenant_id=tenant_id) or {}
        ).get("enforce_paused_at"))
    ctx = {"tenant_id": tenant_id, "role": "tenant", "active": active,
           "paused": paused,
           "csrf_token": request.cookies.get(CSRF_COOKIE_NAME, ""),
           # Light by default now. The rule that made dark the landing
           # place was written for a palette of three saturated hues, which
           # a dark field held apart; one hue in five steps separates on
           # white. The toggle still works and dark is measured to the same
           # standard.
           "theme": request.cookies.get("theme") or "light"}
    ctx.update(extra)
    return ctx


@router.get("/dashboard/ui", response_class=HTMLResponse)
def protection_status(request: Request, ip: str | None = None,
                      feature: str | None = None,
                      allowed: str = "", skipped: str = "",
                      tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)):
    """The list and the selected source, side by side.

    `?ip=` selects a row and `?ip=&feature=` narrows that to one of the seven
    dimensions. Real URLs rather than client-side toggles, so both deep-link,
    survive a refresh, work with the back button, and need no JavaScript at
    all. A decision exists to be pasted into a ticket, and only a URL does
    that.
    """
    now = datetime.now(timezone.utc)
    mitigations = list_mitigations(tenant_id=tenant_id, resource=resource)
    # One Query on the base table's own partition key — no GSI, no scan.
    # Without it an empty mitigation list is indistinguishable from a dead
    # agent, and the page reassures the customer either way.
    agents = AgentsTable(resource).query_by_tenant(tenant_id)
    health = agent_health(agents, now)
    # Read here rather than further down, because every row needs it: while
    # enforcement is paused, "3 of 3 written" is false of all of them. Same
    # GetItem the gates are read from below, moved up, not added.
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    paused = bool(tenant.get("enforce_paused_at"))
    rows = _rows(mitigations, now, agents, paused=paused)
    # Every held row carries its own reason, because the approved screen
    # opens the reason AT the row. The statistics are already in memory for
    # scoring, so seven subtractions per row cost no read.
    _mgr = ModelManager()
    _mgr.load(resource, tenant_id)
    for r in rows:
        r["features_breakdown"] = decompose(
            r["features"] or [],
            getattr(_mgr.stats, "feature_means", None),
            getattr(_mgr.stats, "feature_stds", None))
    selected = next((r for r in rows if r["ip"] == ip), None)
    stats = None
    if selected:
        # The statistics travel with the model that is already cached for
        # scoring, so opening a detail pane costs no extra read.
        mgr = ModelManager()
        mgr.load(resource, tenant_id)
        stats = mgr.stats
        selected["features_breakdown"] = decompose(
            selected["features"] or [],
            getattr(stats, "feature_means", None),
            getattr(stats, "feature_stds", None),
        )
    # The model is loaded here anyway for the detail pane, and its cached
    # stats carry this tenant's own gate. Bands drawn from the module
    # constants would show every tenant somebody else's threshold.
    if not selected:
        mgr = ModelManager()
        mgr.load(resource, tenant_id)
        stats = mgr.stats
    # The gate of RECORD is on Tenants, not on the model: save_model rewrites
    # the Models item every night and would silently revert the operator. The
    # console reads it from there rather than taking the copy that rode in on
    # the stats, because a control whose own screen still shows the old
    # position has not visibly done anything. One small GetItem on a page
    # that already costs a Query and a model load. Read above, where the
    # pause flag on the same item is needed first.
    enforced_sigma = abs(getattr(stats, "tier1_z", TIER1_Z))
    tier1_sigma = abs(float(tenant.get("tier1_z", -enforced_sigma)))
    tier2_sigma = abs(float(tenant.get("tier2_z",
                                       getattr(stats, "tier2_z", TIER2_Z))))

    # The shape below the gate. One Query for the last 24 hours, and the
    # same rows carry the request count, so they are two renderings of one
    # read rather than two reads.
    now_ts = int(now.timestamp())
    series = TenantHistoryTable(resource).query_series(
        tenant_id, now_ts - 86_400, now_ts, fill=False)
    bins: dict[str, int] = {}
    measured = 0
    # What the four stages of the loop actually did with 24 hours of traffic.
    # Three numbers off rows that were already read for the shape below the
    # gate, so the figure at the end of each lane is measured rather than
    # asserted. `record_traffic` has written tier1 and tier2 per hour since
    # the history table existed; nothing had ever read them back.
    slowed = blocked = 0
    for row in series:
        measured += int(row.get("requests", 0))
        # `tier1_decisions`, not `tier1`. The keyword `record_traffic` takes
        # and the attribute it stores have different names, and reading the
        # parameter name gave three lanes reading "9.270 / 0 / 0" on a screen
        # that was holding five sources at the time. Eighth instance of a
        # field written, declared, and dropped at the read layer.
        slowed += int(row.get("tier1_decisions", 0))
        blocked += int(row.get("tier2_decisions", 0))
        for key, value in row.items():
            if key.startswith("n") and key[1:].isdigit():
                bins[key] = bins.get(key, 0) + int(value)
    flow = {"measured": measured, "slowed": slowed, "blocked": blocked,
            "passed": max(0, measured - slowed - blocked)}

    # Two keys on one table in one BatchGetItem. The global ceiling matters
    # as much as this tenant's own: ingest is refused platform-wide, so a
    # tenant inside its share can still be unmeasured.
    throttle = usage_protection_status(resource, tenant_id)
    state = axis_state(health, throttle, model_ready=stats is not None)

    # No feed, no reading. The marks are withheld here rather than hidden in
    # CSS, so there is genuinely nothing on the page to misread.
    axis = build_axis(tier1_sigma, tier2_sigma, bins,
                      rows if state["plot"] == "live" else [],
                      selected_ip=selected["ip"] if selected else None)

    # What each of the thirteen legal gates would have caught. The sources
    # already past the current gate count at every line below it too - they
    # are not in `bins`, because a source that is acted on is stored by
    # address and never folded into the anonymous shape.
    curve = gate_curve(bins, tier1_sigma,
                       len([r for r in rows if r["z"] is not None]))

    # Stored and enforced immediately: the ingest path reads the Tenants item
    # on every batch anyway, to refuse a suspended tenant, so it judges by the
    # value of record. What still lags is the COPY on the model, which the
    # nightly retrain writes and which every path without a request context
    # falls back to. The two differ for a while and the screen says which is
    # which - it just no longer says the customer is waiting.
    pending = (state["gates_armed"]
               and abs(enforced_sigma - tier1_sigma) > 1e-9)

    return templates.TemplateResponse(request, "dashboard_status.html", _shell(
        request, tenant_id, "status", paused=paused,
        mitigations=rows, health=health, selected=selected,
        detail_open=selected is not None,
        axis=axis, axis_state=state, measured=measured, flow=flow,
        curve=curve, enforced_sigma=enforced_sigma, pending=pending,
        active_count=len(mitigations),
        outcome=_bulk_outcome(allowed, skipped),
    ))


def _bulk_outcome(allowed: str, skipped: str) -> dict | None:
    """What the bulk action did, carried back from the redirect.

    Declared as strings and parsed here on purpose. `int` in the signature
    would make a hand-edited URL a 422 on the main screen of the product,
    and this value is reflected into the page, so it is checked on the way
    out rather than trusted on the way in.
    """
    try:
        ok, bad = int(allowed), int(skipped)
    except ValueError:
        return None
    if ok < 0 or bad < 0 or (ok == 0 and bad == 0):
        return None
    return {"allowed": ok, "skipped": bad}


@router.get("/dashboard/ui/agents", response_class=HTMLResponse)
def agents_page(request: Request, id: str | None = None,
                tenant_id: str = Depends(dashboard_auth),
                resource=Depends(get_dynamo_resource)):
    """The fleet, and the selected machine beside it.

    Until now a customer whose protection had stopped was told only that no
    telemetry had arrived. Which of their servers had gone quiet was not
    shown anywhere in the product, on a screen they would be reading during
    the incident it caused.

    `?id=` selects, like `?ip=` on Protection: a real URL, so it deep-links
    into a ticket, survives a refresh, and needs no script. The lookup is
    done over the rows already fetched, never by id against the table, so an
    id guessed from another tenant finds nothing rather than finding
    somebody else's machine.
    """
    return _agents_page(request, tenant_id, resource, selected_id=id)


def _agents_page(request, tenant_id, resource, selected_id=None,
                 created=None, error=None, label=""):
    now = datetime.now(timezone.utc)
    rows = [agent_state(a.model_dump(), now)
            for a in list_own_agents(tenant_id=tenant_id, resource=resource)]
    selected = next((r for r in rows if r["agent_id"] == selected_id), None)
    live = sum(1 for r in rows if r["state"] == "live")

    return templates.TemplateResponse(request, "dashboard_agents.html", _shell(
        request, tenant_id, "agents", resource=resource,
        agents=rows, selected=selected, detail_open=selected is not None,
        live_count=live, quiet_count=sum(1 for r in rows if r["state"] == "quiet"),
        # Reporting and protecting are two different claims, and this is the
        # count for the second one. Only agents that actually said so: an
        # agent that has never reported its backends is unknown, not broken.
        toothless_count=sum(1 for r in rows if r["can_enforce"] == "no"),
        agent_count=len(rows), created=created, error=error,
        new_label=label,
        # The command on screen has to point at THIS deployment. Read from
        # the request rather than configured, so it is right on the laptop,
        # on the Lambda URL and behind CloudFront without three settings.
        backend_url=str(request.base_url).rstrip("/"),
    ))


# One synchronous write each against a table on the account-wide 25 WCU
# pool. `flag_all_for_ip` taught the rest: a caller over the cap is refused,
# never served the first fifty in silence.
BULK_ALLOW_LIMIT = 50

# Values that ride in the query string to keep a POST body-less (ADR-005)
# land in CloudFront access logs, Referer headers and browser history. Spec
# 12.8 refuses long or free-form fields there; these two are neither secret
# nor another party's data, so they are bounded rather than moved into a
# signed body. The numbers match the maxlength in the markup.
AGENT_LABEL_MAX = 60
ALLOW_REASON_MAX = 120


# Registered BEFORE /whitelist/{ip}, and that ordering is load-bearing:
# FastAPI matches in declaration order, so the other way round this reads as
# an attempt to allow an address named "bulk" and loses the whole selection
# behind a success-shaped response.
@router.post("/dashboard/ui/allowed/bulk", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def bulk_allow_ui(ip: list[str] = Query(default=[]),
                  tenant_id: str = Depends(dashboard_auth),
                  claims: dict = Depends(dashboard_claims),
                  resource=Depends(get_dynamo_resource)):
    """Allow several sources in one action.

    The addresses ride in the query string, repeated, so this POST still has
    no body and still needs no CloudFront payload hash (ADR-005). The same
    reason the single-source button posts to a path.

    The outcome travels home as two integers rather than a rendered message:
    the caller posts with hx-swap="none" from a page it is about to leave,
    so a body would be discarded. Integers are also the only thing safe to
    reflect back into a page from a request.
    """
    wanted = list(dict.fromkeys(ip))  # a duplicated checkbox is one allowance
    if len(wanted) > BULK_ALLOW_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=(f"Select at most {BULK_ALLOW_LIMIT} sources at a time. "
                    f"Nothing was changed."))

    allowed = 0
    for address in wanted:
        try:
            # `claims` is passed explicitly because this calls the API
            # route as a plain function, not through FastAPI's dependency
            # injection — an unfilled Depends() default would arrive here as
            # the marker object itself.
            add_whitelist(WhitelistRequest(ip=address, reason="allowed in bulk"),
                          tenant_id=tenant_id, claims=claims, resource=resource)
            allowed += 1
        except ValidationError:
            # Counted, not raised. Two malformed rows must not cost the
            # operator the eighteen that were fine.
            continue

    return Response(status_code=200, headers={
        "HX-Redirect": f"/dashboard/ui?allowed={allowed}&skipped={len(wanted) - allowed}",
    })


@router.post("/dashboard/ui/agents", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_agent_ui(request: Request, label: str = "",
                 tenant_id: str = Depends(dashboard_auth),
                 claims: dict = Depends(dashboard_claims),
                 resource=Depends(get_dynamo_resource)):
    """Mint an agent from the console.

    This replaces an instruction that pointed at nothing. The landing page
    told new customers to "get the token from Detection model in the
    console", and no page in the console has ever shown a token, so there
    was no path from signing up to being protected.

    A token was never the right answer anyway: it is a bearer credential for
    the whole account, it would have to be read off a screen and pasted
    through a shell history, and it expires within the hour. The console is
    already authenticated as this tenant, so it calls the same registration
    route the CLI calls and hands back the one credential the agent needs.

    Nothing new is minted and nothing new is stored: `register_agent` is the
    existing route, unchanged, including the suspended-tenant check that
    stops a session outliving its suspension.
    """
    # Capped here as well as in the markup. A maxlength attribute is a
    # courtesy to a browser, not a control: this value rides in the query
    # string to keep the request body-less (ADR-005), so an unbounded one
    # reaches CloudFront access logs and browser history, which is the half
    # of spec 12.8 a cap can answer.
    label = label.strip()[:AGENT_LABEL_MAX]
    error = None
    created = None
    if not label:
        # The only thing that will ever tell its owner which machine went
        # quiet. A blank one produces a fleet of indistinguishable rows.
        error = "Đặt cho máy một cái tên mà sau này bạn nhận ra."
    else:
        try:
            # `claims` explicitly, never the Depends() default: this calls
            # the API route as a plain function, and the audit row names the
            # actor from it.
            created = register_agent(AgentRegisterRequest(agent_label=label),
                                     tenant_id=tenant_id, claims=claims,
                                     resource=resource)
        except ValidationError:
            error = "Tên đó không dùng được."
        except HTTPException as e:
            error = str(e.detail)

    return _agents_page(request, tenant_id, resource, created=created,
                        error=error, label=label)


@router.post("/dashboard/ui/gate", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def move_gate(tier: int = 1, sigma: float = 0.0,
              tenant_id: str = Depends(dashboard_auth),
              claims: dict = Depends(dashboard_claims),
              resource=Depends(get_dynamo_resource)):
    """Move one gate to one of its thirteen legal positions.

    Body-less: the values ride in the query string, the same shape the theme
    toggle uses, because a form-encoded body would need CloudFront's payload
    hash (ADR-005) and the whole point of this control is that it needs no
    script at all.

    The position is checked against the thirteen the curve offers. Accepting
    4.1 would set a gate whose consequence the screen cannot show, because no
    bin measures it, and a control that can be put somewhere it cannot report
    on is a control that lies.
    """
    if tier not in (1, 2) or sigma not in TenantHistoryTable.NEAR_BINS:
        raise HTTPException(status_code=400,
                            detail="Đó không phải một trong các vị trí có sẵn.")

    tenants = TenantsTable(resource)
    tenant = tenants.get(tenant_id=tenant_id) or {}
    other = float(tenant.get("tier2_z" if tier == 1 else "tier1_z",
                             TIER2_Z if tier == 1 else TIER1_Z))
    # The slow gate sits closer to normal than the block gate. The other way
    # round, every source past the block line is blocked without ever being
    # slowed, and the middle band is empty and meaningless.
    if (tier == 1 and -sigma <= other) or (tier == 2 and -sigma >= other):
        raise HTTPException(
            status_code=400,
            detail=("The slow gate has to sit closer to your normal than the "
                    "block gate."))

    which = "tier1_z" if tier == 1 else "tier2_z"
    old = tenants.set_threshold(tenant_id, which, -sigma)
    # Reporting, so it is wrapped: a throttled audit write must not lose the
    # threshold change the customer just made.
    _try_history(TenantHistoryTable(resource).record_setting,
                 tenant_id, actor=actor_of(claims), what=which,
                 old=old, new=-sigma, now=time.time())

    # Back to the screen they moved it from. The consequence of the move is
    # the thing they were reading.
    return Response(status_code=200, headers={"HX-Redirect": "/dashboard/ui"})


@router.post("/dashboard/ui/enforcement", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def set_enforcement(on: int = 1,
                    tenant_id: str = Depends(dashboard_auth),
                    claims: dict = Depends(dashboard_claims),
                    resource=Depends(get_dynamo_resource)):
    """The off switch.

    Until this existed the only way to stop a customer's own servers turning
    visitors away was to revoke every agent key - which also stops the
    telemetry that would tell them whether stopping was the right call. At
    3am, with real customers being refused, that is not a control, it is a
    demolition.

    Paused, the platform keeps scoring and keeps writing history, and serves
    an empty active set. The agents already release every local rule when
    they see one, so this works on agents that are ALREADY INSTALLED and
    needs no new version rolled out to a single machine.

    Body-less, like the gate: the value rides in the query string, so
    CloudFront's OAC needs no payload hash (ADR-005).
    """
    paused = not bool(on)
    TenantsTable(resource).set_enforcement(tenant_id, paused)
    # Audited. Turning protection off is the single most consequential thing
    # a tenant can do in this console, and "who switched it off, and when" is
    # the first question the review after an incident asks.
    _try_history(TenantHistoryTable(resource).record_setting,
                 tenant_id, actor=actor_of(claims), what="enforcement",
                 old="on" if paused else "off",
                 new="off" if paused else "on", now=time.time())
    return Response(status_code=200, headers={"HX-Redirect": "/dashboard/ui"})


@router.get("/dashboard/ui/audit", response_class=HTMLResponse)
def audit_page(request: Request, days: int = 30,
               tenant_id: str = Depends(dashboard_auth),
               resource=Depends(get_dynamo_resource)):
    """Who changed what, and when.

    The settings ledger has been written since it shipped and had no screen.
    Every other page in this console answers "what is happening"; this is the
    only one that answers "what did WE do", which is the first question the
    review after an incident opens with.

    One Query on the tenant's own partition, bounded by a window the reader
    chose. No index, no scan, and the rows carry their own TTL so the window
    can never be wider than the retention.
    """
    now = datetime.now(timezone.utc)
    if days not in (7, 30):
        days = 30
    until = int(now.timestamp())
    items = TenantHistoryTable(resource).query_settings(
        tenant_id, until - days * 86_400, until)
    rows = ledger_rows(items, now)
    return templates.TemplateResponse(request, "dashboard_audit.html", _shell(
        request, tenant_id, "audit", resource=resource, entries=rows, days=days,
    ))


@router.get("/dashboard/ui/system", response_class=HTMLResponse)
def system_page(request: Request, tenant_id: str = Depends(dashboard_auth),
                resource=Depends(get_dynamo_resource)):
    """How the machinery works, drawn from the machinery.

    Its own screen rather than a panel on the Gate screen. This is the
    question somebody asks on their first day and again the first time
    something goes wrong, and neither is a daily event - putting it above the
    gates would push the thing people open every morning below the fold.

    Two reads, both of which every other console page already makes: the
    tenant's agents and their active mitigations.
    """
    now = datetime.now(timezone.utc)
    agents = AgentsTable(resource).query_by_tenant(tenant_id)
    rows = _rows(list_mitigations(tenant_id=tenant_id, resource=resource),
                 now, agents)
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    sys = system_picture(
        agents, rows,
        abs(float(tenant.get("tier1_z", TIER1_Z))),
        abs(float(tenant.get("tier2_z", TIER2_Z))), now) if agents else None

    # What the customer's own share of the day looks like. Two keys on one
    # table in one BatchGetItem, the same read the Gate screen already makes
    # to decide whether the scale can show anything at all.
    throttle = usage_protection_status(resource, tenant_id)
    used = int(throttle.get("tenant_requests", 0) or 0)
    quota = int(throttle.get("tenant_quota", 0) or 0)
    budget = {"used": used, "quota": quota,
              "width": round(min(used / quota, 1.0) * 520, 1) if quota else 0}

    return templates.TemplateResponse(request, "dashboard_system.html", _shell(
        request, tenant_id, "system", resource=resource, sys=sys,
        agent_count=len(agents), caps=capabilities(), budget=budget,
    ))


@router.get("/dashboard/ui/allowed", response_class=HTMLResponse)
def whitelist_page(request: Request, tenant_id: str = Depends(dashboard_auth),
                   resource=Depends(get_dynamo_resource)):
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    rows = _whitelist_rows(whitelist)
    return templates.TemplateResponse(request, "dashboard_allowed.html", _shell(
        request, tenant_id, "whitelist", resource=resource,
        whitelist=rows,
        whitelist_count=len(whitelist.whitelisted_ips),
        # Only when something has actually been allowed. On an empty register
        # the warning is a lecture about a cost the customer has not paid.
        cost=_baseline_cost(resource, tenant_id) if rows else None,
    ))


@router.get("/dashboard/ui/model", response_class=HTMLResponse)
def model_page(request: Request, tenant_id: str = Depends(dashboard_auth),
               resource=Depends(get_dynamo_resource)):
    model = model_status(tenant_id=tenant_id, resource=resource)

    # The gate of record is on Tenants; the model carries last night's copy.
    # This screen had 4.0 and 5.0 written into the template and read the
    # module constants, so a customer who had moved their gate was shown
    # somebody else's threshold on the page that explains their own model.
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    tier1_sigma = abs(float(tenant.get("tier1_z", model.tier1_z or TIER1_Z)))
    tier2_sigma = abs(float(tenant.get("tier2_z", model.tier2_z or TIER2_Z)))

    thresholds = None
    if model.model_ready and model.score_std:
        # The thresholds expressed in this tenant's own units. "Mean score
        # -0.015" told a customer nothing; "we slow at -0.073 for your
        # traffic" is the same two numbers doing something useful.
        thresholds = {
            "slow": model.score_mean - tier1_sigma * model.score_std,
            "block": model.score_mean - tier2_sigma * model.score_std,
            "slow_sigma": tier1_sigma,
            "block_sigma": tier2_sigma,
        }

    # The staging model, which is written every night even when promotion is
    # refused - deliberately, because it is the evidence for why - and which
    # no screen has ever read. Evidence nobody can see is not evidence. One
    # more projected GetItem, about 0.5 RCU, never the 238KB blob.
    staged = ModelsTable(resource).get_metadata(tenant_id=tenant_id,
                                                stage_version="staging")
    # Only worth a word when it differs from what is serving. On an ordinary
    # night both stages hold the same version and a panel headed "not
    # promoted" would be alarming and wrong.
    if staged and staged.get("version") == model.version:
        staged = None

    return templates.TemplateResponse(request, "dashboard_model.html", _shell(
        request, tenant_id, "model", resource=resource, model=model, thresholds=thresholds,
        baseline=baseline_rows(model.features or list(FEATURE_NAMES),
                               model.feature_means, model.feature_stds),
        staged=staged,
    ))


# --- mutations, swapped in place by htmx ---------------------------------

@router.post("/dashboard/ui/allowed", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_whitelist_form(request: Request, ip: str = "", reason: str = "",
                       tenant_id: str = Depends(dashboard_auth),
                       claims: dict = Depends(dashboard_claims),
                       resource=Depends(get_dynamo_resource)):
    """The add form. Its values ride in the query string (see the
    data-params-in-url hook in ui-status.js), so this POST has no body and
    therefore needs no CloudFront payload hash."""
    # Keyword arguments deliberately: this call sat one positional ahead
    # of the signature the moment `back` was added, and it would have
    # passed the tenant id in as the return destination.
    return add_whitelist_ui(request, ip=ip, reason=reason,
                            tenant_id=tenant_id, claims=claims, resource=resource)


_WHITELIST_RETURNS = {"status": "/dashboard/ui"}


@router.post("/dashboard/ui/allowed/{ip}", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_whitelist_ui(request: Request, ip: str, reason: str = "", back: str = "",
                     because: str = "",
                     tenant_id: str = Depends(dashboard_auth),
                     claims: dict = Depends(dashboard_claims),
                     resource=Depends(get_dynamo_resource)):
    """The IP travels in the path, not a form body.

    That is what keeps this request out of signed-post.js: CloudFront's OAC
    only demands x-amz-content-sha256 when there IS a body (ADR-005), so a
    body-less POST goes through plain htmx with no hashing at all. It also
    lets the mitigation table offer a one-click "Allow this IP" instead of
    making the customer retype an address out of the table above.

    `because` is the line of evidence the appeal was made from, sent by the
    button that sits on that line. Checked BEFORE the whitelist write, not
    after: a 400 that has already let the source through is worse than no
    check at all.
    """
    from services.backend.ml.feature_engineering import FEATURE_NAMES

    # Same reasoning as the agent label: it travels in the URL, so its bound
    # is enforced here rather than trusted to the markup.
    reason = reason.strip()[:ALLOW_REASON_MAX]

    if because and because not in FEATURE_NAMES:
        raise HTTPException(status_code=400,
                            detail="Đó không phải một trong các đặc trưng được đo.")

    error = None
    message = None
    try:
        add_whitelist(WhitelistRequest(ip=ip, reason=reason, because=because),
                      tenant_id=tenant_id, claims=claims, resource=resource)
        message = f"{ip} added to your allowed list. Any block on it has been lifted."
    except ValidationError:
        error = f"“{ip}” is not a valid IP address."
    # Posted from the mitigation table, where hx-swap="none" discards the
    # body: the only way to show the operator that anything happened is to
    # send them back to the list the source has just left.
    if not error:
        sent_back = hx_return(back, _WHITELIST_RETURNS)
        if sent_back is not None:
            return sent_back
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_allowed_table.html", {
        "whitelist": _whitelist_rows(whitelist), "error": error, "message": message,
    })


@router.delete("/dashboard/ui/allowed/{ip}", response_class=HTMLResponse,
               dependencies=[Depends(verify_csrf)])
def remove_whitelist_ui(request: Request, ip: str, tenant_id: str = Depends(dashboard_auth),
                        claims: dict = Depends(dashboard_claims),
                        resource=Depends(get_dynamo_resource)):
    # `claims` passed explicitly, never left to the Depends() default: this
    # calls the API route as a plain function, so the default arrives as the
    # marker object rather than the resolved claims.
    remove_whitelist(ip, tenant_id=tenant_id, claims=claims, resource=resource)
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_allowed_table.html", {
        "whitelist": _whitelist_rows(whitelist),
        "message": f"Đã bỏ {ip}. Từ giờ nó được chấm như mọi địa chỉ khác.",
    })


# --- history -------------------------------------------------------------

def _episode_rows(episodes, now):
    return [{
        "ip": e.ip,
        "hour": datetime.fromtimestamp(e.hour_start, tz=timezone.utc).strftime("%d %b %H:00"),
        "hour_start": e.hour_start,
        "span": _span(e.first_ts, e.last_ts),
        "tier": e.max_tier,
        "tier_label": tier_label(e.max_tier),
        "tier_css": tier_css(e.max_tier),
        "severity": severity_detail(e.last_z),
        # The column is scanned, not read. The phrase belongs in a detail
        # pane; a figure belongs in a table.
        "sigma_short": f"{abs(e.last_z):.1f}σ" if e.last_z is not None else "n/a",
        "decisions": e.decisions,
        "tier1": e.tier1_count,
        "tier2": e.tier2_count,
        "is_new": e.is_new,
        "score": e.last_score,
        "z": e.last_z,
    } for e in episodes]


def _span(first_ts: int, last_ts: int) -> str:
    a = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%H:%M")
    b = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%H:%M")
    return a if a == b else f"{a} to {b}"


@router.get("/dashboard/ui/history", response_class=HTMLResponse)
def history_page(request: Request, days: int = 1, ip: str | None = None,
                 tenant_id: str = Depends(dashboard_auth),
                 resource=Depends(get_dynamo_resource)):
    """What the product did, over time.

    The chart and the table come from the same query window so they can
    never disagree — a chart showing a spike the table below it does not
    list is worse than no chart.

    `?ip=` narrows the table to one source. The detail pane has linked here
    with that parameter since it was written and this route did not have it,
    so the operator landed on the unfiltered list and read the wrong object
    with nothing on the screen saying so. The CHART is deliberately left
    unfiltered: it plots the worst deviation per hour across the whole
    tenant, and narrowing it to one source would be a different quantity
    under the same caption.
    """
    days = days if days in (1, 7) else 1
    now = datetime.now(timezone.utc)
    until = int(now.timestamp())
    since = until - days * 86_400

    episodes = list_history(since=since, until=until, tenant_id=tenant_id, resource=resource)
    series = list_series(since=since, until=until, tenant_id=tenant_id, resource=resource)

    # The same ruler as every other screen, rotated. The deviation chart this
    # replaces plotted one number per hour - the worst sigma seen - which
    # answers "was there a spike" and cannot answer "where did my traffic
    # sit", the question the gate control is for. Both come out of the
    # thirteen bins the hourly row already carries, so this is a second
    # reading of the Query above rather than a second Query.
    #
    # The tenant's own gates, from Tenants: bands drawn from the module
    # constants would show every tenant somebody else's threshold, cutting
    # through their own history.
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    # No feed, no reading. Principle 1.2: an instrument that has lost its
    # signal does not render a reading with a warning beside it.
    has_feed = any(p.batches for p in series) or bool(episodes)

    # The BLOCK gate, and only on the 7-day view. ADR-006 measured 0.00%
    # false positives below z = -5.0, which is what makes this gate safe and
    # is also what makes it impossible to tune on a day of data: the 5.0 to
    # 6.0 bins are empty on almost every ordinary day, so a 24-hour curve
    # offers thirteen rows reading zero at exactly the line the operator is
    # being asked to move. Seven days of bins is about 20 RCU a view, which
    # is why it sits behind the tab and is not switched on everywhere.
    block_curve = None
    if days == 7:
        mgr = ModelManager()
        mgr.load(resource, tenant_id)
        if mgr.stats is not None:
            bins: dict[str, int] = {}
            for point in series:
                for key, value in (point.near or {}).items():
                    bins[key] = bins.get(key, 0) + int(value)
            block_curve = gate_curve(
                bins, abs(float(tenant.get("tier2_z", TIER2_Z))),
                sum(e.tier2_count for e in episodes))

    shown = [e for e in episodes if e.ip == ip] if ip else episodes
    selected, breakdown, drifted = _historic_why(resource, tenant_id, shown)         if ip else (None, [], False)

    return templates.TemplateResponse(request, "dashboard_history.html", _shell(
        request, tenant_id, "history", resource=resource,
        has_feed=has_feed, block_curve=block_curve,
        empty_message=_history_empty_message(series),
        episodes=_timeline(_episode_rows(shown, now), days, now), days=days,
        filter_ip=ip, selected_episode=selected,
        breakdown=breakdown, drifted=drifted,
        unread=sum(1 for e in episodes if e.is_new),
        total_blocked=sum(e.tier2_count for e in shown),
        total_slowed=sum(e.tier1_count for e in shown),
    ))


def _historic_why(resource, tenant_id: str, shown):
    """The most recent episode for this source, broken out across the seven.

    The vector is frozen at decision time and the baseline is read live, so a
    retrain between the two makes the two halves of the explanation describe
    different models. `stats_version` is on the episode for exactly this, and
    this is the only place in the product able to notice. An episode that
    never recorded a version claims nothing: "we cannot tell" and "it moved"
    are different statements, and asserting the second would put a warning on
    every episode written before Phase 0.
    """
    if not shown:
        return None, [], False
    episode = max(shown, key=lambda e: e.last_ts)
    # Already cached for scoring in a warm container, so this is free on the
    # common path and one GetItem on a cold one.
    mgr = ModelManager()
    mgr.load(resource, tenant_id)
    stats = mgr.stats
    breakdown = decompose(episode.last_features,
                          getattr(stats, "feature_means", None),
                          getattr(stats, "feature_stds", None))
    live = getattr(stats, "version", None)
    drifted = bool(episode.stats_version and live
                   and episode.stats_version != live)
    return episode, breakdown, drifted


def _timeline(rows, days, now):
    """Where each episode sits on the window, as a percentage.

    Computed here and not in the template. A template that does arithmetic is
    a template nothing can test, and this particular sum - an hour stamp
    against a window whose length the reader chose - is the one that decides
    whether a mark lands on the right day.

    The mark's size carries how far out it was, on the same six-sigma ceiling
    the axis uses, so a big dot here and a far dot there mean one thing.
    """
    span = max(days, 1) * 86400
    start = int(now.timestamp()) - span
    out = []
    for r in rows:
        hour = int(r.get("hour_start") or 0)
        if not hour:
            continue
        left = (hour - start) / span * 100
        sigma = abs(float(r.get("z") or 0))
        out.append({**r,
                    "left": round(min(max(left, 0.0), 100.0), 2),
                    "size": round(8 + min(sigma, 6.0) / 6.0 * 10, 1)})
    return out


def _history_empty_message(series) -> str:
    if not any(p.batches for p in series):
        return "Không có telemetry nào trong khoảng này."
    return "Không có gì vượt cổng của bạn trong khoảng này."


@router.post("/dashboard/ui/history/mark-read", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def mark_read_ui(request: Request, tenant_id: str = Depends(dashboard_auth),
                 resource=Depends(get_dynamo_resource)):
    mark_history_read(tenant_id=tenant_id, resource=resource)
    return HTMLResponse('<p class="field-hint" data-live>Marked as read.</p>')


# --- taking the evidence out of the product --------------------------------

# Excel and Sheets execute a cell that opens with one of these, so a value
# that reaches a spreadsheet is a formula and not a string. No address can
# begin with one and none of the columns below are free text today, which is
# exactly the condition that makes this cheap to keep and expensive to add
# back after the first column that is.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_LEAD):
        text = "'" + text
    if any(c in text for c in ',"\n\r'):
        text = '"' + text.replace('"', '""') + '"'
    return text


@router.get("/dashboard/ui/mitigations.csv")
def export_mitigations(request: Request,
                       tenant_id: str = Depends(dashboard_auth),
                       resource=Depends(get_dynamo_resource)):
    """What is being held right now, as a file.

    A decision this product makes ends up in someone else's ticket, someone
    else's spreadsheet, or someone else's compliance pack, and until now the
    only way out of the console was a screenshot. A screenshot loses the one
    column that matters in a dispute - whether the customer's own servers had
    actually applied the rule yet - because it is the column nobody thinks to
    scroll to.

    The same Query the page already runs, rendered differently. No new index,
    no new access pattern, and no wider window: this is the active set, which
    is bounded by the mitigation TTLs rather than by a page size.
    """
    now = datetime.now(timezone.utc)
    mitigations = list_mitigations(tenant_id=tenant_id, resource=resource)
    agents = AgentsTable(resource).query_by_tenant(tenant_id)
    # The file is evidence, so it carries the same caveat the screen does: a
    # row exported while enforcement is paused was never in force anywhere.
    paused = bool((TenantsTable(resource).get(tenant_id=tenant_id) or {})
                  .get("enforce_paused_at"))
    rows = _rows(mitigations, now, agents, paused=paused)

    lines = ["source,distance_sigma,doing,at_your_servers,ends,reason"]
    for r in rows:
        lines.append(",".join(_csv_cell(v) for v in (
            r["ip"],
            "" if r["z"] is None else "%.2f" % abs(r["z"]),
            r["tier_label"],
            r["reach"]["detail"],
            r["expires_exact"] or "no expiry",
            r["reason"],
        )))

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return Response(
        "\r\n".join(lines) + "\r\n",
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="traffic-shaper-{stamp}.csv"',
            # The active set is a moment in time and a cached copy of it is a
            # wrong answer, not a stale one.
            "Cache-Control": "no-store",
        },
    )
