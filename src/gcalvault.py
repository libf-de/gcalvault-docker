import os
import glob
import re
from datetime import datetime

import requests
import urllib.parse
import pathlib
from getopt import gnu_getopt, GetoptError
from googleapiclient.discovery import build

from .google_oauth2 import GoogleOAuth2
from .git_vault_repo import GitVaultRepo
from .etag_manager import ETagManager

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


class Gcalvault:

    def __init__(self, google_oauth2=None, google_apis=None):
        self.command = None
        self.user = None
        self.includes = []
        self.export_only = False
        self.clean = False
        self.push_repo = False
        self.no_cache = False
        self.ignore_roles = []
        self.calendars = []
        self.conf_dir = os.path.expanduser("~/.gcalvault")
        self.output_dir = os.getcwd()
        self.client_id = DEFAULT_CLIENT_ID
        self.client_secret = DEFAULT_CLIENT_SECRET
        self.userfile_path = os.path.join(self.conf_dir, '.user')
        self.client_id_file = os.path.join(self.conf_dir, '.client-id')
        self.client_secret_file = os.path.join(self.conf_dir, '.client-secret')

        self._repo = None
        self._google_oauth2 = google_oauth2 if google_oauth2 is not None else GoogleOAuth2()
        self._google_apis = google_apis if google_apis is not None else GoogleApis()

    def run(self, cli_args):
        self._fetch_env()
        if not self._parse_options(cli_args):
            return
        getattr(self, self.command)()

    def noop(self):
        self._ensure_dirs()
        pass

    def sync(self):
        self._ensure_dirs()
        credentials = self._get_oauth2_credentials()

        if not self.export_only:
            self._repo = GitVaultRepo("gcalvault", self.output_dir, [".ics"])

        if self.no_cache and os.path.exists(os.path.join(self.conf_dir, ".etags")): # TODO: Do properly :(
            os.remove(os.path.join(self.conf_dir, ".etags"))

        calendars = self._get_calendars_singular(credentials)

        if self.ignore_roles:
            calendars = [cal for cal in calendars if cal.access_role not in self.ignore_roles]

        if self.includes:
            calendars = [cal for cal in calendars if cal.id in self.includes]

        cal_ids = [cal.id for cal in calendars]
        for include in self.includes:
            if include not in cal_ids:
                raise GcalvaultError(f"Specified calendar '{include}' was not found")

        if self.clean:
            self._clean_output_dir(calendars)

        self._dl_and_save_calendars(calendars, credentials)

        if self._repo:
            self._repo.commit(f"gcalvault sync on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
            if self.push_repo:
                self._repo.push()

    @staticmethod
    def usage():
        return pathlib.Path(usage_file_path).read_text().strip()

    @staticmethod
    def version():
        return pathlib.Path(version_file_path).read_text().strip()

    def _fetch_env(self):
        print(os.environ)
        self.export_only = (os.getenv("EXPORT_ONLY") or "false").lower() == "true"
        self.ignore_roles.append((os.getenv("IGNORE_ROLES") or "").split(","))
        self.conf_dir = os.getenv("CONF_DIR") or self.conf_dir
        self.output_dir = os.getenv("OUTPUT_DIR") or self.output_dir
        self.client_id = os.getenv("CLIENT_ID") or self.client_id
        self.client_secret = os.getenv("CLIENT_SECRET") or self.client_secret
        self.command = os.getenv("TASK_COMMAND") or self.command
        self.push_repo = (os.getenv("PUSH_REPO") or "false").lower() == "true"
        self.no_cache = (os.getenv("NO_CACHE") or "false").lower() == "true"

    def _parse_options(self, cli_args):
        show_help = show_version = authenticate = False

        try:
            (opts, pos_args) = gnu_getopt(
                cli_args,
                'aefi:c:o:h',
                ['export-only', 'clean', 'ignore-role=',
                 'conf-dir=', 'output-dir=', 'vault-dir=',
                 'client-id=', 'client-secret=',
                 'help', 'version', 'auth', 'push', 'no-cache',]
            )
        except GetoptError as e:
            raise GcalvaultError(e) from e

        for opt, val in opts:
            if opt in ['-e', '--export-only']:
                self.export_only = True
            elif opt in ['-f', '--clean']:
                self.clean = True
            elif opt in ['-p', '--push']:
                self.push_repo = True
            elif opt in ['--no-cache']:
                self.no_cache = True
            elif opt in ['-i', '--ignore-role']:
                self.ignore_roles.append(val.lower())
            elif opt in ['-c', '--conf-dir']:
                self.conf_dir = val
                self.userfile_path = os.path.join(self.conf_dir, '.user')
                self.client_id_file = os.path.join(self.conf_dir, '.client-id')
                self.client_secret_file = os.path.join(self.conf_dir, '.client-secret')
            elif opt in ['-o', '--output-dir', '--vault-dir']:
                self.output_dir = val
            elif opt in ['--client-id']:
                self.client_id = val
            elif opt in ['--client-secret']:
                self.client_secret = val
            elif opt in ['-h', '--help']:
                show_help = True
            elif opt in ['-a', '--auth']:
                authenticate = True
            elif opt in ['--version']:
                show_version = True

        if len(opts) == 0 and len(pos_args) == 0:
            show_help = True

        if show_help:
            print(self.usage())
            return False
        if show_version:
            print(self.version())
            return False
        if len(pos_args) >= 1:
            self.command = pos_args[0]
        if len(pos_args) >= 2:
            self.user = pos_args[1].lower().strip()
        elif os.path.exists(self.userfile_path):
            self.user = ''.join(open(self.userfile_path).readlines())
        if authenticate:
            self._authenticate()
            return False
        for arg in pos_args[2:]:
            self.includes.append(arg.lower())

        if self.command is None:
            raise GcalvaultError("<command> argument is required")
        if self.command not in COMMANDS:
            raise GcalvaultError("Invalid <command> argument")
        if self.user is None:
            raise GcalvaultError("<user> argument is required")

        return True

    def _authenticate(self):
        """
        Prompt user for email and authenticate with Google,
        to ensure credentials for headless operation
        :return: none
        """
        self._ensure_dirs()
        token_file_path = os.path.join(self.conf_dir, f"{self.user}.token.json")
        if os.path.exists(token_file_path):
            print(f"Removing existing configuration {self.user}.token.json...")
            os.remove(token_file_path)
        self.user = None
        while self.user is None:
            self.user = input("Enter your google account email: ")
            if re.fullmatch(r"([A-Za-z0-9]+[.-_])*[A-Za-z0-9]+@[A-Za-z0-9-]+(\.[A-Z|a-z]{2,})+", self.user) is None:
                print("Invalid email specified!")
                self.user = None
        if self._get_oauth2_credentials() is not None:
            print("Authenticated successfully!")
            # Save used Client ID/Secret if not the default ones
            if self.client_secret != DEFAULT_CLIENT_SECRET or self.client_id != DEFAULT_CLIENT_ID:
                with open(self.client_id_file, 'w') as cif:
                    cif.write(self.client_id)
                    cif.close()
                with open(self.client_secret_file, 'w') as csf:
                    csf.write(self.client_secret)
                    csf.close()

    def _ensure_dirs(self):
        """
        Ensure working directories (config and output) are existant
        :return: none
        """
        for directory in [self.conf_dir, self.output_dir]:
            pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

    def _get_oauth2_credentials(self):
        token_file_path = os.path.join(self.conf_dir, f"{self.user}.token.json")

        # Load Client ID/Secret from file if exists and currently using default ones
        if self.client_id == DEFAULT_CLIENT_ID and os.path.exists(self.client_id_file):
            self.client_id = ''.join(open(self.client_id_file).readlines())

        if self.client_secret == DEFAULT_CLIENT_SECRET and os.path.exists(self.client_secret_file):
            self.client_secret = ''.join(open(self.client_secret_file).readlines())

        (credentials, new_authorization) = self._google_oauth2 \
            .get_credentials(token_file_path, self.client_id, self.client_secret, OAUTH_SCOPES, self.user)

        if new_authorization:
            user_info = self._google_oauth2.request_user_info(credentials)
            profile_email = user_info['email'].lower().strip()
            if self.user != profile_email:
                if os.path.exists(token_file_path):
                    os.remove(token_file_path)
                raise GcalvaultError(
                    f"Authenticated user - {profile_email} - was different than <user> argument specified")
            with open(self.userfile_path, 'w') as f:
                f.write(self.user)
                f.close()

        return credentials

    def _get_calendars(self, credentials):
        calendars = []
        calendar_list = self._google_apis.request_cal_list(credentials)
        for item in calendar_list['items']:
            cal_details = self._google_apis.request_cal_details(credentials, item['id'])
            calendars.append(
                Calendar(item['id'], item['summary'], cal_details['etag'], item['accessRole']))
        return calendars

    def _get_calendars_singular(self, credentials):
        """
        Updates the etag in the stored calendar list
        :param credentials: Google API credentials
        :return: list<Calendar>
        """
        if len(self.calendars) == 0 or self.no_cache:
            self.calendars = self._get_calendars(credentials)
            return self.calendars
        for calendar in self.calendars:
            cal_details = self._google_apis.request_cal_details(credentials, calendar.id)
            calendar.etag = cal_details['etag']
        return self.calendars

    def _clean_output_dir(self, calendars):
        cal_file_names = [cal.file_name for cal in calendars]
        file_names_on_disk = [os.path.basename(file).lower() for file in
                              glob.glob(os.path.join(self.output_dir, "*.ics"))]
        for file_name_on_disk in file_names_on_disk:
            if file_name_on_disk not in cal_file_names:
                os.remove(os.path.join(self.output_dir, file_name_on_disk))
                if self._repo:
                    self._repo.remove_file(file_name_on_disk)
                print(f"Removed file '{file_name_on_disk}'")

    def _dl_and_save_calendars(self, calendars, credentials):
        etags = ETagManager(self.conf_dir)
        for calendar in calendars:
            self._dl_and_save_calendar(calendar, credentials, etags)

    def _dl_and_save_calendar(self, calendar, credentials, etags):
        cal_file_path = os.path.join(self.output_dir, calendar.file_name)

        etag_changed = etags.test_for_change_and_save(calendar.id, calendar.etag)
        if os.path.exists(cal_file_path) and not etag_changed:
            print(f"Calendar '{calendar.name}' is up to date")
            return

        print(f"Downloading calendar '{calendar.name}'")
        ical = self._google_apis.request_cal_as_ical(calendar.id, credentials)

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
    def request_cal_details(credentials, cal_id):
        with build('calendar', 'v3', credentials=credentials) as service:
            return service.events().list(calendarId=cal_id, maxResults=1).execute()
            #return service.calendars().get(calendarId=cal_id).execute()
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
