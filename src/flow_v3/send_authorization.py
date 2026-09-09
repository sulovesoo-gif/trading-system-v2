"""FLOW authorization only. Missing, malformed or disagreeing approvals deny SEND."""
import os

PROFILE = 'FLOW_V3_LIVE_SEND'
ENVIRONMENT = 'FLOW_V3_ACTUAL_SEND'


def environment_enabled():
    return os.environ.get(ENVIRONMENT, 'N') == 'Y'


def send_authorized(query, created_at=None):
    if not environment_enabled():
        return False
    row = query.execute(
        'SELECT enabled,updated_at FROM flow_v3_send_profile WHERE profile_code=%s', (PROFILE,)
    ).fetchone()
    value = row.get('enabled') if isinstance(row, dict) else row[0] if row else None
    if value != 'Y' or not environment_enabled():
        return False
    if created_at is not None:
        approved_at = row['updated_at'] if isinstance(row, dict) else row[1]
        return created_at >= approved_at
    return True
