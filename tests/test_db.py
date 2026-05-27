from stock_rating.db import format_masked_database_url, mask_database_url


def test_mask_database_url_hides_password_components() -> None:
    masked = mask_database_url("postgresql://postgres:secret@db.example.com:5432/postgres")

    assert masked.username == "postgres"
    assert masked.host == "db.example.com"
    assert masked.port == 5432
    assert masked.database == "postgres"


def test_format_masked_database_url_replaces_password() -> None:
    masked = mask_database_url("postgresql://postgres:secret@db.example.com:5432/postgres")

    formatted = format_masked_database_url(masked)

    assert formatted == "postgresql://postgres:***@db.example.com:5432/postgres"