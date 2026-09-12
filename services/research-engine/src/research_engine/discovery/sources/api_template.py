"""Template for a source that needs an API key. Registered, visible, and deliberately not working.

Most business directories and platforms only allow automated access through their own API, with a
key the operator obtains and terms they accept. This module is the shape such a connector takes:
declared capabilities, declared credentials, a configuration form, and a refusal with
`auth_required` until it is configured.

It is disabled in the registry and it stays disabled after configuration, because there is no real
endpoint behind it. Nothing here invents a key, guesses an endpoint or pretends to return data - the
UI says "template" and the run says why it cannot start. To turn it into a real source: copy this
module, point `_ENDPOINT` at the documented API, implement `discover`, and register it.
"""

from collections.abc import Mapping
from typing import Final

from research_engine.discovery.sources.base import (
    DiscoveryError,
    DiscoveryPage,
    Estimate,
    SourceContext,
    SourceRequest,
)
from research_engine.domain import (
    Capability,
    ConfigField,
    ConfigFieldKind,
    DiscoveryErrorCode,
    JsonObject,
    SourceKind,
    SourceMetadata,
)

KEY: Final = "directory.api_template"
VERSION: Final = "1.0.0"
_NOT_IMPLEMENTED: Final = (
    "This is a connector template, not a source. Copy the module, point it at a directory API you "
    "are authorised to use, implement discover(), and register it in discovery/registry.py."
)


class ApiKeyTemplateSource:
    """A blocked connector: it shows what a keyed source declares and refuses to run."""

    metadata = SourceMetadata(
        key=KEY,
        name="Business directory API (template, not configured)",
        kind=SourceKind.DIRECTORY,
        version=VERSION,
        capabilities=[
            Capability.PAGINATION,
            Capability.GEOGRAPHY_LOCALITY,
            Capability.CATEGORY_TERMS,
        ],
        terms_url=None,
        terms_summary=(
            "A directory that only allows automated access through its own API. It needs a key the "
            "operator obtains and terms they accept; this app never works around either."
        ),
        requires_credentials=True,
        automation_allowed=True,
        default_requests_per_minute=10,
        default_daily_request_budget=500,
        default_page_size=50,
        config_fields=[
            ConfigField(
                key="base_url",
                label="API base URL",
                kind=ConfigFieldKind.TEXT,
                required=True,
                help="The documented endpoint of the directory's own API.",
            ),
            ConfigField(
                key="api_key",
                label="API key",
                kind=ConfigFieldKind.SECRET,
                required=True,
                help=(
                    "Issued to the operator by the directory. Stored with the connector, "
                    "never inside a campaign."
                ),
            ),
        ],
    )

    async def validate_config(self, config: Mapping[str, object]) -> list[str]:
        problems = [
            f"{field.label} is required"
            for field in self.metadata.config_fields
            if field.required and not str(config.get(field.key) or "").strip()
        ]
        return problems or [_NOT_IMPLEMENTED]

    async def estimate(self, request: SourceRequest, context: SourceContext) -> Estimate:
        return Estimate(value=None, basis="the source is not configured, so nothing can be counted")

    async def discover(
        self, request: SourceRequest, cursor: str | None, context: SourceContext
    ) -> DiscoveryPage:
        raise DiscoveryError(DiscoveryErrorCode.AUTH_REQUIRED, _NOT_IMPLEMENTED)

    def checkpoint(self, cursor: str | None) -> JsonObject:
        return {}


SOURCE = ApiKeyTemplateSource()
