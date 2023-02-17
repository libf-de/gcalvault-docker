from __future__ import annotations

import argparse
import os
import glob
import re
from datetime import datetime

import requests
import urllib.parse
import pathlib
from getopt import gnu_getopt, GetoptError
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from .google_oauth2 import GoogleOAuth2
from .git_vault_repo import GitVaultRepo
from .etag_manager import ETagManager
from .settings import Settings

# from deprecated import deprecated

# Note: OAuth2 auth code flow for "installed applications" assumes the client secret
# cannot actually be kept secret (must be embedded in application/source code).
# Access to user data must be consented by the user and [more importantly] the
# access & refresh tokens are stored locally with the user running the program.
DEFAULT_CLIENT_ID = "261805543927-7p1s5ee657kg0427vs2r1f90dins6hdd.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "pLKRSKrIIWw7K-CD1DWWV2_Y"
OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.readonly",
]

GOOGLE_CALDAV_URI_FORMAT = "https://apidata.googleusercontent.com/caldav/v2/{cal_id}/events"

COMMANDS = ['sync', 'noop']

dirname = os.path.dirname(__file__)
usage_file_path = os.path.join(dirname, "USAGE.txt")
version_file_path = os.path.join(dirname, "VERSION.txt")

conf_dir = "/app/config/"


