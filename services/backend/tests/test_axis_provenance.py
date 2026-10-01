"""An instrument with no feed must not show a reading.

Not a reading with a caution light beside it - no reading. The G1000 draws a
red X across the airspeed tape itself when the pitot feed fails; it does not
light a lamp somewhere else on the panel. The test that distinguishes this
from a status tile someone moved: in the no-signal state there is no reading
on the screen at all.

Three states this product has always conflated, and one it states wrongly
today: a quota-throttled tenant is told "the process stopped, or it cannot
reach the API", so a customer restarts a healthy agent at 3am and it does
not help.
"""
from services.backend.ui.presenters import (
    DEGRADED, HEALTHY, NEVER_CONNECTED, axis_state,
)

FED = {"state": HEALTHY, "reporting": 3, "last_seen_label": "12 giây trước"}
QUIET = {"state": DEGRADED, "reporting": 0, "last_seen_label": "3 giờ trước"}
NEVER = {"state": NEVER_CONNECTED, "reporting": 0, "last_seen_label": None}
OPEN = {"throttled": False, "throttled_reason": None}
CAPPED = {"throttled": True, "throttled_reason": "tenant"}
GLOBAL = {"throttled": True, "throttled_reason": "global"}


def test_a_fed_axis_draws_its_bands_and_its_marks():
    assert axis_state(FED, OPEN, model_ready=True)["plot"] == "live"


def test_a_dead_feed_deletes_the_reading():
    """The whole rule. A tile leaves the reading up and adds a warning; this
    takes the reading away."""
    state = axis_state(QUIET, OPEN, model_ready=True)

    assert state["state"] == "no_signal"
    assert state["plot"] == "outline"


def test_a_dead_feed_says_nothing_is_being_measured_and_when_it_stopped():
    state = axis_state(QUIET, OPEN, model_ready=True)

    assert "3 giờ trước" in state["sentence"]
    assert "không" in state["sentence"].lower()


def test_a_tenant_that_never_connected_is_not_the_same_as_one_gone_quiet():
    """Two completely different next actions: install it, versus go and look
    at the machine."""
    never = axis_state(NEVER, OPEN, model_ready=False)
    quiet = axis_state(QUIET, OPEN, model_ready=True)

    assert never["sentence"] != quiet["sentence"]
    # The distinguishing fact, not a keyword: a tenant that never connected
    # has no "since when", and a sentence that invents one would send them
    # looking for a machine that stopped, which is the other case entirely.
    assert "từ" not in never["sentence"].lower()
    assert "từ" in quiet["sentence"].lower()


def test_an_empty_axis_and_a_dead_agent_never_read_the_same():
    """The most damaging thing a security product can do with a blank
    screen. A quiet tenant keeps its bands; a dead one loses them."""
    assert axis_state(FED, OPEN, model_ready=True)["plot"] != \
        axis_state(QUIET, OPEN, model_ready=True)["plot"]


def test_a_throttled_tenant_is_told_the_truth_about_its_agent():
    """Today this case is displayed as a dead agent, so the customer
    restarts a process that is working perfectly."""
    state = axis_state(FED, CAPPED, model_ready=True)

    assert state["state"] == "throttled"
    assert state["plot"] == "frozen"
    assert "khởi động lại" in state["sentence"].lower()


def test_the_two_throttle_reasons_are_different_sentences():
    """One the customer can act on by sending less. One they cannot act on
    at all, because somebody else filled the day."""
    assert axis_state(FED, CAPPED, True)["sentence"] != \
        axis_state(FED, GLOBAL, True)["sentence"]


def test_throttling_outranks_a_healthy_feed():
    """The agent is reporting and being refused. The screen must show the
    refusal, not the reporting."""
    assert axis_state(FED, CAPPED, model_ready=True)["state"] == "throttled"


def test_a_dead_agent_outranks_throttling():
    """Nothing is arriving to be refused. Naming the quota would send the
    customer to the wrong problem."""
    assert axis_state(QUIET, CAPPED, model_ready=True)["state"] == "no_signal"


def test_day_one_draws_the_gates_unarmed():
    """With no model nothing is enforced. Gates drawn as live thresholds
    would be a promise the product is not keeping yet."""
    state = axis_state(FED, OPEN, model_ready=False)

    assert state["gates_armed"] is False
    assert "đang đo" in state["sentence"].lower()


def test_a_fed_and_modelled_tenant_has_armed_gates():
    assert axis_state(FED, OPEN, model_ready=True)["gates_armed"] is True


def test_no_sentence_contains_an_em_dash():
    """A standing instruction for every rendered string in this product."""
    for health in (FED, QUIET, NEVER):
        for throttle in (OPEN, CAPPED, GLOBAL):
            for ready in (True, False):
                assert "—" not in axis_state(health, throttle, ready)["sentence"]


def test_every_state_produces_a_sentence():
    """A state with no sentence is a screen that has gone quiet about why."""
    for health in (FED, QUIET, NEVER):
        for throttle in (OPEN, CAPPED, GLOBAL):
            for ready in (True, False):
                assert axis_state(health, throttle, ready)["sentence"].strip()
