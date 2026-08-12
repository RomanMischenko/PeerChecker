import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if available
load_dotenv()


def parse_admin_ids(raw_value: str) -> list[int]:
    """Parse comma-separated string of Telegram admin user IDs."""
    if not raw_value:
        return []
    result = []
    for item in raw_value.split(","):
        cleaned = item.strip()
        if cleaned.isdigit():
            result.append(int(cleaned))
    return result


def parse_coalitions(raw_value: str) -> dict[int, str]:
    """
    Parse coalition string into dict mapping ID to Name.
    Expected format: "604:Northern,605:Powder,606:Secret"
    """
    default_coalitions = {
        604: "Northern",
        605: "Powder",
        606: "Secret",
    }
    if not raw_value:
        return default_coalitions

    coalitions = {}
    items = raw_value.split(",")
    for item in items:
        if ":" in item:
            parts = item.strip().split(":", 1)
            cid_str, cname = parts[0].strip(), parts[1].strip()
            if cid_str.isdigit() and cname:
                coalitions[int(cid_str)] = cname

    return coalitions if coalitions else default_coalitions


def parse_project_ids(raw_value: str) -> list[int]:
    """Parse comma-separated string of project IDs."""
    default_ids = [73187, 73188, 73189, 73328, 73190, 73191, 73192, 73193, 73194, 73195, 73196]
    if not raw_value:
        return default_ids

    result = []
    for item in raw_value.split(","):
        cleaned = item.strip()
        if cleaned.isdigit():
            result.append(int(cleaned))
    return result if result else default_ids


def parse_class_names(raw_value: str) -> list[str]:
    """Parse comma-separated string of target class/wave names."""
    if not raw_value:
        return []
    return [item.strip().upper() for item in raw_value.split(",") if item.strip()]


def _get_env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_env_float(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Config:
    TELEGRAM_BOT_TOKEN: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    TELEGRAM_ADMIN_IDS: list[int] = field(
        default_factory=lambda: parse_admin_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
    )
    S21_LOGIN: str = field(
        default_factory=lambda: os.getenv("S21_LOGIN", "").strip()
    )
    S21_PASSWORD: str = field(
        default_factory=lambda: os.getenv("S21_PASSWORD", "").strip()
    )
    CAMPUS_ID: str = field(
        default_factory=lambda: os.getenv(
            "CAMPUS_ID", "5a23bec9-f989-485d-935b-3f0dc61c4812"
        ).strip()
    )
    TARGET_COALITIONS: dict[int, str] = field(
        default_factory=lambda: parse_coalitions(os.getenv("TARGET_COALITIONS", ""))
    )
    CHECK_INTERVAL_MINUTES: int = field(
        default_factory=lambda: _get_env_int("CHECK_INTERVAL_MINUTES", 60)
    )
    MIN_XP: int = field(
        default_factory=lambda: _get_env_int("MIN_XP", 0)
    )
    MIN_LOGTIME: float = field(
        default_factory=lambda: _get_env_float("MIN_LOGTIME", 0.0)
    )
    TARGET_PROJECT_IDS: list[int] = field(
        default_factory=lambda: parse_project_ids(os.getenv("TARGET_PROJECT_IDS", ""))
    )
    MIN_ACCEPTED_PROJECTS: int = field(
        default_factory=lambda: _get_env_int("MIN_ACCEPTED_PROJECTS", 3)
    )
    TARGET_CLASS_NAME: str = field(
        default_factory=lambda: os.getenv("TARGET_CLASS_NAME", "").strip()
    )

    @property
    def target_class_names(self) -> list[str]:
        return parse_class_names(self.TARGET_CLASS_NAME)

    BASE_DIR: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)

    DATA_DIR: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", "data"))
    )

    @property
    def db_path(self) -> Path:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return self.DATA_DIR / "peers.db"

    @property
    def log_file(self) -> Path:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return self.DATA_DIR / "app.log"

    def validate(self) -> None:
        """Ensure critical config fields are provided."""
        missing = []
        if not self.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.S21_LOGIN:
            missing.append("S21_LOGIN")
        if not self.S21_PASSWORD:
            missing.append("S21_PASSWORD")

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


# Single instance
config = Config()
