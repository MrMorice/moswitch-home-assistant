"""Normal ZHA provider with a bounded MoSwitch release-catalog cache."""

from datetime import timedelta

from zigpy.ota.providers import RemoteZigpyProvider


class MoSwitchOtaProvider(RemoteZigpyProvider):
    # The upstream default is 24 h. New verified builds should be discoverable
    # through device queries without restarting Home Assistant.
    INDEX_EXPIRATION_TIME = timedelta(minutes=5)
