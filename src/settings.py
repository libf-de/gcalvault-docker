from __future__ import annotations

import json
import os
from types import SimpleNamespace as Namespace

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .gcalvault import Calendar


class Settings:

    @classmethod
    def is_docker(cls):
        return os.environ.get("IS_DOCKER", False)

    @classmethod
    def get_root_dir(cls):
        return "/app" if cls.is_docker() else os.path.join(os.path.expanduser("~"), ".gcalvault")

    @classmethod
    def get_default_conf_dir(cls):
        return os.path.join(cls.get_root_dir(), "conf")

    @classmethod
    def get_configfile(cls):
        return os.path.join(cls.get_default_conf_dir(), "config.json")

    @classmethod
    def get_configfile(cls, config_dir):
        return os.path.join(config_dir, "config.json")

    @classmethod
    def get_default_output_dir(cls):
        return os.path.join(cls.get_root_dir(), "output")

    @classmethod
    def get_default_ssh_key(cls):
        return os.path.join(cls.get_root_dir(), "ssh-key")

    DEFAULT_CLIENT_ID = "261805543927-7p1s5ee657kg0427vs2r1f90dins6hdd.apps.googleusercontent.com"
    DEFAULT_CLIENT_SECRET = "pLKRSKrIIWw7K-CD1DWWV2_Y"

    # self.export_only = (os.getenv("EXPORT_ONLY") or "false").lower() == "true"
    #         self.ignore_roles.append((os.getenv("IGNORE_ROLES") or "").split(","))
    #         self.conf_dir = os.getenv("CONF_DIR") or self.conf_dir
    #         self.output_dir = os.getenv("OUTPUT_DIR") or self.output_dir
    #         self.client_id = os.getenv("CLIENT_ID") or self.client_id
    #         self.client_secret = os.getenv("CLIENT_SECRET") or self.client_secret
    #         self.command = os.getenv("TASK_COMMAND") or self.command
    #         self.push_repo = (os.getenv("PUSH_REPO") or "false").lower() == "true"
    #         self.no_cache = (os.getenv("NO_CACHE") or "false").lower() == "true"
    def __init__(self, username: str, client_id: str | None, client_secret: str | None,
                 calendars: list[Calendar] | None, ignored_roles: list[str] | None,
                 output_dir: str | None, conf_dir: str | None,
                 push_repo: bool, ssh_key_dir: str | None,
                 command: str | None):
        self.username = username
        self.client_id = client_id or Settings.DEFAULT_CLIENT_ID
        self.client_secret = client_secret or Settings.DEFAULT_CLIENT_ID
        self.calendars = calendars or []
        self.ignored_roles = ignored_roles or []
        self.output_dir = output_dir or Settings.get_default_output_dir()
        self.push_repo = push_repo
        self.ssh_key_dir = ssh_key_dir or Settings.get_default_ssh_key()
        self.conf_dir = conf_dir or Settings.get_default_conf_dir()
        self.command = command or 'sync'
        self.always_update = False

    def to_json(self):
        return json.dumps(self, default=lambda o: o.__dict__)

    @staticmethod
    def from_json(input_json) -> Settings:
        return json.load(input_json, object_hook=lambda d: Namespace(**d))

    @classmethod
    def default_settings(cls):
        return cls('', None, None, None, None, None, False, None, None, None)
