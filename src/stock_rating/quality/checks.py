from stock_rating.pipeline.daily import RefreshTask


def count_stale_tasks(tasks: list[RefreshTask]) -> int:
    return sum(1 for task in tasks if task.freshness_status == "stale")
