from next_whistle_shared.contracts.agent import BoundContext
from next_whistle_shared.evidence.dispatcher import EvidenceLedger
from next_whistle_shared.validation.evidence_validator import EvidenceValidator

SQUADS = {"p_1": {"name": "Nia Cole"}, "t_1": {"name": "Tarnhaven Town"}}


def validator():
    context = BoundContext(
        request_id="req_v", match_id="m_rvr_hrb", dataset_version="1.3.0", rules_version="1.2.0", cutoff_ms=4_600_000,
        evidence_cutoff_ms=4_600_000, intent="explain", agent="storyteller",
    )
    return EvidenceValidator(context, EvidenceLedger(), SQUADS)


def issues_for(text: str, field: str = "what_happened") -> list[str]:
    from next_whistle_shared.contracts.agent import Explanation

    fields = {"headline": "Plain headline", "what_happened": "Plain words.", "why_it_matters": "It matters.", "uncertainty": ""}
    fields[field] = text
    answer = Explanation(**fields)
    return [i.check_id for i in validator().validate(answer).issues if i.check_id in {"V10", "V5"}]


def test_a_title_case_headline_is_not_read_as_a_name_and_a_phrase_with_a_real_name_is_accepted():
    assert issues_for("Why Nia Cole's goal was disallowed", "headline") == []
    assert issues_for("Disallowed Goal For Offside", "headline") == []
    assert issues_for("Why Nia Cole was offside") == []
    assert issues_for("Tarnhaven Town pushed forward") == []


def test_an_invented_name_in_the_body_is_still_rejected():
    assert issues_for("Then Nia Smith scored") == ["V10"]
    assert issues_for("Jalen Brooks was booked") == ["V10"]


def test_ordinal_and_spoken_minutes_are_not_stray_numbers_but_other_digits_still_are():
    assert issues_for("It happened in the 61st minute.") == []
    assert issues_for("It happened in minute 61.") == []
    assert issues_for("After 61 minutes of play.") == []
    assert issues_for("The attacker was 88.6 metres out.") == ["V5"]


def test_a_law_number_is_a_reference_not_a_stray_number():
    assert issues_for("The referee applied Law 11 here.") == []
    assert issues_for("It was 11 metres out.") == ["V5"]


def issues_all(text: str, field: str = "what_happened") -> list[str]:
    from next_whistle_shared.contracts.agent import Explanation

    fields = {"headline": "Plain headline", "what_happened": "Plain words.", "why_it_matters": "It matters.", "uncertainty": ""}
    fields[field] = text
    return [i.check_id for i in validator().validate(Explanation(**fields)).issues if i.check_id in {"V10", "V19"}]


def test_sentence_starting_words_are_not_names_but_a_surname_in_a_sentence_is_checked():
    assert issues_all("Under Law eleven, Nia Cole was offside.") == []
    assert issues_all("The referee applied the Laws of the Game.") == []
    assert issues_all("She passed to Brooks.") == ["V10"]


def test_internal_field_names_and_ids_never_reach_the_viewer():
    assert issues_all("The withinUncertainty flag is false.") == ["V19"]
    assert issues_all("See e_m_rvr_hrb_01290 for the details.") == ["V19"]
    assert issues_all("Checked marginM and geometryRef.") == ["V19"]
    assert issues_all("The margin was too close to call.") == []
