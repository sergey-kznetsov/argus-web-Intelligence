from __future__ import annotations

from dataclasses import dataclass


class ConsumerContractError(ValueError):
    """Raised when a profiled consumer requests an unsupported ARGUS contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class ConsumerCapabilityProfile:
    capability: str
    allowed_facts: tuple[str, ...]
    default_requested_facts: tuple[str, ...] = ()
    description: str = ""

    def accepts_fact(self, fact: str) -> bool:
        return "*" in self.allowed_facts or fact in self.allowed_facts


@dataclass(frozen=True, slots=True)
class ConsumerProfile:
    consumer_id: str
    version: int
    default_capability: str
    capabilities: tuple[ConsumerCapabilityProfile, ...]
    description: str = ""

    def capability(self, name: str) -> ConsumerCapabilityProfile | None:
        for profile in self.capabilities:
            if profile.capability == name:
                return profile
        return None


@dataclass(frozen=True, slots=True)
class ResolvedConsumerContract:
    consumer_id: str
    profile_version: int | None
    capability: str | None
    requested_facts: tuple[str, ...]
    legacy_unregistered: bool = False


class ConsumerProfileRegistry:
    """Resolve consumer identity into a versioned, bounded research contract."""

    def __init__(self, profiles: tuple[ConsumerProfile, ...]) -> None:
        self._profiles = profiles
        by_id: dict[str, ConsumerProfile] = {}
        for profile in profiles:
            key = self._identity(profile.consumer_id)
            if key in by_id:
                raise ValueError(f"duplicate consumer profile: {profile.consumer_id}")
            by_id[key] = profile
        self._by_id = by_id

    def get(self, consumer: str) -> ConsumerProfile | None:
        return self._by_id.get(self._identity(consumer))

    def all(self) -> tuple[ConsumerProfile, ...]:
        return self._profiles

    def resolve(
        self,
        *,
        consumer: str,
        capability: str | None,
        requested_facts: list[str] | tuple[str, ...],
        profile_version: int | None,
    ) -> ResolvedConsumerContract:
        requested_consumer = consumer.strip()
        profile = self.get(requested_consumer)
        structured_request = (
            capability is not None
            or bool(requested_facts)
            or profile_version is not None
        )

        if profile is None:
            if structured_request:
                raise ConsumerContractError(
                    "UNKNOWN_CONSUMER",
                    (
                        f"consumer '{requested_consumer}' is not registered for profiled "
                        "ARGUS requests"
                    ),
                )
            return ResolvedConsumerContract(
                consumer_id=requested_consumer,
                profile_version=None,
                capability=None,
                requested_facts=(),
                legacy_unregistered=True,
            )

        if profile_version is not None and profile_version != profile.version:
            raise ConsumerContractError(
                "UNSUPPORTED_CONSUMER_PROFILE_VERSION",
                (
                    f"consumer '{profile.consumer_id}' supports profile version "
                    f"{profile.version}, got {profile_version}"
                ),
            )

        capability_name = self._token(capability or profile.default_capability, "capability")
        capability_profile = profile.capability(capability_name)
        if capability_profile is None:
            allowed = ", ".join(item.capability for item in profile.capabilities)
            raise ConsumerContractError(
                "UNSUPPORTED_CAPABILITY",
                (
                    f"consumer '{profile.consumer_id}' does not support capability "
                    f"'{capability_name}'; allowed: {allowed}"
                ),
            )

        facts = self._fact_list(requested_facts)
        if not facts:
            facts = capability_profile.default_requested_facts

        unsupported = [fact for fact in facts if not capability_profile.accepts_fact(fact)]
        if unsupported:
            raise ConsumerContractError(
                "UNSUPPORTED_REQUESTED_FACT",
                (
                    f"capability '{capability_profile.capability}' does not allow: "
                    f"{', '.join(unsupported)}"
                ),
            )

        return ResolvedConsumerContract(
            consumer_id=profile.consumer_id,
            profile_version=profile.version,
            capability=capability_profile.capability,
            requested_facts=facts,
        )

    @staticmethod
    def _identity(value: str) -> str:
        return value.strip().casefold()

    @staticmethod
    def _token(value: str, field: str) -> str:
        normalized = value.strip().casefold().replace("-", "_")
        if not normalized:
            raise ConsumerContractError(
                "INVALID_CONSUMER_CONTRACT",
                f"{field} must not be blank",
            )
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.")
        if any(char not in allowed for char in normalized):
            raise ConsumerContractError(
                "INVALID_CONSUMER_CONTRACT",
                f"{field} must use lowercase ASCII contract tokens",
            )
        return normalized

    @classmethod
    def _fact_list(cls, values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = cls._token(value, "requested_facts")
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return tuple(result)


KRAKEN_URBAN_SIGNALS = ConsumerCapabilityProfile(
    capability="urban_signals",
    allowed_facts=(
        "review",
        "complaint",
        "public_appeal",
        "post",
        "comment",
        "resident_message",
        "local_news_mention",
        "incident_mention",
    ),
    default_requested_facts=(
        "review",
        "complaint",
        "public_appeal",
        "post",
        "comment",
        "resident_message",
        "local_news_mention",
        "incident_mention",
    ),
    description=(
        "Public resident and local-context messages suitable for Kraken spatial-semantic "
        "analysis."
    ),
)

CONSUMER_PROFILE_REGISTRY = ConsumerProfileRegistry(
    (
        ConsumerProfile(
            consumer_id="kraken.development.uds",
            version=1,
            default_capability="urban_signals",
            capabilities=(KRAKEN_URBAN_SIGNALS,),
            description="Kraken Development UDS spatial-semantic urban signal analysis.",
        ),
        ConsumerProfile(
            consumer_id="test",
            version=1,
            default_capability="generic_research",
            capabilities=(
                ConsumerCapabilityProfile(
                    capability="generic_research",
                    allowed_facts=("*",),
                    description="CI/manual smoke profile; not a product consumer contract.",
                ),
            ),
            description="Internal ARGUS CI and manual smoke consumer.",
        ),
    )
)


def consumer_profile_catalog() -> list[dict[str, object]]:
    """Return a stable JSON-ready registry description for documentation/introspection."""

    result: list[dict[str, object]] = []
    for profile in CONSUMER_PROFILE_REGISTRY.all():
        result.append(
            {
                "consumer_id": profile.consumer_id,
                "version": profile.version,
                "default_capability": profile.default_capability,
                "description": profile.description,
                "capabilities": [
                    {
                        "capability": capability.capability,
                        "allowed_facts": list(capability.allowed_facts),
                        "default_requested_facts": list(
                            capability.default_requested_facts
                        ),
                        "description": capability.description,
                    }
                    for capability in profile.capabilities
                ],
            }
        )
    return result
