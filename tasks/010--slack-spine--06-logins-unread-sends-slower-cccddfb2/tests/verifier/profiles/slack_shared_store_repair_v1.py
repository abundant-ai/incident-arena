PROFILE = {
    "type": "database_survival",
    "timeout_s": 30,
    "protected_events": [
        {"service": "svc-auth", "event": "store_consistency_strict"},
        {"service": "svc-workspace", "event": "store_consistency_strict"},
        {"service": "svc-notification", "event": "store_consistency_strict"},
    ],
}
