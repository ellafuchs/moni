import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import find_dotenv, load_dotenv

# One .env resolution for the whole app (bot, web, tests, gmail_auth): the nearest .env up
# the tree, falling back to the project root when none exists yet. Never overrides real env vars.
ENV_PATH = Path(find_dotenv() or Path(__file__).resolve().parent.parent / ".env")
load_dotenv(ENV_PATH)


class ConfigManager:
    """Manages application configuration.

    config.json holds ONLY non-secret data: mailing list, model name/provider, the
    master-file snapshot, the schedule and the program codes.

    Secrets come ONLY from the environment (loaded from the project-root .env) and are
    never read from or written to config.json. Any secret key found in an old
    config.json is ignored and dropped on the next save.
      OPENAI_API_KEY        -> get_api_key()  (model_provider openai / unset)
      GEMINI_API_KEY        -> get_api_key()  (model_provider google_genai)
      MONI_SENDER           -> get_notifier_email()
      GOOGLE_CLIENT_ID      -> get_google_client_id()
      GOOGLE_CLIENT_SECRET  -> get_google_client_secret()
      GOOGLE_REFRESH_TOKEN  -> get_google_refresh_token()
    """

    PROGRAMS_SHEET = "תוכניות"
    KEY = "קוד"
    PROGRAM_NAME = "שם תוכנית"

    def __init__(self, path: str):
        # Initialise values
        self.path = path
        self.ids: dict[str, str] = {}
        self.model_name: str | None = None
        self.model_provider: str | None = None
        self.model_fallback: str | None = None
        self.mailing_list: list[str] | None = None
        self.last_master_update: str | None = None
        self.last_master_filename: str | None = None
        self.schedule: list[dict] | None = None

        self._load()

    def _load(self) -> None:
        # Check that config file exists
        if not Path(self.path).is_file():
            raise FileNotFoundError(f"config not found: {self.path}")

        with open(self.path) as f:
            config = json.load(f)

        self.model_name = config.get("model_name")
        self.model_provider = config.get("model_provider")
        self.model_fallback = config.get("model_fallback")
        self.mailing_list = list(config.get("mailing_list") or [])
        self.last_master_update = config.get("last_master_update")
        self.last_master_filename = config.get("last_master_filename")
        self.schedule = config.get("schedule")

        self.ids = {}
        for program in config.get("programs") or []:
            self.ids[program.get("key")] = program.get("name")

    def _save(self) -> None:
        config = {
            "model_name": self.model_name,
            "model_provider": self.model_provider,
            "model_fallback": self.model_fallback,
            "mailing_list": self.mailing_list,
            "last_master_update": self.last_master_update,
            "last_master_filename": self.last_master_filename,
            "schedule": self.schedule,
            "programs": [],
        }

        for key, name in self.ids.items():
            config["programs"].append({"key": key, "name": name})

        with open(self.path, "w") as f:
            json.dump(config, f, indent=2)

    API_KEY_ENV_BY_PROVIDER = {
        "openai": "OPENAI_API_KEY",
        "google_genai": "GEMINI_API_KEY",
    }

    def get_api_key(self) -> str | None:
        """The LLM key for the configured provider, from the environment / .env only.

        Never read from or written to config.json. Unknown/unset provider -> OpenAI.
        """
        env_var = self.API_KEY_ENV_BY_PROVIDER.get(self.model_provider or "openai", "OPENAI_API_KEY")
        return os.environ.get(env_var) or None

    def get_model_name(self) -> str | None:
        return self.model_name

    def set_model_name(self, model_name: str) -> None:
        self.model_name = model_name
        self._save()

    def get_model_provider(self) -> str | None:
        return self.model_provider

    def get_model_fallback(self) -> str | None:
        """A second model of the same provider, used only when the first one is out of quota."""
        return self.model_fallback

    def set_model_fallback(self, model_fallback: str | None) -> None:
        self.model_fallback = model_fallback or None
        self._save()

    def set_model_provider(self, model_provider: str) -> None:
        self.model_provider = model_provider
        self._save()

    def get_mailing_list(self) -> list[str] | None:
        return self.mailing_list

    def set_mailing_list(self, mailing_list: list[str]) -> None:
        self.mailing_list = mailing_list
        self._save()

    @staticmethod
    def get_notifier_email() -> str | None:
        """Sending Gmail address, from MONI_SENDER in the environment / .env only."""
        return os.environ.get("MONI_SENDER") or None

    @staticmethod
    def get_google_client_id() -> str | None:
        """OAuth client id from the Google Cloud Console (GOOGLE_CLIENT_ID)."""
        return os.environ.get("GOOGLE_CLIENT_ID") or None

    @staticmethod
    def get_google_client_secret() -> str | None:
        """OAuth client secret from the Google Cloud Console (GOOGLE_CLIENT_SECRET)."""
        return os.environ.get("GOOGLE_CLIENT_SECRET") or None

    @staticmethod
    def get_google_refresh_token() -> str | None:
        """Refresh token written by bot/gmail_auth.py (GOOGLE_REFRESH_TOKEN)."""
        return os.environ.get("GOOGLE_REFRESH_TOKEN") or None

    def get_last_master(self) -> dict:
        return {
            "date": self.last_master_update,
            "name": self.last_master_filename,
        }

    def set_last_master(self, date: str, name: str) -> None:
        self.last_master_update = date
        self.last_master_filename = name
        self._save()

    def get_schedule(self) -> list[dict] | None:
        return self.schedule

    def set_schedule(self, schedule: list[dict]) -> None:
        self.schedule = schedule
        self._save()

    def get_ids(self) -> dict[str, str]:
        return self.ids

    @staticmethod
    def read_master_programs(path: str) -> dict[str, str]:
        """Read the Master תוכניות sheet -> {normalized 6-digit code: program name}.

        Read-only: does not touch or save config. Codes come from Excel as ints and are
        zero-padded to 6-digit strings so they match the codes BudgetLetter produces
        (e.g. 45211 -> "045211"). This is the full Master code set (FR-4) used by the
        relevance gate and the table's `master` column.

        Raises:
            FileNotFoundError: if `path` does not exist.
            ValueError: if the programs sheet or expected columns are missing.
        """
        if not Path(path).is_file():
            raise FileNotFoundError(f"master xlsx not found: {path}")

        try:
            df = pd.read_excel(path, sheet_name=ConfigManager.PROGRAMS_SHEET)
        except ValueError as e:
            raise ValueError(
                f"sheet '{ConfigManager.PROGRAMS_SHEET}' not found in master file"
            ) from e

        missing = {ConfigManager.KEY, ConfigManager.PROGRAM_NAME} - set(df.columns)
        if missing:
            raise ValueError(
                f"missing expected columns {missing} in sheet "
                f"'{ConfigManager.PROGRAMS_SHEET}'"
            )

        codes = df[ConfigManager.KEY].astype("Int64").astype(str).str.zfill(6)
        return dict(zip(codes, df[ConfigManager.PROGRAM_NAME]))

    @staticmethod
    def read_master_names(path: str) -> dict[str, str]:
        """{6-digit code: full program name} from the master workbook.

        The תוכניות sheet holds shortened names; the yearly fiscal sheet (its name starts
        with the year, e.g. '2026פיסקלי דיגיטלי ') has the full ones under 'קוד תכנית' /
        'שם תכנית'. Full names win, תוכניות names fill the gaps. Read-only; never raises
        for a missing sheet — returns what it could find.
        """
        names: dict[str, str] = {}
        try:
            xls = pd.ExcelFile(path)
        except Exception:  # noqa: BLE001 - no master, no names
            return names
        try:
            names.update(ConfigManager.read_master_programs(path))
        except Exception:  # noqa: BLE001
            pass
        fiscal = next((n for n in xls.sheet_names if str(n).strip().startswith("20")), None)
        if fiscal:
            try:
                df = pd.read_excel(path, sheet_name=fiscal, usecols=["קוד תכנית", "שם תכנית"])
                for code, name in zip(df["קוד תכנית"], df["שם תכנית"]):
                    code = str(code).strip()
                    if code.isdigit() and isinstance(name, str) and name.strip():
                        names.setdefault(code.zfill(6), name.strip())
                        names[code.zfill(6)] = name.strip()
            except Exception:  # noqa: BLE001 - keep the short names
                pass
        return names

    def load_master(self, path: str) -> None:
        """Load program ids from the master file into config and save.

        Delegates the read/normalize to read_master_programs (FR-4), then persists.

        Raises:
            FileNotFoundError: if `path` does not exist.
            ValueError: if the programs sheet or expected columns are missing.
        """
        self.ids = ConfigManager.read_master_programs(path)
        self.last_master_update = datetime.now().strftime("%d/%m/%Y")
        self.last_master_filename = Path(path).name
        self._save()


if __name__ == "__main__":
    config = ConfigManager("./files/config.json")
    config.load_master("./files/master.xlsx")

    # print(config.get_api_key(), config.get_model_name())
