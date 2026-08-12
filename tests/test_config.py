from app.config import parse_wave_projects_from_env, Config


def test_parse_wave_projects_from_env():
    env_mock = {
        "TARGET_PROJECT_IDS_25_04_NN": "19172, 19173, 19174",
        "TARGET_PROJECT_IDS_26_04_NN": "73187, 73188",
        "SOME_OTHER_VAR": "123",
    }
    res = parse_wave_projects_from_env(env_mock)
    assert res == {
        "25_04_NN": [19172, 19173, 19174],
        "26_04_NN": [73187, 73188],
    }


def test_config_wave_projects_property(monkeypatch):
    monkeypatch.setenv("TARGET_PROJECT_IDS_26_08_NN", "73187,73188,73189")
    cfg = Config()
    assert cfg.wave_projects.get("26_08_NN") == [73187, 73188, 73189]
