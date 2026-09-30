PROFILE = {
    "type": "database_survival",
    "timeout_s": 30,
    "protected_events": [
        {"service": "svc-channel", "event": "read_consistency_strict"},
        {"service": "svc-auth", "event": "store_consistency_strict"},
    ],
}
