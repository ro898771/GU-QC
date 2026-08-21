"""
Best-effort telemetry for GU-QC: startup health check, feature-usage
capture, and failure reporting, sent to the internal Telemetry API
(lib.user_api).

Unlike a long-running GUI app, main.py / plot.py are short-lived CLI
scripts, so calls here are synchronous rather than fire-and-forget on a
background thread -- a daemon thread started right before a script exits
(e.g. in an except handler) would otherwise get killed mid-POST before it
ever reaches the server. Each call is bounded by BaseApiClient's default
5s timeout and never raises -- a Telemetry outage must never stop the
actual GU processing from proceeding.
"""

from lib.user_api.health_client import HealthClient
from lib.user_api.info_feature_client import InfoFeatureClient
from lib.user_api.info_logs_client import InfoLogsClient
from lib.user_api.local_identity import LocalIdentity

TOOL_NAME = "GU-QC"
VERSION = "0.1.0"


def _resolve_identity():
    identity = LocalIdentity()
    try:
        user_name = identity.get_current_username()
    except Exception:
        user_name = "unknown"
    try:
        ip_address = identity.get_local_ip()
    except Exception:
        ip_address = "unknown"
    return user_name, ip_address


def log_startup_health() -> None:
    """Check Telemetry API reachability. Always prints the result (success
    or failure) so there's visible confirmation telemetry is reachable at all."""
    try:
        ok, detail = HealthClient().check()
        if ok:
            print("Startup health check: OK")
        else:
            print(f"Startup health check: Failed - {detail}")
    except Exception as e:
        print(f"Startup health check: Failed - {e}")


def log_feature_click(feature_name: str) -> None:
    """Report a feature/selection usage event (InfoFeatureClient). Silent on
    failure -- a dropped usage-count ping isn't worth surfacing to the user."""
    user_name, ip_address = _resolve_identity()
    try:
        InfoFeatureClient().create(
            tool_name=TOOL_NAME,
            version=VERSION,
            feature_name=feature_name,
            user_name=user_name,
            ip_address=ip_address,
        )
    except Exception:
        pass


def log_feature_error(feature_name: str, error_message: str) -> None:
    """Report that `feature_name` failed, with the real error text folded
    into the activity-log entry (InfoLogsClient) so the failure reason is
    visible in the Telemetry API."""
    user_name, ip_address = _resolve_identity()
    try:
        InfoLogsClient().create(
            tool_name=TOOL_NAME,
            version=VERSION,
            user_name=user_name,
            ip_address=ip_address,
            log_content=f"{feature_name}: Failed on {error_message}",
        )
    except Exception:
        pass
