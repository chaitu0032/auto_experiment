"""Durable state: layout, JSONL logs, registry, locks, inbox, approvals, recovery.

Nothing the master knows lives only in memory; it is all derived from here.
"""