class Gcalvault:

    def __init__(self, google_oauth2=None, google_apis=None):
        self.config: Settings = Settings.default_settings()
        self.auth: Credentials | None = None
        self._repo = None
        self._google_oauth2 = google_oauth2 if google_oauth2 is not None else GoogleOAuth2()
        self._google_apis = google_apis if google_apis is not None else GoogleApis()

        self.export_only = False

    def load_settings(self, args):
        if Settings.is_docker() and os.path.exists(Settings.get_configfile()):
            self.config = Settings.from_json(open(Settings.get_configfile()))
            return

        print("[D] Arguments:")
        print(args)

        p = argparse.ArgumentParser(
            prog='GCalVault',
            description='Backups your Google calendars into a Git repository of ics files',
            epilog='Please note that this program is optimized to run in a docker container'
        )

        p.add_argument('--export-only', '-e', action='store_true', required=False)
        p.add_argument('--push', '-p', action='store_true', required=False)
        p.add_argument('--ignore-role', '-i', action='extend', required=False, nargs='*')
        p.add_argument('--calendar', '-k', action='extend', required=False, nargs='*')
        p.add_argument('--conf-dir', '-c', default=Settings.get_default_conf_dir(), required=False, type=str)
        p.add_argument('--output-dir', '-o', default=Settings.get_default_output_dir(), required=False, type=str)
        p.add_argument('--client-id', default=Settings.DEFAULT_CLIENT_ID, required=False, type=str)
        p.add_argument('--client-secret', default=Settings.DEFAULT_CLIENT_SECRET, required=False, type=str)
        p.add_argument('--ssh-key', default=Settings.get_default_ssh_key(), required=False, type=str)
        p.add_argument('--username', default='', required=False)
        p.add_argument('command', nargs='?', default="AUTO", choices=['AUTO','sync', 'setup', 'test', 'clean-sync'])

        ap = p.parse_args(args)

        # If we got here, don't want to "setup" and are running in docker, exit with error
        if ap.command != 'setup' and Settings.is_docker():
            print("[CRIT] Running in docker, and no config file found! Please setup the container "
                  "first (see README.md)")
            exit(1)

        # If there is a config file, load configuration from there
        conf_file = Settings.get_configfile(ap.conf_dir)
        if os.path.exists(conf_file):
            self.config = Settings.from_json(open(conf_file))
        else:
            self.config = Settings(ap.username, ap.client_id, ap.client_secret, [], ap.ignore_role,
                                   ap.output_dir, ap.push, ap.ssh_key, ap.conf_dir, ap.command)
            if ap.command != 'setup':
                # Load user calendar and populate the configuration array if not setting up
                self._load_calendars_from_commandline(ap.calendar)

        self.export_only = ap.export_only

        # If no command specified on commandline, run the last one read from config
        if ap.command != 'AUTO':
            self.config.command = ap.command

    def run(self, cli_args):
        self.load_settings(cli_args)
        self._ensure_dirs()

        assert self.config is not None

        # Process commands, sync being the default
        if self.config.command == "clean-sync":
            self._clean_output_dir()
            self.sync()
        elif self.config.command == "setup":
            self.setup()
        elif self.config.command == "test":
            try:
                if self._get_oauth2_credentials() is None:
                    print("[CRIT] Failed to authenticate!")
                    exit(1)
                else:
                    print("[INFO] Authenticated successfully!")
                    exit(0)
            except Exception as e:
                print("[CRIT] Failed to authenticate:")
                print(e)
                exit(1)
        else:
            self.sync()

    def setup(self):
        print("  ┌─────────────────────────────────┐")
        print("  │          GCalVault Setup        │")
        print("  └─────────────────────────────────┘")
        print("")
        if not os.access(self.config.conf_dir, os.W_OK):
            print(
                f"Config dir ({self.config.conf_dir}) is not writable! Change permissions, or specify custom config directory with -c.")
            exit(1)
        if self._rlinput("Use custom Client ID/Secret for OAuth? (Y/n): ",
                         "Y" if self.config.client_id == Settings.DEFAULT_CLIENT_ID else "N") != "n":
            self.config.client_id = self._rlinput(
                "Client ID: ",
                self.config.client_id if self.config.client_id != Settings.DEFAULT_CLIENT_ID
                else "")

            self.config.client_secret = self._rlinput(
                "Client Secret: ",
                self.config.client_secret if self.config.client_secret != Settings.DEFAULT_CLIENT_SECRET
                else "")
        self.config.username = self._rlinput("Google account email: ", self.config.username)
        print("  ┌───────────────────────────────────────────────┐")
        print("  │Authenticating with Google                     │")
        print("  ├───────────────────────────────────────────────┤")
        print("  │If you login for the first time with the spec- │")
        print("  │specified account, a url will be printed to the│")
        print("  │terminal. Open it in a browser and login with  │")
        print("  │the email you specified, and grant access to   │")
        print("  │your calendars. If you get an 'Invalid Request'│")
        print("  │error, please specify custom Client ID/Secret. │")
        print("  │If you get 'An unknown exception occurred', try│")
        print("  │to use Chrome/Chromium.                        │")
        print("  └───────────────────────────────────────────────┘")
        self._ensure_auth()
        assert self.auth is not None
        print(" Fetching calendars...")
        print("")
        self._fetch_calendars()
        print("")
        print("Your calendars: [index | name ]")
        cal_dict = dict(enumerate(self.config.calendars))
        for cal_idx in cal_dict.keys():
            print(f"{cal_idx} | {cal_dict.get(cal_idx).name}")
        print("Enter the indices of calendars to backup, comma separated")
        to_use = input("OR leave empty to backup all calendars: ")

        if to_use != "":
            self.config.calendars = []
            for use_idx in to_use.split(","):
                self.config.calendars.append(cal_dict.get(use_idx))
            print("Backing up selected calendars.")
        else:
            print("Backing up ALL calendars.")

        print("")
        if input("Change advanced settings? (y/N):").lower() == "y":
            valid_outp_dir = False

            print("---( Output directory )---")
            print(
                "Directory where the backup repository is stored. Must be writeable, will prompt to create if not exists.")
            while not valid_outp_dir:
                outp_dir = self._rlinput("Output directory? ('d' for default, current shown): ", self.config.output_dir)
                if outp_dir.lower() == "d":
                    self.config.output_dir = Settings.get_default_output_dir()
                    print(f"Using default output directory: {self.config.output_dir}")
                elif not os.path.isdir(outp_dir):
                    if os.access(os.path.dirname(outp_dir), os.W_OK):
                        if input("Specified directory does not exist! Create it? (Y/n)") != "n":
                            try:
                                os.makedirs(outp_dir)
                            except:
                                print("Could not create directory!")
                            self.config.output_dir = outp_dir
                            valid_outp_dir = True
                    else:
                        print("Directory does not exist, and parent directory is not writable!")
                elif not os.access(outp_dir, os.W_OK):
                    print("Directory is not writable!")
                else:
                    assert os.path.isdir(outp_dir) and os.access(outp_dir, os.W_OK)
                    self.config.output_dir = outp_dir
                    valid_outp_dir = True

            print("---( Push repository )---")
            print(
                "If you've added a remote to the backup git repository, you can enable pushing the repo after every backup.")
            self.config.push_repo = self._rlinput("Push repository? (y/N)",
                                                  "y" if self.config.push_repo else "n") == "y"
            print("")

            print("")
            print("---( Ignored roles )---")
            print(
                "Ignore calendars where user has specified roles, e.g. if you enter »reader«, only writeable calendars will be backed up. Leave empty to sync all calendars, separate multiple with comma (,).")
            self.config.ignored_roles = self._rlinput("Ignored roles: ", ",".join(self.config.ignored_roles)).split(",")
            print("")

            print("")
            print("---( Always update )---")
            print(
                "Always get list of all calenders on every backup? If enabled, new calendars will be backed up automatically without user action, BUT will also override selected calendars (will always backup all calendars)!")
            self.config.always_update = self._rlinput("Always update? (y/N)",
                                                      "y" if self.config.always_update else "n") == "y"
            print("")

            print("---( Default action )---")
            print("Seems unnecessary, but anyway... possible values:")
            print("sync --> (default) backup calendars into git repository")
            print("clean-sync  --> removes the output directory and backs up calendars")
            print("setup --> Starts setup??")
            print("test --> exits with 0 if authentication is successful, exit with 1 otherwise (for healthcheck)")
            self.config.command = self._rlinput("Default action? :", "sync")
            print("")

        cf = open(Settings.get_configfile(self.config.conf_dir), "w")
        cf.write(self.config.to_json())
        cf.close()

        print("Successfully created config file.")
        if Settings.is_docker():
            print("You may now start the container normally.")
        exit(0)

    def sync(self):
        self._ensure_dirs()
        credentials = self._get_oauth2_credentials()

        if not self.export_only:
            self._repo = GitVaultRepo("gcalvault", self.config.output_dir, [".ics"])

        # if self.no_cache and os.path.exists(os.path.join(self.conf_dir, ".etags")): # TODO: Do properly :(
        #    os.remove(os.path.join(self.conf_dir, ".etags"))

        # calendars = self._get_calendars_singular(credentials)

        # if self.ignore_roles:
        #    calendars = [cal for cal in calendars if cal.access_role not in self.ignore_roles]

        # if self.includes:
        #    calendars = [cal for cal in calendars if cal.id in self.includes]

        # cal_ids = [cal.id for cal in calendars]
        # for include in self.includes:
        #    if include not in cal_ids:
        #        raise GcalvaultError(f"Specified calendar '{include}' was not found")

        self._dl_and_save_calendars()

        if self._repo:
            self._repo.commit(f"gcalvault sync on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
            if self.config.push_repo:
                self._repo.push()

    @staticmethod
    def usage():
        return pathlib.Path(usage_file_path).read_text().strip()

    @staticmethod
    def version():
        return pathlib.Path(version_file_path).read_text().strip()

    # @deprecated() # TODO: Move to (docker) setup
    # def _fetch_env(self):
    #    print(os.environ)
    #    self.export_only = (os.getenv("EXPORT_ONLY") or "false").lower() == "true"
    #    self.ignore_roles.append((os.getenv("IGNORE_ROLES") or "").split(","))
    #    self.conf_dir = os.getenv("CONF_DIR") or self.conf_dir
    #    self.output_dir = os.getenv("OUTPUT_DIR") or self.output_dir
    #    self.client_id = os.getenv("CLIENT_ID") or self.client_id
    #    self.client_secret = os.getenv("CLIENT_SECRET") or self.client_secret
    #    self.command = os.getenv("TASK_COMMAND") or self.command
    #    self.push_repo = (os.getenv("PUSH_REPO") or "false").lower() == "true"
    #    self.no_cache = (os.getenv("NO_CACHE") or "false").lower() == "true"

    @staticmethod
    def _rlinput(prompt: str, prefill: str = '') -> str:
        """
        Input method with default value
        TODO: Ensure pyreadline on mac and windows
        :param prompt: Text of the prompt
        :param prefill: Default value
        :return: String
        """
        import readline
        readline.set_startup_hook(lambda: readline.insert_text(prefill))
        try:
            return input(prompt)  # or raw_input in Python 2
        finally:
            readline.set_startup_hook()

    # @deprecated()
    # def _parse_options(self, cli_args):
    #     show_help = show_version = authenticate = False
    #
    #     try:
    #         (opts, pos_args) = gnu_getopt(
    #             cli_args,
    #             'aefi:c:o:h',
    #             ['export-only', 'clean', 'ignore-role=',
    #              'conf-dir=', 'output-dir=', 'vault-dir=',
    #              'client-id=', 'client-secret=',
    #              'help', 'version', 'auth', 'push', 'no-cache', ]
    #         )
    #     except GetoptError as e:
    #         raise GcalvaultError(e) from e
    #
    #     for opt, val in opts:
    #         if opt in ['-e', '--export-only']:
    #             self.export_only = True
    #         # elif opt in ['-p', '--push']:
    #         # self.push_repo = True
    #         # elif opt in ['--no-cache']:
    #         # self.no_cache = True
    #         # elif opt in ['-i', '--ignore-role']:
    #         # self.ignore_roles.append(val.lower())
    #         # elif opt in ['-c', '--conf-dir']:
    #         # self.conf_dir = val
    #         # self.userfile_path = os.path.join(self.conf_dir, '.user')
    #         # self.client_id_file = os.path.join(self.conf_dir, '.client-id')
    #         # self.client_secret_file = os.path.join(self.conf_dir, '.client-secret')
    #         # elif opt in ['-o', '--output-dir', '--vault-dir']:
    #         # self.output_dir = val
    #         # elif opt in ['--client-id']:
    #         # self.client_id = val
    #         # elif opt in ['--client-secret']:
    #         # self.client_secret = val
    #         elif opt in ['-h', '--help']:
    #             show_help = True
    #         elif opt in ['-a', '--auth']:
    #             authenticate = True
    #         elif opt in ['--version']:
    #             show_version = True
    #
    #     if len(opts) == 0 and len(pos_args) == 0:
    #         show_help = True
    #
    #     if show_help:
    #         print(self.usage())
    #         return False
    #     if show_version:
    #         print(self.version())
    #         return False
    #     # if len(pos_args) >= 1:
    #     # self.command = pos_args[0]
    #     # if len(pos_args) >= 2:
    #     # self.config.user = pos_args[1].lower().strip()
    #     # elif os.path.exists(self.userfile_path):
    #     # self.config.user = ''.join(open(self.config.userfile_path).readlines())
    #     if authenticate:
    #         self._authenticate()
    #         return False
    #     # for arg in pos_args[2:]:
    #     # self.includes.append(arg.lower())
    #
    #     # if self.command is None:
    #     # raise GcalvaultError("<command> argument is required")
    #     # if self.command not in COMMANDS:
    #     # raise GcalvaultError("Invalid <command> argument")
    #     # if self.config.user is None:
    #     #    raise GcalvaultError("<user> argument is required")
    #
    #     return True

    # @deprecated()
    # def _authenticate(self):
    #     """
    #     Prompt user for email and authenticate with Google,
    #     to ensure credentials for headless operation
    #     :return: none
    #     """
    #     self._ensure_dirs()
    #     token_file_path = os.path.join(self.config.conf_dir, f"{self.config.username}.token.json")
    #     if os.path.exists(token_file_path):
    #         print(f"Removing existing configuration {self.config.username}.token.json...")
    #         os.remove(token_file_path)
    #     self.config.username = None
    #     while self.config.username is None:
    #         self.config.username = input("Enter your google account email: ")
    #         if re.fullmatch(r"([A-Za-z0-9]+[.-_])*[A-Za-z0-9]+@[A-Za-z0-9-]+(\.[A-Z|a-z]{2,})+",
    #                         self.config.username) is None:
    #             print("Invalid email specified!")
    #             self.config.user = None
    #     if self._get_oauth2_credentials() is not None:
    #         print("Authenticated successfully!")

    def _ensure_dirs(self):
        """
        Ensure working directories (config and output) are existant
        :return: none
        """
        for directory in [self.config.conf_dir, self.config.output_dir]:
            pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

    def _ensure_auth(self):
        self._ensure_dirs()
        token_file_path = os.path.join(self.config.conf_dir, f"{self.config.username}.token.json")

        (credentials, new_authorization) = self._google_oauth2.get_credentials(token_file_path, self.config.client_id,
                                                                               self.config.client_secret, OAUTH_SCOPES,
                                                                               self.config.username)
        if new_authorization:
            user_info = self._google_oauth2.request_user_info(credentials)
            profile_email = user_info['email'].lower().strip()
            if self.config.username != profile_email:
                if os.path.exists(token_file_path):
                    os.remove(token_file_path)
                raise GcalvaultError(
                    f"Authenticated user - {profile_email} - was different than <user> argument specified")

        self.auth = credentials

    #deprecated?
    def _get_oauth2_credentials(self):
        token_file_path = os.path.join(self.config.conf_dir, f"{self.config.user}.token.json")

        (credentials, new_authorization) = self._google_oauth2 \
            .get_credentials(token_file_path, self.config.client_id, self.config.client_secret, OAUTH_SCOPES,
                             self.config.user)

        if new_authorization:
            user_info = self._google_oauth2.request_user_info(credentials)
            profile_email = user_info['email'].lower().strip()
            if self.config.user != profile_email:
                if os.path.exists(token_file_path):
                    os.remove(token_file_path)
                raise GcalvaultError(
                    f"Authenticated user - {profile_email} - was different than <user> argument specified")
            with open(self.config.userfile_path, 'w') as f:
                f.write(self.config.user)
                f.close()

        return credentials

    def _load_calendars_from_commandline(self, calendar_ids):
        self._ensure_dirs()
        self._ensure_auth()
        assert self.auth is not None

        calendar_list = self._google_apis.request_cal_list(self.auth)
        for item in filter(lambda cid: cid in calendar_ids, calendar_list):
            self.config.calendars.append(Calendar(item['id'], item['summary'], 'NUL', item['accessRole']))

        # TODO: Remove duplicates!

    def _fetch_calendars(self):
        assert self.auth is not None
        if len(self.config.calendars) == 0:  # TODO: Maybe disable cache here?
            calendar_list = self._google_apis.request_cal_list(self.auth)
            for item in calendar_list['items']:
                self.config.calendars.append(Calendar(item['id'], item['summary'], 'NUL', item['accessRole']))
        for cal in self.config.calendars:
            cal.etag = self._google_apis.request_cal_etag(self.auth, cal.id)

    # @deprecated()
    # def _get_calendars(self, credentials):
    #     calendars = []
    #     calendar_list = self._google_apis.request_cal_list(credentials)
    #     for item in calendar_list['items']:
    #         cal_details = self._google_apis.request_cal_details(credentials, item['id'])
    #         calendars.append(
    #             Calendar(item['id'], item['summary'], cal_details['etag'], item['accessRole']))
    #     return calendars

    # @deprecated()
    # def _get_calendars_singular(self, credentials):
    #     """
    #     Updates the etag in the stored calendar list
    #     :param credentials: Google API credentials
    #     :return: list<Calendar>
    #     """
    #     if len(self.config.calendars) == 0:
    #         self.config.calendars = self._get_calendars(credentials)
    #         return self.config.calendars
    #     for calendar in self.config.calendars:
    #         cal_details = self._google_apis.request_cal_details(credentials, calendar.id)
    #         calendar.etag = cal_details['etag']
    #     return self.config.calendars

    def _clean_output_dir(self):
        cal_file_names = [cal.file_name for cal in self.config.calendars]
        file_names_on_disk = [os.path.basename(file).lower() for file in
                              glob.glob(os.path.join(self.config.output_dir, "*.ics"))]
        for file_name_on_disk in file_names_on_disk:
            if file_name_on_disk not in cal_file_names:
                os.remove(os.path.join(self.config.output_dir, file_name_on_disk))
                if self._repo:
                    self._repo.remove_file(file_name_on_disk)
                print(f"Removed file '{file_name_on_disk}'")

    def _dl_and_save_calendars(self):
        assert self.auth is not None

        etags = ETagManager(self.config.conf_dir)  # TODO: Remove?
        for calendar in (self.config.calendars if not self.config.ignored_roles
        else filter(lambda c: c.access_role not in self.config.ignored_roles, self.config.calendars)):
            self._dl_and_save_calendar(calendar, etags)

    def _dl_and_save_calendar(self, calendar, etags):
        assert self.auth is not None

        cal_file_path = os.path.join(self.config.output_dir, calendar.file_name)

        etag_changed = etags.test_for_change_and_save(calendar.id, calendar.etag)
        if os.path.exists(cal_file_path) and not etag_changed:
            print(f"Calendar '{calendar.name}' is up to date")
            return

        print(f"Downloading calendar '{calendar.name}'")
        ical = self._google_apis.request_cal_as_ical(calendar.id, self.auth)

        with open(cal_file_path, 'w') as file:
            file.write(ical)
        print(f"Saved calendar '{calendar.id}'")

        if self._repo:
            self._repo.add_file(calendar.file_name)


class GcalvaultError(RuntimeError):
    pass


class Calendar:

    def __init__(self, id, name, etag, access_role):
        self.id = id
        self.name = name
        self.etag = etag
        self.access_role = access_role

        self.file_name = f"{self.id.strip().lower()}.ics"


class GoogleApis:

    @staticmethod
    def request_cal_etag(credentials, cal_id):
        with build('calendar', 'v3', credentials=credentials) as service:
            return service.events().list(calendarId=cal_id, maxResults=1).execute()['etag']
            #

    @staticmethod
    def request_cal_details(credentials, cal_id):
        with build('calendar', 'v3', credentials=credentials) as service:
            return service.calendars().get(calendarId=cal_id).execute()

    @staticmethod
    def request_cal_list(credentials):
        with build('calendar', 'v3', credentials=credentials) as service:
            return service.calendarList().list().execute()

    def request_cal_as_ical(self, cal_id, credentials):
        url = GOOGLE_CALDAV_URI_FORMAT.format(cal_id=urllib.parse.quote(cal_id))
        return self._request_with_token(url, credentials).text

    @staticmethod
    def _request_with_token(url, credentials, raise_for_status=True):
        headers = {'Authorization': f"Bearer {credentials.token}"}
        response = requests.get(url, headers=headers)
        if raise_for_status:
            response.raise_for_status()
        return response
