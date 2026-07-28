"""Pure profit-recognition math for the cost-recovery P&L method.

No Frappe imports — these helpers are deterministic so they can be unit-tested
in isolation. See docs/superpowers/specs/2026-07-28-pnl-cost-recovery-design.md.

Cost recovery (variant A, per deal): a deal recognizes no profit until the money
collected on it covers its cost of goods (COGS); every dollar collected beyond
COGS is profit, capped at the deal's total profit (margin + interest).
"""


def recognized_amount(collected, cogs, total_profit):
    """Profit recognized on a deal once `collected` has been received."""
    if total_profit <= 0:
        return 0.0
    above_cost = collected - cogs
    if above_cost <= 0:
        return 0.0
    return min(above_cost, total_profit)


def recognized_delta(collected_before, collected_after, cogs, total_profit):
    """Profit recognized during a period = recognized(after) - recognized(before)."""
    return (
        recognized_amount(collected_after, cogs, total_profit)
        - recognized_amount(collected_before, cogs, total_profit)
    )


def split_recognized(recognized, margin, total_profit):
    """Split a recognized-profit amount into (margin_part, interest_part),
    proportional to the deal's margin vs interest composition."""
    if total_profit <= 0:
        return 0.0, 0.0
    margin_part = recognized * (margin / total_profit)
    return margin_part, recognized - margin_part
