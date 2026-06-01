from stock_rating.pipeline.daily import run_pipeline


def main() -> None:
    run_pipeline(refresh_prices=False, rebuild_all_stored_ratings=True)


if __name__ == "__main__":
    main()
