from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import httpx

from .exceptions import MyQApiError, MyQAuthError
from .models import MyQDevice

_LOGGER = logging.getLogger(__name__)

CLIENT_ID = "ANDROID_CGI_MYQ"
SCOPE = "MyQ_Residential offline_access"
REDIRECT_URI = "com.myqops://android"

AUTH_BASE = "https://partner-identity.myq-cloud.com"
AUTHORIZE_URL = f"{AUTH_BASE}/connect/authorize"
TOKEN_URL = f"{AUTH_BASE}/connect/token"

ACCOUNTS_URL = "https://accounts.myq-cloud.com/api/v6.0/accounts"
DEVICES_URL = "https://devices.myq-cloud.com/api/v5.2/Accounts/{account_id}/Devices"
COMMAND_URL = (
    "https://account-devices-gdo.myq-cloud.com/api/v5.2/"
    "Accounts/{account_id}/door_openers/{serial_number}/{action}"
)

# Current MyQ mobile-app identity.
MYQ_APP_ID = (
    "D9D7B25035D549D8A3EA16A9FFB8C927D4A19B55B8944011B2670A8321BF8312"
)
MYQ_APP_VERSION = "5.243.1.73243"
MYQ_APP_USER_AGENT = "sdk_gphone_x86/Android 11"

MYQ_LOGIN_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 11; sdk_gphone_x86) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/83.0.4103.106 Mobile Safari/537.36"
)

# Current MyQ mobile application Firebase App Check values.
FIREBASE_PROJECT_ID = "myq-transition-test"
FIREBASE_APP_ID = "1:169499880894:android:120796f2b5e44ca7"
FIREBASE_API_KEY = "AIzaSyDYwdJBRp6H3UhrCp5LGY8XTPJG7hTeCgw"
FIREBASE_DEBUG_TOKEN = "25A02BB5-4064-4555-9414-F3449D5E5E75"
ANDROID_PACKAGE = "com.chamberlain.android.liftmaster.myq"
ANDROID_CERT_SHA1 = "da2bda70ee8a9062d076babe65924caf9a8b98e9"

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SERVER_RETRY_STATUSES = {429, 500, 502, 503, 504, 521, 522}


class MyQVerificationRequired(MyQAuthError):
    """MyQ requires a six-digit verification code."""


@dataclass
class _Page:
    url: str
    status: int
    location: str | None
    body: str


@dataclass
class _Form:
    action: str
    fields: dict[str, str]
    email_field: str | None = None
    password_field: str | None = None
    otp_field: str | None = None


