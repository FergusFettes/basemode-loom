from basemode import ObservationContext

from basemode_loom.basemode_adapter import loom_observation


def test_loom_observation_contains_only_allow_listed_provenance(monkeypatch) -> None:
    monkeypatch.setattr("basemode_loom.basemode_adapter._loom_version", lambda: "1.2.3")

    observation = loom_observation()

    assert observation == ObservationContext(
        source="loom",
        source_version="1.2.3",
        contribution_eligible=False,
    )
    assert observation.verification_probe_id is None
