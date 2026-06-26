import json
from datetime import datetime
from pathlib import Path

import pandas as pd


class ConfigManager:
    """Manages application configuration"""

    PROGRAMS_SHEET = "תוכניות"
    KEY = "קוד"
    PROGRAM_NAME = "שם תוכנית"

    def __init__(self, path: str):
        # Initialise values
        self.path = path
        self.ids: dict[str, str] = {}
        self.api_key: str | None = None
        self.model_name: str | None = None
        self.mailing_list: list[str] | None = None
        self.notifier_email: str | None = None
        self.notifier_password: str | None = None
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

        self.api_key = config.get("api_key")
        self.model_name = config.get("model_name")
        self.mailing_list = config.get("mailing_list")
        self.notifier_email = config.get("notifier").get("email")
        self.notifier_password = config.get("notifier").get("password")
        self.last_master_update = config.get("last_master_update")
        self.last_master_filename = config.get("last_master_filename")
        self.schedule = config.get("schedule")

        self.ids = {}
        for program in config.get("programs"):
            self.ids[program.get("key")] = program.get("name")

    def _save(self) -> None:
        config = {
            "api_key": self.api_key,
            "model_name": self.model_name,
            "mailing_list": self.mailing_list,
            "notifier": {
                "email": self.notifier_email,
                "password": self.notifier_password,
            },
            "last_master_update": self.last_master_update,
            "last_master_filename": self.last_master_filename,
            "schedule": self.schedule,
            "programs": [],
        }

        for key, name in self.ids.items():
            config["programs"].append({"key": key, "name": name})

        with open(self.path, "w") as f:
            json.dump(config, f, indent=2)

    def get_api_key(self) -> str | None:
        return self.api_key

    def set_api_key(self, key: str) -> str | None:
        self.api_key = key
        self._save()

    def get_model_name(self) -> str | None:
        return self.model_name

    def set_model_name(self, model_name: str) -> None:
        self.model_name = model_name
        self._save()

    def get_mailing_list(self) -> list[str] | None:
        return self.mailing_list

    def set_mailing_list(self, mailing_list: list[str]) -> None:
        self.mailing_list = mailing_list
        self._save()

    def get_notifier_email(self) -> str | None:
        return self.notifier_email

    def set_notifier_email(self, notifier_email: str) -> None:
        self.notifier_email = notifier_email
        self._save()

    def get_notifier_password(self) -> str | None:
        return self.notifier_password

    def set_notifier_password(self, notifier_password: str) -> None:
        self.notifier_password = notifier_password
        self._save()

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

    def load_master(self, path: str) -> None:
        """
        Load program ids from master_xlsx_path and store them in the config gile.

        Raises:
            FileNotFoundError: if master_xlsx_path does not exist.
            ValueError: if the programs sheet or expected columns are missing.
        """

        if not Path(path).is_file():
            raise FileNotFoundError(f"master xlsx not found: {path}")

        try:
            df = pd.read_excel(path, sheet_name=self.PROGRAMS_SHEET)
        except ValueError as e:
            raise ValueError(
                f"sheet '{self.PROGRAMS_SHEET}' not found in master file"
            ) from e

        missing = {self.KEY, self.PROGRAM_NAME} - set(df.columns)
        if missing:
            raise ValueError(
                f"missing expected columns {missing} in sheet '{self.PROGRAMS_SHEET}'"
            )

        # Codes come from Excel as ints; zero-pad to 6-digit strings so they match
        # the codes produced by PdfTableExtractor (e.g. 45211 -> "045211").
        codes = df[self.KEY].astype("Int64").astype(str).str.zfill(6)
        self.ids = dict(zip(codes, df[self.PROGRAM_NAME]))

        self.last_master_update = datetime.now().strftime("%d/%m/%Y")
        self.last_master_filename = Path(path).name

        self._save()


if __name__ == "__main__":
    config = ConfigManager("./files/config.json")
    config.load_master("./files/master.xlsx")

    # print(config.get_api_key(), config.get_model_name())