class MyQCloudClient:
    """Async MyQ cloud client using OAuth 2.0 + PKCE."""

    def __init__(
        self,
        session: Any,
        username: str,
        password: str,
        host: str,
        api_version: str = "v6.0",
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_update_callback: Callable[[str | None, str | None], Awaitable[None]] | None = None,
    ) -> None:
        # The Home Assistant aiohttp session is retained for API compatibility,
        # but the OAuth flow uses its own IPv4-forced httpx client.
        self.session = session
        self.username = username
        self.password = password
        self.host = host
        self.api_version = api_version

        self.access_token: str | None = access_token
        self.refresh_token: str | None = refresh_token
        self._token_update_callback = token_update_callback

        self.account_id: str | None = None
        self.account_ids: list[str] = []

        self._oauth_cookies: dict[str, str] = {}
        self._pkce_verifier: str | None = None
        self._mfa_form: _Form | None = None
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http

        # Construct the transport off the HA event loop. This avoids the
        # load_verify_locations blocking warning that occurred previously.
        transport = await asyncio.to_thread(
            httpx.AsyncHTTPTransport,
            local_address="0.0.0.0",
            retries=0,
        )

        self._http = httpx.AsyncClient(
            transport=transport,
            http2=True,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                30.0,
                connect=15.0,
            ),
        )

        return self._http

    async def _notify_token_update(self) -> None:
        """Persist updated OAuth tokens when the client is used by an entry."""
        if self._token_update_callback is None:
            return

        await self._token_update_callback(
            self.access_token,
            self.refresh_token,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _login_headers(
        self,
        extra: dict[str, str] | None = None,
        *,
        referer: str | None = None,
        form_post: bool = False,
    ) -> dict[str, str]:
        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.9"
            ),
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": MYQ_LOGIN_USER_AGENT,
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "upgrade-insecure-requests": "1",
        }

        if referer:
            headers["Referer"] = referer

        if form_post:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Origin"] = AUTH_BASE
            headers["sec-fetch-site"] = "same-origin"

        if self._oauth_cookies:
            headers["Cookie"] = self._cookie_header()

        if extra:
            headers.update(extra)

        return headers

    def _api_headers(
        self,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "App-Version": MYQ_APP_VERSION,
            "BrandId": "1",
            "MyQApplicationId": MYQ_APP_ID,
            "User-Agent": MYQ_APP_USER_AGENT,
        }

        if self.access_token:
            token = self.access_token

            # OAuth access tokens must be sent as a Bearer token.
            if not token.lower().startswith("bearer "):
                token = f"Bearer {token}"

            headers["Authorization"] = token

        if extra:
            headers.update(extra)

        return headers

    def _merge_cookies(self, response: httpx.Response) -> None:
        for raw_cookie in response.headers.get_list("set-cookie"):
            first_part = raw_cookie.split(";", 1)[0].strip()

            if "=" not in first_part:
                continue

            name, value = first_part.split("=", 1)
            name = name.strip()

            if name:
                self._oauth_cookies[name] = value.strip()

    def _cookie_header(self) -> str:
        return "; ".join(
            f"{name}={value}"
            for name, value in self._oauth_cookies.items()
        )

    @staticmethod
    def _make_pkce() -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)[:128]

        digest = hashlib.sha256(
            verifier.encode("ascii")
        ).digest()

        challenge = (
            base64.urlsafe_b64encode(digest)
            .decode("ascii")
            .rstrip("=")
        )

        return verifier, challenge

    async def _request_page(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Page:
        client = await self._get_http()

        url = urljoin(AUTH_BASE, url)

        response = await client.request(
            method,
            url,
            data=data,
            headers=headers,
        )

        self._merge_cookies(response)

        body = response.text

        return _Page(
            url=str(response.url),
            status=response.status_code,
            location=response.headers.get("location"),
            body=body,
        )

    async def _follow_redirects(
        self,
        page: _Page,
    ) -> tuple[str | None, _Page]:
        current = page

        for _ in range(12):
            location = current.location

            if not location:
                return None, current

            target = urljoin(current.url, location)

            # This is the custom OAuth redirect URI used by the MyQ app.
            if target.startswith(REDIRECT_URI):
                query = parse_qs(urlparse(target).query)
                code = (query.get("code") or [None])[0]

                if code:
                    return code, current

                error = (query.get("error") or ["unknown"])[0]
                description = (
                    query.get("error_description") or [""]
                )[0]

                raise MyQAuthError(
                    "MyQ OAuth callback did not contain an "
                    f"authorization code "
                    f"(error={error}, description={description})"
                )

            current = await self._request_page(
                "GET",
                target,
                headers=self._login_headers(
                    referer=current.url,
                ),
            )

        raise MyQApiError(
            "Too many redirects while completing MyQ authentication"
        )

    @staticmethod
    def _attribute(tag: str, name: str) -> str | None:
        pattern = re.compile(
            rf"\b{re.escape(name)}\s*=\s*"
            r'(?:\"([^\"]*)\"|\'([^\']*)\'|([^\s>]+))',
            re.IGNORECASE,
        )

        match = pattern.search(tag)

        if match is None:
            return None

        value = next(
            group for group in match.groups()
            if group is not None
        )

        return html.unescape(value)

    @classmethod
    def _parse_forms(cls, page_html: str) -> list[_Form]:
        forms: list[_Form] = []

        for form_match in re.finditer(
            r"(<form\b[^>]*>)(.*?)</form>",
            page_html,
            re.IGNORECASE | re.DOTALL,
        ):
            form_tag = form_match.group(1)
            form_body = form_match.group(2)

            action = cls._attribute(form_tag, "action") or ""

            fields: dict[str, str] = {}
            email_field: str | None = None
            password_field: str | None = None
            otp_field: str | None = None
            visible_fields: list[str] = []

            for input_match in re.finditer(
                r"<input\b[^>]*>",
                form_body,
                re.IGNORECASE,
            ):
                input_tag = input_match.group(0)

                name = cls._attribute(input_tag, "name")

                if not name:
                    continue

                if re.search(
                    r"\bdisabled\b",
                    input_tag,
                    re.IGNORECASE,
                ):
                    continue

                field_type = (
                    cls._attribute(input_tag, "type")
                    or "text"
                ).lower()

                if field_type in {
                    "button",
                    "image",
                    "reset",
                    "submit",
                }:
                    continue

                fields[name] = (
                    cls._attribute(input_tag, "value")
                    or ""
                )

                identity = " ".join(
                    (
                        name,
                        cls._attribute(input_tag, "id") or "",
                        cls._attribute(
                            input_tag,
                            "autocomplete",
                        ) or "",
                    )
                ).lower()

                if (
                    field_type == "email"
                    or "email" in identity
                ):
                    email_field = name

                if field_type == "password":
                    password_field = name

                if (
                    "otp" in identity
                    or "one-time-code" in identity
                    or re.search(
                        r"(^|\W)"
                        r"(verification|security)[_-]?code"
                        r"($|\W)",
                        identity,
                    )
                ):
                    otp_field = name

                if field_type in {
                    "number",
                    "tel",
                    "text",
                }:
                    visible_fields.append(name)

            if (
                otp_field is None
                and "verifyotp" in action.lower()
            ):
                otp_field = next(
                    (
                        name
                        for name in fields
                        if (
                            "otp" in name.lower()
                            or name.lower().endswith("code")
                        )
                    ),
                    visible_fields[0]
                    if len(visible_fields) == 1
                    else None,
                )

            forms.append(
                _Form(
                    action=action,
                    fields=fields,
                    email_field=email_field,
                    password_field=password_field,
                    otp_field=otp_field,
                )
            )

        return forms

    @classmethod
    def _find_login_form(cls, body: str) -> _Form:
        for form in cls._parse_forms(body):
            if form.password_field and form.email_field:
                if form.action:
                    return form

        raise MyQApiError(
            "The MyQ sign-in form could not be found"
        )

    @classmethod
    def _find_mfa_form(cls, body: str) -> _Form:
        for form in cls._parse_forms(body):
            if form.otp_field and form.action:
                return form

        raise MyQApiError(
            "The MyQ verification-code form could not be found"
        )

    @classmethod
    def _find_consent_form(cls, body: str) -> _Form | None:
        for form in cls._parse_forms(body):
            if "consent" in form.action.lower():
                return form

        return None

    @staticmethod
    def _extract_verification_token(
        body: str,
    ) -> str | None:
        patterns = (
            r'<input[^>]+name=["\']'
            r'__RequestVerificationToken'
            r'["\'][^>]+value=["\']([^"\']+)["\']',

            r'<input[^>]+value=["\']([^"\']+)["\']'
            r'[^>]+name=["\']'
            r'__RequestVerificationToken'
            r'["\']',
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                body,
                re.IGNORECASE,
            )

            if match:
                return match.group(1)

        return None

    @staticmethod
    def _validation_error(body: str) -> str | None:
        flattened = re.sub(r"\s+", " ", body)

        match = re.search(
            r"validation-summary-errors.*?"
            r"<ul>(.*?)</ul>|"
            r"field-validation-error[^>]*>(.*?)<",
            flattened,
            re.IGNORECASE,
        )

        if not match:
            return None

        raw = match.group(1) or match.group(2) or ""

        message = html.unescape(
            re.sub(r"<[^>]+>", " ", raw)
        ).strip()

        return re.sub(r"\s+", " ", message) or None

    @staticmethod
    def _is_cloudflare_challenge(body: str) -> bool:
        lowered = body.lower()

        return (
            "just a moment" in lowered
            or "verify you are human" in lowered
        )

    async def _refresh_access_token(self) -> None:
        """Exchange the saved MyQ refresh token for a new access token."""
        if not self.refresh_token:
            raise MyQAuthError("No MyQ refresh token is available")

        app_check_token = await self._get_app_check_token()
        client = await self._get_http()

        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "scope": SCOPE,
            },
            headers=self._api_headers(
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "Firebase-AppCheck-Token": app_check_token,
                }
            ),
        )

        body = response.text

        _LOGGER.info(
            "myQ OAuth refresh-token exchange: HTTP %s",
            response.status_code,
        )

        if response.status_code in (400, 401):
            raise MyQAuthError(
                "MyQ refresh token was rejected "
                f"(HTTP {response.status_code}): {body[:250]}"
            )

        if response.status_code >= 400:
            raise MyQApiError(
                "MyQ refresh-token exchange failed "
                f"(HTTP {response.status_code}): {body[:250]}"
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            raise MyQApiError(
                "MyQ returned invalid JSON during refresh-token exchange"
            ) from err

        token = payload.get("access_token")
        if not token:
            raise MyQAuthError(
                "MyQ refresh response contained no access token"
            )

        token_type = payload.get("token_type") or "Bearer"
        self.access_token = f"{token_type} {token}"

        new_refresh = payload.get("refresh_token")
        if new_refresh:
            self.refresh_token = new_refresh

        await self._notify_token_update()

        _LOGGER.info("myQ OAuth: refresh-token authentication succeeded")

    async def authenticate(self) -> None:
        """Authenticate using a saved refresh token or interactive OAuth."""

        # A refresh token is the preferred path for the permanent runtime
        # client. This prevents Home Assistant restarts/API token expiry from
        # forcing the user through the username/password + MFA flow again.
        if self.refresh_token:
            try:
                await self._refresh_access_token()
                self.account_ids = []
                self.account_id = None
                return
            except MyQAuthError as err:
                _LOGGER.warning(
                    "myQ OAuth: saved refresh token was rejected; "
                    "falling back to interactive authentication: %s",
                    err,
                )
                self.access_token = None
                self.refresh_token = None

        self._oauth_cookies.clear()
        self.access_token = None
        self.account_ids = []
        self.account_id = None
        self._mfa_form = None

        verifier, challenge = self._make_pkce()
        self._pkce_verifier = verifier

        _LOGGER.warning(
            "myQ OAuth: starting authentication"
        )

        # MyQ's current implementation intentionally double-encodes
        # this value through URL query construction.
        params = {
            "acr_values": "unified_flow:v1 brand:myq",
            "client_id": CLIENT_ID,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "login",
            "ui_locales": "en-US",
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
        }

        authorize_url = (
            f"{AUTHORIZE_URL}?{urlencode(params)}"
        )

        try:
            # Stage 1.
            page = await self._request_page(
                "GET",
                authorize_url,
                headers=self._login_headers(),
            )

            _LOGGER.warning(
                "myQ OAuth stage 1 authorize: "
                "HTTP %s, redirect=%s, cookies=%d",
                page.status,
                bool(page.location),
                len(self._oauth_cookies),
            )

            if page.status in (400, 401):
                raise MyQAuthError(
                    "MyQ authorization endpoint rejected the request"
                )

            if page.status not in _REDIRECT_STATUSES:
                raise MyQApiError(
                    "Unexpected MyQ authorization response "
                    f"(HTTP {page.status}): "
                    f"{page.body[:250]}"
                )

            if not page.location:
                raise MyQApiError(
                    "MyQ authorization response contained "
                    "no login redirect"
                )

            # Stage 2.
            login_url = urljoin(
                page.url,
                page.location,
            )

            login_page = await self._request_page(
                "GET",
                login_url,
                headers=self._login_headers(),
            )

            _LOGGER.warning(
                "myQ OAuth stage 2 login page: HTTP %s",
                login_page.status,
            )

            if login_page.status >= 400:
                raise MyQApiError(
                    "Unable to load MyQ login page "
                    f"(HTTP {login_page.status})"
                )

            if self._is_cloudflare_challenge(
                login_page.body
            ):
                raise MyQAuthError(
                    "MyQ returned a browser verification challenge"
                )

            login_form = self._find_login_form(
                login_page.body
            )

            request_token = self._extract_verification_token(
                login_page.body
            )

            if not request_token:
                raise MyQApiError(
                    "MyQ login page did not contain "
                    "__RequestVerificationToken"
                )

            fields = dict(login_form.fields)

            fields[
                login_form.email_field
                or "Email"
            ] = self.username

            fields[
                login_form.password_field
                or "Password"
            ] = self.password

            # The current MyQ flow expects these fields.
            fields["UnifiedFlowRequested"] = "True"
            fields["__RequestVerificationToken"] = request_token
            fields["brand"] = "myq"

            # Stage 3.
            submitted = await self._request_page(
                "POST",
                urljoin(
                    login_page.url,
                    login_form.action,
                ),
                data=fields,
                headers=self._login_headers(
                    referer=login_page.url,
                    form_post=True,
                ),
            )

            _LOGGER.warning(
                "myQ OAuth stage 3 credential POST: "
                "HTTP %s, redirect=%s",
                submitted.status,
                bool(submitted.location),
            )

            if submitted.status in (400, 401):
                message = self._validation_error(
                    submitted.body
                )

                raise MyQAuthError(
                    message
                    or "MyQ rejected the supplied credentials"
                )

            if submitted.status >= 400:
                raise MyQApiError(
                    "MyQ credential submission failed "
                    f"(HTTP {submitted.status}): "
                    f"{submitted.body[:250]}"
                )

            # Stage 4.
            code, result = await self._follow_redirects(
                submitted
            )

            # No OAuth callback means MyQ stopped us at an
            # intermediate page. In the current flow that is MFA.
            if code is None:
                if self._is_cloudflare_challenge(
                    result.body
                ):
                    raise MyQAuthError(
                        "MyQ returned a browser verification challenge"
                    )

                try:
                    self._mfa_form = self._find_mfa_form(
                        result.body
                    )
                except MyQApiError:
                    message = self._validation_error(
                        result.body
                    )

                    if message:
                        raise MyQAuthError(
                            message
                        ) from None

                    raise MyQApiError(
                        "MyQ returned an unexpected "
                        "post-login page: "
                        f"{result.body[:250]}"
                    ) from None

                _LOGGER.warning(
                    "myQ OAuth MFA required; "
                    "verification form detected at %s",
                    result.url,
                )

                raise MyQVerificationRequired(
                    "MyQ sent a verification code. "
                    "Enter the six-digit code to continue."
                )

            await self._exchange_code(code)

        except MyQVerificationRequired:
            raise

        except (MyQAuthError, MyQApiError):
            raise

        except httpx.HTTPError as err:
            raise MyQApiError(
                f"Unable to reach MyQ during authentication: {err}"
            ) from err

        except Exception as err:
            _LOGGER.exception(
                "Unexpected MyQ OAuth failure"
            )

            raise MyQApiError(
                f"Unexpected MyQ authentication failure: {err}"
            ) from err

    async def submit_verification_code(
        self,
        code: str,
    ) -> None:
        """Submit the six-digit MyQ MFA code."""

        if not re.fullmatch(r"\d{6}", code):
            raise MyQAuthError(
                "The MyQ verification code must contain "
                "exactly six digits"
            )

        if self._mfa_form is None:
            raise MyQAuthError(
                "The MyQ verification session is no longer active"
            )

        fields = dict(self._mfa_form.fields)

        fields[self._mfa_form.otp_field or "code"] = code

        _LOGGER.warning(
            "myQ OAuth: submitting verification code"
        )

        try:
            mfa_url = urljoin(AUTH_BASE, self._mfa_form.action)

            submitted = await self._request_page(
                "POST",
                mfa_url,
                data=fields,
                headers=self._login_headers(
                    referer=mfa_url,
                    form_post=True,
                ),
            )

            code_value, result = await self._follow_redirects(
                submitted
            )

            # Some accounts present a consent page after MFA.
            code_value, result = await self._follow_consent(
                code_value,
                result,
            )

            if code_value is None:
                message = self._validation_error(
                    result.body
                )

                # MyQ may return the MFA form again after
                # rejecting the code.
                try:
                    self._mfa_form = self._find_mfa_form(
                        result.body
                    )
                except MyQApiError:
                    pass

                raise MyQAuthError(
                    message
                    or "MyQ rejected the verification code"
                )

            _LOGGER.warning(
                "myQ OAuth: MFA accepted, OAuth code received; "
                "exchanging authorization code for tokens"
            )

            await self._exchange_code(code_value)

            _LOGGER.warning(
                "myQ OAuth: token exchange completed successfully"
            )

            self._mfa_form = None

            _LOGGER.warning(
                "myQ OAuth: MFA verification succeeded"
            )

        except MyQAuthError:
            raise

        except MyQApiError:
            raise

        except httpx.HTTPError as err:
            raise MyQApiError(
                f"Unable to reach MyQ while submitting "
                f"the verification code: {err}"
            ) from err

        except Exception as err:
            _LOGGER.exception(
                "Unexpected MyQ MFA failure"
            )

            raise MyQApiError(
                f"Unexpected MyQ MFA failure: {err}"
            ) from err

    async def _follow_consent(
        self,
        code: str | None,
        page: _Page,
    ) -> tuple[str | None, _Page]:
        if code is not None:
            return code, page

        if urlparse(page.url).path.lower() != "/consent":
            return None, page

        form = self._find_consent_form(page.body)

        if form is None:
            raise MyQApiError(
                "The MyQ consent form could not be found"
            )

        fields = dict(form.fields)
        fields["button"] = "yes"

        consent_url = urljoin(
            page.url,
            form.action,
        )

        consented = await self._request_page(
            "POST",
            consent_url,
            data=fields,
            headers=self._login_headers(
                referer=page.url,
                form_post=True,
            ),
        )

        # MyQ can return HTTP 200 while requiring us to resume
        # the original OAuth authorize callback.
        if consented.status == 200:
            query = parse_qs(
                urlparse(consent_url).query
            )

            return_url = (
                query.get("returnUrl") or [""]
            )[0]

            if return_url:
                resumed_url = urljoin(
                    AUTH_BASE,
                    return_url,
                )

                parsed = urlparse(resumed_url)

                if parsed.path == "/connect/authorize/callback":
                    resumed = await self._request_page(
                        "GET",
                        resumed_url,
                        headers=self._login_headers(
                            referer=page.url,
                        ),
                    )

                    return await self._follow_redirects(
                        resumed
                    )

        return await self._follow_redirects(
            consented
        )

    async def _exchange_code(
        self,
        code: str,
    ) -> None:
        if not self._pkce_verifier:
            raise MyQAuthError(
                "The MyQ PKCE verifier is missing"
            )

        app_check_token = await self._get_app_check_token()

        token_body = {
            "client_id": CLIENT_ID,
            "code": code,
            "code_verifier": self._pkce_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
        }

        client = await self._get_http()

        headers = self._api_headers(
            {
                "Content-Type": (
                    "application/x-www-form-urlencoded"
                ),
                "Accept": "application/json",
                "Firebase-AppCheck-Token": app_check_token,
            }
        )

        response = await client.post(
            TOKEN_URL,
            data=token_body,
            headers=headers,
        )

        body = response.text

        _LOGGER.warning(
            "myQ OAuth token exchange: HTTP %s",
            response.status_code,
        )

        if response.status_code in (400, 401):
            raise MyQAuthError(
                "MyQ token exchange was rejected "
                f"(HTTP {response.status_code}): "
                f"{body[:250]}"
            )

        if response.status_code >= 400:
            raise MyQApiError(
                "MyQ token exchange failed "
                f"(HTTP {response.status_code}): "
                f"{body[:250]}"
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            raise MyQApiError(
                "MyQ returned invalid JSON during "
                "the token exchange"
            ) from err

        token = payload.get("access_token")

        if not token:
            raise MyQAuthError(
                "MyQ token response contained no access token"
            )

        token_type = (
            payload.get("token_type")
            or "Bearer"
        )

        self.access_token = (
            f"{token_type} {token}"
        )

        self.refresh_token = payload.get(
            "refresh_token"
        )

        await self._notify_token_update()

        _LOGGER.warning(
            "myQ OAuth: authentication succeeded"
        )

    async def _get_app_check_token(self) -> str:
        client = await self._get_http()

        endpoint = (
            "https://firebaseappcheck.googleapis.com/v1/"
            f"projects/{FIREBASE_PROJECT_ID}/"
            f"apps/{FIREBASE_APP_ID}:exchangeDebugToken"
        )

        response = await client.post(
            endpoint,
            params={"key": FIREBASE_API_KEY},
            json={
                "debugToken": FIREBASE_DEBUG_TOKEN,
            },
            headers={
                "X-Android-Package": ANDROID_PACKAGE,
                "X-Android-Cert": ANDROID_CERT_SHA1,
                "Accept": "application/json",
            },
        )

        body = response.text

        if response.status_code >= 400:
            raise MyQApiError(
                "Firebase App Check failed "
                f"(HTTP {response.status_code}): "
                f"{body[:250]}"
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            raise MyQApiError(
                "Firebase App Check returned invalid JSON"
            ) from err

        token = payload.get("token")

        if not isinstance(token, str) or not token:
            raise MyQApiError(
                "Firebase App Check did not return a token"
            )

        return token

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        allow_reauth: bool = True,
        **kwargs: Any,
    ) -> Any:
        if not self.access_token:
            await self.authenticate()

        client = await self._get_http()

        last_error: Exception | None = None
        caller_headers = kwargs.pop(
            "headers",
            None,
        )

        for request_url in self._regional_urls(url):
            headers = self._api_headers(
                caller_headers
            )

            try:
                response = await client.request(
                    method,
                    request_url,
                    headers=headers,
                    **kwargs,
                )

                status = response.status_code

                if status in (401, 403):
                    if (
                        method.upper() == "PUT"
                        and status == 403
                    ):
                        raise MyQApiError(
                            "MyQ refused the device command "
                            "(HTTP 403); the opener may be "
                            "offline or unavailable"
                        )

                    if allow_reauth:
                        _LOGGER.warning(
                            "myQ API returned HTTP %s; "
                            "reacquiring OAuth token",
                            status,
                        )

                        self.access_token = None

                        await self.authenticate()

                        return await self._request_json(
                            method,
                            url,
                            allow_reauth=False,
                            headers=caller_headers,
                            **kwargs,
                        )

                    raise MyQAuthError(
                        "MyQ authentication was rejected "
                        f"(HTTP {status})"
                    )

                if status in _SERVER_RETRY_STATUSES:
                    last_error = MyQApiError(
                        "myQ service returned HTTP "
                        f"{status} at "
                        f"{urlparse(request_url).hostname}"
                    )
                    continue

                if status >= 400:
                    raise MyQApiError(
                        f"myQ API returned HTTP {status}: "
                        f"{response.text[:250]}"
                    )

                if status == 204:
                    return {}

                text = response.text

                if not text.strip():
                    return {}

                try:
                    return json.loads(text)
                except ValueError as err:
                    raise MyQApiError(
                        "myQ returned a non-JSON response: "
                        f"{text[:250]}"
                    ) from err

            except (MyQAuthError, MyQApiError):
                raise

            except httpx.HTTPError as err:
                last_error = MyQApiError(
                    "myQ request failed at "
                    f"{urlparse(request_url).hostname}: "
                    f"{err}"
                )

                continue

        if last_error:
            raise last_error

        raise MyQApiError(
            "myQ request failed"
        )

    @staticmethod
    def _regional_urls(url: str) -> list[str]:
        parsed = urlparse(url)
        host_parts = parsed.netloc.split(".")

        if not host_parts:
            return [url]

        first = host_parts[0]
        urls: list[str] = []

        for region in ("", "east", "west"):
            regional_first = (
                first
                if not region
                else f"{first}-{region}"
            )

            regional_host = ".".join(
                [regional_first, *host_parts[1:]]
            )

            urls.append(
                parsed._replace(
                    netloc=regional_host
                ).geturl()
            )

        return urls

    async def get_accounts(
        self,
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            "GET",
            ACCOUNTS_URL,
        )

        if isinstance(payload, dict):
            accounts = payload.get(
                "accounts",
                [],
            )
        elif isinstance(payload, list):
            accounts = payload
        else:
            accounts = []

        if not isinstance(accounts, list):
            return []

        self.account_ids = [
            str(item.get("id"))
            for item in accounts
            if (
                isinstance(item, dict)
                and item.get("id")
            )
        ]

        self.account_id = (
            self.account_ids[0]
            if self.account_ids
            else None
        )

        return [
            item
            for item in accounts
            if isinstance(item, dict)
        ]

    @staticmethod
    def _normalize_device(
        item: dict[str, Any],
    ) -> dict[str, Any]:
        state = item.get("state")
        state_data = (
            state
            if isinstance(state, dict)
            else {}
        )

        normalized = dict(item)

        normalized["serialNumber"] = (
            item.get("serial_number")
            or item.get("serialNumber")
            or item.get("deviceId")
        )

        normalized["doorState"] = (
            state_data.get("door_state")
            or item.get("doorState")
            or state_data.get("last_status")
            or item.get("lastStatus")
        )

        normalized["isOnline"] = (
            state_data.get("online")
            if "online" in state_data
            else item.get("isOnline")
        )

        normalized["wifiSignalStrength"] = (
            state_data.get(
                "wifi_signal_strength"
            )
            or state_data.get(
                "wifiSignalStrength"
            )
            or item.get(
                "wifi_signal_strength"
            )
            or item.get(
                "wifiSignalStrength"
            )
        )

        faults = (
            state_data.get(
                "active_fault_codes"
            )
            or state_data.get(
                "activeFaultCodes"
            )
            or item.get(
                "active_fault_codes"
            )
            or item.get(
                "activeFaultCodes"
            )
            or []
        )

        normalized["activeFaultCodes"] = faults

        family = str(
            item.get("device_family")
            or ""
        ).lower()

        if (
            "garage" in family
            or family == "gdo"
            or state_data.get("door_state")
            is not None
        ):
            normalized["productType"] = "gdo"

        return normalized

    async def get_devices(self) -> list[MyQDevice]:
        if not self.account_ids:
            accounts = await self.get_accounts()

            if not accounts:
                raise MyQApiError(
                    "The myQ account has no accessible "
                    "accounts/homes"
                )

        devices: list[MyQDevice] = []

        for account_id in self.account_ids:
            url = DEVICES_URL.format(
                account_id=account_id
            )

            payload = await self._request_json(
                "GET",
                url,
            )

            if isinstance(payload, dict):
                raw_items = payload.get(
                    "items",
                    payload.get(
                        "devices",
                        [],
                    ),
                )
            elif isinstance(payload, list):
                raw_items = payload
            else:
                raw_items = []

            if not isinstance(raw_items, list):
                continue

            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue

                state = raw.get("state")
                state_data = (
                    state
                    if isinstance(state, dict)
                    else {}
                )

                family = str(
                    raw.get("device_family")
                    or ""
                ).lower()

                looks_like_garage_door = (
                    "garage" in family
                    or family == "gdo"
                    or state_data.get(
                        "door_state"
                    ) is not None
                )

                if not looks_like_garage_door:
                    continue

                normalized = self._normalize_device(
                    raw
                )

                normalized["account_id"] = (
                    raw.get("account_id")
                    or account_id
                )

                device = MyQDevice.from_json(
                    normalized
                )

                if device is not None:
                    devices.append(device)

        _LOGGER.debug(
            "myQ device refresh found %d garage door(s)",
            len(devices),
        )

        return devices

    async def command(
        self,
        device: MyQDevice,
        action: str,
    ) -> None:
        if action not in ("open", "close"):
            raise MyQApiError(
                f"Unsupported myQ command: {action}"
            )

        account_id = (
            device.attributes.get("account_id")
            or device.attributes.get("accountId")
            or self.account_id
        )

        if not account_id:
            await self.get_devices()

            account_id = (
                device.attributes.get(
                    "account_id"
                )
                or device.attributes.get(
                    "accountId"
                )
                or self.account_id
            )

        if not account_id:
            raise MyQApiError(
                "Unable to determine the myQ account "
                "for this device"
            )

        url = COMMAND_URL.format(
            account_id=account_id,
            serial_number=device.serial_number,
            action=action,
        )

        await self._request_json(
            "PUT",
            url,
        )